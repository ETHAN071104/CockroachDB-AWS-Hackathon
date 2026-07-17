from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from study.database import (
    StoredStudyInteraction,
    StoredStudySession,
    get_study_session,
    list_interaction_sources,
    list_session_interactions,
)


@dataclass(frozen=True)
class StudyOutcomeCounts:
    understood: int
    partial: int
    confused: int
    unrated: int


@dataclass(frozen=True)
class StudySessionReport:
    session: StoredStudySession
    interactions: tuple[StoredStudyInteraction, ...]
    outcome_counts: StudyOutcomeCounts
    source_filenames: tuple[str, ...]
    review_interactions: tuple[StoredStudyInteraction, ...]

    @property
    def interaction_count(self) -> int:
        return len(self.interactions)


def build_session_report(
    session_id: int,
) -> StudySessionReport:
    """
    Build a deterministic report for one study session.

    No LLM is called and no database records are changed.
    """
    session = get_study_session(session_id)

    if session is None:
        raise ValueError(
            f"Study session ID {session_id} does not exist."
        )

    interactions = list_session_interactions(
        session_id
    )

    outcome_counter = Counter(
        interaction.outcome
        for interaction in interactions
    )

    filenames: set[str] = set()

    for interaction in interactions:
        sources = list_interaction_sources(
            interaction.id
        )

        for source in sources:
            filenames.add(source.filename)

    review_interactions = [
        interaction
        for interaction in interactions
        if interaction.outcome in {
            "partial",
            "confused",
        }
    ]

    return StudySessionReport(
        session=session,
        interactions=tuple(interactions),
        outcome_counts=StudyOutcomeCounts(
            understood=outcome_counter["understood"],
            partial=outcome_counter["partial"],
            confused=outcome_counter["confused"],
            unrated=outcome_counter["unrated"],
        ),
        source_filenames=tuple(
            sorted(filenames)
        ),
        review_interactions=tuple(
            review_interactions
        ),
    )


def format_session_report(
    report: StudySessionReport,
) -> str:
    """
    Convert a session report into terminal-friendly text.
    """
    counts = report.outcome_counts

    lines = [
        "=" * 60,
        f"STUDY SESSION {report.session.id}",
        "=" * 60,
        f"Status: {report.session.status}",
        f"Started: {report.session.started_at}",
        f"Ended: {report.session.ended_at or 'Not completed'}",
        f"Questions answered: {report.interaction_count}",
        "",
        "Learning outcomes:",
        f"- Understood: {counts.understood}",
        f"- Partial: {counts.partial}",
        f"- Confused: {counts.confused}",
        f"- Unrated: {counts.unrated}",
        "",
        "Files used:",
    ]

    if report.source_filenames:
        lines.extend(
            f"- {filename}"
            for filename in report.source_filenames
        )
    else:
        lines.append("- No document sources recorded")

    lines.append("")
    lines.append("Questions requiring review:")

    if report.review_interactions:
        for interaction in report.review_interactions:
            lines.append(
                f"- [{interaction.outcome}] "
                f"{interaction.question}"
            )
    else:
        lines.append("- None")

    return "\n".join(lines)