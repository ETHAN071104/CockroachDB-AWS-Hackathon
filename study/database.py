from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from rag.database import get_connection


# ============================================================
# ALLOWED VALUES
# ============================================================

ALLOWED_SESSION_STATUSES = {
    "active",
    "completed",
}

ALLOWED_INTERACTION_OUTCOMES = {
    "unrated",
    "understood",
    "partial",
    "confused",
}


# ============================================================
# DATABASE MODELS
# ============================================================

@dataclass(frozen=True)
class StoredStudySession:
    id: int
    status: str
    started_at: str
    ended_at: str | None


@dataclass(frozen=True)
class StoredStudyInteraction:
    id: int
    session_id: int
    question: str
    answer: str
    outcome: str
    created_at: str


@dataclass(frozen=True)
class StudySourceInput:
    source_index: int
    filename: str
    page_number: int | None
    chunk_index: int | None
    distance: float


@dataclass(frozen=True)
class StoredInteractionSource:
    id: int
    interaction_id: int
    source_index: int
    filename: str
    page_number: int | None
    chunk_index: int | None
    distance: float


# ============================================================
# INITIALIZATION
# ============================================================

def initialize_study_database() -> None:
    """
    Create study-session history tables when they do not exist.
    """
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS study_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL DEFAULT 'active',
                started_at TEXT NOT NULL,
                ended_at TEXT,

                CHECK (
                    status IN (
                        'active',
                        'completed'
                    )
                ),

                CHECK (
                    (
                        status = 'active'
                        AND ended_at IS NULL
                    )
                    OR
                    (
                        status = 'completed'
                        AND ended_at IS NOT NULL
                    )
                )
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_study_sessions_status
            ON study_sessions(status)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_study_sessions_started_at
            ON study_sessions(started_at)
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS study_interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                outcome TEXT NOT NULL DEFAULT 'unrated',
                created_at TEXT NOT NULL,

                CHECK (
                    outcome IN (
                        'unrated',
                        'understood',
                        'partial',
                        'confused'
                    )
                ),

                FOREIGN KEY (
                    session_id
                )
                REFERENCES study_sessions(id)
                ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_study_interactions_session
            ON study_interactions(session_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_study_interactions_created_at
            ON study_interactions(created_at)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_study_interactions_outcome
            ON study_interactions(outcome)
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS
            study_interaction_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                interaction_id INTEGER NOT NULL,
                source_index INTEGER NOT NULL,
                filename TEXT NOT NULL,
                page_number INTEGER,
                chunk_index INTEGER,
                distance REAL NOT NULL,

                CHECK (
                    source_index > 0
                ),

                CHECK (
                    distance >= 0.0
                ),

                UNIQUE (
                    interaction_id,
                    source_index
                ),

                FOREIGN KEY (
                    interaction_id
                )
                REFERENCES study_interactions(id)
                ON DELETE CASCADE
            )
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_study_sources_interaction
            ON study_interaction_sources(interaction_id)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_study_sources_filename
            ON study_interaction_sources(filename)
            """
        )


# ============================================================
# ROW CONVERSION
# ============================================================

def row_to_study_session(
    row: sqlite3.Row,
) -> StoredStudySession:
    ended_at_value = row["ended_at"]

    return StoredStudySession(
        id=int(row["id"]),
        status=str(row["status"]),
        started_at=str(row["started_at"]),
        ended_at=(
            str(ended_at_value)
            if ended_at_value is not None
            else None
        ),
    )


def row_to_study_interaction(
    row: sqlite3.Row,
) -> StoredStudyInteraction:
    return StoredStudyInteraction(
        id=int(row["id"]),
        session_id=int(row["session_id"]),
        question=str(row["question"]),
        answer=str(row["answer"]),
        outcome=str(row["outcome"]),
        created_at=str(row["created_at"]),
    )


def row_to_interaction_source(
    row: sqlite3.Row,
) -> StoredInteractionSource:
    page_value = row["page_number"]
    chunk_value = row["chunk_index"]

    return StoredInteractionSource(
        id=int(row["id"]),
        interaction_id=int(
            row["interaction_id"]
        ),
        source_index=int(
            row["source_index"]
        ),
        filename=str(row["filename"]),
        page_number=(
            int(page_value)
            if page_value is not None
            else None
        ),
        chunk_index=(
            int(chunk_value)
            if chunk_value is not None
            else None
        ),
        distance=float(row["distance"]),
    )


# ============================================================
# SESSION OPERATIONS
# ============================================================

def create_study_session() -> StoredStudySession:
    """
    Create one new active study session.

    Use get_or_create_active_study_session() in normal
    application code so an interrupted session can be resumed.
    """
    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO study_sessions (
                status,
                started_at,
                ended_at
            )
            VALUES ('active', ?, NULL)
            """,
            (timestamp,),
        )

        session_id = cursor.lastrowid

    if session_id is None:
        raise RuntimeError(
            "SQLite did not return a study-session ID."
        )

    session = get_study_session(
        int(session_id)
    )

    if session is None:
        raise RuntimeError(
            "The study session was created but could not "
            "be loaded."
        )

    return session


def get_study_session(
    session_id: int,
) -> Optional[StoredStudySession]:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                id,
                status,
                started_at,
                ended_at
            FROM study_sessions
            WHERE id = ?
            """,
            (int(session_id),),
        ).fetchone()

    if row is None:
        return None

    return row_to_study_session(row)


def get_active_study_session() -> Optional[StoredStudySession]:
    """
    Return the most recently started active session.
    """
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                id,
                status,
                started_at,
                ended_at
            FROM study_sessions
            WHERE status = 'active'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    if row is None:
        return None

    return row_to_study_session(row)


def get_or_create_active_study_session() -> StoredStudySession:
    """
    Resume an interrupted active session or create a new one.
    """
    active_session = get_active_study_session()

    if active_session is not None:
        return active_session

    return create_study_session()


def end_study_session(
    session_id: int,
) -> StoredStudySession:
    """
    Complete one active study session.
    """
    existing = get_study_session(
        session_id
    )

    if existing is None:
        raise ValueError(
            f"Study session ID {session_id} does not exist."
        )

    if existing.status == "completed":
        return existing

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE study_sessions
            SET
                status = 'completed',
                ended_at = ?
            WHERE id = ?
              AND status = 'active'
            """,
            (
                timestamp,
                int(session_id),
            ),
        )

        if cursor.rowcount == 0:
            raise RuntimeError(
                f"Study session ID {session_id} could not "
                "be completed."
            )

    completed = get_study_session(
        session_id
    )

    if completed is None:
        raise RuntimeError(
            "The study session was completed but could not "
            "be loaded."
        )

    return completed


def list_study_sessions() -> list[StoredStudySession]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                status,
                started_at,
                ended_at
            FROM study_sessions
            ORDER BY id DESC
            """
        ).fetchall()

    return [
        row_to_study_session(row)
        for row in rows
    ]


# ============================================================
# INTERACTION OPERATIONS
# ============================================================

def insert_study_interaction(
    session_id: int,
    question: str,
    answer: str,
    outcome: str = "unrated",
) -> StoredStudyInteraction:
    """
    Store one question-and-answer interaction.
    """
    session = get_study_session(
        session_id
    )

    if session is None:
        raise ValueError(
            f"Study session ID {session_id} does not exist."
        )

    if session.status != "active":
        raise ValueError(
            "Interactions can only be added to an active "
            "study session."
        )

    cleaned_question = question.strip()
    cleaned_answer = answer.strip()
    cleaned_outcome = outcome.strip().lower()

    if not cleaned_question:
        raise ValueError(
            "Study question cannot be empty."
        )

    if not cleaned_answer:
        raise ValueError(
            "Study answer cannot be empty."
        )

    if cleaned_outcome not in ALLOWED_INTERACTION_OUTCOMES:
        allowed = ", ".join(
            sorted(ALLOWED_INTERACTION_OUTCOMES)
        )

        raise ValueError(
            "Invalid interaction outcome. "
            f"Allowed values: {allowed}"
        )

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO study_interactions (
                session_id,
                question,
                answer,
                outcome,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(session_id),
                cleaned_question,
                cleaned_answer,
                cleaned_outcome,
                timestamp,
            ),
        )

        interaction_id = cursor.lastrowid

    if interaction_id is None:
        raise RuntimeError(
            "SQLite did not return an interaction ID."
        )

    interaction = get_study_interaction(
        int(interaction_id)
    )

    if interaction is None:
        raise RuntimeError(
            "The interaction was inserted but could not "
            "be loaded."
        )

    return interaction


def get_study_interaction(
    interaction_id: int,
) -> Optional[StoredStudyInteraction]:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                id,
                session_id,
                question,
                answer,
                outcome,
                created_at
            FROM study_interactions
            WHERE id = ?
            """,
            (int(interaction_id),),
        ).fetchone()

    if row is None:
        return None

    return row_to_study_interaction(row)


def list_session_interactions(
    session_id: int,
) -> list[StoredStudyInteraction]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                session_id,
                question,
                answer,
                outcome,
                created_at
            FROM study_interactions
            WHERE session_id = ?
            ORDER BY id ASC
            """,
            (int(session_id),),
        ).fetchall()

    return [
        row_to_study_interaction(row)
        for row in rows
    ]


def update_interaction_outcome(
    interaction_id: int,
    outcome: str,
) -> StoredStudyInteraction:
    """
    Record the learner's outcome for one interaction.
    """
    cleaned_outcome = outcome.strip().lower()

    if cleaned_outcome not in ALLOWED_INTERACTION_OUTCOMES:
        allowed = ", ".join(
            sorted(ALLOWED_INTERACTION_OUTCOMES)
        )

        raise ValueError(
            "Invalid interaction outcome. "
            f"Allowed values: {allowed}"
        )

    existing = get_study_interaction(
        interaction_id
    )

    if existing is None:
        raise ValueError(
            f"Interaction ID {interaction_id} does not exist."
        )

    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE study_interactions
            SET outcome = ?
            WHERE id = ?
            """,
            (
                cleaned_outcome,
                int(interaction_id),
            ),
        )

        if cursor.rowcount == 0:
            raise RuntimeError(
                f"Interaction ID {interaction_id} could not "
                "be updated."
            )

    updated = get_study_interaction(
        interaction_id
    )

    if updated is None:
        raise RuntimeError(
            "The interaction outcome was updated but the "
            "record could not be loaded."
        )

    return updated


# ============================================================
# SOURCE OPERATIONS
# ============================================================

def insert_interaction_sources(
    interaction_id: int,
    sources: list[StudySourceInput],
) -> list[StoredInteractionSource]:
    """
    Store the document sources used for one interaction.
    """
    interaction = get_study_interaction(
        interaction_id
    )

    if interaction is None:
        raise ValueError(
            f"Interaction ID {interaction_id} does not exist."
        )

    if not sources:
        return []

    source_indexes = [
        source.source_index
        for source in sources
    ]

    if len(source_indexes) != len(
        set(source_indexes)
    ):
        raise ValueError(
            "Source indexes must be unique within an "
            "interaction."
        )

    rows: list[
        tuple[int, int, str, int | None, int | None, float]
    ] = []

    for source in sources:
        if source.source_index <= 0:
            raise ValueError(
                "Source index must be greater than zero."
            )

        cleaned_filename = (
            source.filename.strip()
        )

        if not cleaned_filename:
            raise ValueError(
                "Source filename cannot be empty."
            )

        numeric_distance = float(
            source.distance
        )

        if numeric_distance < 0:
            raise ValueError(
                "Source distance cannot be negative."
            )

        rows.append(
            (
                int(interaction_id),
                int(source.source_index),
                cleaned_filename,
                source.page_number,
                source.chunk_index,
                numeric_distance,
            )
        )

    with get_connection() as connection:
        connection.executemany(
            """
            INSERT INTO study_interaction_sources (
                interaction_id,
                source_index,
                filename,
                page_number,
                chunk_index,
                distance
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    return list_interaction_sources(
        interaction_id
    )


def list_interaction_sources(
    interaction_id: int,
) -> list[StoredInteractionSource]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                interaction_id,
                source_index,
                filename,
                page_number,
                chunk_index,
                distance
            FROM study_interaction_sources
            WHERE interaction_id = ?
            ORDER BY source_index ASC
            """,
            (int(interaction_id),),
        ).fetchall()

    return [
        row_to_interaction_source(row)
        for row in rows
    ]

def insert_study_interaction_with_sources(
    session_id: int,
    question: str,
    answer: str,
    sources: list[StudySourceInput],
    outcome: str = "unrated",
) -> tuple[
    StoredStudyInteraction,
    list[StoredInteractionSource],
]:
    """
    Atomically store one study interaction and all document
    sources used for its answer.

    If any source insertion fails, the interaction insertion
    is rolled back as part of the same SQLite transaction.
    """
    session = get_study_session(
        session_id
    )

    if session is None:
        raise ValueError(
            f"Study session ID {session_id} does not exist."
        )

    if session.status != "active":
        raise ValueError(
            "Interactions can only be added to an active "
            "study session."
        )

    cleaned_question = question.strip()
    cleaned_answer = answer.strip()
    cleaned_outcome = outcome.strip().lower()

    if not cleaned_question:
        raise ValueError(
            "Study question cannot be empty."
        )

    if not cleaned_answer:
        raise ValueError(
            "Study answer cannot be empty."
        )

    if cleaned_outcome not in ALLOWED_INTERACTION_OUTCOMES:
        allowed = ", ".join(
            sorted(ALLOWED_INTERACTION_OUTCOMES)
        )

        raise ValueError(
            "Invalid interaction outcome. "
            f"Allowed values: {allowed}"
        )

    # ========================================================
    # VALIDATE SOURCE INPUTS BEFORE WRITING
    # ========================================================

    prepared_sources: list[
        tuple[
            int,
            str,
            int | None,
            int | None,
            float,
        ]
    ] = []

    seen_source_indexes: set[int] = set()

    for source in sources:
        source_index = int(
            source.source_index
        )

        if source_index <= 0:
            raise ValueError(
                "Source index must be greater than zero."
            )

        if source_index in seen_source_indexes:
            raise ValueError(
                "Source indexes must be unique within an "
                "interaction."
            )

        seen_source_indexes.add(
            source_index
        )

        filename = source.filename.strip()

        if not filename:
            raise ValueError(
                "Source filename cannot be empty."
            )

        page_number = (
            int(source.page_number)
            if source.page_number is not None
            else None
        )

        chunk_index = (
            int(source.chunk_index)
            if source.chunk_index is not None
            else None
        )

        distance = float(
            source.distance
        )

        if distance < 0:
            raise ValueError(
                "Source distance cannot be negative."
            )

        prepared_sources.append(
            (
                source_index,
                filename,
                page_number,
                chunk_index,
                distance,
            )
        )

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    interaction_id: int | None = None

    # ========================================================
    # ATOMIC SQLITE WRITE
    # ========================================================

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO study_interactions (
                session_id,
                question,
                answer,
                outcome,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(session_id),
                cleaned_question,
                cleaned_answer,
                cleaned_outcome,
                timestamp,
            ),
        )

        if cursor.lastrowid is None:
            raise RuntimeError(
                "SQLite did not return an interaction ID."
            )

        interaction_id = int(
            cursor.lastrowid
        )

        if prepared_sources:
            source_rows = [
                (
                    interaction_id,
                    source_index,
                    filename,
                    page_number,
                    chunk_index,
                    distance,
                )
                for (
                    source_index,
                    filename,
                    page_number,
                    chunk_index,
                    distance,
                ) in prepared_sources
            ]

            connection.executemany(
                """
                INSERT INTO study_interaction_sources (
                    interaction_id,
                    source_index,
                    filename,
                    page_number,
                    chunk_index,
                    distance
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                source_rows,
            )

    interaction = get_study_interaction(
        interaction_id
    )

    if interaction is None:
        raise RuntimeError(
            "The interaction was stored but could not be "
            "loaded."
        )

    stored_sources = list_interaction_sources(
        interaction_id
    )

    return interaction, stored_sources