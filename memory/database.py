from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from rag.database import get_connection


ALLOWED_MEMORY_TYPES = {
    "profile",
    "learning_state",
    "episodic",
    "procedural",
}

ALLOWED_MEMORY_STATUSES = {
    "active",
    "archived",
}


@dataclass(frozen=True)
class StoredMemory:
    id: int
    memory_type: str
    content: str
    confidence: float
    importance: float
    status: str
    created_at: str
    updated_at: str


def initialize_memory_database() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                memory_type TEXT NOT NULL,
                content TEXT NOT NULL,
                confidence REAL NOT NULL,
                importance REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                CHECK (
                    memory_type IN (
                        'profile',
                        'learning_state',
                        'episodic',
                        'procedural'
                    )
                ),
                CHECK (
                    status IN (
                        'active',
                        'archived'
                    )
                ),
                CHECK (
                    confidence >= 0.0
                    AND confidence <= 1.0
                ),
                CHECK (
                    importance >= 0.0
                    AND importance <= 1.0
                )
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memories_status
            ON memories(status)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_memories_type
            ON memories(memory_type)
            """
        )


def validate_memory_type(memory_type: str) -> str:
    """
    Normalize user-friendly inputs such as:

    learning state
    learning-state
    Learning_State

    into:

    learning_state
    """
    cleaned = (
        memory_type
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )

    # Remove accidental repeated underscores.
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")

    if cleaned not in ALLOWED_MEMORY_TYPES:
        allowed = ", ".join(sorted(ALLOWED_MEMORY_TYPES))

        raise ValueError(
            f"Invalid memory type. Allowed values: {allowed}"
        )

    return cleaned


def validate_score(
    score: float,
    field_name: str,
) -> float:
    numeric_score = float(score)

    if not 0.0 <= numeric_score <= 1.0:
        raise ValueError(
            f"{field_name} must be between 0.0 and 1.0."
        )

    return numeric_score


def row_to_memory(row: sqlite3.Row) -> StoredMemory:
    return StoredMemory(
        id=int(row["id"]),
        memory_type=str(row["memory_type"]),
        content=str(row["content"]),
        confidence=float(row["confidence"]),
        importance=float(row["importance"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def insert_memory(
    memory_type: str,
    content: str,
    confidence: float,
    importance: float,
) -> int:
    cleaned_type = validate_memory_type(memory_type)
    cleaned_content = content.strip()

    if not cleaned_content:
        raise ValueError("Memory content cannot be empty.")

    cleaned_confidence = validate_score(
        confidence,
        "Confidence",
    )
    cleaned_importance = validate_score(
        importance,
        "Importance",
    )

    timestamp = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO memories (
                memory_type,
                content,
                confidence,
                importance,
                status,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, 'active', ?, ?)
            """,
            (
                cleaned_type,
                cleaned_content,
                cleaned_confidence,
                cleaned_importance,
                timestamp,
                timestamp,
            ),
        )

        memory_id = cursor.lastrowid

    if memory_id is None:
        raise RuntimeError(
            "SQLite did not return a memory ID."
        )

    return int(memory_id)


def get_memory(
    memory_id: int,
) -> Optional[StoredMemory]:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                id,
                memory_type,
                content,
                confidence,
                importance,
                status,
                created_at,
                updated_at
            FROM memories
            WHERE id = ?
            """,
            (memory_id,),
        ).fetchone()

    if row is None:
        return None

    return row_to_memory(row)


def list_memories(
    include_archived: bool = False,
) -> list[StoredMemory]:
    with get_connection() as connection:
        if include_archived:
            rows = connection.execute(
                """
                SELECT
                    id,
                    memory_type,
                    content,
                    confidence,
                    importance,
                    status,
                    created_at,
                    updated_at
                FROM memories
                ORDER BY id DESC
                """
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT
                    id,
                    memory_type,
                    content,
                    confidence,
                    importance,
                    status,
                    created_at,
                    updated_at
                FROM memories
                WHERE status = 'active'
                ORDER BY id DESC
                """
            ).fetchall()

    return [row_to_memory(row) for row in rows]


def update_memory_record(
    memory_id: int,
    memory_type: str,
    content: str,
    confidence: float,
    importance: float,
) -> bool:
    cleaned_type = validate_memory_type(memory_type)
    cleaned_content = content.strip()

    if not cleaned_content:
        raise ValueError("Memory content cannot be empty.")

    cleaned_confidence = validate_score(
        confidence,
        "Confidence",
    )
    cleaned_importance = validate_score(
        importance,
        "Importance",
    )

    timestamp = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE memories
            SET
                memory_type = ?,
                content = ?,
                confidence = ?,
                importance = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                cleaned_type,
                cleaned_content,
                cleaned_confidence,
                cleaned_importance,
                timestamp,
                memory_id,
            ),
        )

        return cursor.rowcount > 0


def archive_memory_record(
    memory_id: int,
) -> bool:
    timestamp = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE memories
            SET
                status = 'archived',
                updated_at = ?
            WHERE id = ?
            """,
            (timestamp, memory_id),
        )

        return cursor.rowcount > 0


def delete_memory_record(
    memory_id: int,
) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM memories
            WHERE id = ?
            """,
            (memory_id,),
        )

        return cursor.rowcount > 0