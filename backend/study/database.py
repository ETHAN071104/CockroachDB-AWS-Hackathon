from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from backend.rag.database import get_connection
from backend.domain import DEFAULT_WORKSPACE_ID


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
class QuizQuestionSourceInput:
    source_index: int
    filename: str
    page_number: int | None
    chunk_index: int | None
    distance: float | None
    document_id: int | None = None
    notebook_id: int | None = None
    mime_type: str | None = None
    slide_number: int | None = None
    excerpt: str | None = None


@dataclass(frozen=True)
class QuizQuestionAttemptInput:
    question_number: int
    question: str
    options: tuple[str, str, str, str]

    presented: bool
    selected_option: int | None
    correct_option: int
    is_correct: bool
    skipped: bool

    explanation: str

    sources: tuple[
        QuizQuestionSourceInput,
        ...
    ] = ()


@dataclass(frozen=True)
class StoredQuizAttempt:
    id: int
    requested_topic: str
    quiz_topic: str
    status: str

    total_questions: int
    presented_questions: int
    answered_questions: int
    skipped_questions: int
    correct_answers: int

    score_percentage: float
    accuracy_percentage: float | None

    confidence: float
    created_at: str


@dataclass(frozen=True)
class StoredQuizQuestionAttempt:
    id: int
    quiz_attempt_id: int
    question_number: int
    question: str
    options: tuple[str, str, str, str]

    presented: bool
    selected_option: int | None
    correct_option: int
    is_correct: bool
    skipped: bool

    explanation: str


@dataclass(frozen=True)
class StoredQuizQuestionSource:
    id: int
    question_attempt_id: int
    source_index: int
    filename: str
    page_number: int | None
    chunk_index: int | None
    distance: float | None
    document_id: int | None = None
    notebook_id: int | None = None
    mime_type: str | None = None
    slide_number: int | None = None
    excerpt: str | None = None

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
    document_id: int | None = None
    notebook_id: int | None = None
    mime_type: str | None = None
    slide_number: int | None = None
    excerpt: str | None = None


@dataclass(frozen=True)
class StoredInteractionSource:
    id: int
    interaction_id: int
    source_index: int
    filename: str
    page_number: int | None
    chunk_index: int | None
    distance: float
    document_id: int | None = None
    notebook_id: int | None = None
    mime_type: str | None = None
    slide_number: int | None = None
    excerpt: str | None = None


def _clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _validate_optional_lineage(
    *,
    document_id: int | None,
    notebook_id: int | None,
    mime_type: str | None,
    page_number: int | None,
    slide_number: int | None,
    excerpt: str | None,
) -> None:
    for value, label in (
        (document_id, "Document ID"),
        (notebook_id, "Notebook ID"),
        (page_number, "Page number"),
        (slide_number, "Slide number"),
    ):
        if value is not None and (
            isinstance(value, bool) or int(value) <= 0
        ):
            raise ValueError(f"{label} must be greater than zero.")

    if page_number is not None and slide_number is not None:
        raise ValueError(
            "A source cannot have both page and slide numbers."
        )

    if mime_type is not None:
        cleaned_mime_type = mime_type.strip()
        if not cleaned_mime_type:
            raise ValueError("Source MIME type cannot be empty.")
        if len(cleaned_mime_type) > 255:
            raise ValueError(
                "Source MIME type cannot exceed 255 characters."
            )

    if excerpt is not None and len(excerpt.strip()) > 2_000:
        raise ValueError(
            "Source excerpt cannot exceed 2000 characters."
        )


# ============================================================
# INITIALIZATION
# ============================================================

def _add_columns_if_missing(
    connection: sqlite3.Connection,
    table_name: str,
    columns: dict[str, str],
) -> None:
    existing_columns = {
        str(row["name"])
        for row in connection.execute(
            f"PRAGMA table_info({table_name})"
        ).fetchall()
    }
    for column_name, definition in columns.items():
        if column_name not in existing_columns:
            connection.execute(
                f"ALTER TABLE {table_name} "
                f"ADD COLUMN {column_name} {definition}"
            )

def _row_to_stored_quiz_attempt(
    row,
) -> StoredQuizAttempt:
    return StoredQuizAttempt(
        id=row["id"],
        requested_topic=row["requested_topic"],
        quiz_topic=row["quiz_topic"],
        status=row["status"],
        total_questions=row["total_questions"],
        presented_questions=row[
            "presented_questions"
        ],
        answered_questions=row[
            "answered_questions"
        ],
        skipped_questions=row[
            "skipped_questions"
        ],
        correct_answers=row["correct_answers"],
        score_percentage=row["score_percentage"],
        accuracy_percentage=row[
            "accuracy_percentage"
        ],
        confidence=row["confidence"],
        created_at=row["created_at"],
    )


def _row_to_stored_quiz_question_attempt(
    row,
) -> StoredQuizQuestionAttempt:
    options_value = json.loads(
        row["options_json"]
    )

    if (
        not isinstance(options_value, list)
        or len(options_value) != 4
    ):
        raise RuntimeError(
            "Stored quiz question contains invalid options."
        )

    return StoredQuizQuestionAttempt(
        id=row["id"],
        quiz_attempt_id=row["quiz_attempt_id"],
        question_number=row["question_number"],
        question=row["question"],
        options=(
            str(options_value[0]),
            str(options_value[1]),
            str(options_value[2]),
            str(options_value[3]),
        ),
        presented=bool(row["presented"]),
        selected_option=row["selected_option"],
        correct_option=row["correct_option"],
        is_correct=bool(row["is_correct"]),
        skipped=bool(row["skipped"]),
        explanation=row["explanation"],
    )


def _row_to_stored_quiz_question_source(
    row,
) -> StoredQuizQuestionSource:
    return StoredQuizQuestionSource(
        id=row["id"],
        question_attempt_id=row[
            "question_attempt_id"
        ],
        source_index=row["source_index"],
        filename=row["filename"],
        page_number=row["page_number"],
        chunk_index=row["chunk_index"],
        distance=row["distance"],
        document_id=row["document_id"],
        notebook_id=row["notebook_id"],
        mime_type=row["mime_type"],
        slide_number=row["slide_number"],
        excerpt=row["excerpt"],
    )

def validate_quiz_question_inputs(
    questions: list[
        QuizQuestionAttemptInput
    ],
) -> None:
    if not questions:
        raise ValueError(
            "A quiz attempt must contain at least one "
            "question."
        )

    expected_numbers = list(
        range(1, len(questions) + 1)
    )

    actual_numbers = [
        question.question_number
        for question in questions
    ]

    if actual_numbers != expected_numbers:
        raise ValueError(
            "Quiz question numbers must be sequential, "
            "starting at 1."
        )

    for question in questions:
        if not question.question.strip():
            raise ValueError(
                "Quiz question text cannot be empty."
            )

        if len(question.options) != 4:
            raise ValueError(
                "Each quiz question must contain four "
                "options."
            )

        cleaned_options = [
            option.strip()
            for option in question.options
        ]

        if any(
            not option
            for option in cleaned_options
        ):
            raise ValueError(
                "Quiz options cannot be empty."
            )

        if len(
            {
                option.casefold()
                for option in cleaned_options
            }
        ) != 4:
            raise ValueError(
                "Quiz options must be unique."
            )

        if not 1 <= question.correct_option <= 4:
            raise ValueError(
                "Correct option must be between 1 and 4."
            )

        if not question.explanation.strip():
            raise ValueError(
                "Quiz explanation cannot be empty."
            )

        if not question.presented:
            if question.selected_option is not None:
                raise ValueError(
                    "An unpresented question cannot have a "
                    "selected option."
                )

            if question.skipped:
                raise ValueError(
                    "An unpresented question cannot be "
                    "marked skipped."
                )

            if question.is_correct:
                raise ValueError(
                    "An unpresented question cannot be "
                    "marked correct."
                )

        elif question.skipped:
            if question.selected_option is not None:
                raise ValueError(
                    "A skipped question cannot have a "
                    "selected option."
                )

            if question.is_correct:
                raise ValueError(
                    "A skipped question cannot be correct."
                )

        else:
            if question.selected_option is None:
                raise ValueError(
                    "A presented, non-skipped question must "
                    "contain a selected option."
                )

            if not 1 <= question.selected_option <= 4:
                raise ValueError(
                    "Selected option must be between 1 and 4."
                )

            expected_correctness = (
                question.selected_option
                == question.correct_option
            )

            if (
                question.is_correct
                != expected_correctness
            ):
                raise ValueError(
                    "Quiz correctness does not match the "
                    "selected and correct options."
                )

        source_indexes = [
            source.source_index
            for source in question.sources
        ]

        if len(source_indexes) != len(
            set(source_indexes)
        ):
            raise ValueError(
                "Quiz question source indexes must be "
                "unique."
            )

        for source in question.sources:
            if source.source_index <= 0:
                raise ValueError(
                    "Source index must be greater than zero."
                )

            if not source.filename.strip():
                raise ValueError(
                    "Source filename cannot be empty."
                )

            _validate_optional_lineage(
                document_id=source.document_id,
                notebook_id=source.notebook_id,
                mime_type=source.mime_type,
                page_number=source.page_number,
                slide_number=source.slide_number,
                excerpt=source.excerpt,
            )
            
def get_quiz_attempt(
    quiz_attempt_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> StoredQuizAttempt | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM quiz_attempts
            WHERE id = ? AND workspace_id = ?
            """,
            (quiz_attempt_id, workspace_id),
        ).fetchone()

    if row is None:
        return None

    return _row_to_stored_quiz_attempt(
        row
    )


def list_quiz_attempts(
    limit: int | None = None,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[StoredQuizAttempt]:
    if limit is not None and limit <= 0:
        raise ValueError(
            "Quiz-attempt limit must be greater than zero."
        )

    query = """
        SELECT *
        FROM quiz_attempts
        WHERE workspace_id = ?
        ORDER BY created_at DESC, id DESC
    """

    parameters: tuple[object, ...] = (workspace_id,)

    if limit is not None:
        query += " LIMIT ?"
        parameters = (workspace_id, limit)

    with get_connection() as connection:
        rows = connection.execute(
            query,
            parameters,
        ).fetchall()

    return [
        _row_to_stored_quiz_attempt(row)
        for row in rows
    ]


def list_quiz_question_attempts(
    quiz_attempt_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[StoredQuizQuestionAttempt]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM quiz_question_attempts
            WHERE quiz_attempt_id = ? AND workspace_id = ?
            ORDER BY question_number ASC
            """,
            (quiz_attempt_id, workspace_id),
        ).fetchall()

    return [
        _row_to_stored_quiz_question_attempt(
            row
        )
        for row in rows
    ]


def list_quiz_question_sources(
    question_attempt_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[StoredQuizQuestionSource]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM quiz_question_sources
            WHERE question_attempt_id = ? AND workspace_id = ?
            ORDER BY source_index ASC
            """,
            (question_attempt_id, workspace_id),
        ).fetchall()

    return [
        _row_to_stored_quiz_question_source(
            row
        )
        for row in rows
    ]
            
def insert_quiz_attempt_with_questions(
    *,
    requested_topic: str,
    quiz_topic: str,
    confidence: float,
    aborted: bool,
    questions: list[
        QuizQuestionAttemptInput
    ],
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> tuple[
    StoredQuizAttempt,
    tuple[StoredQuizQuestionAttempt, ...],
]:
    cleaned_requested_topic = (
        requested_topic.strip()
    )

    cleaned_quiz_topic = quiz_topic.strip()

    if not cleaned_requested_topic:
        raise ValueError(
            "Requested quiz topic cannot be empty."
        )

    if not cleaned_quiz_topic:
        raise ValueError(
            "Generated quiz topic cannot be empty."
        )

    if not 0 <= confidence <= 1:
        raise ValueError(
            "Quiz confidence must be between 0 and 1."
        )

    validate_quiz_question_inputs(
        questions
    )

    total_questions = len(questions)

    presented_questions = sum(
        1
        for question in questions
        if question.presented
    )

    answered_questions = sum(
        1
        for question in questions
        if (
            question.presented
            and not question.skipped
            and question.selected_option is not None
        )
    )

    skipped_questions = sum(
        1
        for question in questions
        if question.skipped
    )

    correct_answers = sum(
        1
        for question in questions
        if question.is_correct
    )

    score_percentage = (
        correct_answers
        / total_questions
        * 100
    )

    accuracy_percentage = (
        correct_answers
        / answered_questions
        * 100
        if answered_questions > 0
        else None
    )

    status = (
        "aborted"
        if aborted
        else "completed"
    )

    created_at = datetime.now(
        timezone.utc
    ).isoformat()

    question_ids: list[int] = []

    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO quiz_attempts (
                workspace_id,
                requested_topic,
                quiz_topic,
                status,
                total_questions,
                presented_questions,
                answered_questions,
                skipped_questions,
                correct_answers,
                score_percentage,
                accuracy_percentage,
                confidence,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
                cleaned_requested_topic,
                cleaned_quiz_topic,
                status,
                total_questions,
                presented_questions,
                answered_questions,
                skipped_questions,
                correct_answers,
                score_percentage,
                accuracy_percentage,
                confidence,
                created_at,
            ),
        )

        quiz_attempt_id = cursor.lastrowid

        if quiz_attempt_id is None:
            raise RuntimeError(
                "Quiz attempt ID was not created."
            )

        for question in questions:
            question_cursor = connection.execute(
                """
                INSERT INTO quiz_question_attempts (
                    workspace_id,
                    quiz_attempt_id,
                    question_number,
                    question,
                    options_json,
                    presented,
                    selected_option,
                    correct_option,
                    is_correct,
                    skipped,
                    explanation
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    quiz_attempt_id,
                    question.question_number,
                    question.question.strip(),
                    json.dumps(
                        list(question.options),
                        ensure_ascii=False,
                    ),
                    int(question.presented),
                    question.selected_option,
                    question.correct_option,
                    int(question.is_correct),
                    int(question.skipped),
                    question.explanation.strip(),
                ),
            )

            question_attempt_id = (
                question_cursor.lastrowid
            )

            if question_attempt_id is None:
                raise RuntimeError(
                    "Quiz question attempt ID was not "
                    "created."
                )

            question_ids.append(
                question_attempt_id
            )

            for source in question.sources:
                connection.execute(
                    """
                    INSERT INTO quiz_question_sources (
                        workspace_id,
                        question_attempt_id,
                        source_index,
                        filename,
                        page_number,
                        chunk_index,
                        distance,
                        document_id,
                        notebook_id,
                        mime_type,
                        slide_number,
                        excerpt
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workspace_id,
                        question_attempt_id,
                        source.source_index,
                        source.filename.strip(),
                        source.page_number,
                        source.chunk_index,
                        source.distance,
                        source.document_id,
                        source.notebook_id,
                        _clean_optional_text(source.mime_type),
                        source.slide_number,
                        _clean_optional_text(source.excerpt),
                    ),
                )

    stored_attempt = get_quiz_attempt(
        quiz_attempt_id,
        workspace_id=workspace_id,
    )

    if stored_attempt is None:
        raise RuntimeError(
            "Stored quiz attempt could not be loaded."
        )

    stored_questions = tuple(
        question
        for question
        in list_quiz_question_attempts(
            quiz_attempt_id,
            workspace_id=workspace_id,
        )
    )

    return (
        stored_attempt,
        stored_questions,
    )

def initialize_quiz_database_tables(
    connection,
) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS quiz_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            requested_topic TEXT NOT NULL,
            quiz_topic TEXT NOT NULL,

            status TEXT NOT NULL
                CHECK (
                    status IN (
                        'completed',
                        'aborted'
                    )
                ),

            total_questions INTEGER NOT NULL
                CHECK (total_questions > 0),

            presented_questions INTEGER NOT NULL
                CHECK (presented_questions >= 0),

            answered_questions INTEGER NOT NULL
                CHECK (answered_questions >= 0),

            skipped_questions INTEGER NOT NULL
                CHECK (skipped_questions >= 0),

            correct_answers INTEGER NOT NULL
                CHECK (correct_answers >= 0),

            score_percentage REAL NOT NULL
                CHECK (
                    score_percentage >= 0
                    AND score_percentage <= 100
                ),

            accuracy_percentage REAL
                CHECK (
                    accuracy_percentage IS NULL
                    OR (
                        accuracy_percentage >= 0
                        AND accuracy_percentage <= 100
                    )
                ),

            confidence REAL NOT NULL
                CHECK (
                    confidence >= 0
                    AND confidence <= 1
                ),

            created_at TEXT NOT NULL,
            workspace_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001'
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS quiz_question_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            quiz_attempt_id INTEGER NOT NULL,

            question_number INTEGER NOT NULL
                CHECK (question_number > 0),

            question TEXT NOT NULL,
            options_json TEXT NOT NULL,

            presented INTEGER NOT NULL
                CHECK (presented IN (0, 1)),

            selected_option INTEGER
                CHECK (
                    selected_option IS NULL
                    OR selected_option BETWEEN 1 AND 4
                ),

            correct_option INTEGER NOT NULL
                CHECK (correct_option BETWEEN 1 AND 4),

            is_correct INTEGER NOT NULL
                CHECK (is_correct IN (0, 1)),

            skipped INTEGER NOT NULL
                CHECK (skipped IN (0, 1)),

            explanation TEXT NOT NULL,
            workspace_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001',

            FOREIGN KEY (
                quiz_attempt_id
            )
            REFERENCES quiz_attempts(id)
            ON DELETE CASCADE,

            UNIQUE (
                quiz_attempt_id,
                question_number
            )
        )
        """
    )

    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS quiz_question_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            question_attempt_id INTEGER NOT NULL,

            source_index INTEGER NOT NULL
                CHECK (source_index > 0),

            filename TEXT NOT NULL,
            page_number INTEGER,
            chunk_index INTEGER,
            distance REAL,
            document_id INTEGER,
            notebook_id INTEGER,
            mime_type TEXT,
            slide_number INTEGER,
            excerpt TEXT,
            workspace_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001',

            FOREIGN KEY (
                question_attempt_id
            )
            REFERENCES quiz_question_attempts(id)
            ON DELETE CASCADE,

            UNIQUE (
                question_attempt_id,
                source_index
            )
        )
        """
    )

    _add_columns_if_missing(
        connection,
        "quiz_question_sources",
        {
            "document_id": "INTEGER",
            "notebook_id": "INTEGER",
            "mime_type": "TEXT",
            "slide_number": "INTEGER",
            "excerpt": "TEXT",
        },
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_quiz_attempts_created_at
        ON quiz_attempts(created_at)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_quiz_questions_attempt_id
        ON quiz_question_attempts(quiz_attempt_id)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_quiz_sources_question_id
        ON quiz_question_sources(question_attempt_id)
        """
    )

    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_quiz_sources_document_id
        ON quiz_question_sources(document_id)
        """
    )

def initialize_study_database() -> None:
    """
    Create study-session history tables when they do not exist.
    """
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS study_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL DEFAULT 'active',
                started_at TEXT NOT NULL,
                ended_at TEXT,
                workspace_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001',

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

        _add_columns_if_missing(
            connection,
            "study_sessions",
            {
                "workspace_id": (
                    "TEXT NOT NULL DEFAULT "
                    "'00000000-0000-4000-8000-000000000001'"
                )
            },
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_study_sessions_status
            ON study_sessions(status)
            """
        )

        active_rows = connection.execute(
            """
            SELECT id, started_at, workspace_id
            FROM study_sessions
            WHERE status = 'active'
            ORDER BY workspace_id ASC, started_at DESC, id DESC
            """
        ).fetchall()
        seen_active_workspaces: set[str] = set()
        duplicate_active_rows = []
        for row in active_rows:
            workspace_id = str(row["workspace_id"])
            if workspace_id in seen_active_workspaces:
                duplicate_active_rows.append(row)
            else:
                seen_active_workspaces.add(workspace_id)
        for row in duplicate_active_rows:
            connection.execute(
                """
                UPDATE study_sessions
                SET status = 'completed',
                    ended_at = started_at
                WHERE id = ?
                """,
                (int(row["id"]),),
            )

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_study_sessions_single_active
            ON study_sessions(workspace_id)
            WHERE status = 'active'
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
                workspace_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001',

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
                document_id INTEGER,
                notebook_id INTEGER,
                mime_type TEXT,
                slide_number INTEGER,
                excerpt TEXT,
                workspace_id TEXT NOT NULL DEFAULT '00000000-0000-4000-8000-000000000001',

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

        _add_columns_if_missing(
            connection,
            "study_interaction_sources",
            {
                "document_id": "INTEGER",
                "notebook_id": "INTEGER",
                "mime_type": "TEXT",
                "slide_number": "INTEGER",
                "excerpt": "TEXT",
            },
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
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_study_sources_document_id
            ON study_interaction_sources(document_id)
            """
        )
        initialize_quiz_database_tables(
            connection
        )
        workspace_column = {
            "workspace_id": (
                "TEXT NOT NULL DEFAULT "
                "'00000000-0000-4000-8000-000000000001'"
            )
        }
        for table_name in (
            "study_interactions",
            "study_interaction_sources",
            "quiz_attempts",
            "quiz_question_attempts",
            "quiz_question_sources",
        ):
            _add_columns_if_missing(
                connection,
                table_name,
                workspace_column,
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
    document_value = row["document_id"]
    notebook_value = row["notebook_id"]
    slide_value = row["slide_number"]
    mime_value = row["mime_type"]
    excerpt_value = row["excerpt"]

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
        document_id=(
            int(document_value)
            if document_value is not None
            else None
        ),
        notebook_id=(
            int(notebook_value)
            if notebook_value is not None
            else None
        ),
        mime_type=(
            str(mime_value)
            if mime_value is not None
            else None
        ),
        slide_number=(
            int(slide_value)
            if slide_value is not None
            else None
        ),
        excerpt=(
            str(excerpt_value)
            if excerpt_value is not None
            else None
        ),
    )


# ============================================================
# SESSION OPERATIONS
# ============================================================

def create_study_session(
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> StoredStudySession:
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
                ended_at,
                workspace_id
            )
            VALUES ('active', ?, NULL, ?)
            """,
            (timestamp, workspace_id),
        )

        session_id = cursor.lastrowid

    if session_id is None:
        raise RuntimeError(
            "SQLite did not return a study-session ID."
        )

    session = get_study_session(
        int(session_id),
        workspace_id=workspace_id,
    )

    if session is None:
        raise RuntimeError(
            "The study session was created but could not "
            "be loaded."
        )

    return session


def get_study_session(
    session_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
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
            WHERE id = ? AND workspace_id = ?
            """,
            (int(session_id), workspace_id),
        ).fetchone()

    if row is None:
        return None

    return row_to_study_session(row)


def get_active_study_session(
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> Optional[StoredStudySession]:
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
            WHERE status = 'active' AND workspace_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (workspace_id,),
        ).fetchone()

    if row is None:
        return None

    return row_to_study_session(row)


def get_or_create_active_study_session(
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> StoredStudySession:
    """
    Resume an interrupted active session or create a new one.
    """
    timestamp = datetime.now(timezone.utc).isoformat()

    with get_connection() as connection:
        if not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT id, status, started_at, ended_at
            FROM study_sessions
            WHERE status = 'active' AND workspace_id = ?
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """,
            (workspace_id,),
        ).fetchone()

        if row is None:
            cursor = connection.execute(
                """
                INSERT INTO study_sessions (
                    status,
                    started_at,
                    ended_at,
                    workspace_id
                )
                VALUES ('active', ?, NULL, ?)
                """,
                (timestamp, workspace_id),
            )
            session_id = cursor.lastrowid
            if session_id is None:
                raise RuntimeError(
                    "SQLite did not return a study-session ID."
                )
            row = connection.execute(
                """
                SELECT id, status, started_at, ended_at
                FROM study_sessions
                WHERE id = ? AND workspace_id = ?
                """,
                (int(session_id), workspace_id),
            ).fetchone()

    if row is None:
        raise RuntimeError(
            "Active study session could not be loaded."
        )
    return row_to_study_session(row)


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


def list_study_sessions(
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[StoredStudySession]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                id,
                status,
                started_at,
                ended_at
            FROM study_sessions
            WHERE workspace_id = ?
            ORDER BY id DESC
            """,
            (workspace_id,),
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
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
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
            WHERE session_id = ? AND workspace_id = ?
            ORDER BY id ASC
            """,
            (int(session_id), workspace_id),
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

    rows: list[tuple[object, ...]] = []

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

        _validate_optional_lineage(
            document_id=source.document_id,
            notebook_id=source.notebook_id,
            mime_type=source.mime_type,
            page_number=source.page_number,
            slide_number=source.slide_number,
            excerpt=source.excerpt,
        )

        rows.append(
            (
                int(interaction_id),
                int(source.source_index),
                cleaned_filename,
                source.page_number,
                source.chunk_index,
                numeric_distance,
                source.document_id,
                source.notebook_id,
                _clean_optional_text(source.mime_type),
                source.slide_number,
                _clean_optional_text(source.excerpt),
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
                distance,
                document_id,
                notebook_id,
                mime_type,
                slide_number,
                excerpt
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    return list_interaction_sources(
        interaction_id
    )


def list_interaction_sources(
    interaction_id: int,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
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
                distance,
                document_id,
                notebook_id,
                mime_type,
                slide_number,
                excerpt
            FROM study_interaction_sources
            WHERE interaction_id = ? AND workspace_id = ?
            ORDER BY source_index ASC
            """,
            (int(interaction_id), workspace_id),
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
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
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
        session_id,
        workspace_id=workspace_id,
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
            int | None,
            int | None,
            str | None,
            int | None,
            str | None,
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

        _validate_optional_lineage(
            document_id=source.document_id,
            notebook_id=source.notebook_id,
            mime_type=source.mime_type,
            page_number=page_number,
            slide_number=source.slide_number,
            excerpt=source.excerpt,
        )

        prepared_sources.append(
            (
                source_index,
                filename,
                page_number,
                chunk_index,
                distance,
                source.document_id,
                source.notebook_id,
                _clean_optional_text(source.mime_type),
                source.slide_number,
                _clean_optional_text(source.excerpt),
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
                workspace_id,
                session_id,
                question,
                answer,
                outcome,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                workspace_id,
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
                    workspace_id,
                    interaction_id,
                    source_index,
                    filename,
                    page_number,
                    chunk_index,
                    distance,
                    document_id,
                    notebook_id,
                    mime_type,
                    slide_number,
                    excerpt,
                )
                for (
                    source_index,
                    filename,
                    page_number,
                    chunk_index,
                    distance,
                    document_id,
                    notebook_id,
                    mime_type,
                    slide_number,
                    excerpt,
                ) in prepared_sources
            ]

            connection.executemany(
                """
                INSERT INTO study_interaction_sources (
                    workspace_id,
                    interaction_id,
                    source_index,
                    filename,
                    page_number,
                    chunk_index,
                    distance,
                    document_id,
                    notebook_id,
                    mime_type,
                    slide_number,
                    excerpt
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        interaction_id,
        workspace_id=workspace_id,
    )

    return interaction, stored_sources
