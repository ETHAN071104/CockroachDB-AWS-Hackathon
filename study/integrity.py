from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Literal

from rag.database import get_connection


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
    record_id: int | None = None


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
    record_id: int | None = None,
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

def run_study_integrity_check() -> StudyIntegrityReport:
    """
    Run read-only integrity checks across study and quiz data.
    """
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

        for table_name in sorted(
            REQUIRED_TABLES & existing_tables
        ):
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

    return StudyIntegrityReport(
        issues=tuple(issues),
        table_counts=tuple(table_counts),
    )


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