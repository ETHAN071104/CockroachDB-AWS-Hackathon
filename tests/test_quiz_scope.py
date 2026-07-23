from __future__ import annotations

import tempfile
import unittest

from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import backend.rag.database as rag_database
import backend.study.quiz_api as quiz_api
from backend.application.dependencies import configure_application_dependencies
from backend.application.learning_loop import AdaptationContext
from backend.rag.notebooks import (
    DocumentNotFoundError,
    assign_document_to_notebook,
    create_notebook,
)
from backend.rag.rag_service import RetrievedSource
from backend.rag.scope import RetrievalScope
from backend.study.quiz_generator import (
    GeneratedGroundedQuiz,
    GroundedQuiz,
    GroundedQuizQuestion,
)
from backend.study.quiz_scope import (
    QuizScopeUnavailableError,
    resolve_quiz_scope,
)


class QuizScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary_directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        database_path = Path(temporary_directory) / "quiz-scope.db"
        self.stack.enter_context(patch.object(rag_database, "DATABASE_PATH", database_path))
        self.stack.enter_context(patch.object(rag_database, "ensure_directories"))
        rag_database.initialize_database()
        configure_application_dependencies(None)
        self.addCleanup(configure_application_dependencies, None)

        self.document_id = rag_database.insert_document(
            filename="scope-source.pdf",
            mime_type="application/pdf",
            file_hash="quiz-scope-source",
            file_data=b"scope source",
        )
        rag_database.update_chunk_count(self.document_id, 2)
        self.global_document_id = rag_database.insert_document(
            filename="global-only.pdf",
            mime_type="application/pdf",
            file_hash="global-only-source",
            file_data=b"global source",
        )
        rag_database.update_chunk_count(self.global_document_id, 1)
        self.notebook = create_notebook("Scope Notebook")
        assign_document_to_notebook(self.document_id, self.notebook.id)
        self.standard_adaptation = AdaptationContext(
            workflow_type="quiz",
            topic="scope",
            memory_ids=(),
            learning_signal_ids=(),
            memory_summaries=(),
            signal_summaries=(),
            applied_changes={},
            reason="No learner-specific adaptation is available.",
        )
        self.stack.enter_context(
            patch.object(
                quiz_api,
                "build_adaptation_context",
                return_value=self.standard_adaptation,
            )
        )

    def test_global_notebook_and_document_scope_metadata(self) -> None:
        global_scope = resolve_quiz_scope(None, personalized=False)
        self.assertEqual(global_scope.type, "global")
        self.assertEqual(global_scope.label, "All indexed documents")
        self.assertEqual(global_scope.document_count, 2)
        self.assertEqual(
            set(global_scope.resolved_document_ids),
            {self.document_id, self.global_document_id},
        )
        self.assertFalse(global_scope.personalized)

        notebook_scope = resolve_quiz_scope(
            RetrievalScope(notebook_id=self.notebook.id),
            personalized=False,
        )
        self.assertEqual(notebook_scope.type, "notebook")
        self.assertEqual(notebook_scope.label, "Scope Notebook")
        self.assertEqual(notebook_scope.notebook_name, "Scope Notebook")
        self.assertEqual(notebook_scope.document_count, 1)

        document_scope = resolve_quiz_scope(
            RetrievalScope(document_ids=(self.document_id,)),
            personalized=False,
        )
        self.assertEqual(document_scope.type, "document")
        self.assertEqual(document_scope.label, "scope-source.pdf")
        self.assertEqual(document_scope.document_name, "scope-source.pdf")
        self.assertEqual(document_scope.resolved_document_ids, (self.document_id,))

    def test_adaptive_scope_is_metadata_only_and_keeps_same_documents(self) -> None:
        standard = resolve_quiz_scope(
            RetrievalScope(document_ids=(self.document_id,)),
            personalized=False,
        )
        adaptive = resolve_quiz_scope(
            RetrievalScope(document_ids=(self.document_id,)),
            personalized=True,
        )
        self.assertEqual(adaptive.type, "adaptive-document")
        self.assertTrue(adaptive.personalized)
        self.assertEqual(adaptive.resolved_document_ids, standard.resolved_document_ids)
        self.assertIn("previous weaknesses", adaptive.description)

    def test_empty_global_and_notebook_fail_before_quiz_generation(self) -> None:
        rag_database.delete_document_record(self.document_id)
        rag_database.delete_document_record(self.global_document_id)
        generator = self.stack.enter_context(
            patch.object(quiz_api, "generate_grounded_quiz")
        )

        with self.assertRaisesRegex(
            QuizScopeUnavailableError,
            "Upload and index at least one document",
        ):
            quiz_api.generate_quiz_for_api("scope", 1)
        generator.assert_not_called()

        empty_notebook = create_notebook("Empty Notebook")
        with self.assertRaisesRegex(
            QuizScopeUnavailableError,
            "has no indexed study material",
        ):
            quiz_api.generate_quiz_for_api(
                "scope",
                1,
                RetrievalScope(notebook_id=empty_notebook.id),
            )
        generator.assert_not_called()

    def test_cross_workspace_document_never_falls_back_global(self) -> None:
        other_workspace_id = "22222222-2222-4222-8222-222222222222"
        other_document_id = rag_database.insert_document(
            filename="other-workspace.pdf",
            mime_type="application/pdf",
            file_hash="other-workspace-source",
            file_data=b"other source",
            workspace_id=other_workspace_id,
        )
        rag_database.update_chunk_count(
            other_document_id,
            1,
            workspace_id=other_workspace_id,
        )
        generator = self.stack.enter_context(
            patch.object(quiz_api, "generate_grounded_quiz")
        )

        with self.assertRaises(DocumentNotFoundError):
            quiz_api.generate_quiz_for_api(
                "scope",
                1,
                RetrievalScope(document_ids=(other_document_id,)),
            )
        generator.assert_not_called()

    def test_presented_quiz_carries_resolved_scope_without_answer_leakage(self) -> None:
        generated = GeneratedGroundedQuiz(
            requested_topic="scope",
            sources=(
                RetrievedSource(
                    index=1,
                    filename="scope-source.pdf",
                    page_number=1,
                    chunk_index=0,
                    distance=0.1,
                    text="Grounded evidence.",
                    document_id=self.document_id,
                    mime_type="application/pdf",
                ),
            ),
            quiz=GroundedQuiz(
                should_generate=True,
                topic="Scope",
                questions=[
                    GroundedQuizQuestion(
                        question="What is grounded?",
                        options=["A", "B", "C", "D"],
                        correct_option=1,
                        explanation="Grounded evidence [1].",
                        source_indexes=[1],
                    )
                ],
                confidence=0.9,
                reason="Grounded.",
            ),
        )
        self.stack.enter_context(
            patch.object(quiz_api, "generate_grounded_quiz", return_value=generated)
        )
        presented = quiz_api.generate_quiz_for_api(
            "scope",
            1,
            RetrievalScope(document_ids=(self.document_id,)),
        )
        self.assertEqual(presented.scope.type, "document")
        self.assertEqual(presented.scope.resolved_document_ids, (self.document_id,))
        self.assertEqual(presented.questions[0].options, ("A", "B", "C", "D"))
        self.assertFalse(hasattr(presented.questions[0], "correct_option"))
        self.assertTrue(
            all(isinstance(document_id, int) for document_id in presented.scope.resolved_document_ids)
        )


if __name__ == "__main__":
    unittest.main()
