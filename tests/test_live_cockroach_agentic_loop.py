from __future__ import annotations

import os
import sqlite3
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.application.dependencies import (
    configure_application_dependencies,
    get_application_dependencies,
)
from backend.application.learning_loop import ENRICHMENT_WORKFLOW
from backend.domain import DEFAULT_WORKSPACE_ID
from backend.infrastructure.cockroach.importer import destination_counts
from backend.memory import proposals
from backend.memory.service import search_memories
from backend.rag import config
from backend.rag.rag_service import RetrievedSource
from backend.repositories.cockroach.connection import dispose_engine, get_engine
from backend.study import quiz_api
from backend.study.quiz_generator import (
    GeneratedGroundedQuiz,
    GroundedQuiz,
    GroundedQuizQuestion,
)


@unittest.skipUnless(
    os.getenv("RUN_LIVE_COCKROACH_AGENTIC_LOOP") == "1",
    "Set RUN_LIVE_COCKROACH_AGENTIC_LOOP=1 only for the authorized live loop.",
)
class LiveCockroachAgenticLoopTest(unittest.TestCase):
    def test_complete_loop_uses_only_cockroach_and_survives_restart(self) -> None:
        self.assertEqual(config.PERSISTENCE_BACKEND, "cockroach")
        configure_application_dependencies(None)
        dependencies = get_application_dependencies()
        baseline = destination_counts(get_engine())
        adapter_names = {
            type(value).__name__
            for name, value in vars(dependencies).items()
            if name not in {"workspace_id", "unit_of_work"}
        }
        self.assertFalse(any(name.startswith(("SQLite", "Chroma")) for name in adapter_names))

        local_access_error = AssertionError(
            "Cockroach runtime attempted to access a SQLite or Chroma source."
        )
        with (
            patch.object(sqlite3, "connect", side_effect=local_access_error),
            patch("chromadb.PersistentClient", side_effect=local_access_error),
        ):
            self._verify_api_startup_and_dashboard()
            source = self._retrieve_document_evidence(dependencies)
            run_token = uuid4().hex
            topic = f"gate6live{run_token}"
            generated = self._generated_quiz(topic, source)
            test_vector = [1.0] + [0.0] * (config.EMBEDDING_DIMENSION - 1)

            with (
                patch.object(quiz_api, "generate_grounded_quiz", return_value=generated),
                patch(
                    "backend.repositories.cockroach.vectors.encode_documents",
                    return_value=[test_vector],
                ),
                patch(
                    "backend.repositories.cockroach.vectors.encode_query",
                    return_value=test_vector,
                ),
            ):
                first = quiz_api.generate_quiz_for_api(topic, 1)
                self.assertFalse(first.adaptation.adapted)
                self.assertIsNotNone(
                    dependencies.workflows.get(first.quiz_id, quiz_api.PENDING_QUIZ_WORKFLOW)
                )

                restarted = self._restart_dependencies()
                self.assertIsNotNone(
                    restarted.workflows.get(first.quiz_id, quiz_api.PENDING_QUIZ_WORKFLOW)
                )
                submitted = quiz_api.submit_quiz(
                    first.quiz_id,
                    [quiz_api.QuizResponse(question_number=1, selected_option=1)],
                )
                self.assertEqual(
                    self._valid_citation_count(submitted.attempt_id),
                    1,
                )
                self.assertEqual(len(submitted.learning_signals), 1)
                self.assertEqual(submitted.learning_signals[0].signal_type, "knowledge_gap")
                self.assertEqual(len(submitted.memory_proposals), 1)
                proposal_id = submitted.memory_proposals[0].id
                enrichment_id = submitted.enrichment_workflow_id
                self.assertIsNotNone(enrichment_id)

                restarted = self._restart_dependencies()
                self.assertIsNotNone(proposals.get_memory_proposal(proposal_id))
                self.assertIsNotNone(
                    restarted.workflows.get(str(enrichment_id), ENRICHMENT_WORKFLOW)
                )
                edited_content = (
                    f"The learner needs targeted practice with {topic} before advanced work."
                )
                accepted = proposals.decide_memory_proposal(
                    proposal_id, "accept", edited_content=edited_content
                )
                self.assertIsNotNone(accepted.saved_memory)
                memory_id = accepted.saved_memory.id
                signal_id = submitted.learning_signals[0].id
                linked_signal = restarted.learning_signals.get(signal_id)
                self.assertEqual(linked_signal.memory_id, memory_id)

                job_rows = restarted.vector_outbox.list_retryable(limit=100)
                self.assertFalse(any(job.entity_id == str(memory_id) for job in job_rows))
                vector_matches = search_memories(topic, k=5)
                self.assertIn(memory_id, [match.memory_id for match in vector_matches])

                later = quiz_api.generate_quiz_for_api(topic, 1)
                self.assertTrue(later.adaptation.adapted)
                self.assertIn(memory_id, later.adaptation.memory_ids)
                self.assertNotEqual(first.adaptation.applied_changes, later.adaptation.applied_changes)
                event = next(
                    item
                    for item in restarted.adaptation_events.list("quiz")
                    if item.request_id == later.quiz_id
                )
                self.assertIn(memory_id, event.memory_ids)
                self.assertIn(signal_id, event.learning_signal_ids)
                self.assertTrue(event.applied_changes)
                self.assertTrue(event.reason)

                final_dependencies = self._restart_dependencies()
                self.assertIsNotNone(final_dependencies.memories.get(memory_id))
                self.assertIsNotNone(final_dependencies.learning_signals.get(signal_id))
                self.assertIsNotNone(
                    final_dependencies.workflows.get(
                        later.quiz_id, quiz_api.PENDING_QUIZ_WORKFLOW
                    )
                )
                self.assertTrue(
                    any(
                        item.request_id == later.quiz_id
                        for item in final_dependencies.adaptation_events.list("quiz")
                    )
                )
                persisted_matches = search_memories(topic, k=5)
                self.assertIn(memory_id, [match.memory_id for match in persisted_matches])
                self.assertEqual(self._lineage_mismatch_count(), 0)

        final_counts = destination_counts(get_engine())
        expected_growth = {
            "quiz_attempts": 1,
            "quiz_question_attempts": 1,
            "quiz_question_sources": 1,
            "learning_signals": 1,
            "memories": 1,
            "learner_memory_embeddings": 1,
            "vector_outbox": 1,
            "workflow_states": 4,
            "adaptation_events": 2,
        }
        for name, growth in expected_growth.items():
            self.assertEqual(final_counts[name], baseline[name] + growth, name)
        for name in set(baseline) - set(expected_growth):
            self.assertEqual(final_counts[name], baseline[name], name)

    def _verify_api_startup_and_dashboard(self) -> None:
        from backend.api.app import create_app

        with TestClient(create_app(get_application_dependencies())) as client:
            health = client.get("/api/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["status"], "ok")
            dashboard = client.get("/api/dashboard")
            self.assertEqual(dashboard.status_code, 200)
            self.assertGreaterEqual(dashboard.json()["counts"]["documents"], 1)

    def _retrieve_document_evidence(self, dependencies) -> RetrievedSource:
        with get_engine().connect() as connection:
            vector = list(
                connection.execute(
                    text(
                        "SELECT embedding::FLOAT8[] FROM document_chunks "
                        "WHERE workspace_id=:workspace_id AND embedding IS NOT NULL "
                        "ORDER BY chunk_index LIMIT 1"
                    ),
                    {"workspace_id": DEFAULT_WORKSPACE_ID},
                ).scalar_one()
            )
        with patch(
            "backend.repositories.cockroach.vectors.encode_query", return_value=vector
        ):
            results = dependencies.document_vectors.search(
                "controlled live evidence", 1, {"document_id": {"$eq": 1}}
            )
        self.assertEqual(len(results), 1)
        document, distance = results[0]
        metadata = document.metadata
        return RetrievedSource(
            index=1,
            filename=str(metadata["filename"]),
            page_number=metadata.get("page_number"),
            chunk_index=int(metadata["chunk_index"]),
            distance=float(distance),
            text=document.page_content,
            document_id=int(metadata["document_id"]),
            mime_type=str(metadata["mime_type"]),
            slide_number=metadata.get("slide_number"),
        )

    def _restart_dependencies(self):
        dispose_engine()
        configure_application_dependencies(None)
        return get_application_dependencies()

    @staticmethod
    def _valid_citation_count(attempt_public_id: int) -> int:
        with get_engine().connect() as connection:
            return int(
                connection.execute(
                    text(
                        """
                        SELECT count(*)
                        FROM quiz_question_sources src
                        JOIN quiz_question_attempts q ON q.id=src.question_attempt_id
                        JOIN quiz_attempts attempt ON attempt.id=q.quiz_attempt_id
                        JOIN document_chunks chunk ON chunk.id=src.document_chunk_id
                        WHERE attempt.public_id=:attempt_id
                          AND chunk.workspace_id=src.workspace_id
                          AND chunk.document_id=src.document_id
                          AND chunk.chunk_index=src.chunk_index
                        """
                    ),
                    {"attempt_id": int(attempt_public_id)},
                ).scalar_one()
            )

    @staticmethod
    def _lineage_mismatch_count() -> int:
        with get_engine().connect() as connection:
            return int(
                connection.execute(
                    text(
                        """
                        SELECT count(*) FROM quiz_question_sources src
                        WHERE src.document_id IS NOT NULL
                          AND src.chunk_index IS NOT NULL
                          AND (
                            src.document_chunk_id IS NULL OR NOT EXISTS (
                              SELECT 1 FROM document_chunks chunk
                              WHERE chunk.id=src.document_chunk_id
                                AND chunk.workspace_id=src.workspace_id
                                AND chunk.document_id=src.document_id
                                AND chunk.chunk_index=src.chunk_index
                            )
                          )
                        """
                    )
                ).scalar_one()
            )

    @staticmethod
    def _generated_quiz(topic: str, source: RetrievedSource) -> GeneratedGroundedQuiz:
        return GeneratedGroundedQuiz(
            requested_topic=topic,
            sources=(source,),
            quiz=GroundedQuiz(
                should_generate=True,
                topic=topic,
                questions=[
                    GroundedQuizQuestion(
                        question=f"Which controlled answer is correct for {topic}?",
                        options=["Incorrect", "Correct", "Distractor A", "Distractor B"],
                        correct_option=2,
                        explanation="The imported CockroachDB evidence supports option 2 [1].",
                        source_indexes=[1],
                    )
                ],
                confidence=0.95,
                reason="Controlled CockroachDB evidence supports the quiz.",
            ),
        )


if __name__ == "__main__":
    unittest.main()
