from __future__ import annotations

import hashlib
import json
import base64
import shutil
import sqlite3
import tempfile
import uuid
import zipfile

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.api.health import API_VERSION
from backend.api.public_ids import serialize_public_ids_in_data
from backend.rag import config as rag_config


EXPORT_FORMAT = "local-study-companion"
EXPORT_FORMAT_VERSION = 1
CHROMA_DATABASE_FILENAME = "chroma.sqlite3"
CHROMA_INDEX_FILENAMES = frozenset(
    {
        "data_level0.bin",
        "header.bin",
        "index_metadata.pickle",
        "length.bin",
        "link_lists.bin",
    }
)


class ExportCreationError(RuntimeError):
    """Raised when a safe export cannot be assembled."""


@dataclass(frozen=True)
class ExportArtifact:
    archive_path: Path
    workspace_path: Path
    download_name: str


@dataclass(frozen=True)
class _IncludedFile:
    archive_path: str
    staged_path: Path
    store: str


def build_study_export(
    *,
    workspace_id: str | None = None,
    database_path: Path | None = None,
    document_chroma_path: Path | None = None,
    memory_chroma_path: Path | None = None,
    temp_parent: Path | None = None,
    created_at: datetime | None = None,
    app_version: str = API_VERSION,
) -> ExportArtifact:
    """Create one allowlisted ZIP export in an isolated temporary directory."""
    if rag_config.PERSISTENCE_BACKEND == "cockroach":
        if workspace_id is None:
            raise ExportCreationError(
                "A workspace-scoped CockroachDB export is required."
            )
        return _build_cockroach_export(
            workspace_id=workspace_id,
            temp_parent=temp_parent,
            created_at=created_at,
            app_version=app_version,
        )
    try:
        workspace = Path(
            tempfile.mkdtemp(
                prefix="study-companion-export-",
                dir=str(temp_parent) if temp_parent is not None else None,
            )
        ).resolve()
    except Exception as error:
        raise ExportCreationError("Study data export could not be created.") from error


    try:
        timestamp = _utc_timestamp(created_at)
        staging_root = workspace / "staging"
        data_root = staging_root / "data"
        data_root.mkdir(parents=True)

        included: list[_IncludedFile] = []
        app_source = _regular_file(
            database_path or rag_config.DATABASE_PATH,
            required=True,
        )
        if app_source is None:
            raise ExportCreationError("A required export source is unavailable.")
        app_snapshot = data_root / "app.db"
        _backup_sqlite(app_source, app_snapshot)
        included.append(
            _IncludedFile(
                archive_path="data/app.db",
                staged_path=app_snapshot,
                store="application_database",
            )
        )

        document_files = _snapshot_chroma_store(
            source_path=document_chroma_path or rag_config.CHROMA_PATH,
            staging_path=data_root / "chroma",
            archive_root="data/chroma",
            store_name="document_chroma",
        )
        memory_files = _snapshot_chroma_store(
            source_path=memory_chroma_path or rag_config.MEMORY_CHROMA_PATH,
            staging_path=data_root / "memory_chroma",
            archive_root="data/memory_chroma",
            store_name="memory_chroma",
        )
        included.extend(document_files)
        included.extend(memory_files)
        included.sort(key=lambda item: item.archive_path)

        manifest = _build_manifest(
            timestamp=timestamp,
            app_version=app_version,
            included=included,
            document_file_count=len(document_files),
            memory_file_count=len(memory_files),
        )
        compact_timestamp = timestamp.replace("-", "").replace(":", "")
        archive_name = f"study-companion-export-{compact_timestamp}.zip"
        archive_path = workspace / archive_name
        _write_archive(archive_path, manifest, included)

        return ExportArtifact(
            archive_path=archive_path,
            workspace_path=workspace,
            download_name=archive_name,
        )
    except Exception as error:
        shutil.rmtree(workspace, ignore_errors=True)
        if isinstance(error, ExportCreationError):
            raise
        raise ExportCreationError("Study data export could not be created.") from error


def _build_cockroach_export(
    *,
    workspace_id: str,
    temp_parent: Path | None,
    created_at: datetime | None,
    app_version: str,
) -> ExportArtifact:
    """Create a credential-free logical snapshot without touching SQLite/Chroma."""
    from sqlalchemy import text
    from uuid import UUID

    from backend.repositories.cockroach.connection import get_engine

    allowlist = (
        "workspaces", "notebooks", "documents", "document_blobs",
        "notebook_documents", "cached_intelligence", "topics", "topic_sources",
        "document_chunks", "study_sessions", "study_interactions",
        "study_interaction_sources", "quiz_attempts", "quiz_question_attempts",
        "quiz_question_sources", "learner_memories", "memory_relationships",
        "learner_memory_embeddings", "workflow_states", "learning_signals",
        "adaptation_events", "embedding_jobs",
    )
    try:
        workspace = Path(
            tempfile.mkdtemp(
                prefix="study-companion-export-",
                dir=str(temp_parent) if temp_parent is not None else None,
            )
        ).resolve()
        timestamp = _utc_timestamp(created_at)
        payload: dict[str, object] = {
            "format": EXPORT_FORMAT,
            "format_version": EXPORT_FORMAT_VERSION,
            "app_version": app_version,
            "created_at": timestamp,
            "persistence_backend": "cockroach",
            "credentials_recorded": False,
            "tables": {},
        }
        workspace_uuid = UUID(workspace_id)
        with get_engine().connect() as connection:
            tables: dict[str, object] = {}
            for table_name in allowlist:
                if table_name == "workspaces":
                    rows = connection.execute(
                        text(
                            "SELECT name, created_at, updated_at "
                            "FROM workspaces WHERE id=:workspace_id"
                        ),
                        {"workspace_id": workspace_uuid},
                    ).mappings().all()
                else:
                    rows = connection.execute(
                        text(
                            f"SELECT * FROM {table_name} "
                            "WHERE workspace_id=:workspace_id"
                        ),
                        {"workspace_id": workspace_uuid},
                    ).mappings().all()
                tables[table_name] = [
                    {
                        key: _logical_export_value(value, key=key)
                        for key, value in row.items()
                        if key != "workspace_id"
                    }
                    for row in rows
                ]
            payload["tables"] = tables
        data_path = workspace / "cockroach-data.json"
        data_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        compact_timestamp = timestamp.replace("-", "").replace(":", "")
        archive_name = f"study-companion-export-{compact_timestamp}.zip"
        archive_path = workspace / archive_name
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(data_path, "cockroach-data.json")
        data_path.unlink()
        return ExportArtifact(archive_path, workspace, archive_name)
    except Exception as error:
        if "workspace" in locals():
            shutil.rmtree(workspace, ignore_errors=True)
        raise ExportCreationError("Study data export could not be created.") from error


def _logical_export_value(
    value: object,
    *,
    key: str | None = None,
) -> object:
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, (dict, list)):
        return serialize_public_ids_in_data(value, key=key)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return serialize_public_ids_in_data(value, key=key)
    return str(value)


def cleanup_export_artifact(artifact: ExportArtifact) -> None:
    """Remove the entire isolated export workspace after transmission."""
    raw_workspace = artifact.workspace_path
    if raw_workspace.is_symlink():
        return
    workspace = raw_workspace.resolve()
    archive_path = artifact.archive_path.resolve()
    if (
        workspace.name.startswith("study-companion-export-")
        and archive_path.parent == workspace
    ):
        shutil.rmtree(workspace, ignore_errors=True)


def _utc_timestamp(value: datetime | None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc).replace(microsecond=0)
    return current.isoformat().replace("+00:00", "Z")


def _regular_file(path: Path, *, required: bool) -> Path | None:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ExportCreationError("An export source is not a regular file.")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        if required:
            raise ExportCreationError("A required export source is unavailable.")
        return None
    if not resolved.is_file():
        if required:
            raise ExportCreationError("A required export source is unavailable.")
        return None
    return resolved


def _directory(path: Path) -> Path | None:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ExportCreationError("An export store is not a regular directory.")
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        return None
    if not resolved.is_dir():
        raise ExportCreationError("An export store is not a regular directory.")
    return resolved


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_store_file(path: Path, root: Path) -> Path | None:
    if path.is_symlink():
        return None
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError:
        return None
    if not resolved.is_file() or not _is_within(resolved, root):
        return None
    return resolved


def _is_segment_directory(path: Path, root: Path) -> bool:
    if path.is_symlink() or not path.is_dir():
        return False
    try:
        resolved = path.resolve(strict=True)
        parsed = uuid.UUID(path.name)
    except (FileNotFoundError, ValueError):
        return False
    return _is_within(resolved, root) and str(parsed) == path.name.lower()


def _backup_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_uri = f"{source.as_uri()}?mode=ro"
    with closing(
        sqlite3.connect(source_uri, uri=True, timeout=5.0)
    ) as source_connection:
        source_connection.execute("PRAGMA busy_timeout = 5000")
        with closing(sqlite3.connect(destination)) as destination_connection:
            source_connection.backup(destination_connection)
            destination_connection.commit()


def _snapshot_chroma_store(
    *,
    source_path: Path,
    staging_path: Path,
    archive_root: str,
    store_name: str,
) -> list[_IncludedFile]:
    root = _directory(source_path)
    if root is None:
        return []

    included: list[_IncludedFile] = []
    database_source = _safe_store_file(root / CHROMA_DATABASE_FILENAME, root)
    if database_source is not None:
        database_snapshot = staging_path / CHROMA_DATABASE_FILENAME
        _backup_sqlite(database_source, database_snapshot)
        included.append(
            _IncludedFile(
                archive_path=f"{archive_root}/{CHROMA_DATABASE_FILENAME}",
                staged_path=database_snapshot,
                store=store_name,
            )
        )

    for segment in sorted(root.iterdir(), key=lambda item: item.name):
        if not _is_segment_directory(segment, root):
            continue
        for filename in sorted(CHROMA_INDEX_FILENAMES):
            source = _safe_store_file(segment / filename, root)
            if source is None:
                continue
            destination = staging_path / segment.name / filename
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            included.append(
                _IncludedFile(
                    archive_path=f"{archive_root}/{segment.name}/{filename}",
                    staged_path=destination,
                    store=store_name,
                )
            )

    return included


def _file_record(item: _IncludedFile) -> dict[str, Any]:
    digest = hashlib.sha256()
    size = 0
    with item.staged_path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return {
        "path": item.archive_path,
        "store": item.store,
        "size": size,
        "sha256": digest.hexdigest(),
    }


def _build_manifest(
    *,
    timestamp: str,
    app_version: str,
    included: list[_IncludedFile],
    document_file_count: int,
    memory_file_count: int,
) -> dict[str, Any]:
    return {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        "app_version": app_version,
        "created_at": timestamp,
        "stores": {
            "application_database": {
                "type": "sqlite",
                "file_count": 1,
            },
            "document_chroma": {
                "type": "chroma",
                "collection": rag_config.CHROMA_COLLECTION,
                "present": document_file_count > 0,
                "file_count": document_file_count,
            },
            "memory_chroma": {
                "type": "chroma",
                "collection": rag_config.MEMORY_CHROMA_COLLECTION,
                "present": memory_file_count > 0,
                "file_count": memory_file_count,
            },
        },
        "files": [_file_record(item) for item in included],
    }


def _write_archive(
    archive_path: Path,
    manifest: dict[str, Any],
    included: list[_IncludedFile],
) -> None:
    manifest_data = json.dumps(
        manifest,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    with zipfile.ZipFile(
        archive_path,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
    ) as archive:
        archive.writestr("manifest.json", manifest_data)
        for item in included:
            archive.write(item.staged_path, item.archive_path)
