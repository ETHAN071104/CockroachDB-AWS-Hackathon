from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from rag.config import DATABASE_PATH, ensure_directories


@dataclass(frozen=True)
class StoredDocument:
    id: int
    filename: str
    mime_type: str
    file_hash: str
    chunk_count: int
    created_at: str


def get_connection() -> sqlite3.Connection:
    ensure_directories()

    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")

    return connection


def initialize_database() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
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
            CREATE INDEX IF NOT EXISTS idx_documents_file_hash
            ON documents(file_hash)
            """
        )


def find_document_by_hash(
    file_hash: str,
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
                created_at
            FROM documents
            WHERE file_hash = ?
            """,
            (file_hash,),
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
    )


def insert_document(
    filename: str,
    mime_type: str,
    file_hash: str,
    file_data: bytes,
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
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                filename,
                mime_type,
                file_hash,
                sqlite3.Binary(file_data),
                0,
                created_at,
            ),
        )

        document_id = cursor.lastrowid

    if document_id is None:
        raise RuntimeError("SQLite did not return a document ID.")

    return int(document_id)


def update_chunk_count(
    document_id: int,
    chunk_count: int,
) -> None:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE documents
            SET chunk_count = ?
            WHERE id = ?
            """,
            (chunk_count, document_id),
        )

        if cursor.rowcount == 0:
            raise ValueError(
                f"Document ID {document_id} does not exist."
            )


def get_document_file_data(
    document_id: int,
) -> tuple[str, bytes]:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT filename, file_data
            FROM documents
            WHERE id = ?
            """,
            (document_id,),
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
                created_at
            FROM documents
            WHERE id = ?
            """,
            (document_id,),
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
    )


def list_documents() -> list[StoredDocument]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                filename,
                mime_type,
                file_hash,
                chunk_count,
                created_at
            FROM documents
            ORDER BY id DESC
            """
        ).fetchall()

    return [
        StoredDocument(
            id=int(row["id"]),
            filename=str(row["filename"]),
            mime_type=str(row["mime_type"]),
            file_hash=str(row["file_hash"]),
            chunk_count=int(row["chunk_count"]),
            created_at=str(row["created_at"]),
        )
        for row in rows
    ]


def delete_document_record(
    document_id: int,
) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM documents
            WHERE id = ?
            """,
            (document_id,),
        )

        return cursor.rowcount > 0


def delete_document_record_if_exists(
    document_id: int,
) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            DELETE FROM documents
            WHERE id = ?
            """,
            (document_id,),
        )


def document_count() -> int:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS total
            FROM documents
            """
        ).fetchone()

    if row is None:
        return 0

    return int(row["total"])