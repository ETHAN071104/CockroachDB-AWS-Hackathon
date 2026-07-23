from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.errors import install_error_handlers
from backend.api.export_service import (
    CHROMA_INDEX_FILENAMES,
    ExportArtifact,
    ExportCreationError,
    build_study_export,
    cleanup_export_artifact,
)
from backend.api.routes.system import router as system_router


SEGMENT_ID = "11111111-2222-4333-8444-555555555555"
SYMLINK_SEGMENT_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _create_database(path: Path, table: str, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(f"CREATE TABLE {table} (value TEXT NOT NULL)")
        connection.execute(f"INSERT INTO {table} (value) VALUES (?)", (value,))
        connection.commit()


def _open_wal_database(path: Path, table: str, value: str) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute(f"CREATE TABLE {table} (value TEXT NOT NULL)")
    connection.execute(f"INSERT INTO {table} (value) VALUES (?)", (value,))
    connection.commit()
    return connection


def _read_value(database_path: Path, table: str) -> str:
    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute(f"SELECT value FROM {table}").fetchone()
    assert row is not None
    return str(row[0])


class StudyExportTest(unittest.TestCase):
    def test_export_snapshots_and_allowlists_all_data_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "data" / "app.db"
            document_store = root / "data" / "chroma"
            memory_store = root / "data" / "memory_chroma"
            temp_parent = root / "exports"
            temp_parent.mkdir()

            app_connection = _open_wal_database(
                database_path,
                "application_state",
                "app snapshot",
            )
            document_connection = _open_wal_database(
                document_store / "chroma.sqlite3",
                "vectors",
                "document vectors",
            )
            _create_database(
                memory_store / "chroma.sqlite3",
                "vectors",
                "memory vectors",
            )
            try:
                document_segment = document_store / SEGMENT_ID
                memory_segment = memory_store / SEGMENT_ID
                document_segment.mkdir()
                memory_segment.mkdir()
                for filename in CHROMA_INDEX_FILENAMES:
                    (document_segment / filename).write_bytes(
                        f"document:{filename}".encode("utf-8")
                    )
                    (memory_segment / filename).write_bytes(
                        f"memory:{filename}".encode("utf-8")
                    )

                # Files outside the exact SQLite/HNSW allowlist must never enter ZIP.
                (root / "data" / ".env").write_text("API_KEY=secret", encoding="utf-8")
                (document_store / "debug.log").write_text("secret log", encoding="utf-8")
                (document_store / "pending_registry.json").write_text(
                    "pending", encoding="utf-8"
                )
                (document_segment / "secret.txt").write_text("secret", encoding="utf-8")
                invalid_segment = document_store / "not-a-segment"
                invalid_segment.mkdir()
                (invalid_segment / "header.bin").write_bytes(b"excluded")
                (root / ".venv").mkdir()
                (root / ".venv" / "secret.txt").write_text("excluded", encoding="utf-8")
                (root / "frontend").mkdir()
                (root / "frontend" / "bundle.js").write_text("excluded", encoding="utf-8")

                symlink_created = False
                symlink_segment = document_store / SYMLINK_SEGMENT_ID
                symlink_segment.mkdir()
                outside_file = root / "outside-header.bin"
                outside_file.write_bytes(b"outside")
                try:
                    os.symlink(outside_file, symlink_segment / "header.bin")
                    symlink_created = True
                except OSError:
                    pass

                artifact = build_study_export(
                    database_path=database_path,
                    document_chroma_path=document_store,
                    memory_chroma_path=memory_store,
                    temp_parent=temp_parent,
                    created_at=datetime(2026, 7, 18, 5, 30, tzinfo=timezone.utc),
                    app_version="0.7.0-test",
                )
            finally:
                app_connection.close()
                document_connection.close()

            self.assertTrue(artifact.archive_path.is_file())
            self.assertEqual(
                artifact.download_name,
                "study-companion-export-20260718T053000Z.zip",
            )

            extraction_root = root / "verify"
            extraction_root.mkdir()
            with zipfile.ZipFile(artifact.archive_path) as archive:
                names = set(archive.namelist())
                manifest_bytes = archive.read("manifest.json")
                manifest = json.loads(manifest_bytes)
                archive.extract("data/app.db", extraction_root)
                archive.extract("data/chroma/chroma.sqlite3", extraction_root)
                archive.extract("data/memory_chroma/chroma.sqlite3", extraction_root)

                data_names = names - {"manifest.json"}
                records = {item["path"]: item for item in manifest["files"]}
                self.assertEqual(set(records), data_names)
                for name, record in records.items():
                    content = archive.read(name)
                    self.assertEqual(record["size"], len(content))
                    self.assertEqual(
                        record["sha256"],
                        hashlib.sha256(content).hexdigest(),
                    )

            self.assertEqual(manifest["format"], "local-study-companion")
            self.assertEqual(manifest["format_version"], 1)
            self.assertEqual(manifest["app_version"], "0.7.0-test")
            self.assertEqual(manifest["created_at"], "2026-07-18T05:30:00Z")
            self.assertTrue(manifest["stores"]["document_chroma"]["present"])
            self.assertTrue(manifest["stores"]["memory_chroma"]["present"])
            self.assertNotIn(str(root), manifest_bytes.decode("utf-8"))

            excluded_fragments = (
                ".env",
                ".log",
                ".venv",
                "frontend",
                "pending_registry",
                "secret",
                "not-a-segment",
                "-wal",
                "-shm",
            )
            for fragment in excluded_fragments:
                self.assertFalse(any(fragment in name for name in names), fragment)
            if symlink_created:
                self.assertNotIn(
                    f"data/chroma/{SYMLINK_SEGMENT_ID}/header.bin",
                    names,
                )

            self.assertEqual(
                _read_value(extraction_root / "data" / "app.db", "application_state"),
                "app snapshot",
            )
            self.assertEqual(
                _read_value(
                    extraction_root / "data" / "chroma" / "chroma.sqlite3",
                    "vectors",
                ),
                "document vectors",
            )
            self.assertEqual(
                _read_value(
                    extraction_root / "data" / "memory_chroma" / "chroma.sqlite3",
                    "vectors",
                ),
                "memory vectors",
            )

            workspace = artifact.workspace_path
            cleanup_export_artifact(artifact)
            self.assertFalse(workspace.exists())

    def test_symlink_database_is_rejected_and_temp_files_are_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "real.db"
            link = root / "linked.db"
            export_parent = root / "exports"
            export_parent.mkdir()
            _create_database(source, "records", "value")
            try:
                os.symlink(source, link)
            except OSError as error:
                self.skipTest(f"Symlinks are unavailable: {type(error).__name__}")

            with self.assertRaises(ExportCreationError):
                build_study_export(
                    database_path=link,
                    document_chroma_path=root / "missing-documents",
                    memory_chroma_path=root / "missing-memories",
                    temp_parent=export_parent,
                )

            self.assertEqual(list(export_parent.iterdir()), [])

    def test_export_route_streams_zip_then_removes_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "study-companion-export-route"
            workspace.mkdir()
            archive_path = workspace / "safe-export.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("manifest.json", "{}")
            artifact = ExportArtifact(
                archive_path=archive_path,
                workspace_path=workspace,
                download_name="study-companion-export-test.zip",
            )
            application = FastAPI()
            install_error_handlers(application)
            application.include_router(system_router)

            with patch(
                "backend.api.routes.system.build_study_export",
                return_value=artifact,
            ):
                with TestClient(application) as client:
                    response = client.get("/api/system/export")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "application/zip")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertIn(
                "study-companion-export-test.zip",
                response.headers["content-disposition"],
            )
            self.assertGreater(len(response.content), 0)
            self.assertFalse(workspace.exists())

    def test_export_route_returns_only_generic_structured_failure(self) -> None:
        application = FastAPI()
        install_error_handlers(application)
        application.include_router(system_router)
        private_detail = "C:/private/data/app.db"

        with patch(
            "backend.api.routes.system.build_study_export",
            side_effect=ExportCreationError(private_detail),
        ):
            with TestClient(application) as client:
                response = client.get("/api/system/export")

        self.assertEqual(response.status_code, 500)
        payload = response.json()["error"]
        self.assertEqual(payload["code"], "EXPORT_FAILED")
        self.assertEqual(payload["title"], "Study-data export failed")
        self.assertEqual(
            payload["message"],
            "Study data export could not be created.",
        )
        self.assertTrue(payload["retryable"])
        self.assertRegex(payload["request_id"], r"^[a-f0-9]{32}$")
        self.assertNotIn(private_detail, response.text)

    def test_application_factory_registers_export_and_keeps_integrity(self) -> None:
        paths = set(
            create_app(
                allow_legacy_default_workspace=True
            ).openapi()["paths"]
        )
        self.assertIn("/api/system/export", paths)
        self.assertIn("/api/system/integrity", paths)


if __name__ == "__main__":
    unittest.main()
