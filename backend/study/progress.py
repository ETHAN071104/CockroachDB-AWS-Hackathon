from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from backend.application.dependencies import get_application_dependencies
from backend.study.database import (
    StoredStudyInteraction,
)
from backend.study.reporting import (
    StudyOutcomeCounts,
    build_session_report,
)


# ============================================================
# REPORT MODELS
# ============================================================

@dataclass(frozen=True)
class StudySessionProgressRow:
    """
    One completed session inside the overall progress report.
    """

    session_id: int
    started_at: str
    ended_at: str
    interaction_count: int
    outcome_counts: StudyOutcomeCounts


@dataclass(frozen=True)
class StudyProgressReport:
    """
    Deterministic progress report across completed sessions.
    """

    sessions: tuple[StudySessionProgressRow, ...]
    outcome_counts: StudyOutcomeCounts
    source_filenames: tuple[str, ...]
    review_interactions: tuple[
        StoredStudyInteraction,
        ...
    ]
    understood_interactions: tuple[
        StoredStudyInteraction,
        ...
    ]

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    @property
    def total_questions(self) -> int:
        return sum(
            session.interaction_count
            for session in self.sessions
        )

    @property
    def rated_question_count(self) -> int:
        counts = self.outcome_counts

        return (
            counts.understood
            + counts.partial
            + counts.confused
        )

    @property
    def understanding_rate(self) -> float | None:
        """
        Percentage of rated interactions marked understood.

        Unrated interactions are excluded because they provide
        no evidence of understanding.
        """
        rated_count = self.rated_question_count

        if rated_count == 0:
            return None

        return (
            self.outcome_counts.understood
            / rated_count
        )


# ============================================================
# REPORT GENERATION
# ============================================================

def build_progress_report(
    session_limit: int | None = None,
) -> StudyProgressReport:
    """
    Build a deterministic report across completed sessions.

    When session_limit is provided, only the most recent
    completed sessions are included.

    Active sessions are ignored.
    """
    if (
        session_limit is not None
        and session_limit <= 0
    ):
        raise ValueError(
            "Session limit must be greater than zero."
        )

    completed_sessions = [
        session
        for session in get_application_dependencies().study_sessions.list()
        if session.status == "completed"
    ]

    # list_study_sessions() returns newest first.
    if session_limit is not None:
        completed_sessions = completed_sessions[
            :session_limit
        ]

    # Display and aggregate in chronological order.
    completed_sessions.reverse()

    outcome_counter: Counter[str] = Counter()

    source_filenames: set[str] = set()

    review_interactions: list[
        StoredStudyInteraction
    ] = []

    understood_interactions: list[
        StoredStudyInteraction
    ] = []

    session_rows: list[
        StudySessionProgressRow
    ] = []

    for session in completed_sessions:
        session_report = build_session_report(
            session.id
        )

        counts = session_report.outcome_counts

        outcome_counter["understood"] += (
            counts.understood
        )
        outcome_counter["partial"] += (
            counts.partial
        )
        outcome_counter["confused"] += (
            counts.confused
        )
        outcome_counter["unrated"] += (
            counts.unrated
        )

        source_filenames.update(
            session_report.source_filenames
        )

        review_interactions.extend(
            session_report.review_interactions
        )

        understood_interactions.extend(
            interaction
            for interaction
            in session_report.interactions
            if interaction.outcome == "understood"
        )

        session_rows.append(
            StudySessionProgressRow(
                session_id=session.id,
                started_at=session.started_at,
                ended_at=(
                    session.ended_at
                    or "Unknown"
                ),
                interaction_count=(
                    session_report.interaction_count
                ),
                outcome_counts=counts,
            )
        )

    return StudyProgressReport(
        sessions=tuple(session_rows),
        outcome_counts=StudyOutcomeCounts(
            understood=outcome_counter[
                "understood"
            ],
            partial=outcome_counter[
                "partial"
            ],
            confused=outcome_counter[
                "confused"
            ],
            unrated=outcome_counter[
                "unrated"
            ],
        ),
        source_filenames=tuple(
            sorted(source_filenames)
        ),
        review_interactions=tuple(
            review_interactions
        ),
        understood_interactions=tuple(
            understood_interactions
        ),
    )


# ============================================================
# TERMINAL FORMATTING
# ============================================================

def format_progress_report(
    report: StudyProgressReport,
) -> str:
    """
    Convert the progress report into terminal-friendly text.
    """
    if report.session_count == 0:
        return "\n".join(
            [
                "=" * 60,
                "STUDY PROGRESS",
                "=" * 60,
                "No completed study sessions were found.",
            ]
        )

    counts = report.outcome_counts

    understanding_rate = (
        f"{report.understanding_rate * 100:.1f}%"
        if report.understanding_rate is not None
        else "N/A"
    )

    lines = [
        "=" * 60,
        "STUDY PROGRESS",
        "=" * 60,
        (
            "Completed sessions: "
            f"{report.session_count}"
        ),
        (
            "Questions answered: "
            f"{report.total_questions}"
        ),
        (
            "Rated questions: "
            f"{report.rated_question_count}"
        ),
        (
            "Understanding rate: "
            f"{understanding_rate}"
        ),
        "",
        "Overall learning outcomes:",
        (
            "- Understood: "
            f"{counts.understood}"
        ),
        (
            "- Partial: "
            f"{counts.partial}"
        ),
        (
            "- Confused: "
            f"{counts.confused}"
        ),
        (
            "- Unrated: "
            f"{counts.unrated}"
        ),
        "",
        "Files studied:",
    ]

    if report.source_filenames:
        lines.extend(
            f"- {filename}"
            for filename
            in report.source_filenames
        )
    else:
        lines.append(
            "- No document sources recorded"
        )

    lines.extend(
        [
            "",
            "Session breakdown:",
        ]
    )

    for session in report.sessions:
        session_counts = (
            session.outcome_counts
        )

        lines.extend(
            [
                "",
                (
                    f"- Session {session.session_id}"
                ),
                (
                    f"  Started: "
                    f"{session.started_at}"
                ),
                (
                    f"  Ended: "
                    f"{session.ended_at}"
                ),
                (
                    f"  Questions: "
                    f"{session.interaction_count}"
                ),
                (
                    "  Outcomes: "
                    f"{session_counts.understood} understood, "
                    f"{session_counts.partial} partial, "
                    f"{session_counts.confused} confused, "
                    f"{session_counts.unrated} unrated"
                ),
            ]
        )

    lines.extend(
        [
            "",
            "Questions requiring review:",
        ]
    )

    if report.review_interactions:
        for interaction in (
            report.review_interactions[-20:]
        ):
            lines.append(
                f"- Session {interaction.session_id} "
                f"[{interaction.outcome}]: "
                f"{interaction.question}"
            )
    else:
        lines.append("- None")

    return "\n".join(lines)
