from __future__ import annotations

import sqlite3
import tempfile
import unittest

from contextlib import closing
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import backend.rag.database as rag_database


app_module = import_module("backend.api.app")
compatibility_app_module = import_module("api.app")


HEALTHY_VECTOR_STATUS = {
    "status": "ok",
    "collection_present": True,
}


class ApiFoundationTest(unittest.TestCase):
    def test_legacy_entry_points_delegate_to_backend_packages(self) -> None:
        cli_module = import_module("main")
        backend_cli_module = import_module("backend.cli")

        self.assertIs(compatibility_app_module.app, app_module.app)
        self.assertIs(compatibility_app_module.create_app, app_module.create_app)
        self.assertIs(cli_module.main, backend_cli_module.main)

    def test_factory_and_module_level_app_are_fastapi_apps(self) -> None:
        application = app_module.create_app(allow_legacy_default_workspace=True)

        self.assertIsInstance(application, FastAPI)
        self.assertIsInstance(app_module.app, FastAPI)
        self.assertIsNot(application, app_module.app)
        self.assertEqual(application.title, "Local Study Companion API")
        self.assertEqual(application.version, "0.8.0")

    def test_lifespan_initializes_storage_and_health_stays_lazy(self) -> None:
        application = app_module.create_app(allow_legacy_default_workspace=True)

        with (
            patch.object(app_module, "initialize_database") as initialize_documents,
            patch.object(app_module, "initialize_memory_database") as initialize_memory,
            patch.object(app_module, "initialize_study_database") as initialize_study,
            patch.object(
                app_module,
                "probe_vector_store",
                return_value=HEALTHY_VECTOR_STATUS.copy(),
            ) as probe_documents,
            patch.object(
                app_module,
                "probe_memory_vector_store",
                return_value=HEALTHY_VECTOR_STATUS.copy(),
            ) as probe_memory,
            patch(
                "backend.api.health.check_database",
                return_value={"status": "ok"},
            ) as check_database,
            patch(
                "backend.rag.vector_store.get_embedding_model"
            ) as get_embedding_model,
            patch("backend.llm.factory.create_chat_model") as create_chat_model,
        ):
            with TestClient(application) as client:
                response = client.get("/api/health")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["status"], "ok")
            self.assertEqual(response.json()["database"]["status"], "ok")
            self.assertEqual(
                response.json()["documents_vector_store"],
                HEALTHY_VECTOR_STATUS,
            )
            self.assertEqual(
                response.json()["memory_vector_store"],
                HEALTHY_VECTOR_STATUS,
            )

            initialize_documents.assert_called_once_with()
            initialize_memory.assert_called_once_with()
            initialize_study.assert_called_once_with()
            probe_documents.assert_called_once_with()
            probe_memory.assert_called_once_with()
            check_database.assert_called_once_with()
            get_embedding_model.assert_not_called()
            create_chat_model.assert_not_called()

    def test_not_found_uses_structured_error_envelope(self) -> None:
        application = app_module.create_app(allow_legacy_default_workspace=True)

        with self._client(application) as client:
            response = client.get("/api/does-not-exist")

        self.assertEqual(response.status_code, 404)
        error = response.json()["error"]
        self.assertEqual(error["code"], "RESOURCE_NOT_FOUND")
        self.assertEqual(error["title"], "Resource not found")
        self.assertEqual(
            error["reason"],
            "The requested resource was not found.",
        )
        self.assertEqual(
            error["message"],
            "The requested resource was not found.",
        )
        self.assertFalse(error["retryable"])
        self.assertIsNone(error["details"])
        self.assertRegex(error["request_id"], r"^[a-f0-9]{32}$")
        self.assertEqual(response.headers["x-request-id"], error["request_id"])

    def test_validation_error_uses_structured_safe_details(self) -> None:
        application = app_module.create_app(allow_legacy_default_workspace=True)

        @application.get("/_test/items/{item_id}")
        def get_test_item(item_id: int) -> dict[str, int]:
            return {"item_id": item_id}

        with self._client(application) as client:
            response = client.get("/_test/items/not-an-integer")

        payload = response.json()
        self.assertEqual(response.status_code, 422)
        self.assertEqual(payload["error"]["code"], "VALIDATION_ERROR")
        self.assertEqual(
            payload["error"]["message"],
            "Request validation failed.",
        )
        self.assertEqual(
            payload["error"]["reason"],
            "One or more request fields are invalid.",
        )
        self.assertFalse(payload["error"]["retryable"])
        self.assertRegex(
            payload["error"]["request_id"],
            r"^[a-f0-9]{32}$",
        )
        self.assertEqual(
            payload["error"]["details"][0]["field"],
            "path.item_id",
        )
        self.assertNotIn("input", payload["error"]["details"][0])

    def test_unexpected_error_hides_exception_and_stack_trace(self) -> None:
        application = app_module.create_app(allow_legacy_default_workspace=True)

        @application.get("/_test/failure")
        def fail() -> None:
            raise RuntimeError("secret filesystem path C:/private/data")

        with (
            patch("backend.api.errors.LOGGER.error") as log_error,
            self._client(
                application,
                raise_server_exceptions=False,
            ) as client,
        ):
            response = client.get("/_test/failure")

        self.assertEqual(response.status_code, 500)
        payload = response.json()["error"]
        self.assertEqual(payload["code"], "INTERNAL_ERROR")
        self.assertEqual(
            payload["title"],
            "Agentbook could not complete the request",
        )
        self.assertEqual(
            payload["message"],
            "An unexpected server error occurred.",
        )
        self.assertFalse(payload["retryable"])
        self.assertRegex(payload["request_id"], r"^[a-f0-9]{32}$")
        self.assertNotIn("secret filesystem path", response.text)
        self.assertNotIn("Traceback", response.text)
        log_error.assert_called_once()

    def test_cors_allows_vite_loopback_origin(self) -> None:
        application = app_module.create_app(allow_legacy_default_workspace=True)

        with self._client(application) as client:
            response = client.options(
                "/api/health",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "content-type",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["access-control-allow-origin"],
            "http://localhost:5173",
        )
        self.assertIn("GET", response.headers["access-control-allow-methods"])
        self.assertIn(
            "content-type",
            response.headers["access-control-allow-headers"].lower(),
        )
        self.assertNotIn("access-control-allow-credentials", response.headers)

    def _client(
        self,
        application: FastAPI,
        *,
        raise_server_exceptions: bool = True,
    ):
        storage_status = {
            "document_vector_status": HEALTHY_VECTOR_STATUS.copy(),
            "memory_vector_status": HEALTHY_VECTOR_STATUS.copy(),
        }
        storage_patch = patch.object(
            app_module,
            "initialize_storage",
            return_value=storage_status,
        )
        storage_patch.start()
        self.addCleanup(storage_patch.stop)
        return TestClient(
            application,
            raise_server_exceptions=raise_server_exceptions,
        )


class SQLiteConnectionSafetyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "app.db"

        database_path_patch = patch.object(
            rag_database,
            "DATABASE_PATH",
            self.database_path,
        )
        directories_patch = patch.object(
            rag_database,
            "ensure_directories",
        )
        database_path_patch.start()
        directories_patch.start()
        self.addCleanup(database_path_patch.stop)
        self.addCleanup(directories_patch.stop)

    def test_initialize_enables_wal_and_connections_use_pragmas(self) -> None:
        rag_database.initialize_database()

        with rag_database.get_connection() as connection:
            journal_mode = connection.execute(
                "PRAGMA journal_mode"
            ).fetchone()[0]
            foreign_keys = connection.execute(
                "PRAGMA foreign_keys"
            ).fetchone()[0]
            busy_timeout = connection.execute(
                "PRAGMA busy_timeout"
            ).fetchone()[0]

        self.assertEqual(str(journal_mode).lower(), "wal")
        self.assertEqual(foreign_keys, 1)
        self.assertEqual(busy_timeout, 5000)

    def test_connection_closes_after_context_exit(self) -> None:
        with rag_database.get_connection() as connection:
            connection.execute("SELECT 1").fetchone()

        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")

    def test_exception_rolls_back_and_closes_connection(self) -> None:
        rag_database.initialize_database()

        with self.assertRaisesRegex(RuntimeError, "force rollback"):
            with rag_database.get_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO documents (
                        filename,
                        mime_type,
                        file_hash,
                        file_data,
                        chunk_count,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "rollback.txt",
                        "text/plain",
                        "rollback-hash",
                        sqlite3.Binary(b"content"),
                        0,
                        "2026-07-18T00:00:00+00:00",
                    ),
                )
                raise RuntimeError("force rollback")

        with closing(sqlite3.connect(self.database_path)) as verification:
            count = verification.execute(
                "SELECT COUNT(*) FROM documents"
            ).fetchone()[0]

        self.assertEqual(count, 0)
        with self.assertRaises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
