from __future__ import annotations

import os
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from backend.application.dependencies import (
    configure_application_dependencies,
    get_application_dependencies,
)
from backend.infrastructure.cockroach.importer import destination_counts
from backend.rag import config
from backend.rag.rag_service import RetrievedSource
from backend.repositories.cockroach.connection import dispose_engine, get_engine
from backend.study import quiz_api
from backend.study.quiz_generator import (
    GeneratedGroundedQuiz,
    GroundedQuiz,
    GroundedQuizQuestion,
)


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.getenv("RUN_LIVE_COCKROACH_DOCUMENT_SMOKE") == "1",
    "Set RUN_LIVE_COCKROACH_DOCUMENT_SMOKE=1 only for the authorized live smoke test.",
)
class LiveCockroachDocumentSmokeTest(unittest.TestCase):
    def test_safe_text_upload_to_adaptation_survives_restart(self) -> None:
        self.assertEqual(config.PERSISTENCE_BACKEND, "cockroach")
        sample = (ROOT / "examples" / "retrieval_distance.txt").read_bytes()
        token = uuid4().hex
        topic = f"gate7txt{token}"
        payload = sample + f"\n\nControlled smoke topic: {topic}.\n".encode()
        filename = f"cockroach-smoke-{token}.txt"
        vector = [0.0, 1.0] + [0.0] * (config.EMBEDDING_DIMENSION - 2)

        configure_application_dependencies(None)
        baseline = destination_counts(get_engine())
        local_access_error = AssertionError(
            "Cockroach document smoke attempted to access SQLite or Chroma."
        )
        with (
            patch.object(sqlite3, "connect", side_effect=local_access_error),
            patch("chromadb.PersistentClient", side_effect=local_access_error),
            patch(
                "backend.repositories.cockroach.vectors.encode_documents",
                side_effect=lambda values: [list(vector) for _ in values],
            ),
            patch(
                "backend.repositories.cockroach.vectors.encode_query",
                return_value=vector,
            ),
        ):
            with TestClient(self._app()) as client:
                uploaded = client.post(
                    "/api/documents",
                    files={"file": (filename, payload, "text/plain")},
                )
                self.assertEqual(uploaded.status_code, 200, uploaded.text)
                upload_body = uploaded.json()
                self.assertEqual(upload_body["status"], "indexed")
                document_id = int(upload_body["document"]["id"])
                chunk_count = int(upload_body["document"]["chunk_count"])
                self.assertGreaterEqual(chunk_count, 1)

                dependencies = get_application_dependencies()
                self.assertEqual(dependencies.blobs.read(document_id), payload)
                chunks = dependencies.document_vectors.search(
                    topic, 5, {"document_id": {"$eq": document_id}}
                )
                self.assertTrue(chunks)
                self.assertTrue(all(int(item[0].metadata["document_id"]) == document_id for item in chunks))
                document, distance = chunks[0]
                source = RetrievedSource(
                    index=1,
                    filename=str(document.metadata["filename"]),
                    page_number=document.metadata.get("page_number"),
                    chunk_index=int(document.metadata["chunk_index"]),
                    distance=float(distance),
                    text=document.page_content,
                    document_id=document_id,
                    mime_type=str(document.metadata["mime_type"]),
                    slide_number=document.metadata.get("slide_number"),
                )
                generated = self._generated_quiz(topic, source)
                with patch.object(
                    quiz_api, "generate_grounded_quiz", return_value=generated
                ):
                    presented = client.post(
                        "/api/study/actions/quizzes/generate",
                        json={
                            "topic": topic,
                            "question_count": 1,
                            "document_ids": [document_id],
                        },
                    )
                    self.assertEqual(presented.status_code, 200, presented.text)
                    quiz_id = presented.json()["quiz_id"]
                    submitted = client.post(
                        f"/api/study/actions/quizzes/{quiz_id}/submit",
                        json={
                            "responses": [
                                {"question_number": 1, "selected_option": 1}
                            ]
                        },
                    )
                    self.assertEqual(submitted.status_code, 200, submitted.text)
                    submission = submitted.json()
                    self.assertEqual(submission["correct_answers"], 0)
                    self.assertEqual(len(submission["learning_signals"]), 1)
                    self.assertEqual(len(submission["memory_proposals"]), 1)
                    proposal_id = submission["memory_proposals"][0]["proposal_id"]
                    signal_id = submission["learning_signals"][0]["id"]
                    accepted = client.post(
                        f"/api/memories/proposals/{proposal_id}/decision",
                        json={
                            "decision": "accept",
                            "edited_content": (
                                f"The learner needs review of {topic} retrieval distance concepts."
                            ),
                        },
                    )
                    self.assertEqual(accepted.status_code, 200, accepted.text)
                    memory_id = int(accepted.json()["saved_memory"]["id"])
                    memory_search = client.get(
                        "/api/memories/search", params={"q": topic, "limit": 5}
                    )
                    self.assertEqual(memory_search.status_code, 200, memory_search.text)
                    self.assertIn(
                        memory_id,
                        [item["memory_id"] for item in memory_search.json()["items"]],
                    )

            self._restart()
            with TestClient(self._app()) as restarted_client:
                self.assertEqual(
                    restarted_client.get(f"/api/documents/{document_id}").status_code,
                    200,
                )
                persisted_search = restarted_client.get(
                    "/api/memories/search", params={"q": topic, "limit": 5}
                )
                self.assertIn(
                    memory_id,
                    [item["memory_id"] for item in persisted_search.json()["items"]],
                )
                with patch.object(
                    quiz_api, "generate_grounded_quiz", return_value=generated
                ):
                    later = restarted_client.post(
                        "/api/study/actions/quizzes/generate",
                        json={
                            "topic": topic,
                            "question_count": 1,
                            "document_ids": [document_id],
                        },
                    )
                self.assertEqual(later.status_code, 200, later.text)
                adaptation = later.json()["adaptation"]
                self.assertTrue(adaptation["adapted_using_learner_memory"])
                self.assertIn(memory_id, adaptation["memory_ids"])
                self.assertIn(signal_id, adaptation["learning_signal_ids"])
                later_quiz_id = later.json()["quiz_id"]

            final_dependencies = self._restart()
            self.assertEqual(final_dependencies.blobs.read(document_id), payload)
            self.assertIsNotNone(final_dependencies.memories.get(memory_id))
            self.assertIsNotNone(final_dependencies.learning_signals.get(signal_id))
            event = next(
                item
                for item in final_dependencies.adaptation_events.list("quiz")
                if item.request_id == later_quiz_id
            )
            self.assertIn(memory_id, event.memory_ids)
            self.assertTrue(event.reason)

        final_counts = destination_counts(get_engine())
        expected_growth = {
            "documents": 1,
            "document_blobs": 1,
            "document_chunks": chunk_count,
            "quiz_attempts": 1,
            "quiz_question_attempts": 1,
            "quiz_question_sources": 1,
            "learning_signals": 1,
            "memories": 1,
            "learner_memory_embeddings": 1,
            "vector_outbox": 2,
            "workflow_states": 4,
            "adaptation_events": 2,
        }
        for name, growth in expected_growth.items():
            self.assertEqual(final_counts[name], baseline[name] + growth, name)
        for name in set(baseline) - set(expected_growth):
            self.assertEqual(final_counts[name], baseline[name], name)

    @staticmethod
    def _app():
        from backend.api.app import create_app

        return create_app(get_application_dependencies())

    @staticmethod
    def _restart():
        dispose_engine()
        configure_application_dependencies(None)
        return get_application_dependencies()

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
                        question="How should a retrieval distance be interpreted?",
                        options=[
                            "As a relevance percentage",
                            "As a model- and metric-dependent distance",
                            "As the number of source pages",
                            "As a confidence probability",
                        ],
                        correct_option=2,
                        explanation=(
                            "Distance depends on the embedding model and metric and is not a percentage [1]."
                        ),
                        source_indexes=[1],
                    )
                ],
                confidence=0.95,
                reason="The uploaded safe text contains direct evidence.",
            ),
        )


if __name__ == "__main__":
    unittest.main()
