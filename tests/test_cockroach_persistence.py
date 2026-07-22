from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.application.dependencies import build_application_dependencies
from backend.domain import deterministic_legacy_uuid
from backend.rag import config
from backend.repositories.cockroach import CockroachUnitOfWork


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


class CockroachPersistenceUnitTest(unittest.TestCase):
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
