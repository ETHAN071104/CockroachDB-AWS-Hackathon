from __future__ import annotations

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from backend.application.dependencies import (
    build_application_dependencies,
    configure_application_dependencies,
    get_application_dependencies,
)
from backend.application.learning_loop import (
    ENRICHMENT_WORKFLOW,
    analyze_quiz_outcomes,
)
from backend.application.vector_outbox import VectorOutboxService, VectorSynchronizationError
from backend.domain import DEFAULT_WORKSPACE_ID
from backend.memory import proposals, service as memory_service
from backend.rag import database as rag_database
from backend.rag.rag_service import RetrievedSource
from backend.repositories.chroma import ChromaDocumentVectorRepository, ChromaMemoryVectorRepository
from backend.repositories.sqlite import initialize_foundation_schema
from backend.study import database as study_database
from backend.study import quiz_api
from backend.study.quiz_generator import (
    GeneratedGroundedQuiz,
    GroundedQuiz,
    GroundedQuizQuestion,
)


SECOND_WORKSPACE_ID = "00000000-0000-4000-8000-000000000099"


class _MemoryVectors:
    def __init__(self) -> None:
        self.documents: dict[str, object] = {}
        self.fail_add = False

    def add_documents(self, *, documents, ids) -> None:
        if self.fail_add:
            raise RuntimeError("memory vector provider failed")
        self.documents.update(zip(ids, documents, strict=True))

    def delete(self, *, ids) -> None:
        for vector_id in ids:
            self.documents.pop(vector_id, None)

    def similarity_search_with_score(self, *, query, k, filter=None):
        del query, filter
        return [(document, 0.1) for document in self.documents.values()][:k]


class _DocumentVectors:
    def add_documents(self, *, documents, ids) -> None:
        del documents, ids

    def get(self, *, where, include=None):
        del where, include
        return {"ids": []}

    def delete(self, *, ids) -> None:
        del ids


class AgenticLearningLoopTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.database_path = Path(temporary) / "agentic-loop.db"
        self.stack.enter_context(
            patch.object(rag_database, "DATABASE_PATH", self.database_path)
        )
        self.stack.enter_context(patch.object(rag_database, "ensure_directories"))
        rag_database.initialize_database()
        from backend.memory.database import initialize_memory_database

        initialize_memory_database()
        study_database.initialize_study_database()
        initialize_foundation_schema()
        configure_application_dependencies(None)
        self.addCleanup(configure_application_dependencies, None)
        self.document_id = rag_database.insert_document(
            "plants.pdf",
            "application/pdf",
            "agentic-loop-source",
            b"Chlorophyll source",
        )
        rag_database.update_chunk_count(self.document_id, 1)
        self.generated = self._generated_quiz()
        self.vectors = _MemoryVectors()
        self.stack.enter_context(
            patch.object(memory_service, "get_memory_vector_store", return_value=self.vectors)
        )
        self.stack.enter_context(
            patch.object(quiz_api, "generate_grounded_quiz", return_value=self.generated)
        )

    def test_complete_quiz_signal_memory_adaptation_loop_survives_restart(self) -> None:
        first = quiz_api.generate_quiz_for_api("plant energy", 1)
        self.assertFalse(first.adaptation.adapted)
        submitted = quiz_api.submit_quiz(
            first.quiz_id,
            [quiz_api.QuizResponse(question_number=1, selected_option=1)],
        )

        dependencies = get_application_dependencies()
        self.assertEqual(len(dependencies.quizzes.list_attempts()), 1)
        self.assertEqual(len(dependencies.memories.list()), 0)
        self.assertEqual(len(submitted.learning_signals), 1)
        signal = submitted.learning_signals[0]
        self.assertEqual(signal.signal_type, "knowledge_gap")
        self.assertEqual(signal.occurrence_count, 1)
        self.assertEqual(len(signal.evidence), 1)
        self.assertEqual(len(submitted.memory_proposals), 1)
        proposal_id = submitted.memory_proposals[0].id
        enrichment_id = submitted.enrichment_workflow_id
        self.assertIsNotNone(enrichment_id)

        # Both approval state and post-transaction enrichment work are durable.
        configure_application_dependencies(None)
        restarted = get_application_dependencies()
        self.assertIsNotNone(proposals.get_memory_proposal(proposal_id))
        self.assertIsNotNone(
            restarted.workflows.get(str(enrichment_id), ENRICHMENT_WORKFLOW)
        )

        # Repeated trusted evidence updates the same signal and proposal.
        second = quiz_api.generate_quiz_for_api("plant energy", 1)
        repeated = quiz_api.submit_quiz(
            second.quiz_id,
            [quiz_api.QuizResponse(question_number=1, selected_option=1)],
        )
        repeated_signal = repeated.learning_signals[0]
        self.assertEqual(repeated_signal.id, signal.id)
        self.assertEqual(repeated_signal.signal_type, "repeated_error")
        self.assertEqual(repeated_signal.occurrence_count, 2)
        self.assertGreater(repeated_signal.confidence, signal.confidence)
        self.assertEqual(repeated.memory_proposals[0].id, proposal_id)

        # Reprocessing the exact stored evidence is idempotent.
        run_result = quiz_api.score_quiz(
            self.generated,
            [quiz_api.QuizResponse(question_number=1, selected_option=1)],
        )
        stored_attempt = restarted.quizzes.get_attempt(repeated.attempt_id)
        stored_questions = restarted.quizzes.list_questions(repeated.attempt_id)
        with restarted.unit_of_work():
            duplicate = analyze_quiz_outcomes(
                generated=self.generated,
                run_result=run_result,
                stored_attempt=stored_attempt,
                stored_questions=stored_questions,
            )
        self.assertEqual(duplicate.signals[0].occurrence_count, 2)
        self.assertEqual(len(restarted.learning_signals.list()), 1)
        self.assertEqual(restarted.workflows.count_pending("memory_proposal"), 1)

        edited_memory = (
            "The learner needs focused practice with Plant Energy concepts."
        )
        accepted = proposals.decide_memory_proposal(
            proposal_id,
            "accept",
            edited_content=edited_memory,
        )
        self.assertIsNotNone(accepted.saved_memory)
        self.assertEqual(accepted.saved_memory.content, edited_memory)
        linked = restarted.learning_signals.get(signal.id)
        self.assertEqual(linked.memory_id, accepted.saved_memory.id)
        self.assertEqual(len(restarted.memories.list()), 1)

        # The otherwise identical quiz now has observable learner adaptation.
        later = quiz_api.generate_quiz_for_api("plant energy", 1)
        self.assertTrue(later.adaptation.adapted)
        self.assertIn(accepted.saved_memory.id, later.adaptation.memory_ids)
        event = next(
            item
            for item in restarted.adaptation_events.list("quiz")
            if item.request_id == later.quiz_id
        )
        self.assertIn(accepted.saved_memory.id, event.memory_ids)
        self.assertTrue(event.applied_changes)
        self.assertTrue(event.reason)

        restarted.workspaces.create(SECOND_WORKSPACE_ID, "Isolated learner")
        isolated_dependencies = build_application_dependencies(SECOND_WORKSPACE_ID)
        isolated_document_id = isolated_dependencies.documents.insert(
            "isolated-plants.pdf",
            "application/pdf",
            "isolated-agentic-loop-source",
            b"Isolated chlorophyll source",
        )
        rag_database.update_chunk_count(
            isolated_document_id,
            1,
            workspace_id=SECOND_WORKSPACE_ID,
        )
        configure_application_dependencies(isolated_dependencies)
        isolated = quiz_api.generate_quiz_for_api("plant energy", 1)
        self.assertFalse(isolated.adaptation.adapted)
        self.assertNotEqual(later.adaptation.applied_changes, isolated.adaptation.applied_changes)
        self.assertEqual(isolated_dependencies.learning_signals.list(), [])
        self.assertEqual(isolated_dependencies.memories.list(), [])

    def test_correct_evidence_improves_and_resolves_weakness_with_recoverable_outbox(self) -> None:
        wrong = quiz_api.generate_quiz_for_api("plant energy", 1)
        result = quiz_api.submit_quiz(
            wrong.quiz_id,
            [quiz_api.QuizResponse(question_number=1, selected_option=1)],
        )
        proposal_id = result.memory_proposals[0].id
        accepted = proposals.decide_memory_proposal(proposal_id, "accept")
        memory_id = accepted.saved_memory.id

        correct = quiz_api.generate_quiz_for_api("plant energy", 1)
        improving = quiz_api.submit_quiz(
            correct.quiz_id,
            [quiz_api.QuizResponse(question_number=1, selected_option=2)],
        )
        self.assertEqual(improving.learning_signals[0].status, "improving")
        self.assertLess(improving.learning_signals[0].confidence, result.learning_signals[0].confidence)

        # A provider failure occurs only after the relational transaction commits.
        self.vectors.fail_add = True
        resolved_quiz = quiz_api.generate_quiz_for_api("plant energy", 1)
        with self.assertRaises(VectorSynchronizationError):
            quiz_api.submit_quiz(
                resolved_quiz.quiz_id,
                [quiz_api.QuizResponse(question_number=1, selected_option=2)],
            )
        dependencies = get_application_dependencies()
        resolved = dependencies.learning_signals.list()[0]
        self.assertEqual(resolved.status, "resolved")
        self.assertEqual(len(dependencies.quizzes.list_attempts()), 3)
        self.assertEqual(dependencies.memories.get(memory_id).confidence, resolved.confidence)
        failed_jobs = dependencies.vector_outbox.list_retryable()
        self.assertTrue(any(job.status == "failed" for job in failed_jobs))

        self.vectors.fail_add = False
        service = VectorOutboxService(
            dependencies.vector_outbox,
            ChromaDocumentVectorRepository(lambda: _DocumentVectors()),
            ChromaMemoryVectorRepository(lambda: self.vectors),
        )
        reconciliation = service.reconcile()
        self.assertGreaterEqual(reconciliation.completed, 1)
        self.assertEqual(reconciliation.failed, 0)

    def _generated_quiz(self) -> GeneratedGroundedQuiz:
        source = RetrievedSource(
            index=1,
            filename="plants.pdf",
            page_number=2,
            chunk_index=0,
            distance=0.1,
            text="Chlorophyll captures light energy in plants.",
            document_id=self.document_id,
            mime_type="application/pdf",
        )
        return GeneratedGroundedQuiz(
            requested_topic="plant energy",
            sources=(source,),
            quiz=GroundedQuiz(
                should_generate=True,
                topic="Plant Energy",
                questions=[
                    GroundedQuizQuestion(
                        question="What captures light energy in plants?",
                        options=["Roots", "Chlorophyll", "Oxygen", "Soil"],
                        correct_option=2,
                        explanation="Chlorophyll captures light energy [1].",
                        source_indexes=[1],
                    )
                ],
                confidence=0.95,
                reason="Grounded evidence supports the quiz.",
            ),
        )


if __name__ == "__main__":
    unittest.main()
