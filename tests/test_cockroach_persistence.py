from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from uuid import UUID

from backend.application.dependencies import build_application_dependencies
from backend.domain import deterministic_legacy_uuid
from backend.rag import config
from backend.repositories.cockroach import CockroachUnitOfWork
from backend.repositories.cockroach.study import _resolve_quiz_citation_lineage
from backend.repositories.interfaces import RepositoryConflictError


class _SerializationFailure(RuntimeError):
    sqlstate = "40001"


class _Transaction:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class _Connection:
    def __init__(self) -> None:
        self.transaction = _Transaction()
        self.closed = False

    def begin(self) -> _Transaction:
        return self.transaction

    def close(self) -> None:
        self.closed = True


class _Engine:
    def __init__(self) -> None:
        self.connections: list[_Connection] = []

    def connect(self) -> _Connection:
        connection = _Connection()
        self.connections.append(connection)
        return connection


class _ScalarRows:
    def __init__(self, values) -> None:
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return list(self.values)


class _LineageConnection:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.parameters = []

    def execute(self, _statement, parameters):
        self.parameters.append(parameters)
        return _ScalarRows(self.responses.pop(0))


class CockroachPersistenceUnitTest(unittest.TestCase):
    def test_quiz_citation_lineage_requires_one_owned_chunk(self) -> None:
        workspace = "00000000-0000-4000-8000-000000000001"
        document_id = UUID("10000000-0000-4000-8000-000000000001")
        chunk_id = UUID("20000000-0000-4000-8000-000000000001")
        connection = _LineageConnection(([document_id], [chunk_id]))

        resolved = _resolve_quiz_citation_lineage(
            connection, workspace, document_public_id=17, chunk_index=3
        )

        self.assertEqual(resolved, (document_id, chunk_id))
        self.assertEqual(connection.parameters[0]["workspace_id"], UUID(workspace))
        self.assertEqual(connection.parameters[1]["document_id"], document_id)
        self.assertEqual(connection.parameters[1]["chunk_index"], 3)

    def test_quiz_citation_lineage_rejects_missing_or_cross_workspace_document(self) -> None:
        connection = _LineageConnection(([],))
        with self.assertRaises(RepositoryConflictError):
            _resolve_quiz_citation_lineage(
                connection,
                "00000000-0000-4000-8000-000000000099",
                document_public_id=1,
                chunk_index=0,
            )

    def test_quiz_citation_lineage_rejects_missing_or_ambiguous_chunk(self) -> None:
        workspace = "00000000-0000-4000-8000-000000000001"
        document_id = UUID("10000000-0000-4000-8000-000000000001")
        for chunks in ([], [UUID(int=1), UUID(int=2)]):
            with self.subTest(chunk_count=len(chunks)):
                connection = _LineageConnection(([document_id], chunks))
                with self.assertRaises(RepositoryConflictError):
                    _resolve_quiz_citation_lineage(
                        connection, workspace, document_public_id=1, chunk_index=99
                    )

    def test_legacy_uuid_is_deterministic_and_table_scoped(self) -> None:
        workspace = "00000000-0000-4000-8000-000000000001"
        first = deterministic_legacy_uuid(workspace, "documents", 17)
        self.assertEqual(first, deterministic_legacy_uuid(workspace, "documents", 17))
        self.assertNotEqual(first, deterministic_legacy_uuid(workspace, "memories", 17))

    def test_serialization_retry_uses_fresh_transactions(self) -> None:
        engine = _Engine()
        attempts = 0

        def work(_unit_of_work):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise _SerializationFailure("retry")
            return "ok"

        with (
            patch("backend.repositories.cockroach.unit_of_work.get_engine", return_value=engine),
            patch("backend.repositories.cockroach.unit_of_work.time.sleep"),
            patch("backend.repositories.cockroach.unit_of_work.random.uniform", return_value=0.0),
        ):
            unit = CockroachUnitOfWork(maximum_retries=3, base_delay_ms=1)
            self.assertEqual(unit.run(work), "ok")
        self.assertEqual(attempts, 3)
        self.assertEqual(unit.retry_count, 2)
        self.assertEqual(len(engine.connections), 3)
        self.assertTrue(engine.connections[0].transaction.rolled_back)
        self.assertTrue(engine.connections[1].transaction.rolled_back)
        self.assertTrue(engine.connections[2].transaction.committed)

    def test_cockroach_composition_has_no_sqlite_or_chroma_adapters(self) -> None:
        with (
            patch.object(config, "PERSISTENCE_BACKEND", "cockroach"),
            patch.object(config, "DATABASE_URL", "configured-for-test"),
        ):
            dependencies = build_application_dependencies()
        adapter_names = {
            type(value).__name__
            for name, value in vars(dependencies).items()
            if name not in {"workspace_id", "unit_of_work"}
        }
        self.assertTrue(adapter_names)
        self.assertFalse(any(name.startswith("SQLite") for name in adapter_names))
        self.assertFalse(any(name.startswith("Chroma") for name in adapter_names))


@unittest.skipUnless(
    os.getenv("RUN_LIVE_COCKROACH_TESTS") == "1",
    "Set RUN_LIVE_COCKROACH_TESTS=1 after applying the CockroachDB schema.",
)
class LiveCockroachContractTest(unittest.TestCase):
    def test_live_schema_revision_and_vector_capability(self) -> None:
        from sqlalchemy import text

        from backend.repositories.cockroach.connection import get_engine

        with get_engine().connect() as connection:
            revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
            vector = connection.execute(
                text("SELECT '[1,0,0]'::VECTOR(3) <=> '[0,1,0]'::VECTOR(3)")
            ).scalar_one()
        self.assertIn(revision, {"0001_agentbook_cockroach_schema", "0002_cockroach_vector_indexes"})
        self.assertAlmostEqual(float(vector), 1.0)


if __name__ == "__main__":
    unittest.main()
