from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest

from contextlib import ExitStack, closing
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

import backend.rag.database as rag_database
import backend.rag.ingestion as rag_ingestion
import backend.rag.vector_store as rag_vector_store


app_module = import_module("backend.api.app")


class FakeVectorStore:
    def __init__(self) -> None:
        self.documents: dict[str, Any] = {}
        self.add_calls = 0
        self.deleted_ids: list[str] = []

    def add_documents(
        self,
        *,
        documents: list[Any],
        ids: list[str],
    ) -> None:
        self.add_calls += 1
        self.documents.update(zip(ids, documents, strict=True))

    def get(self, *, where: dict[str, Any]) -> dict[str, list[str]]:
        document_id = where.get("document_id")
        ids = [
            vector_id
            for vector_id, document in self.documents.items()
            if document.metadata.get("document_id") == document_id
        ]
        return {"ids": ids}

    def delete(self, *, ids: list[str]) -> None:
        self.deleted_ids.extend(ids)
        for vector_id in ids:
            self.documents.pop(vector_id, None)


class NotebookDocumentApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temporary_directory = self.stack.enter_context(
            tempfile.TemporaryDirectory()
        )
        self.database_path = Path(self.temporary_directory) / "app.db"
        self.vector_store = FakeVectorStore()

        self.stack.enter_context(
            patch.object(rag_database, "DATABASE_PATH", self.database_path)
        )
        self.stack.enter_context(
            patch.object(rag_database, "ensure_directories")
        )
        self.stack.enter_context(
            patch.object(
                rag_ingestion,
                "get_vector_store",
                return_value=self.vector_store,
            )
        )
        self.stack.enter_context(
            patch.object(
                rag_vector_store,
                "get_vector_store",
                return_value=self.vector_store,
            )
        )
        self.stack.enter_context(
            patch.object(
                app_module,
                "probe_vector_store",
                return_value={"status": "ok", "collection_present": False},
            )
        )
        self.stack.enter_context(
            patch.object(
                app_module,
                "probe_memory_vector_store",
                return_value={"status": "ok", "collection_present": False},
            )
        )

        self.client = self.stack.enter_context(
            TestClient(
                app_module.create_app(allow_legacy_default_workspace=True)
            )
        )

    def test_notebook_crud_counts_and_empty_only_delete(self) -> None:
        created = self._create_notebook(
            name="Biology",
            description="Cell and plant notes",
        )
        notebook_id = int(created["id"])

        self.assertEqual(created["name"], "Biology")
        self.assertEqual(created["description"], "Cell and plant notes")
        self.assertEqual(created["document_count"], 0)
        self._assert_timestamp(created["created_at"])
        self._assert_timestamp(created["updated_at"])

        listed = self.client.get("/api/notebooks")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            [item["id"] for item in self._items(listed.json(), "notebooks")],
            [notebook_id],
        )

        updated_response = self.client.patch(
            f"/api/notebooks/{notebook_id}",
            json={
                "name": "Plant Biology",
                "description": "Photosynthesis and cells",
            },
        )
        self.assertEqual(updated_response.status_code, 200)
        updated = self._resource(updated_response.json(), "notebook")
        self.assertEqual(updated["name"], "Plant Biology")
        self.assertEqual(updated["description"], "Photosynthesis and cells")

        uploaded = self._upload_text(
            filename="leaves.txt",
            content=b"Leaves use chlorophyll to capture light energy.",
            notebook_id=notebook_id,
        )
        document_id = self._document_id(uploaded)

        nonempty_delete = self.client.delete(f"/api/notebooks/{notebook_id}")
        self.assertEqual(nonempty_delete.status_code, 409)
        self.assertIn("error", nonempty_delete.json())

        details = self.client.get(f"/api/notebooks/{notebook_id}")
        self.assertEqual(details.status_code, 200)
        notebook = self._resource(details.json(), "notebook")
        self.assertEqual(notebook["document_count"], 1)

        unassign = self.client.patch(
            f"/api/documents/{document_id}/notebook",
            json={"notebook_id": None},
        )
        self.assertEqual(unassign.status_code, 200)

        deleted = self.client.delete(f"/api/notebooks/{notebook_id}")
        self.assertIn(deleted.status_code, {200, 204})
        missing = self.client.get(f"/api/notebooks/{notebook_id}")
        self.assertEqual(missing.status_code, 404)

    def test_document_assignment_move_remove_search_and_safe_detail(self) -> None:
        biology = self._create_notebook(name="Biology")
        chemistry = self._create_notebook(name="Chemistry")
        biology_id = int(biology["id"])
        chemistry_id = int(chemistry["id"])

        upload = self._upload_text(
            filename="Cell Biology Notes.txt",
            content=b"Mitochondria help produce cellular energy.",
            notebook_id=biology_id,
        )
        document_id = self._document_id(upload)

        biology_documents = self.client.get(
            f"/api/notebooks/{biology_id}/documents"
        )
        self.assertEqual(biology_documents.status_code, 200)
        self.assertEqual(
            [
                item["id"]
                for item in self._items(
                    biology_documents.json(),
                    "documents",
                )
            ],
            [document_id],
        )

        move = self.client.patch(
            f"/api/documents/{document_id}/notebook",
            json={"notebook_id": chemistry_id},
        )
        self.assertEqual(move.status_code, 200)
        moved = self._resource(move.json(), "document")
        self.assertEqual(moved["notebook_id"], chemistry_id)

        chemistry_filter = self.client.get(
            "/api/documents",
            params={"notebook_id": chemistry_id},
        )
        self.assertEqual(chemistry_filter.status_code, 200)
        self.assertEqual(
            [
                item["id"]
                for item in self._items(
                    chemistry_filter.json(),
                    "documents",
                )
            ],
            [document_id],
        )

        search = self.client.get(
            "/api/documents",
            params={"q": "BIOLOGY"},
        )
        self.assertEqual(search.status_code, 200)
        self.assertEqual(
            [item["id"] for item in self._items(search.json(), "documents")],
            [document_id],
        )

        detail_response = self.client.get(f"/api/documents/{document_id}")
        self.assertEqual(detail_response.status_code, 200)
        detail = self._resource(detail_response.json(), "document")
        self.assertEqual(detail["id"], document_id)
        self.assertNotIn("file_data", detail)
        self.assertNotIn("file_hash", detail)
        self.assertNotIn("path", detail)
        self.assertNotIn(
            hashlib.sha256(b"Mitochondria help produce cellular energy.").hexdigest(),
            detail_response.text,
        )

        remove = self.client.patch(
            f"/api/documents/{document_id}/notebook",
            json={"notebook_id": None},
        )
        self.assertEqual(remove.status_code, 200)
        removed = self._resource(remove.json(), "document")
        self.assertIsNone(removed["notebook_id"])

    def test_duplicate_upload_returns_existing_without_moving(self) -> None:
        first_notebook = self._create_notebook(name="First")
        second_notebook = self._create_notebook(name="Second")
        first_id = int(first_notebook["id"])
        second_id = int(second_notebook["id"])
        content = b"Same bytes must identify one stored document."

        first_upload = self._upload_text(
            filename="original.txt",
            content=content,
            notebook_id=first_id,
        )
        first_document_id = self._document_id(first_upload)
        first_add_calls = self.vector_store.add_calls

        duplicate_response = self.client.post(
            "/api/documents/upload",
            files={"file": ("renamed.txt", content, "text/plain")},
            data={"notebook_id": str(second_id)},
        )
        self.assertEqual(duplicate_response.status_code, 200)
        duplicate = duplicate_response.json()
        self.assertEqual(self._document_id(duplicate), first_document_id)
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertEqual(self.vector_store.add_calls, first_add_calls)

        first_documents = self.client.get(
            f"/api/notebooks/{first_id}/documents"
        )
        second_documents = self.client.get(
            f"/api/notebooks/{second_id}/documents"
        )
        self.assertEqual(
            [
                item["id"]
                for item in self._items(first_documents.json(), "documents")
            ],
            [first_document_id],
        )
        self.assertEqual(
            self._items(second_documents.json(), "documents"),
            [],
        )

    def test_document_delete_removes_vectors_and_sqlite_record(self) -> None:
        upload = self._upload_text(
            filename="delete-me.txt",
            content=b"This document will be deleted safely.",
        )
        document_id = self._document_id(upload)
        self.assertTrue(self.vector_store.documents)

        response = self.client.delete(f"/api/documents/{document_id}")
        self.assertIn(response.status_code, {200, 204})
        self.assertTrue(self.vector_store.deleted_ids)
        self.assertFalse(self.vector_store.documents)
        self.assertEqual(
            self.client.get(f"/api/documents/{document_id}").status_code,
            404,
        )

    def test_upload_rejects_traversal_unsupported_empty_and_corrupt(self) -> None:
        cases = (
            ("../escape.txt", b"unsafe name", "text/plain"),
            ("notes.md", b"unsupported", "text/markdown"),
            ("empty.txt", b"", "text/plain"),
            ("broken.pdf", b"not a pdf", "application/pdf"),
        )

        for filename, content, mime_type in cases:
            with self.subTest(filename=filename):
                response = self.client.post(
                    "/api/documents/upload",
                    files={"file": (filename, content, mime_type)},
                )
                self.assertIn(response.status_code, {400, 413, 415, 422})
                self.assertIn("error", response.json())

        documents = self.client.get("/api/documents")
        self.assertEqual(documents.status_code, 200)
        self.assertEqual(self._items(documents.json(), "documents"), [])
        self.assertEqual(self.vector_store.add_calls, 0)

    def _create_notebook(
        self,
        *,
        name: str,
        description: str | None = None,
    ) -> dict[str, Any]:
        response = self.client.post(
            "/api/notebooks",
            json={"name": name, "description": description},
        )
        self.assertEqual(response.status_code, 201, response.text)
        return self._resource(response.json(), "notebook")

    def _upload_text(
        self,
        *,
        filename: str,
        content: bytes,
        notebook_id: int | None = None,
    ) -> dict[str, Any]:
        data = (
            {"notebook_id": str(notebook_id)}
            if notebook_id is not None
            else None
        )
        response = self.client.post(
            "/api/documents/upload",
            files={"file": (filename, content, "text/plain")},
            data=data,
        )
        self.assertIn(response.status_code, {200, 201}, response.text)
        return response.json()

    @staticmethod
    def _document_id(payload: dict[str, Any]) -> int:
        document = payload.get("document")
        if isinstance(document, dict):
            return int(document["id"])
        if "document_id" in payload:
            return int(payload["document_id"])
        return int(payload["id"])

    @staticmethod
    def _resource(payload: dict[str, Any], key: str) -> dict[str, Any]:
        resource = payload.get(key)
        return resource if isinstance(resource, dict) else payload

    @staticmethod
    def _items(payload: Any, key: str) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return payload
        for candidate in (key, "items"):
            value = payload.get(candidate)
            if isinstance(value, list):
                return value
        raise AssertionError(f"Response has no {key!r} list: {payload!r}")

    @staticmethod
    def _assert_timestamp(value: str) -> None:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise AssertionError("Timestamp must include timezone information.")


class NotebookMigrationTest(unittest.TestCase):
    def test_additive_migration_preserves_documents_and_unique_assignment(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "legacy.db"
            with closing(sqlite3.connect(database_path)) as connection:
                connection.execute(
                    """
                    CREATE TABLE documents (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        filename TEXT NOT NULL,
                        mime_type TEXT NOT NULL,
                        file_hash TEXT NOT NULL UNIQUE,
                        file_data BLOB NOT NULL,
                        chunk_count INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO documents (
                        filename, mime_type, file_hash, file_data,
                        chunk_count, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "legacy.txt",
                        "text/plain",
                        "legacy-hash",
                        sqlite3.Binary(b"legacy"),
                        1,
                        "2026-07-18T00:00:00+00:00",
                    ),
                )
                connection.commit()

            with (
                patch.object(rag_database, "DATABASE_PATH", database_path),
                patch.object(rag_database, "ensure_directories"),
            ):
                rag_database.initialize_database()

            with closing(sqlite3.connect(database_path)) as connection:
                connection.row_factory = sqlite3.Row
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                self.assertIn("notebooks", tables)
                self.assertIn("notebook_documents", tables)
                legacy = connection.execute(
                    "SELECT filename, file_data FROM documents WHERE id = 1"
                ).fetchone()
                self.assertEqual(legacy["filename"], "legacy.txt")
                self.assertEqual(bytes(legacy["file_data"]), b"legacy")

                notebook_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(notebooks)"
                    ).fetchall()
                }
                self.assertTrue(
                    {"id", "name", "description", "created_at", "updated_at"}
                    <= notebook_columns
                )

                assignment_columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(notebook_documents)"
                    ).fetchall()
                }
                self.assertTrue(
                    {"notebook_id", "document_id"} <= assignment_columns
                )
                self.assertTrue(
                    self._document_id_is_unique(connection),
                    "Each document must belong to at most one notebook.",
                )

    @staticmethod
    def _document_id_is_unique(connection: sqlite3.Connection) -> bool:
        table_info = connection.execute(
            "PRAGMA table_info(notebook_documents)"
        ).fetchall()
        if any(row[1] == "document_id" and int(row[5]) > 0 for row in table_info):
            return True

        for index_row in connection.execute(
            "PRAGMA index_list(notebook_documents)"
        ).fetchall():
            if not bool(index_row[2]):
                continue
            index_columns = [
                row[2]
                for row in connection.execute(
                    f"PRAGMA index_info('{index_row[1]}')"
                ).fetchall()
            ]
            if index_columns == ["document_id"]:
                return True
        return False


if __name__ == "__main__":
    unittest.main()
