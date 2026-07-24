from __future__ import annotations

import tempfile
import unittest

from contextlib import ExitStack
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.rag.database as rag_database
import backend.rag.intelligence_store as intelligence_store
import backend.study.database as study_database
import backend.study.quiz_api as quiz_api
from backend.rag.notebooks import assign_document_to_notebook, create_notebook
from backend.rag.rag_service import RetrievedSource
from backend.study.quiz_generator import (
    GeneratedGroundedQuiz,
    GroundedQuiz,
    GroundedQuizQuestion,
)
from backend.study.quiz_reporting import (
    build_quiz_attempt_report,
    format_quiz_attempt_report,
)


app_module = import_module("backend.api.app")


class ReportQuizAuditTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temporary_directory = self.stack.enter_context(
            tempfile.TemporaryDirectory()
        )
        self.database_path = Path(temporary_directory) / "app.db"
        self.stack.enter_context(
            patch.object(rag_database, "DATABASE_PATH", self.database_path)
        )
        self.stack.enter_context(patch.object(rag_database, "ensure_directories"))
        rag_database.initialize_database()
        study_database.initialize_study_database()

        self.document_id = rag_database.insert_document(
            filename="audit-source.txt",
            mime_type="text/plain",
            file_hash="audit-source-hash",
            file_data=b"audit source",
        )
        rag_database.update_chunk_count(self.document_id, 1)

        quiz_api.clear_quiz_registry()
        self.addCleanup(quiz_api.clear_quiz_registry)
        self.generate_mock = self.stack.enter_context(
            patch.object(
                quiz_api,
                "generate_grounded_quiz",
                return_value=self._generated_quiz(),
            )
        )
        self.stack.enter_context(
            patch.object(
                app_module,
                "probe_vector_store",
                return_value={"status": "ok", "collection_present": True},
            )
        )
        self.stack.enter_context(
            patch.object(
                app_module,
                "probe_memory_vector_store",
                return_value={"status": "ok", "collection_present": False},
            )
        )
        self.stack.enter_context(patch("backend.api.errors.LOGGER.error"))
        self.client = self.stack.enter_context(
            TestClient(
                app_module.create_app(allow_legacy_default_workspace=True),
                raise_server_exceptions=False,
            )
        )

    def test_session_report_preserves_unsorted_notebook_snapshot_after_move(
        self,
    ) -> None:
        session = study_database.create_study_session()
        interaction, stored_sources = (
            study_database.insert_study_interaction_with_sources(
                session_id=session.id,
                question="Where was this document when cited?",
                answer="It was unsorted.",
                sources=[
                    study_database.StudySourceInput(
                        source_index=1,
                        filename="audit-source.txt",
                        page_number=None,
                        chunk_index=0,
                        distance=0.2,
                        document_id=self.document_id,
                        notebook_id=None,
                        mime_type="text/plain",
                        excerpt="Historical unsorted source.",
                    )
                ],
            )
        )
        self.assertIsNone(stored_sources[0].notebook_id)

        moved_notebook = create_notebook("Moved Later")
        assign_document_to_notebook(self.document_id, moved_notebook.id)

        response = self.client.get(
            f"/api/reports/study/sessions/{session.id}"
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(
            payload["interactions"][0]["id"],
            str(interaction.id),
        )
        source = payload["interactions"][0]["sources"][0]
        self.assertEqual(source["document_id"], str(self.document_id))
        self.assertIsNone(source["notebook_id"])

    def test_review_queue_get_applies_and_validates_flat_scope(self) -> None:
        session = study_database.create_study_session()
        interaction, _sources = (
            study_database.insert_study_interaction_with_sources(
                session_id=session.id,
                question="Which audit concept needs review?",
                answer="The scoped concept.",
                outcome="partial",
                sources=[
                    study_database.StudySourceInput(
                        source_index=1,
                        filename="audit-source.txt",
                        page_number=None,
                        chunk_index=0,
                        distance=0.2,
                        document_id=self.document_id,
                        mime_type="text/plain",
                        excerpt="Scoped audit evidence.",
                    )
                ],
            )
        )
        study_database.end_study_session(session.id)

        scoped = self.client.get(
            "/api/study/actions/review-queue",
            params=[("document_ids", str(self.document_id))],
        )
        self.assertEqual(scoped.status_code, 200, scoped.text)
        self.assertEqual(
            scoped.json()["items"][0]["interaction_id"],
            str(interaction.id),
        )

        missing = self.client.get(
            "/api/study/actions/review-queue",
            params=[("document_ids", "999999")],
        )
        self.assertEqual(missing.status_code, 404, missing.text)
        self.assertEqual(missing.json()["error"]["code"], "SCOPE_NOT_FOUND")

        notebook = create_notebook("Review Scope")
        conflicting = self.client.get(
            "/api/study/actions/review-queue",
            params=[
                ("notebook_id", str(notebook.id)),
                ("document_ids", str(self.document_id)),
            ],
        )
        self.assertEqual(conflicting.status_code, 422, conflicting.text)
        self.assertEqual(conflicting.json()["error"]["code"], "VALIDATION_ERROR")

    def test_aborted_attempt_redacts_unpresented_feedback_in_api_and_terminal(
        self,
    ) -> None:
        attempt, _questions = study_database.insert_quiz_attempt_with_questions(
            requested_topic="audit quiz",
            quiz_topic="Audit Quiz",
            confidence=0.9,
            aborted=True,
            questions=[
                study_database.QuizQuestionAttemptInput(
                    question_number=1,
                    question="Presented audit question?",
                    options=(
                        "Wrong A",
                        "PRESENTED_CORRECT_ANSWER",
                        "Wrong C",
                        "Wrong D",
                    ),
                    presented=True,
                    selected_option=2,
                    correct_option=2,
                    is_correct=True,
                    skipped=False,
                    explanation="PRESENTED_EXPLANATION_PUBLIC",
                    sources=(
                        study_database.QuizQuestionSourceInput(
                            source_index=1,
                            filename="PRESENTED_SOURCE_PUBLIC.txt",
                            page_number=None,
                            chunk_index=0,
                            distance=0.1,
                            document_id=self.document_id,
                            mime_type="text/plain",
                            excerpt="Presented source excerpt.",
                        ),
                    ),
                ),
                study_database.QuizQuestionAttemptInput(
                    question_number=2,
                    question="Unpresented audit question?",
                    options=(
                        "Wrong A",
                        "Wrong B",
                        "UNPRESENTED_CORRECT_ANSWER_SECRET",
                        "Wrong D",
                    ),
                    presented=False,
                    selected_option=None,
                    correct_option=3,
                    is_correct=False,
                    skipped=False,
                    explanation="UNPRESENTED_EXPLANATION_SECRET",
                    sources=(
                        study_database.QuizQuestionSourceInput(
                            source_index=2,
                            filename="UNPRESENTED_SOURCE_SECRET.txt",
                            page_number=None,
                            chunk_index=0,
                            distance=0.2,
                            document_id=self.document_id,
                            mime_type="text/plain",
                            excerpt="UNPRESENTED_SOURCE_EXCERPT_SECRET",
                        ),
                    ),
                ),
            ],
        )

        response = self.client.get(f"/api/reports/quizzes/{attempt.id}")
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        presented, unpresented = payload["questions"]

        self.assertEqual(presented["correct_option"], 2)
        self.assertEqual(
            presented["explanation"],
            "PRESENTED_EXPLANATION_PUBLIC",
        )
        self.assertEqual(
            presented["sources"][0]["filename"],
            "PRESENTED_SOURCE_PUBLIC.txt",
        )
        self.assertEqual(unpresented["status"], "not_presented")
        self.assertIsNone(unpresented["correct_option"])
        self.assertIsNone(unpresented["explanation"])
        self.assertEqual(unpresented["sources"], [])
        self.assertNotIn("UNPRESENTED_EXPLANATION_SECRET", response.text)
        self.assertNotIn("UNPRESENTED_SOURCE_SECRET.txt", response.text)
        self.assertNotIn("UNPRESENTED_SOURCE_EXCERPT_SECRET", response.text)

        terminal = format_quiz_attempt_report(
            build_quiz_attempt_report(attempt.id)
        )
        self.assertIn("PRESENTED_CORRECT_ANSWER", terminal)
        self.assertIn("PRESENTED_EXPLANATION_PUBLIC", terminal)
        self.assertIn("PRESENTED_SOURCE_PUBLIC.txt", terminal)
        unpresented_section = terminal.split(
            "2. Unpresented audit question?",
            maxsplit=1,
        )[1]
        self.assertNotIn("Correct answer:", unpresented_section)
        self.assertNotIn("Explanation:", unpresented_section)
        self.assertNotIn("Sources:", unpresented_section)
        self.assertNotIn(
            "UNPRESENTED_CORRECT_ANSWER_SECRET",
            unpresented_section,
        )
        self.assertNotIn(
            "UNPRESENTED_EXPLANATION_SECRET",
            unpresented_section,
        )
        self.assertNotIn(
            "UNPRESENTED_SOURCE_SECRET.txt",
            unpresented_section,
        )

    def test_quiz_generate_accepts_each_canonical_top_level_scope(self) -> None:
        notebook = create_notebook("Quiz Scope")
        assign_document_to_notebook(self.document_id, notebook.id)
        topic_id = "11111111-1111-4111-8111-111111111111"
        intelligence_store.replace_topics_for_scope(
            "documents",
            [self.document_id],
            [
                intelligence_store.TopicInput(
                    topic_id=topic_id,
                    name="Scope audit topic",
                    sources=(
                        intelligence_store.TopicSourcePair(
                            document_id=self.document_id,
                            chunk_index=0,
                            source_index=1,
                            filename="audit-source.txt",
                            mime_type="text/plain",
                            excerpt="Audit source supports this question.",
                        ),
                    ),
                ),
            ],
            fingerprint=intelligence_store.fingerprint_for_documents(
                [self.document_id]
            ),
        )
        cases = (
            ("notebook_id", notebook.id, notebook.id, None, None),
            (
                "document_ids",
                [self.document_id],
                None,
                (self.document_id,),
                None,
            ),
            ("topic_id", topic_id, None, None, topic_id),
        )

        for field, value, expected_notebook, expected_documents, expected_topic in cases:
            with self.subTest(field=field):
                quiz_api.clear_quiz_registry()
                self.generate_mock.reset_mock()
                response = self.client.post(
                    "/api/study/actions/quizzes/generate",
                    json={
                        "topic": "scope audit",
                        "question_count": 1,
                        field: value,
                    },
                )
                self.assertEqual(response.status_code, 200, response.text)
                generated_scope = self.generate_mock.call_args.kwargs["scope"]
                self.assertEqual(generated_scope.notebook_id, expected_notebook)
                self.assertEqual(
                    generated_scope.document_ids,
                    expected_documents,
                )
                self.assertEqual(generated_scope.topic_id, expected_topic)

    def test_quiz_generate_rejects_conflicting_nested_and_top_level_scope(
        self,
    ) -> None:
        notebook = create_notebook("Nested Scope")
        self.generate_mock.reset_mock()

        response = self.client.post(
            "/api/study/actions/quizzes/generate",
            json={
                "topic": "scope conflict",
                "question_count": 1,
                "document_ids": [self.document_id],
                "scope": {"notebook_id": notebook.id},
            },
        )

        self.assertEqual(response.status_code, 422, response.text)
        self.generate_mock.assert_not_called()

    def _generated_quiz(self) -> GeneratedGroundedQuiz:
        return GeneratedGroundedQuiz(
            requested_topic="scope audit",
            sources=(
                RetrievedSource(
                    index=1,
                    filename="audit-source.txt",
                    page_number=None,
                    chunk_index=0,
                    distance=0.1,
                    text="Audit source supports this question.",
                    document_id=self.document_id,
                    mime_type="text/plain",
                ),
            ),
            quiz=GroundedQuiz(
                should_generate=True,
                topic="Scope Audit",
                questions=[
                    GroundedQuizQuestion(
                        question="What supports this question?",
                        options=["A", "B", "C", "D"],
                        correct_option=1,
                        explanation="The audit source supports it [1].",
                        source_indexes=[1],
                    )
                ],
                confidence=0.9,
                reason="One grounded question is available.",
            ),
        )


if __name__ == "__main__":
    unittest.main()
