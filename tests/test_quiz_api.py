from __future__ import annotations

import tempfile
import unittest

from contextlib import ExitStack
from importlib import import_module
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.rag.database as rag_database
import backend.study.database as study_database
import backend.study.quiz_api as quiz_api
from backend.rag.notebooks import assign_document_to_notebook, create_notebook
from backend.rag.rag_service import RetrievedSource
from backend.study.quiz_generator import (
    GeneratedGroundedQuiz,
    GroundedQuiz,
    GroundedQuizQuestion,
)


app_module = import_module("backend.api.app")


class QuizApiTest(unittest.TestCase):
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
        self.stack.enter_context(
            patch.object(rag_database, "ensure_directories")
        )
        rag_database.initialize_database()
        study_database.initialize_study_database()

        self.document_id = rag_database.insert_document(
            filename="plants.pdf",
            mime_type="application/pdf",
            file_hash="quiz-source-hash",
            file_data=b"quiz source",
        )
        rag_database.update_chunk_count(self.document_id, 1)
        self.notebook = create_notebook("Biology")
        assign_document_to_notebook(self.document_id, self.notebook.id)
        self.generated_quiz = self._generated_quiz()

        quiz_api.clear_quiz_registry()
        self.addCleanup(quiz_api.clear_quiz_registry)
        self.stack.enter_context(
            patch.object(
                quiz_api,
                "generate_grounded_quiz",
                return_value=self.generated_quiz,
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

    def test_pre_submit_quiz_never_exposes_answers_or_explanations(self) -> None:
        response = self._generate()
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()

        self.assertEqual(len(payload["questions"]), 3)
        self.assertEqual(payload["scope"]["type"], "document")
        self.assertEqual(payload["scope"]["label"], "plants.pdf")
        self.assertEqual(payload["scope"]["document_count"], 1)
        self.assertEqual(
            payload["scope"]["resolved_document_ids"],
            [str(self.document_id)],
        )
        self.assertFalse(payload["scope"]["personalized"])
        self.assertEqual(quiz_api.pending_quiz_count(), 1)
        self.assertNotIn("correct_option", response.text)
        self.assertNotIn("explanation", response.text)
        for question in payload["questions"]:
            self.assertEqual(len(question["options"]), 4)
            self.assertEqual(
                set(question),
                {"question_number", "question", "options"},
            )
        self.assertEqual(study_database.list_quiz_attempts(), [])

    def test_global_scope_and_empty_explicit_scopes_are_unambiguous(self) -> None:
        global_response = self.client.post(
            "/api/study/actions/quizzes/generate",
            json={"topic": "plant energy", "question_count": 1},
        )
        self.assertEqual(global_response.status_code, 200, global_response.text)
        self.assertEqual(global_response.json()["scope"]["type"], "global")
        self.assertEqual(
            global_response.json()["scope"]["resolved_document_ids"],
            [str(self.document_id)],
        )

        empty_notebook = create_notebook("Empty quiz notebook")
        empty_response = self.client.post(
            "/api/study/actions/quizzes/generate",
            json={
                "topic": "plant energy",
                "question_count": 1,
                "notebook_id": empty_notebook.id,
            },
        )
        self.assertEqual(empty_response.status_code, 422, empty_response.text)
        self.assertEqual(
            empty_response.json()["error"]["code"],
            "SCOPE_EMPTY",
        )
        self.assertIn("has no indexed study material", empty_response.text)

        malformed = self.client.post(
            "/api/study/actions/quizzes/generate",
            json={
                "topic": "plant energy",
                "question_count": 1,
                "document_ids": [],
            },
        )
        self.assertEqual(malformed.status_code, 422, malformed.text)

    def test_empty_global_and_unindexed_document_return_specific_actions(self) -> None:
        rag_database.delete_document_record(self.document_id)
        empty_global = self.client.post(
            "/api/study/actions/quizzes/generate",
            json={"topic": "plant energy", "question_count": 1},
        )
        self.assertEqual(empty_global.status_code, 422, empty_global.text)
        self.assertEqual(
            empty_global.json()["error"]["code"],
            "NO_INDEXED_DOCUMENTS",
        )
        self.assertIn("Upload and index", empty_global.text)

        unindexed_document_id = rag_database.insert_document(
            filename="waiting.pdf",
            mime_type="application/pdf",
            file_hash="waiting-document",
            file_data=b"waiting",
        )
        unindexed = self.client.post(
            "/api/study/actions/quizzes/generate",
            json={
                "topic": "plant energy",
                "question_count": 1,
                "document_ids": [unindexed_document_id],
            },
        )
        self.assertEqual(unindexed.status_code, 422, unindexed.text)
        self.assertEqual(
            unindexed.json()["error"]["code"],
            "DOCUMENT_NOT_READY",
        )
        self.assertIn("not indexed yet", unindexed.text)

    def test_submission_scores_trusted_prefix_skips_and_unpresented_suffix(
        self,
    ) -> None:
        quiz_id = self._generate().json()["quiz_id"]
        response = self.client.post(
            f"/api/study/actions/quizzes/{quiz_id}/submit",
            json={
                "responses": [
                    {"question_number": 1, "selected_option": 2},
                    {"question_number": 2, "selected_option": None},
                ]
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()

        self.assertRegex(payload["attempt_id"], r"^[1-9][0-9]*$")
        self.assertEqual(payload["status"], "aborted")
        self.assertEqual(payload["total_questions"], 3)
        self.assertEqual(payload["presented_questions"], 2)
        self.assertEqual(payload["answered_questions"], 1)
        self.assertEqual(payload["skipped_questions"], 1)
        self.assertEqual(payload["correct_answers"], 1)
        self.assertAlmostEqual(payload["score_percentage"], 100 / 3)
        self.assertAlmostEqual(payload["accuracy_percentage"], 100.0)

        self.assertEqual(len(payload["feedback"]), 2)
        first, second = payload["feedback"]
        self.assertTrue(first["is_correct"])
        self.assertEqual(first["correct_option"], 2)
        self.assertIn("[1]", first["explanation"])
        self.assertTrue(second["skipped"])
        self.assertIsNone(second["selected_option"])
        source = first["sources"][0]
        self.assertEqual(source["document_id"], str(self.document_id))
        self.assertEqual(source["notebook_id"], str(self.notebook.id))
        self.assertEqual(source["mime_type"], "application/pdf")
        self.assertEqual(source["page_number"], 2)
        self.assertEqual(source["chunk_index"], 0)
        self.assertIn("chlorophyll", source["excerpt"].casefold())

        attempt = study_database.get_quiz_attempt(int(payload["attempt_id"]))
        self.assertIsNotNone(attempt)
        stored_questions = study_database.list_quiz_question_attempts(
            int(payload["attempt_id"])
        )
        self.assertEqual(len(stored_questions), 3)
        self.assertTrue(stored_questions[0].presented)
        self.assertTrue(stored_questions[1].presented)
        self.assertFalse(stored_questions[2].presented)
        self.assertEqual(quiz_api.pending_quiz_count(), 0)

        repeated = self.client.post(
            f"/api/study/actions/quizzes/{quiz_id}/submit",
            json={"responses": []},
        )
        self.assertEqual(repeated.status_code, 404)

    def test_invalid_noncontiguous_prefix_does_not_consume_pending_quiz(
        self,
    ) -> None:
        quiz_id = self._generate().json()["quiz_id"]
        invalid = self.client.post(
            f"/api/study/actions/quizzes/{quiz_id}/submit",
            json={
                "responses": [
                    {"question_number": 1, "selected_option": 2},
                    {"question_number": 3, "selected_option": 1},
                ]
            },
        )
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(quiz_api.pending_quiz_count(), 1)
        self.assertEqual(study_database.list_quiz_attempts(), [])

        valid = self.client.post(
            f"/api/study/actions/quizzes/{quiz_id}/submit",
            json={
                "responses": [
                    {"question_number": 1, "selected_option": 1},
                ]
            },
        )
        self.assertEqual(valid.status_code, 200)
        self.assertFalse(valid.json()["feedback"][0]["is_correct"])

    def test_client_cannot_inject_correctness_or_explanation(self) -> None:
        quiz_id = self._generate().json()["quiz_id"]
        response = self.client.post(
            f"/api/study/actions/quizzes/{quiz_id}/submit",
            json={
                "responses": [
                    {
                        "question_number": 1,
                        "selected_option": 1,
                        "correct_option": 1,
                        "is_correct": True,
                        "explanation": "client-controlled",
                    }
                ]
            },
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(quiz_api.pending_quiz_count(), 1)
        self.assertEqual(study_database.list_quiz_attempts(), [])

    def test_empty_presented_prefix_is_valid_aborted_attempt(self) -> None:
        quiz_id = self._generate().json()["quiz_id"]
        response = self.client.post(
            f"/api/study/actions/quizzes/{quiz_id}/submit",
            json={"responses": []},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload["status"], "aborted")
        self.assertEqual(payload["presented_questions"], 0)
        self.assertEqual(payload["answered_questions"], 0)
        self.assertEqual(payload["skipped_questions"], 0)
        self.assertEqual(payload["correct_answers"], 0)
        self.assertEqual(payload["score_percentage"], 0.0)
        self.assertIsNone(payload["accuracy_percentage"])
        self.assertEqual(payload["feedback"], [])

    def test_pure_scorer_derives_correctness_from_server_quiz(self) -> None:
        run = quiz_api.score_quiz(
            self.generated_quiz,
            [
                quiz_api.QuizResponse(
                    question_number=1,
                    selected_option=1,
                )
            ],
        )
        self.assertEqual(len(run.attempts), 1)
        self.assertEqual(run.attempts[0].correct_option, 2)
        self.assertFalse(run.attempts[0].is_correct)
        self.assertTrue(run.aborted)
        self.assertEqual(study_database.list_quiz_attempts(), [])

    def _generate(self):
        return self.client.post(
            "/api/study/actions/quizzes/generate",
            json={
                "topic": "plant energy",
                "question_count": 3,
                "scope": {"document_ids": [self.document_id]},
            },
        )

    def _generated_quiz(self) -> GeneratedGroundedQuiz:
        source = RetrievedSource(
            index=1,
            filename="plants.pdf",
            page_number=2,
            chunk_index=0,
            distance=0.15,
            text="Chlorophyll captures light energy in plants.",
            document_id=self.document_id,
            mime_type="application/pdf",
        )
        questions = [
            GroundedQuizQuestion(
                question="What captures light energy in plants?",
                options=["Roots", "Chlorophyll", "Oxygen", "Soil"],
                correct_option=2,
                explanation="Chlorophyll captures light energy [1].",
                source_indexes=[1],
            ),
            GroundedQuizQuestion(
                question="Which energy source is described?",
                options=["Sound", "Heat", "Motion", "Light"],
                correct_option=4,
                explanation="The source describes light energy [1].",
                source_indexes=[1],
            ),
            GroundedQuizQuestion(
                question="Which organism context is used?",
                options=["Plants", "Fungi", "Animals", "Bacteria"],
                correct_option=1,
                explanation="The excerpt explicitly discusses plants [1].",
                source_indexes=[1],
            ),
        ]
        return GeneratedGroundedQuiz(
            requested_topic="plant energy",
            sources=(source,),
            quiz=GroundedQuiz(
                should_generate=True,
                topic="Plant Energy",
                questions=questions,
                confidence=0.94,
                reason="Grounded evidence supports three questions.",
            ),
        )


if __name__ == "__main__":
    unittest.main()
