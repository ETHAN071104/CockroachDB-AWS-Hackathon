from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from backend.rag.database import get_connection
from backend.domain import DEFAULT_WORKSPACE_ID


# ============================================================
# ALLOWED VALUES
# ============================================================

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

ALLOWED_RELATIONSHIP_TYPES = {
    "consolidated_into",
}


# ============================================================
# DATABASE MODELS
# ============================================================

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


@dataclass(frozen=True)
class StoredMemoryRelationship:
    id: int
    source_memory_id: int
    target_memory_id: int
    relationship_type: str
    created_at: str


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def initialize_memory_database() -> None:
    """
    Create memory and lineage tables when they do not exist.
    """
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
                workspace_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001',

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

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS memory_relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_memory_id INTEGER NOT NULL,
                target_memory_id INTEGER NOT NULL,
                relationship_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                workspace_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001',

                CHECK (
                    relationship_type IN (
                        'consolidated_into'
                    )
                ),

                CHECK (
                    source_memory_id != target_memory_id
                ),

                UNIQUE (
                    source_memory_id,
                    target_memory_id,
                    relationship_type
                ),

                FOREIGN KEY (
                    source_memory_id
                )
                REFERENCES memories(id),

                FOREIGN KEY (
                    target_memory_id
                )
                REFERENCES memories(id)
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_memory_relationships_source
            ON memory_relationships(source_memory_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_memory_relationships_target
            ON memory_relationships(target_memory_id)
            """
        )


# ============================================================
# VALIDATION
# ============================================================

def validate_memory_type(memory_type: str) -> str:
    """
    Normalize user-friendly memory type values.

    Examples:

    learning state
    learning-state
    Learning_State

    become:

    learning_state
    """
    cleaned = (
        memory_type
        .strip()
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )

    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")

    if cleaned not in ALLOWED_MEMORY_TYPES:
        allowed = ", ".join(
            sorted(ALLOWED_MEMORY_TYPES)
        )

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


def validate_relationship_type(
    relationship_type: str,
) -> str:
    cleaned = relationship_type.strip().lower()

    if cleaned not in ALLOWED_RELATIONSHIP_TYPES:
        allowed = ", ".join(
            sorted(ALLOWED_RELATIONSHIP_TYPES)
        )

        raise ValueError(
            "Invalid memory relationship type. "
            f"Allowed values: {allowed}"
        )

    return cleaned


# ============================================================
# ROW CONVERSION
# ============================================================

def row_to_memory(
    row: sqlite3.Row,
) -> StoredMemory:
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


def row_to_memory_relationship(
    row: sqlite3.Row,
) -> StoredMemoryRelationship:
    return StoredMemoryRelationship(
        id=int(row["id"]),
        source_memory_id=int(
            row["source_memory_id"]
        ),
        target_memory_id=int(
            row["target_memory_id"]
        ),
        relationship_type=str(
            row["relationship_type"]
        ),
        created_at=str(row["created_at"]),
    )


# ============================================================
# MEMORY INSERTION
# ============================================================

def insert_memory(
    memory_type: str,
    content: str,
    confidence: float,
    importance: float,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> int:
    cleaned_type = validate_memory_type(
        memory_type
    )

    cleaned_content = content.strip()

    if not cleaned_content:
        raise ValueError(
            "Memory content cannot be empty."
        )

    cleaned_confidence = validate_score(
        confidence,
        "Confidence",
    )

    cleaned_importance = validate_score(
        importance,
        "Importance",
    )

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

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
                updated_at,
                workspace_id
            )
            VALUES (?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (
                cleaned_type,
                cleaned_content,
                cleaned_confidence,
                cleaned_importance,
                timestamp,
                timestamp,
                workspace_id,
            ),
        )

        memory_id = cursor.lastrowid

    if memory_id is None:
        raise RuntimeError(
            "SQLite did not return a memory ID."
        )

    return int(memory_id)


# ============================================================
# MEMORY READS
# ============================================================

def get_memory(
    memory_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
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
            WHERE id = ? AND workspace_id = ?
            """,
            (int(memory_id), workspace_id),
        ).fetchone()

    if row is None:
        return None

    return row_to_memory(row)


def get_memories_by_ids(
    memory_ids: list[int],
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[StoredMemory]:
    """
    Load multiple memories while preserving requested order.

    Missing IDs are omitted. The service layer will reject
    incomplete selections later.
    """
    unique_ids = list(
        dict.fromkeys(
            int(memory_id)
            for memory_id in memory_ids
        )
    )

    if not unique_ids:
        return []

    placeholders = ", ".join(
        "?"
        for _ in unique_ids
    )

    with get_connection() as connection:
        rows = connection.execute(
            f"""
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
            WHERE id IN ({placeholders}) AND workspace_id = ?
            """,
            tuple([*unique_ids, workspace_id]),
        ).fetchall()

    memories_by_id = {
        int(row["id"]): row_to_memory(row)
        for row in rows
    }

    return [
        memories_by_id[memory_id]
        for memory_id in unique_ids
        if memory_id in memories_by_id
    ]


def list_memories(
    include_archived: bool = False,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
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
                WHERE workspace_id = ?
                ORDER BY id DESC
                """,
                (workspace_id,),
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
                WHERE status = 'active' AND workspace_id = ?
                ORDER BY id DESC
                """,
                (workspace_id,),
            ).fetchall()

    return [
        row_to_memory(row)
        for row in rows
    ]


# ============================================================
# MEMORY UPDATES
# ============================================================

def update_memory_record(
    memory_id: int,
    memory_type: str,
    content: str,
    confidence: float,
    importance: float,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> bool:
    cleaned_type = validate_memory_type(
        memory_type
    )

    cleaned_content = content.strip()

    if not cleaned_content:
        raise ValueError(
            "Memory content cannot be empty."
        )

    cleaned_confidence = validate_score(
        confidence,
        "Confidence",
    )

    cleaned_importance = validate_score(
        importance,
        "Importance",
    )

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

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
            WHERE id = ? AND workspace_id = ?
            """,
            (
                cleaned_type,
                cleaned_content,
                cleaned_confidence,
                cleaned_importance,
                timestamp,
                int(memory_id),
                workspace_id,
            ),
        )

        return cursor.rowcount > 0


def archive_memory_record(
    memory_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> bool:
    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE memories
            SET
                status = 'archived',
                updated_at = ?
            WHERE id = ? AND workspace_id = ?
            """,
            (
                timestamp,
                int(memory_id),
                workspace_id,
            ),
        )

        return cursor.rowcount > 0
    
def activate_memory_record(
    memory_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> bool:
    """
    Restore an archived memory to active status.

    Used when rolling back a failed consolidation.
    """
    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE memories
            SET
                status = 'active',
                updated_at = ?
            WHERE id = ?
              AND workspace_id = ?
              AND status = 'archived'
            """,
            (
                timestamp,
                int(memory_id),
                workspace_id,
            ),
        )

        return cursor.rowcount > 0


# ============================================================
# MEMORY DELETION
# ============================================================

def delete_memory_record(
    memory_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM memories
            WHERE id = ? AND workspace_id = ?
            """,
            (int(memory_id), workspace_id),
        )

        return cursor.rowcount > 0


# ============================================================
# MEMORY RELATIONSHIPS / LINEAGE
# ============================================================

def insert_memory_relationships(
    source_memory_ids: list[int],
    target_memory_id: int,
    relationship_type: str = "consolidated_into",
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> None:
    """
    Record that source memories were consolidated into one
    target memory.
    """
    cleaned_relationship_type = (
        validate_relationship_type(
            relationship_type
        )
    )

    unique_source_ids = list(
        dict.fromkeys(
            int(memory_id)
            for memory_id in source_memory_ids
        )
    )

    if not unique_source_ids:
        raise ValueError(
            "At least one source memory ID is required."
        )

    cleaned_target_id = int(
        target_memory_id
    )

    if cleaned_target_id in unique_source_ids:
        raise ValueError(
            "A memory cannot be consolidated into itself."
        )

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    rows = [
        (
            workspace_id,
            source_memory_id,
            cleaned_target_id,
            cleaned_relationship_type,
            timestamp,
        )
        for source_memory_id in unique_source_ids
    ]

    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO memory_relationships (
                workspace_id,
                source_memory_id,
                target_memory_id,
                relationship_type,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )


def get_relationships_for_target(
    target_memory_id: int,
) -> list[StoredMemoryRelationship]:
    """
    Return lineage relationships for one consolidated target.
    """
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                source_memory_id,
                target_memory_id,
                relationship_type,
                created_at
            FROM memory_relationships
            WHERE target_memory_id = ?
            ORDER BY id ASC
            """,
            (int(target_memory_id),),
        ).fetchall()

    return [
        row_to_memory_relationship(row)
        for row in rows
    ]


def delete_relationships_for_target(
    target_memory_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> int:
    """
    Remove lineage records for a consolidation target.

    Used during rollback if consolidation fails.
    """
    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM memory_relationships
            WHERE target_memory_id = ? AND workspace_id = ?
            """,
            (int(target_memory_id), workspace_id),
        )

        return cursor.rowcount
