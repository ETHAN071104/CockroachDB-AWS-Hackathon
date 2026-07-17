from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

from study.database import (
    StoredStudyInteraction,
    list_interaction_sources,
    list_session_interactions,
    list_study_sessions,
)


# ============================================================
# RESULT MODELS
# ============================================================

@dataclass(frozen=True)
class ReviewRecommendation:
    """
    One unresolved study question recommended for review.
    """

    interaction_id: int
    session_id: int
    question: str
    outcome: str
    priority_score: int
    unresolved_count: int
    source_filenames: tuple[str, ...]
    created_at: str
    reason: str


@dataclass(frozen=True)
class StudyReviewQueue:
    """
    Deterministic review queue across completed sessions.
    """

    recommendations: tuple[
        ReviewRecommendation,
        ...
    ]

    completed_session_count: int
    scanned_interaction_count: int

    @property
    def recommendation_count(self) -> int:
        return len(self.recommendations)


# ============================================================
# QUESTION NORMALIZATION
# ============================================================

def normalize_review_question(
    question: str,
) -> str:
    """
    Normalize a question so exact paraphrases caused only by
    capitalization, punctuation, or spacing are grouped.

    This does not perform semantic grouping.
    """
    normalized = unicodedata.normalize(
        "NFKC",
        question,
    )

    normalized = normalized.casefold()

    normalized = re.sub(
        r"[^\w\s]",
        " ",
        normalized,
    )

    normalized = re.sub(
        r"\s+",
        " ",
        normalized,
    )

    return normalized.strip()


# ============================================================
# PRIORITY
# ============================================================

def calculate_review_priority(
    latest_outcome: str,
    unresolved_count: int,
) -> int:
    """
    Calculate an explainable internal priority score.

    Base score:
    - confused: 3
    - partial: 2

    Repeated unresolved attempts add at most two points.
    """
    if latest_outcome == "confused":
        base_score = 3

    elif latest_outcome == "partial":
        base_score = 2

    else:
        raise ValueError(
            "Review priority requires a partial or confused "
            "outcome."
        )

    repetition_bonus = min(
        max(unresolved_count - 1, 0),
        2,
    )

    return base_score + repetition_bonus


def build_review_reason(
    outcome: str,
    unresolved_count: int,
) -> str:
    if outcome == "confused":
        reason = (
            "The latest recorded outcome was confused."
        )
    else:
        reason = (
            "The latest recorded outcome was partially "
            "understood."
        )

    if unresolved_count > 1:
        reason += (
            f" This question had {unresolved_count} "
            "unresolved attempts."
        )

    return reason


# ============================================================
# QUEUE GENERATION
# ============================================================

def build_review_queue(
    session_limit: int | None = None,
    max_items: int = 10,
) -> StudyReviewQueue:
    """
    Build a review queue from completed study sessions.

    The latest recorded outcome for each normalized question
    determines whether it remains unresolved.

    If a question was previously confused but later understood,
    it is not included in the queue.
    """
    if (
        session_limit is not None
        and session_limit <= 0
    ):
        raise ValueError(
            "Session limit must be greater than zero."
        )

    if max_items <= 0:
        raise ValueError(
            "Maximum review items must be greater than zero."
        )

    completed_sessions = [
        session
        for session in list_study_sessions()
        if session.status == "completed"
    ]

    # list_study_sessions() returns newest sessions first.
    if session_limit is not None:
        completed_sessions = completed_sessions[
            :session_limit
        ]

    # Process chronologically so later outcomes replace earlier
    # outcomes for the same normalized question.
    completed_sessions.reverse()

    grouped_interactions: dict[
        str,
        list[StoredStudyInteraction],
    ] = defaultdict(list)

    scanned_interaction_count = 0

    for session in completed_sessions:
        interactions = list_session_interactions(
            session.id
        )

        scanned_interaction_count += len(
            interactions
        )

        for interaction in interactions:
            normalized_question = (
                normalize_review_question(
                    interaction.question
                )
            )

            if not normalized_question:
                continue

            grouped_interactions[
                normalized_question
            ].append(
                interaction
            )

    recommendations: list[
        ReviewRecommendation
    ] = []

    for interactions in grouped_interactions.values():
        # ISO UTC timestamps are sortable as strings in the
        # format currently stored by this application.
        ordered_interactions = sorted(
            interactions,
            key=lambda item: (
                item.created_at,
                item.id,
            ),
        )

        latest_interaction = (
            ordered_interactions[-1]
        )

        # A later understood result resolves the old gap.
        if latest_interaction.outcome not in {
            "partial",
            "confused",
        }:
            continue

        unresolved_interactions = [
            interaction
            for interaction in ordered_interactions
            if interaction.outcome in {
                "partial",
                "confused",
            }
        ]

        unresolved_count = len(
            unresolved_interactions
        )

        source_filenames: set[str] = set()

        for interaction in ordered_interactions:
            sources = list_interaction_sources(
                interaction.id
            )

            source_filenames.update(
                source.filename
                for source in sources
            )

        priority_score = calculate_review_priority(
            latest_outcome=(
                latest_interaction.outcome
            ),
            unresolved_count=unresolved_count,
        )

        recommendations.append(
            ReviewRecommendation(
                interaction_id=(
                    latest_interaction.id
                ),
                session_id=(
                    latest_interaction.session_id
                ),
                question=(
                    latest_interaction.question
                ),
                outcome=(
                    latest_interaction.outcome
                ),
                priority_score=priority_score,
                unresolved_count=(
                    unresolved_count
                ),
                source_filenames=tuple(
                    sorted(source_filenames)
                ),
                created_at=(
                    latest_interaction.created_at
                ),
                reason=build_review_reason(
                    outcome=(
                        latest_interaction.outcome
                    ),
                    unresolved_count=(
                        unresolved_count
                    ),
                ),
            )
        )

    recommendations.sort(
        key=lambda item: (
            item.priority_score,
            item.created_at,
            item.interaction_id,
        ),
        reverse=True,
    )

    return StudyReviewQueue(
        recommendations=tuple(
            recommendations[:max_items]
        ),
        completed_session_count=len(
            completed_sessions
        ),
        scanned_interaction_count=(
            scanned_interaction_count
        ),
    )


# ============================================================
# TERMINAL FORMATTING
# ============================================================

def format_review_queue(
    queue: StudyReviewQueue,
) -> str:
    """
    Format a deterministic review queue for the terminal.
    """
    lines = [
        "=" * 60,
        "RECOMMENDED REVIEW QUEUE",
        "=" * 60,
        (
            "Completed sessions scanned: "
            f"{queue.completed_session_count}"
        ),
        (
            "Interactions scanned: "
            f"{queue.scanned_interaction_count}"
        ),
        (
            "Review items: "
            f"{queue.recommendation_count}"
        ),
    ]

    if not queue.recommendations:
        lines.extend(
            [
                "",
                "No unresolved partial or confused questions "
                "were found.",
            ]
        )

        return "\n".join(lines)

    for position, recommendation in enumerate(
        queue.recommendations,
        start=1,
    ):
        lines.extend(
            [
                "",
                "-" * 60,
                (
                    f"{position}. "
                    f"{recommendation.question}"
                ),
                (
                    "Latest outcome: "
                    f"{recommendation.outcome}"
                ),
                (
                    "Priority score: "
                    f"{recommendation.priority_score}"
                ),
                (
                    "Unresolved attempts: "
                    f"{recommendation.unresolved_count}"
                ),
                (
                    "Latest session: "
                    f"{recommendation.session_id}"
                ),
                (
                    "Interaction ID: "
                    f"{recommendation.interaction_id}"
                ),
                (
                    "Reason: "
                    f"{recommendation.reason}"
                ),
                "Sources:",
            ]
        )

        if recommendation.source_filenames:
            lines.extend(
                f"- {filename}"
                for filename
                in recommendation.source_filenames
            )
        else:
            lines.append(
                "- No document sources recorded"
            )

    return "\n".join(lines)