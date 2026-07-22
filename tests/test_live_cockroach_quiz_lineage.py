from __future__ import annotations

import os
import sqlite3
import unittest
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import text

from backend.domain import DEFAULT_WORKSPACE_ID
from backend.rag.rag_service import RetrievedSource
from backend.repositories.cockroach.connection import (
    bind_connection,
    get_engine,
    reset_connection,
)
from backend.repositories.cockroach.study import CockroachQuizRepository
from backend.repositories.interfaces import RepositoryConflictError
from backend.study import quiz_api
from backend.study.quiz_generator import (
    GeneratedGroundedQuiz,
    GroundedQuiz,
    GroundedQuizQuestion,
)


@unittest.skipUnless(
    os.getenv("RUN_LIVE_COCKROACH_LINEAGE_TESTS") == "1",
    "Set RUN_LIVE_COCKROACH_LINEAGE_TESTS=1 for authorized live contract tests.",
)
class LiveCockroachQuizLineageTest(unittest.TestCase):
    def test_runtime_lineage_and_failure_rollbacks(self) -> None:
        engine = get_engine()
        baseline = self._imported_baseline(engine)
        self.assertEqual(len(baseline), 6)
        self.assertTrue(all(row[1] is not None for row in baseline))

        local_access_error = AssertionError(
            "Cockroach lineage test attempted to access SQLite or Chroma."
        )
        with (
            patch.object(sqlite3, "connect", side_effect=local_access_error),
            patch("chromadb.PersistentClient", side_effect=local_access_error),
        ):
            self._valid_citation_is_linked_and_rolled_back(engine)
            self._missing_chunk_rolls_back(engine)
            self._cross_workspace_pair_rolls_back(engine)

        self.assertEqual(self._imported_baseline(engine), baseline)

    def _valid_citation_is_linked_and_rolled_back(self, engine) -> None:
        topic = "lineage-valid-" + uuid4().hex
        result = self._result(topic, document_id=1, chunk_index=0)
        repository = CockroachQuizRepository()
        with engine.connect() as connection:
            transaction = connection.begin()
            token = bind_connection(connection)
            try:
                _attempt, questions = repository.save_run_result(result)
                row = connection.execute(
                    text(
                        """
                        SELECT src.document_chunk_id,c.workspace_id,c.document_id,
                               c.chunk_index,d.public_id AS document_public_id
                        FROM quiz_question_sources src
                        JOIN quiz_question_attempts q ON q.id=src.question_attempt_id
                        JOIN document_chunks c ON c.id=src.document_chunk_id
                        JOIN documents d ON d.id=c.document_id
                        WHERE q.workspace_id=:workspace_id AND q.public_id=:question_id
                        """
                    ),
                    {
                        "workspace_id": UUID(DEFAULT_WORKSPACE_ID),
                        "question_id": questions[0].id,
                    },
                ).mappings().one()
                self.assertIsNotNone(row["document_chunk_id"])
                self.assertEqual(str(row["workspace_id"]), DEFAULT_WORKSPACE_ID)
                self.assertEqual(int(row["document_public_id"]), 1)
                self.assertEqual(int(row["chunk_index"]), 0)
            finally:
                reset_connection(token)
                transaction.rollback()
        self.assertEqual(self._attempt_count(engine, topic), 0)

    def _missing_chunk_rolls_back(self, engine) -> None:
        topic = "lineage-missing-" + uuid4().hex
        result = self._result(topic, document_id=1, chunk_index=999999)
        with self.assertRaises(RepositoryConflictError):
            CockroachQuizRepository().save_run_result(result)
        self.assertEqual(self._attempt_count(engine, topic), 0)

    def _cross_workspace_pair_rolls_back(self, engine) -> None:
        topic = "lineage-cross-workspace-" + uuid4().hex
        workspace_id = "00000000-0000-4000-8000-000000000099"
        result = self._result(topic, document_id=1, chunk_index=0)
        with engine.connect() as connection:
            transaction = connection.begin()
            connection.execute(
                text(
                    "INSERT INTO workspaces (id,name,created_at,updated_at) "
                    "VALUES (:id,'Lineage rollback test',now(),now())"
                ),
                {"id": UUID(workspace_id)},
            )
            token = bind_connection(connection)
            try:
                with self.assertRaises(RepositoryConflictError):
                    CockroachQuizRepository(workspace_id).save_run_result(result)
                inserted_inside_transaction = int(
                    connection.execute(
                        text(
                            "SELECT count(*) FROM quiz_attempts "
                            "WHERE workspace_id=:workspace_id AND requested_topic=:topic"
                        ),
                        {"workspace_id": UUID(workspace_id), "topic": topic},
                    ).scalar_one()
                )
                self.assertEqual(inserted_inside_transaction, 1)
            finally:
                reset_connection(token)
                transaction.rollback()
        self.assertEqual(self._attempt_count(engine, topic), 0)
        with engine.connect() as connection:
            self.assertEqual(
                int(
                    connection.execute(
                        text("SELECT count(*) FROM workspaces WHERE id=:id"),
                        {"id": UUID(workspace_id)},
                    ).scalar_one()
                ),
                0,
            )

    @staticmethod
    def _attempt_count(engine, topic: str) -> int:
        with engine.connect() as connection:
            return int(
                connection.execute(
                    text("SELECT count(*) FROM quiz_attempts WHERE requested_topic=:topic"),
                    {"topic": topic},
                ).scalar_one()
            )

    @staticmethod
    def _imported_baseline(engine):
        with engine.connect() as connection:
            return tuple(
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT id,document_chunk_id,workspace_id,document_id,chunk_index "
                        "FROM quiz_question_sources "
                        "WHERE legacy_sqlite_id IS NOT NULL ORDER BY id"
                    )
                ).all()
            )

    @staticmethod
    def _result(topic: str, *, document_id: int, chunk_index: int):
        source = RetrievedSource(
            index=1,
            filename="lineage-test.txt",
            page_number=None,
            chunk_index=chunk_index,
            distance=0.0,
            text="Sanitized lineage regression evidence.",
            document_id=document_id,
            mime_type="text/plain",
        )
        generated = GeneratedGroundedQuiz(
            requested_topic=topic,
            sources=(source,),
            quiz=GroundedQuiz(
                should_generate=True,
                topic=topic,
                questions=[
                    GroundedQuizQuestion(
                        question="Which option is correct?",
                        options=["Wrong", "Correct", "Other A", "Other B"],
                        correct_option=2,
                        explanation="Controlled explanation [1].",
                        source_indexes=[1],
                    )
                ],
                confidence=0.95,
                reason="Controlled regression quiz.",
            ),
        )
        return quiz_api.score_quiz(
            generated,
            [quiz_api.QuizResponse(question_number=1, selected_option=1)],
        )


if __name__ == "__main__":
    unittest.main()
