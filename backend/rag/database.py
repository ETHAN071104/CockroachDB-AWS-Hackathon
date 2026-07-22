from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from collections.abc import Iterator
from typing import Optional

from backend.rag.config import DATABASE_PATH, ensure_directories
from backend.domain import DEFAULT_WORKSPACE_ID
from backend.repositories.sqlite.connection import connection_scope


@dataclass(frozen=True)
class StoredDocument:
    id: int
    filename: str
    mime_type: str
    file_hash: str
    chunk_count: int
    created_at: str
    updated_at: str


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """Compatibility entry point backed by the SQLite adapter scope."""
    ensure_directories()
    with connection_scope(DATABASE_PATH) as connection:
        yield connection


def initialize_database() -> None:
    with get_connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                file_hash TEXT NOT NULL UNIQUE,
                file_data BLOB NOT NULL,
                chunk_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT,
                workspace_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'
            )
            """
        )

        document_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(documents)"
            ).fetchall()
        }
        if "updated_at" not in document_columns:
            connection.execute(
                "ALTER TABLE documents ADD COLUMN updated_at TEXT"
            )

        connection.execute(
            """
            UPDATE documents
            SET updated_at = created_at
            WHERE updated_at IS NULL OR updated_at = ''
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_documents_file_hash
            ON documents(file_hash)
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notebooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                workspace_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notebook_documents (
                document_id INTEGER PRIMARY KEY,
                notebook_id INTEGER NOT NULL,
                assigned_at TEXT NOT NULL,
                workspace_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001',
                FOREIGN KEY (document_id)
                    REFERENCES documents(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (notebook_id)
                    REFERENCES notebooks(id)
                    ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_notebook_documents_notebook_id
            ON notebook_documents(notebook_id)
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS cached_intelligence (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                scope_kind TEXT NOT NULL,
                scope_key TEXT NOT NULL,
                result_json TEXT NOT NULL,
                source_snapshot_json TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                workspace_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001',
                UNIQUE (kind, scope_kind, scope_key)
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cached_intelligence_scope
            ON cached_intelligence(scope_kind, scope_key)
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS topics (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                extraction_scope_kind TEXT NOT NULL,
                extraction_scope_key TEXT NOT NULL,
                generated_at TEXT NOT NULL,
                source_fingerprint TEXT NOT NULL,
                workspace_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_topics_extraction_scope
            ON topics(
                extraction_scope_kind,
                extraction_scope_key
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS topic_sources (
                topic_id TEXT NOT NULL,
                document_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
                source_index INTEGER NOT NULL,
                filename TEXT NOT NULL,
                mime_type TEXT NOT NULL,
                page_number INTEGER,
                slide_number INTEGER,
                excerpt TEXT NOT NULL DEFAULT '',
                distance REAL,
                workspace_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001',
                PRIMARY KEY (topic_id, document_id, chunk_index),
                FOREIGN KEY (topic_id)
                    REFERENCES topics(id)
                    ON DELETE CASCADE,
                FOREIGN KEY (document_id)
                    REFERENCES documents(id)
                    ON DELETE CASCADE
            )
            """
        )

        topic_source_columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(topic_sources)"
            ).fetchall()
        }
        topic_source_migrations = {
            "source_index": "INTEGER NOT NULL DEFAULT 1",
            "filename": "TEXT NOT NULL DEFAULT ''",
            "mime_type": "TEXT NOT NULL DEFAULT ''",
            "page_number": "INTEGER",
            "slide_number": "INTEGER",
            "excerpt": "TEXT NOT NULL DEFAULT ''",
            "distance": "REAL",
        }
        for column_name, column_definition in (
            topic_source_migrations.items()
        ):
            if column_name not in topic_source_columns:
                connection.execute(
                    "ALTER TABLE topic_sources "
                    f"ADD COLUMN {column_name} {column_definition}"
                )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_topic_sources_document
            ON topic_sources(document_id, chunk_index)
            """
        )


def find_document_by_hash(
    file_hash: str,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> Optional[StoredDocument]:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                id,
                filename,
                mime_type,
                file_hash,
                chunk_count,
                created_at,
                COALESCE(updated_at, created_at) AS updated_at
            FROM documents
            WHERE file_hash = ? AND workspace_id = ?
            """,
            (file_hash, workspace_id),
        ).fetchone()

    if row is None:
        return None

    return StoredDocument(
        id=int(row["id"]),
        filename=str(row["filename"]),
        mime_type=str(row["mime_type"]),
        file_hash=str(row["file_hash"]),
        chunk_count=int(row["chunk_count"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def insert_document(
    filename: str,
    mime_type: str,
    file_hash: str,
    file_data: bytes,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> int:
    created_at = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO documents (
                filename,
                mime_type,
                file_hash,
                file_data,
                chunk_count,
                created_at,
                updated_at,
                workspace_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filename,
                mime_type,
                file_hash,
                sqlite3.Binary(file_data),
                0,
                created_at,
                created_at,
                workspace_id,
            ),
        )

        document_id = cursor.lastrowid

    if document_id is None:
        raise RuntimeError("SQLite did not return a document ID.")

    return int(document_id)


def update_chunk_count(
    document_id: int,
    chunk_count: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> None:
    updated_at = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE documents
            SET chunk_count = ?,
                updated_at = ?
            WHERE id = ? AND workspace_id = ?
            """,
            (chunk_count, updated_at, document_id, workspace_id),
        )

        if cursor.rowcount == 0:
            raise ValueError(
                f"Document ID {document_id} does not exist."
            )


def get_document_file_data(
    document_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> tuple[str, bytes]:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT filename, file_data
            FROM documents
            WHERE id = ? AND workspace_id = ?
            """,
            (document_id, workspace_id),
        ).fetchone()

    if row is None:
        raise ValueError(
            f"Document ID {document_id} does not exist."
        )

    return (
        str(row["filename"]),
        bytes(row["file_data"]),
    )


def get_document(
    document_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> Optional[StoredDocument]:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                id,
                filename,
                mime_type,
                file_hash,
                chunk_count,
                created_at,
                COALESCE(updated_at, created_at) AS updated_at
            FROM documents
            WHERE id = ? AND workspace_id = ?
            """,
            (document_id, workspace_id),
        ).fetchone()

    if row is None:
        return None

    return StoredDocument(
        id=int(row["id"]),
        filename=str(row["filename"]),
        mime_type=str(row["mime_type"]),
        file_hash=str(row["file_hash"]),
        chunk_count=int(row["chunk_count"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def list_documents(
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[StoredDocument]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                filename,
                mime_type,
                file_hash,
                chunk_count,
                created_at,
                COALESCE(updated_at, created_at) AS updated_at
            FROM documents
            WHERE workspace_id = ?
            ORDER BY id DESC
            """,
            (workspace_id,),
        ).fetchall()

    return [
        StoredDocument(
            id=int(row["id"]),
            filename=str(row["filename"]),
            mime_type=str(row["mime_type"]),
            file_hash=str(row["file_hash"]),
            chunk_count=int(row["chunk_count"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )
        for row in rows
    ]


def delete_document_record(
    document_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> bool:
    with get_connection() as connection:
        notebook_row = connection.execute(
            """
            SELECT notebook_id
            FROM notebook_documents
            WHERE document_id = ? AND workspace_id = ?
            """,
            (document_id, workspace_id),
        ).fetchone()
        cursor = connection.execute(
            """
            DELETE FROM documents
            WHERE id = ? AND workspace_id = ?
            """,
            (document_id, workspace_id),
        )

        if cursor.rowcount > 0 and notebook_row is not None:
            connection.execute(
                """
                UPDATE notebooks
                SET updated_at = ?
                WHERE id = ?
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    int(notebook_row["notebook_id"]),
                ),
            )

        return cursor.rowcount > 0


def delete_document_record_if_exists(
    document_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> None:
    with get_connection() as connection:
        notebook_row = connection.execute(
            """
            SELECT notebook_id
            FROM notebook_documents
            WHERE document_id = ? AND workspace_id = ?
            """,
            (document_id, workspace_id),
        ).fetchone()
        cursor = connection.execute(
            """
            DELETE FROM documents
            WHERE id = ? AND workspace_id = ?
            """,
            (document_id, workspace_id),
        )

        if cursor.rowcount > 0 and notebook_row is not None:
            connection.execute(
                """
                UPDATE notebooks
                SET updated_at = ?
                WHERE id = ?
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    int(notebook_row["notebook_id"]),
                ),
            )


def document_count(
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> int:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM documents
            WHERE workspace_id = ?
            """,
            (workspace_id,),
        ).fetchone()

    if row is None:
        return 0

    return int(row["total"])
