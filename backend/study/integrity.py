from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from backend.rag.database import get_connection
from backend.rag import config


# ============================================================
# REPORT MODELS
# ============================================================

IssueSeverity = Literal[
    "error",
    "warning",
]


@dataclass(frozen=True)
class IntegrityIssue:
    severity: IssueSeverity
    code: str
    message: str
    record_type: str
    record_id: int | str | None = None


@dataclass(frozen=True)
class StudyIntegrityReport:
    issues: tuple[IntegrityIssue, ...]
    table_counts: tuple[tuple[str, int], ...]

    @property
    def error_count(self) -> int:
        return sum(
            1
            for issue in self.issues
            if issue.severity == "error"
        )

    @property
    def warning_count(self) -> int:
        return sum(
            1
            for issue in self.issues
            if issue.severity == "warning"
        )

    @property
    def passed(self) -> bool:
        return self.error_count == 0


# ============================================================
# CONSTANTS
# ============================================================

REQUIRED_TABLES = {
    "study_sessions",
    "study_interactions",
    "study_interaction_sources",
    "quiz_attempts",
    "quiz_question_attempts",
    "quiz_question_sources",
}

OPTIONAL_DOMAIN_TABLES = {
    "documents",
    "notebooks",
    "notebook_documents",
    "memories",
    "memory_relationships",
    "cached_intelligence",
    "topics",
    "topic_sources",
}

VALID_STUDY_OUTCOMES = {
    "unrated",
    "understood",
    "partial",
    "confused",
}


# ============================================================
# INTERNAL HELPERS
# ============================================================

def _append_issue(
    issues: list[IntegrityIssue],
    *,
    severity: IssueSeverity,
    code: str,
    message: str,
    record_type: str,
    record_id: int | str | None = None,
) -> None:
    issues.append(
        IntegrityIssue(
            severity=severity,
            code=code,
            message=message,
            record_type=record_type,
            record_id=record_id,
        )
    )


def _get_existing_tables(
    connection,
) -> set[str]:
    rows = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        """
    ).fetchall()

    return {
        str(row["name"])
        for row in rows
    }


def _get_table_count(
    connection,
    table_name: str,
) -> int:
    row = connection.execute(
        f"SELECT COUNT(*) AS count FROM {table_name}"
    ).fetchone()

    return int(row["count"])


# ============================================================
# SESSION CHECKS
# ============================================================

def _check_study_sessions(
    connection,
    issues: list[IntegrityIssue],
) -> None:
    unique_index = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'index'
          AND name = 'idx_study_sessions_single_active'
        """
    ).fetchone()
    if unique_index is None:
        _append_issue(
            issues,
            severity="warning",
            code="active_session_unique_index_missing",
            message="Active-session uniqueness index is missing.",
            record_type="database_index",
        )

    sessions = connection.execute(
        """
        SELECT
            id,
            status,
            started_at,
            ended_at
        FROM study_sessions
        ORDER BY id
        """
    ).fetchall()

    active_sessions = [
        row
        for row in sessions
        if row["status"] == "active"
    ]

    if len(active_sessions) > 1:
        _append_issue(
            issues,
            severity="error",
            code="multiple_active_sessions",
            message=(
                f"{len(active_sessions)} active study "
                "sessions exist. Only one should be active."
            ),
            record_type="study_session",
        )

    for session in sessions:
        session_id = int(session["id"])
        status = str(session["status"])
        started_at = session["started_at"]
        ended_at = session["ended_at"]

        if not started_at:
            _append_issue(
                issues,
                severity="error",
                code="missing_session_start",
                message="Study session has no start time.",
                record_type="study_session",
                record_id=session_id,
            )

        if status == "active" and ended_at is not None:
            _append_issue(
                issues,
                severity="error",
                code="active_session_has_end",
                message=(
                    "Active study session incorrectly has "
                    "an end time."
                ),
                record_type="study_session",
                record_id=session_id,
            )

        if status == "completed" and ended_at is None:
            _append_issue(
                issues,
                severity="error",
                code="completed_session_missing_end",
                message=(
                    "Completed study session has no end time."
                ),
                record_type="study_session",
                record_id=session_id,
            )


# ============================================================
# INTERACTION CHECKS
# ============================================================

def _check_study_interactions(
    connection,
    issues: list[IntegrityIssue],
) -> None:
    interactions = connection.execute(
        """
        SELECT
            id,
            session_id,
            question,
            answer,
            outcome
        FROM study_interactions
        ORDER BY id
        """
    ).fetchall()

    for interaction in interactions:
        interaction_id = int(interaction["id"])

        if not str(interaction["question"]).strip():
            _append_issue(
                issues,
                severity="error",
                code="empty_interaction_question",
                message="Study interaction question is empty.",
                record_type="study_interaction",
                record_id=interaction_id,
            )

        if not str(interaction["answer"]).strip():
            _append_issue(
                issues,
                severity="error",
                code="empty_interaction_answer",
                message="Study interaction answer is empty.",
                record_type="study_interaction",
                record_id=interaction_id,
            )

        if interaction["outcome"] not in VALID_STUDY_OUTCOMES:
            _append_issue(
                issues,
                severity="error",
                code="invalid_interaction_outcome",
                message=(
                    "Study interaction contains unsupported "
                    f"outcome: {interaction['outcome']}."
                ),
                record_type="study_interaction",
                record_id=interaction_id,
            )

    orphan_interactions = connection.execute(
        """
        SELECT interaction.id
        FROM study_interactions AS interaction
        LEFT JOIN study_sessions AS session
            ON session.id = interaction.session_id
        WHERE session.id IS NULL
        """
    ).fetchall()

    for row in orphan_interactions:
        interaction_id = int(row["id"])

        _append_issue(
            issues,
            severity="error",
            code="orphan_study_interaction",
            message=(
                "Study interaction references a missing "
                "study session."
            ),
            record_type="study_interaction",
            record_id=interaction_id,
        )

    orphan_sources = connection.execute(
        """
        SELECT source.id
        FROM study_interaction_sources AS source
        LEFT JOIN study_interactions AS interaction
            ON interaction.id = source.interaction_id
        WHERE interaction.id IS NULL
        """
    ).fetchall()

    for row in orphan_sources:
        source_id = int(row["id"])

        _append_issue(
            issues,
            severity="error",
            code="orphan_interaction_source",
            message=(
                "Study interaction source references a "
                "missing interaction."
            ),
            record_type="study_interaction_source",
            record_id=source_id,
        )


# ============================================================
# QUIZ QUESTION CHECKS
# ============================================================

def _check_quiz_question(
    *,
    question,
    sources,
    issues: list[IntegrityIssue],
) -> None:
    question_id = int(question["id"])
    presented = bool(question["presented"])
    selected_option = question["selected_option"]
    correct_option = int(question["correct_option"])
    is_correct = bool(question["is_correct"])
    skipped = bool(question["skipped"])
    explanation = str(question["explanation"])

    try:
        options = json.loads(
            question["options_json"]
        )
    except Exception:
        options = None

    if not isinstance(options, list) or len(options) != 4:
        _append_issue(
            issues,
            severity="error",
            code="invalid_quiz_options",
            message=(
                "Quiz question options are not a valid "
                "four-item JSON list."
            ),
            record_type="quiz_question_attempt",
            record_id=question_id,
        )

    elif (
        any(
            not str(option).strip()
            for option in options
        )
        or len(
            {
                str(option).strip().casefold()
                for option in options
            }
        ) != 4
    ):
        _append_issue(
            issues,
            severity="error",
            code="invalid_quiz_option_values",
            message=(
                "Quiz options must be non-empty and unique."
            ),
            record_type="quiz_question_attempt",
            record_id=question_id,
        )

    if not 1 <= correct_option <= 4:
        _append_issue(
            issues,
            severity="error",
            code="invalid_correct_option",
            message=(
                "Correct quiz option is outside the range "
                "1 through 4."
            ),
            record_type="quiz_question_attempt",
            record_id=question_id,
        )

    if not presented:
        if (
            selected_option is not None
            or skipped
            or is_correct
        ):
            _append_issue(
                issues,
                severity="error",
                code="invalid_unpresented_question",
                message=(
                    "An unpresented question contains an "
                    "answer, skip state, or correct state."
                ),
                record_type="quiz_question_attempt",
                record_id=question_id,
            )

    elif skipped:
        if selected_option is not None or is_correct:
            _append_issue(
                issues,
                severity="error",
                code="invalid_skipped_question",
                message=(
                    "A skipped question contains a selected "
                    "answer or is marked correct."
                ),
                record_type="quiz_question_attempt",
                record_id=question_id,
            )

    else:
        if selected_option is None:
            _append_issue(
                issues,
                severity="error",
                code="missing_selected_option",
                message=(
                    "A presented, non-skipped question has "
                    "no selected answer."
                ),
                record_type="quiz_question_attempt",
                record_id=question_id,
            )

        elif not 1 <= int(selected_option) <= 4:
            _append_issue(
                issues,
                severity="error",
                code="invalid_selected_option",
                message=(
                    "Selected quiz option is outside the "
                    "range 1 through 4."
                ),
                record_type="quiz_question_attempt",
                record_id=question_id,
            )

        else:
            expected_correctness = (
                int(selected_option)
                == correct_option
            )

            if is_correct != expected_correctness:
                _append_issue(
                    issues,
                    severity="error",
                    code="incorrect_correctness_flag",
                    message=(
                        "Stored correctness does not match "
                        "the selected and correct options."
                    ),
                    record_type="quiz_question_attempt",
                    record_id=question_id,
                )

    if not sources:
        _append_issue(
            issues,
            severity="error",
            code="missing_quiz_source_lineage",
            message=(
                "Quiz question has no stored document-source "
                "lineage."
            ),
            record_type="quiz_question_attempt",
            record_id=question_id,
        )

    for source in sources:
        source_index = int(source["source_index"])

        if f"[{source_index}]" not in explanation:
            _append_issue(
                issues,
                severity="warning",
                code="citation_not_visible",
                message=(
                    f"Stored source [{source_index}] is not "
                    "visibly cited in the explanation."
                ),
                record_type="quiz_question_attempt",
                record_id=question_id,
            )


# ============================================================
# QUIZ ATTEMPT CHECKS
# ============================================================

def _check_quiz_attempts(
    connection,
    issues: list[IntegrityIssue],
) -> None:
    attempts = connection.execute(
        """
        SELECT *
        FROM quiz_attempts
        ORDER BY id
        """
    ).fetchall()

    for attempt in attempts:
        attempt_id = int(attempt["id"])

        questions = connection.execute(
            """
            SELECT *
            FROM quiz_question_attempts
            WHERE quiz_attempt_id = ?
            ORDER BY question_number
            """,
            (attempt_id,),
        ).fetchall()

        expected_numbers = list(
            range(1, len(questions) + 1)
        )

        actual_numbers = [
            int(question["question_number"])
            for question in questions
        ]

        if actual_numbers != expected_numbers:
            _append_issue(
                issues,
                severity="error",
                code="nonsequential_quiz_questions",
                message=(
                    "Quiz question numbers are not "
                    "sequential starting from 1."
                ),
                record_type="quiz_attempt",
                record_id=attempt_id,
            )

        total_questions = len(questions)

        presented_questions = sum(
            bool(question["presented"])
            for question in questions
        )

        answered_questions = sum(
            1
            for question in questions
            if (
                bool(question["presented"])
                and not bool(question["skipped"])
                and question["selected_option"] is not None
            )
        )

        skipped_questions = sum(
            bool(question["skipped"])
            for question in questions
        )

        correct_answers = sum(
            bool(question["is_correct"])
            for question in questions
        )

        stored_counts = {
            "total_questions": total_questions,
            "presented_questions": presented_questions,
            "answered_questions": answered_questions,
            "skipped_questions": skipped_questions,
            "correct_answers": correct_answers,
        }

        for column_name, calculated_value in (
            stored_counts.items()
        ):
            stored_value = int(
                attempt[column_name]
            )

            if stored_value != calculated_value:
                _append_issue(
                    issues,
                    severity="error",
                    code="quiz_count_mismatch",
                    message=(
                        f"{column_name} is stored as "
                        f"{stored_value}, but calculated as "
                        f"{calculated_value}."
                    ),
                    record_type="quiz_attempt",
                    record_id=attempt_id,
                )

        expected_score = (
            correct_answers
            / total_questions
            * 100
            if total_questions
            else 0.0
        )

        if not math.isclose(
            float(attempt["score_percentage"]),
            expected_score,
            abs_tol=0.000001,
        ):
            _append_issue(
                issues,
                severity="error",
                code="quiz_score_mismatch",
                message=(
                    "Stored overall score does not match "
                    "the question records."
                ),
                record_type="quiz_attempt",
                record_id=attempt_id,
            )

        expected_accuracy = (
            correct_answers
            / answered_questions
            * 100
            if answered_questions
            else None
        )

        stored_accuracy = attempt[
            "accuracy_percentage"
        ]

        accuracy_matches = (
            expected_accuracy is None
            and stored_accuracy is None
        ) or (
            expected_accuracy is not None
            and stored_accuracy is not None
            and math.isclose(
                float(stored_accuracy),
                expected_accuracy,
                abs_tol=0.000001,
            )
        )

        if not accuracy_matches:
            _append_issue(
                issues,
                severity="error",
                code="quiz_accuracy_mismatch",
                message=(
                    "Stored answered-question accuracy does "
                    "not match the question records."
                ),
                record_type="quiz_attempt",
                record_id=attempt_id,
            )

        status = str(attempt["status"])

        if (
            status == "completed"
            and presented_questions != total_questions
        ):
            _append_issue(
                issues,
                severity="error",
                code="incomplete_completed_quiz",
                message=(
                    "Completed quiz did not present every "
                    "generated question."
                ),
                record_type="quiz_attempt",
                record_id=attempt_id,
            )

        if (
            status == "aborted"
            and presented_questions >= total_questions
        ):
            _append_issue(
                issues,
                severity="warning",
                code="fully_presented_aborted_quiz",
                message=(
                    "Quiz is marked aborted even though every "
                    "question was presented."
                ),
                record_type="quiz_attempt",
                record_id=attempt_id,
            )

        for question in questions:
            question_sources = connection.execute(
                """
                SELECT *
                FROM quiz_question_sources
                WHERE question_attempt_id = ?
                ORDER BY source_index
                """,
                (question["id"],),
            ).fetchall()

            _check_quiz_question(
                question=question,
                sources=question_sources,
                issues=issues,
            )

    orphan_questions = connection.execute(
        """
        SELECT question.id
        FROM quiz_question_attempts AS question
        LEFT JOIN quiz_attempts AS attempt
            ON attempt.id = question.quiz_attempt_id
        WHERE attempt.id IS NULL
        """
    ).fetchall()

    for row in orphan_questions:
        _append_issue(
            issues,
            severity="error",
            code="orphan_quiz_question",
            message=(
                "Quiz question references a missing quiz "
                "attempt."
            ),
            record_type="quiz_question_attempt",
            record_id=int(row["id"]),
        )

    orphan_sources = connection.execute(
        """
        SELECT source.id
        FROM quiz_question_sources AS source
        LEFT JOIN quiz_question_attempts AS question
            ON question.id = source.question_attempt_id
        WHERE question.id IS NULL
        """
    ).fetchall()

    for row in orphan_sources:
        _append_issue(
            issues,
            severity="error",
            code="orphan_quiz_source",
            message=(
                "Quiz source references a missing quiz "
                "question."
            ),
            record_type="quiz_question_source",
            record_id=int(row["id"]),
        )


# ============================================================
# PUBLIC CHECK
# ============================================================

def _table_columns(connection, table_name: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(
            f"PRAGMA table_info({table_name})"
        ).fetchall()
    }


def _check_document_and_notebook_integrity(
    connection,
    tables: set[str],
    issues: list[IntegrityIssue],
) -> None:
    if "documents" in tables:
        rows = connection.execute(
            """
            SELECT id
            FROM documents
            WHERE TRIM(filename) = ''
               OR TRIM(mime_type) = ''
               OR chunk_count < 0
            """
        ).fetchall()
        for row in rows:
            _append_issue(
                issues,
                severity="error",
                code="invalid_document_record",
                message="Document metadata is empty or has a negative chunk count.",
                record_type="document",
                record_id=int(row["id"]),
            )

    if "notebooks" in tables:
        rows = connection.execute(
            "SELECT id FROM notebooks WHERE TRIM(name) = ''"
        ).fetchall()
        for row in rows:
            _append_issue(
                issues,
                severity="error",
                code="empty_notebook_name",
                message="Notebook name is empty.",
                record_type="notebook",
                record_id=int(row["id"]),
            )

    if not {
        "documents",
        "notebooks",
        "notebook_documents",
    }.issubset(tables):
        return

    orphan_rows = connection.execute(
        """
        SELECT nd.document_id, nd.notebook_id
        FROM notebook_documents AS nd
        LEFT JOIN documents AS d ON d.id = nd.document_id
        LEFT JOIN notebooks AS n ON n.id = nd.notebook_id
        WHERE d.id IS NULL OR n.id IS NULL
        """
    ).fetchall()
    for row in orphan_rows:
        _append_issue(
            issues,
            severity="error",
            code="orphan_notebook_assignment",
            message="Notebook assignment references a missing record.",
            record_type="notebook_document",
            record_id=int(row["document_id"]),
        )


def _check_memory_integrity(
    connection,
    tables: set[str],
    issues: list[IntegrityIssue],
) -> None:
    if "memories" in tables:
        rows = connection.execute(
            """
            SELECT id
            FROM memories
            WHERE TRIM(content) = ''
               OR status NOT IN ('active', 'archived')
            """
        ).fetchall()
        for row in rows:
            _append_issue(
                issues,
                severity="error",
                code="invalid_memory_record",
                message="Memory content or status is invalid.",
                record_type="memory",
                record_id=int(row["id"]),
            )

    if not {"memories", "memory_relationships"}.issubset(tables):
        return
    rows = connection.execute(
        """
        SELECT r.id
        FROM memory_relationships AS r
        LEFT JOIN memories AS source ON source.id = r.source_memory_id
        LEFT JOIN memories AS target ON target.id = r.target_memory_id
        WHERE source.id IS NULL
           OR target.id IS NULL
           OR r.source_memory_id = r.target_memory_id
        """
    ).fetchall()
    for row in rows:
        _append_issue(
            issues,
            severity="error",
            code="invalid_memory_relationship",
            message="Memory relationship is orphaned or self-referential.",
            record_type="memory_relationship",
            record_id=int(row["id"]),
        )


def _check_source_lineage_integrity(
    connection,
    tables: set[str],
    issues: list[IntegrityIssue],
) -> None:
    source_tables = (
        ("study_interaction_sources", "study_interaction_source"),
        ("quiz_question_sources", "quiz_question_source"),
    )
    for table_name, record_type in source_tables:
        if table_name not in tables:
            continue
        columns = _table_columns(connection, table_name)
        if not {
            "document_id",
            "notebook_id",
            "page_number",
            "slide_number",
            "chunk_index",
        }.issubset(columns):
            continue

        invalid_locations = connection.execute(
            f"""
            SELECT id
            FROM {table_name}
            WHERE (page_number IS NOT NULL AND slide_number IS NOT NULL)
               OR page_number < 1
               OR slide_number < 1
               OR chunk_index < 0
            """
        ).fetchall()
        for row in invalid_locations:
            _append_issue(
                issues,
                severity="error",
                code="invalid_source_lineage",
                message="Source lineage contains an invalid location.",
                record_type=record_type,
                record_id=int(row["id"]),
            )

        if "documents" in tables:
            missing_documents = connection.execute(
                f"""
                SELECT source.id
                FROM {table_name} AS source
                LEFT JOIN documents AS d ON d.id = source.document_id
                WHERE source.document_id IS NOT NULL AND d.id IS NULL
                """
            ).fetchall()
            for row in missing_documents:
                _append_issue(
                    issues,
                    severity="warning",
                    code="historical_source_document_missing",
                    message="Historical lineage references a deleted document.",
                    record_type=record_type,
                    record_id=int(row["id"]),
                )

        if "notebooks" in tables:
            missing_notebooks = connection.execute(
                f"""
                SELECT source.id
                FROM {table_name} AS source
                LEFT JOIN notebooks AS n ON n.id = source.notebook_id
                WHERE source.notebook_id IS NOT NULL AND n.id IS NULL
                """
            ).fetchall()
            for row in missing_notebooks:
                _append_issue(
                    issues,
                    severity="warning",
                    code="historical_source_notebook_missing",
                    message="Historical lineage references a deleted notebook.",
                    record_type=record_type,
                    record_id=int(row["id"]),
                )


def _check_cache_and_topic_integrity(
    connection,
    tables: set[str],
    issues: list[IntegrityIssue],
) -> None:
    if "cached_intelligence" in tables:
        rows = connection.execute(
            """
            SELECT
                id,
                result_json,
                source_snapshot_json,
                fingerprint
            FROM cached_intelligence
            """
        ).fetchall()
        for row in rows:
            cache_id = int(row["id"])
            try:
                json.loads(str(row["result_json"]))
                json.loads(str(row["source_snapshot_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                _append_issue(
                    issues,
                    severity="error",
                    code="invalid_cached_intelligence_json",
                    message="Cached intelligence contains invalid JSON.",
                    record_type="cached_intelligence",
                    record_id=cache_id,
                )
            fingerprint = str(row["fingerprint"])
            if len(fingerprint) != 64 or any(
                character not in "0123456789abcdefABCDEF"
                for character in fingerprint
            ):
                _append_issue(
                    issues,
                    severity="error",
                    code="invalid_cached_fingerprint",
                    message="Cached intelligence fingerprint is invalid.",
                    record_type="cached_intelligence",
                    record_id=cache_id,
                )

    if not {"topics", "topic_sources"}.issubset(tables):
        return

    topic_rows = connection.execute("SELECT id FROM topics").fetchall()
    for row in topic_rows:
        topic_id = str(row["id"])
        try:
            UUID(topic_id)
        except ValueError:
            _append_issue(
                issues,
                severity="error",
                code="invalid_topic_id",
                message="Topic ID is not a UUID.",
                record_type="topic",
                record_id=topic_id,
            )

    if "documents" in tables:
        invalid_sources = connection.execute(
            """
            SELECT ts.topic_id, ts.document_id, ts.chunk_index
            FROM topic_sources AS ts
            LEFT JOIN documents AS d ON d.id = ts.document_id
            WHERE d.id IS NULL
               OR ts.chunk_index < 0
               OR ts.chunk_index >= d.chunk_count
            """
        ).fetchall()
        for row in invalid_sources:
            _append_issue(
                issues,
                severity="error",
                code="invalid_topic_source",
                message="Topic source references a missing document or chunk.",
                record_type="topic",
                record_id=str(row["topic_id"]),
            )

    empty_topics = connection.execute(
        """
        SELECT t.id
        FROM topics AS t
        LEFT JOIN topic_sources AS source ON source.topic_id = t.id
        GROUP BY t.id
        HAVING COUNT(source.topic_id) = 0
        """
    ).fetchall()
    for row in empty_topics:
        _append_issue(
            issues,
            severity="warning",
            code="topic_without_sources",
            message="Topic has no persisted source pairs.",
            record_type="topic",
            record_id=str(row["id"]),
        )


def _check_optional_domain_integrity(
    connection,
    tables: set[str],
    issues: list[IntegrityIssue],
) -> None:
    _check_document_and_notebook_integrity(connection, tables, issues)
    _check_memory_integrity(connection, tables, issues)
    _check_source_lineage_integrity(connection, tables, issues)
    _check_cache_and_topic_integrity(connection, tables, issues)

def run_study_integrity_check(
    workspace_id: str | None = None,
) -> StudyIntegrityReport:
    """
    Run read-only integrity checks across study and quiz data.
    """
    if config.PERSISTENCE_BACKEND == "cockroach":
        if workspace_id is None:
            from backend.application.dependencies import (
                get_application_dependencies,
            )

            workspace_id = get_application_dependencies().workspace_id
        return _run_cockroach_integrity_check(workspace_id)
    issues: list[IntegrityIssue] = []
    table_counts: list[tuple[str, int]] = []

    with get_connection() as connection:
        existing_tables = _get_existing_tables(
            connection
        )

        missing_tables = (
            REQUIRED_TABLES
            - existing_tables
        )

        for table_name in sorted(
            missing_tables
        ):
            _append_issue(
                issues,
                severity="error",
                code="missing_table",
                message=(
                    f"Required table '{table_name}' "
                    "does not exist."
                ),
                record_type="database_table",
            )

        counted_tables = (
            REQUIRED_TABLES | OPTIONAL_DOMAIN_TABLES
        ) & existing_tables
        for table_name in sorted(counted_tables):
            table_counts.append(
                (
                    table_name,
                    _get_table_count(
                        connection,
                        table_name,
                    ),
                )
            )

        if missing_tables:
            return StudyIntegrityReport(
                issues=tuple(issues),
                table_counts=tuple(table_counts),
            )

        _check_study_sessions(
            connection,
            issues,
        )

        _check_study_interactions(
            connection,
            issues,
        )

        _check_quiz_attempts(
            connection,
            issues,
        )

        _check_optional_domain_integrity(
            connection,
            existing_tables,
            issues,
        )

    return StudyIntegrityReport(
        issues=tuple(issues),
        table_counts=tuple(table_counts),
    )


def _run_cockroach_integrity_check(
    workspace_id: str,
) -> StudyIntegrityReport:
    from sqlalchemy import text

    from backend.repositories.cockroach.connection import get_engine

    issues: list[IntegrityIssue] = []
    table_counts: list[tuple[str, int]] = []
    target_tables = sorted(
        REQUIRED_TABLES | (OPTIONAL_DOMAIN_TABLES - {"memories"}) | {"learner_memories"}
    )
    workspace = UUID(workspace_id)
    with get_engine().connect() as connection:
        existing = {
            str(value)
            for value in connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public'"
                )
            ).scalars()
        }
        for table_name in target_tables:
            if table_name not in existing:
                _append_issue(
                    issues, severity="error", code="missing_table",
                    message=f"Required table '{table_name}' does not exist.",
                    record_type="database_table",
                )
                continue
            count = int(
                connection.execute(
                    text(
                        f"SELECT count(*) FROM {table_name} "
                        "WHERE workspace_id=:workspace_id"
                    ),
                    {"workspace_id": workspace},
                ).scalar_one()
            )
            table_counts.append((table_name, count))
        if "study_sessions" in existing:
            active = int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM study_sessions "
                        "WHERE workspace_id=:workspace_id AND status='active'"
                    ),
                    {"workspace_id": workspace},
                ).scalar_one()
            )
            if active > 1:
                _append_issue(
                    issues, severity="error", code="multiple_active_sessions",
                    message="More than one active study session exists.",
                    record_type="study_session",
                )
        orphan_checks = {
            "orphan_study_interaction": (
                "SELECT count(*) FROM study_interactions i LEFT JOIN study_sessions s "
                "ON s.id=i.session_id AND s.workspace_id=i.workspace_id "
                "WHERE i.workspace_id=:workspace_id AND s.id IS NULL"
            ),
            "orphan_quiz_question": (
                "SELECT count(*) FROM quiz_question_attempts q LEFT JOIN quiz_attempts a "
                "ON a.id=q.quiz_attempt_id AND a.workspace_id=q.workspace_id "
                "WHERE q.workspace_id=:workspace_id AND a.id IS NULL"
            ),
            "orphan_document_chunk": (
                "SELECT count(*) FROM document_chunks c LEFT JOIN documents d "
                "ON d.id=c.document_id AND d.workspace_id=c.workspace_id "
                "WHERE c.workspace_id=:workspace_id AND d.id IS NULL"
            ),
        }
        for code, query in orphan_checks.items():
            if int(
                connection.execute(
                    text(query),
                    {"workspace_id": workspace},
                ).scalar_one()
            ):
                _append_issue(
                    issues, severity="error", code=code,
                    message="A CockroachDB relationship has a missing parent.",
                    record_type="database_relationship",
                )
    return StudyIntegrityReport(tuple(issues), tuple(table_counts))


# ============================================================
# TERMINAL FORMATTING
# ============================================================

def format_study_integrity_report(
    report: StudyIntegrityReport,
) -> str:
    lines = [
        "=" * 60,
        "STUDY DATA INTEGRITY CHECK",
        "=" * 60,
        (
            "Result: "
            + (
                "PASS"
                if report.passed
                else "FAIL"
            )
        ),
        f"Errors: {report.error_count}",
        f"Warnings: {report.warning_count}",
        "",
        "TABLE COUNTS",
    ]

    for table_name, count in (
        report.table_counts
    ):
        lines.append(
            f"- {table_name}: {count}"
        )

    lines.extend(
        [
            "",
            "ISSUES",
        ]
    )

    if not report.issues:
        lines.append(
            "- No integrity issues detected."
        )

        return "\n".join(lines)

    for issue in report.issues:
        record_label = issue.record_type

        if issue.record_id is not None:
            record_label += (
                f" {issue.record_id}"
            )

        lines.append(
            f"- [{issue.severity.upper()}] "
            f"{issue.code} — "
            f"{record_label}: "
            f"{issue.message}"
        )

    return "\n".join(lines)
