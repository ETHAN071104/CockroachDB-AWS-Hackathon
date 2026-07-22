from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from backend.rag.scope import RetrievalScope, ResolvedRetrievalScope, resolve_retrieval_scope

from backend.study.quiz_reporting import (
    QuizReviewItem,
    build_quiz_performance_report,
)
from backend.study.recommendations import (
    build_review_queue,
    normalize_review_question,
)
from backend.study.scope_filter import source_matches_scope
from backend.application.learning_loop import AdaptationContext, build_adaptation_context


# ============================================================
# PLAN MODELS
# ============================================================

EvidenceType = Literal[
    "study_outcome",
    "quiz_result",
]


@dataclass(frozen=True)
class StudyPlanEvidence:
    """
    Evidence explaining why one study-plan item exists.
    """

    evidence_type: EvidenceType
    status: str
    reference_id: int
    detail: str


@dataclass(frozen=True)
class StudyPlanItem:
    """
    One prioritized study action.
    """

    rank: int
    title: str
    action: str
    priority_score: int
    estimated_minutes: int

    evidence: tuple[
        StudyPlanEvidence,
        ...
    ]

    source_filenames: tuple[
        str,
        ...
    ]
    source_document_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class AdaptiveStudyPlan:
    """
    Deterministic study plan generated from recorded evidence.
    """

    requested_minutes: int
    allocated_minutes: int

    items: tuple[
        StudyPlanItem,
        ...
    ]

    completed_sessions_scanned: int
    interactions_scanned: int
    quiz_attempts_scanned: int
    adaptation: AdaptationContext

    @property
    def item_count(self) -> int:
        return len(self.items)

    @property
    def remaining_minutes(self) -> int:
        return max(
            self.requested_minutes
            - self.allocated_minutes,
            0,
        )


@dataclass(frozen=True)
class _PlanCandidate:
    """
    Internal candidate before time allocation and ranking.
    """

    title: str
    action: str
    priority_score: int
    desired_minutes: int
    latest_at: str

    evidence: tuple[
        StudyPlanEvidence,
        ...
    ]

    source_filenames: tuple[
        str,
        ...
    ]
    source_document_ids: tuple[int, ...]
    source_pairs: tuple[tuple[int, int], ...]


# ============================================================
# STUDY-OUTCOME CANDIDATES
# ============================================================

def build_study_outcome_candidates(
    *,
    session_limit: int | None,
    resolved_scope: ResolvedRetrievalScope | None = None,
) -> tuple[
    list[_PlanCandidate],
    int,
    int,
]:
    """
    Build plan candidates from partial and confused outcomes.
    """
    queue = build_review_queue(
        session_limit=session_limit,
        max_items=100,
        resolved_scope=resolved_scope,
    )

    candidates: list[_PlanCandidate] = []

    for recommendation in queue.recommendations:
        if recommendation.outcome == "confused":
            action = (
                "Relearn this concept using a simpler "
                "explanation, study one worked example, then "
                "answer a check question without looking at "
                "the explanation."
            )

            desired_minutes = 20

        elif recommendation.outcome == "partial":
            action = (
                "Review the missing distinction or step, "
                "summarize the concept in your own words, then "
                "answer one check question."
            )

            desired_minutes = 15

        else:
            continue

        # Existing queue scores range roughly from 2 to 5.
        # Scaling keeps confused and repeatedly unresolved
        # questions ahead of weaker quiz evidence.
        priority_score = (
            recommendation.priority_score
            * 20
        )

        candidates.append(
            _PlanCandidate(
                title=recommendation.question,
                action=action,
                priority_score=priority_score,
                desired_minutes=desired_minutes,
                latest_at=recommendation.created_at,
                evidence=(
                    StudyPlanEvidence(
                        evidence_type="study_outcome",
                        status=recommendation.outcome,
                        reference_id=(
                            recommendation.interaction_id
                        ),
                        detail=recommendation.reason,
                    ),
                ),
                source_filenames=(
                    recommendation.source_filenames
                ),
                source_document_ids=(
                    recommendation.source_document_ids
                ),
                source_pairs=recommendation.source_pairs,
            )
        )

    return (
        candidates,
        queue.completed_session_count,
        queue.scanned_interaction_count,
    )


# ============================================================
# QUIZ CANDIDATES
# ============================================================

def group_quiz_review_items(
    review_items: tuple[
        QuizReviewItem,
        ...
    ],
) -> dict[
    str,
    list[QuizReviewItem],
]:
    """
    Group repeated quiz gaps using deterministic question
    normalization.
    """
    groups: dict[
        str,
        list[QuizReviewItem],
    ] = {}

    for item in review_items:
        question_text = (
            item.question
            .question_attempt
            .question
        )

        normalized = normalize_review_question(
            question_text
        )

        if not normalized:
            continue

        groups.setdefault(
            normalized,
            [],
        ).append(item)

    return groups


def build_quiz_candidates(
    *,
    attempt_limit: int | None,
    resolved_scope: ResolvedRetrievalScope | None = None,
) -> tuple[
    list[_PlanCandidate],
    int,
]:
    """
    Build plan candidates from incorrect and skipped quiz
    questions.
    """
    report = build_quiz_performance_report(
        attempt_limit=attempt_limit
    )

    eligible_items = (
        report.review_items
        if resolved_scope is None
        else tuple(
            item
            for item in report.review_items
            if any(
                source_matches_scope(
                    document_id=source.document_id,
                    chunk_index=source.chunk_index,
                    resolved_scope=resolved_scope,
                )
                for source in item.question.sources
            )
        )
    )

    grouped_items = group_quiz_review_items(eligible_items)

    candidates: list[_PlanCandidate] = []

    for items in grouped_items.values():
        ordered_items = sorted(
            items,
            key=lambda item: (
                item.attempt_created_at,
                item.quiz_attempt_id,
                item.question.question_attempt.id,
            ),
        )

        latest_item = ordered_items[-1]
        latest_status = latest_item.question.status

        question_attempt = (
            latest_item.question
            .question_attempt
        )

        unresolved_count = len(
            ordered_items
        )

        if latest_status == "incorrect":
            base_score = 70
            desired_minutes = 15

            action = (
                "Review why the selected answer was wrong, "
                "study the cited explanation, then answer the "
                "same concept again without viewing the answer."
            )

        elif latest_status == "skipped":
            base_score = 55
            desired_minutes = 10

            action = (
                "Review the concept and its cited source, then "
                "attempt the skipped question before checking "
                "the correct answer."
            )

        else:
            continue

        repetition_bonus = min(
            max(unresolved_count - 1, 0) * 8,
            24,
        )

        priority_score = (
            base_score
            + repetition_bonus
        )

        source_filenames = {
            source.filename
            for item in ordered_items
            for source in item.question.sources
            if source_matches_scope(
                document_id=source.document_id,
                chunk_index=source.chunk_index,
                resolved_scope=resolved_scope,
            )
        }
        source_document_ids = {
            source.document_id
            for item in ordered_items
            for source in item.question.sources
            if source.document_id is not None
            and source_matches_scope(
                document_id=source.document_id,
                chunk_index=source.chunk_index,
                resolved_scope=resolved_scope,
            )
        }
        source_pairs = {
            (source.document_id, source.chunk_index)
            for item in ordered_items
            for source in item.question.sources
            if source.document_id is not None
            and source.chunk_index is not None
            and source_matches_scope(
                document_id=source.document_id,
                chunk_index=source.chunk_index,
                resolved_scope=resolved_scope,
            )
        }

        detail = (
            f"The latest quiz result was {latest_status}."
        )

        if unresolved_count > 1:
            detail += (
                f" This question appeared as an unresolved "
                f"quiz gap {unresolved_count} times."
            )

        candidates.append(
            _PlanCandidate(
                title=question_attempt.question,
                action=action,
                priority_score=priority_score,
                desired_minutes=desired_minutes,
                latest_at=(
                    latest_item.attempt_created_at
                ),
                evidence=(
                    StudyPlanEvidence(
                        evidence_type="quiz_result",
                        status=latest_status,
                        reference_id=(
                            latest_item.quiz_attempt_id
                        ),
                        detail=detail,
                    ),
                ),
                source_filenames=tuple(
                    sorted(source_filenames)
                ),
                source_document_ids=tuple(
                    sorted(source_document_ids)
                ),
                source_pairs=tuple(
                    sorted(source_pairs)
                ),
            )
        )

    return (
        candidates,
        report.attempt_count,
    )


# ============================================================
# DEDUPLICATION
# ============================================================

def deduplicate_candidates(
    candidates: list[_PlanCandidate],
) -> list[_PlanCandidate]:
    """
    Keep the strongest candidate for identical normalized
    question text.

    This prevents the same exact question from appearing once
    from backend.study feedback and again from quiz history.
    """
    strongest_by_question: dict[
        str,
        _PlanCandidate,
    ] = {}

    for candidate in candidates:
        normalized = normalize_review_question(
            candidate.title
        )

        if not normalized:
            continue

        existing = strongest_by_question.get(
            normalized
        )

        if existing is None:
            strongest_by_question[
                normalized
            ] = candidate

            continue

        candidate_key = (
            candidate.priority_score,
            candidate.latest_at,
        )

        existing_key = (
            existing.priority_score,
            existing.latest_at,
        )

        if candidate_key > existing_key:
            strongest_by_question[
                normalized
            ] = candidate

    return list(
        strongest_by_question.values()
    )


# ============================================================
# PLAN GENERATION
# ============================================================

def build_adaptive_study_plan(
    *,
    total_minutes: int = 45,
    max_items: int = 5,
    session_limit: int | None = None,
    attempt_limit: int | None = None,
    scope: RetrievalScope | None = None,
) -> AdaptiveStudyPlan:
    """
    Build a deterministic adaptive study plan.

    Higher-priority learning gaps consume the available study
    time first.
    """
    if not 10 <= total_minutes <= 240:
        raise ValueError(
            "Study time must be between 10 and 240 minutes."
        )

    if not 1 <= max_items <= 20:
        raise ValueError(
            "Maximum plan items must be between 1 and 20."
        )

    if (
        session_limit is not None
        and session_limit <= 0
    ):
        raise ValueError(
            "Session limit must be greater than zero."
        )

    resolved_scope = (
        resolve_retrieval_scope(scope)
        if scope is not None
        else None
    )

    if (
        attempt_limit is not None
        and attempt_limit <= 0
    ):
        raise ValueError(
            "Quiz-attempt limit must be greater than zero."
        )

    (
        study_candidates,
        completed_session_count,
        interaction_count,
    ) = build_study_outcome_candidates(
        session_limit=session_limit,
        resolved_scope=resolved_scope,
    )

    (
        quiz_candidates,
        quiz_attempt_count,
    ) = build_quiz_candidates(
        attempt_limit=attempt_limit,
        resolved_scope=resolved_scope,
    )

    candidates = deduplicate_candidates(
        study_candidates
        + quiz_candidates
    )

    adaptation = build_adaptation_context(
        "study_plan",
        " ".join(candidate.title for candidate in candidates),
    )
    if adaptation.adapted:
        candidates = [
            replace(
                candidate,
                priority_score=candidate.priority_score + 15,
                desired_minutes=min(candidate.desired_minutes + 5, 30),
                action=(
                    candidate.action
                    + " This item is prioritized because current learner memory "
                    "or learning-signal evidence indicates a relevant gap."
                ),
            )
            for candidate in candidates
        ]

    candidates.sort(
        key=lambda candidate: (
            candidate.priority_score,
            candidate.latest_at,
            candidate.title.casefold(),
        ),
        reverse=True,
    )

    selected_items: list[
        StudyPlanItem
    ] = []

    remaining_minutes = total_minutes

    for candidate in candidates:
        if len(selected_items) >= max_items:
            break

        if remaining_minutes < 5:
            break

        allocated_minutes = min(
            candidate.desired_minutes,
            remaining_minutes,
        )

        selected_items.append(
            StudyPlanItem(
                rank=len(selected_items) + 1,
                title=candidate.title,
                action=candidate.action,
                priority_score=(
                    candidate.priority_score
                ),
                estimated_minutes=(
                    allocated_minutes
                ),
                evidence=candidate.evidence,
                source_filenames=(
                    candidate.source_filenames
                ),
                source_document_ids=(
                    candidate.source_document_ids
                ),
            )
        )

        remaining_minutes -= (
            allocated_minutes
        )

    allocated_minutes = sum(
        item.estimated_minutes
        for item in selected_items
    )

    return AdaptiveStudyPlan(
        requested_minutes=total_minutes,
        allocated_minutes=allocated_minutes,
        items=tuple(selected_items),
        completed_sessions_scanned=(
            completed_session_count
        ),
        interactions_scanned=interaction_count,
        quiz_attempts_scanned=(
            quiz_attempt_count
        ),
        adaptation=adaptation,
    )


# ============================================================
# TERMINAL FORMATTING
# ============================================================

def format_adaptive_study_plan(
    plan: AdaptiveStudyPlan,
) -> str:
    """
    Format an adaptive study plan for the terminal.
    """
    lines = [
        "=" * 60,
        "ADAPTIVE STUDY PLAN",
        "=" * 60,
        (
            "Requested study time: "
            f"{plan.requested_minutes} minutes"
        ),
        (
            "Allocated study time: "
            f"{plan.allocated_minutes} minutes"
        ),
        (
            "Completed sessions scanned: "
            f"{plan.completed_sessions_scanned}"
        ),
        (
            "Study interactions scanned: "
            f"{plan.interactions_scanned}"
        ),
        (
            "Quiz attempts scanned: "
            f"{plan.quiz_attempts_scanned}"
        ),
        (
            "Plan items: "
            f"{plan.item_count}"
        ),
    ]

    if not plan.items:
        lines.extend(
            [
                "",
                "No unresolved study or quiz gaps were found.",
                (
                    "Complete another study session or quiz "
                    "to collect fresh evidence."
                ),
            ]
        )

        return "\n".join(lines)

    for item in plan.items:
        lines.extend(
            [
                "",
                "-" * 60,
                f"{item.rank}. {item.title}",
                (
                    "Time: "
                    f"{item.estimated_minutes} minutes"
                ),
                (
                    "Priority score: "
                    f"{item.priority_score}"
                ),
                "",
                "Action:",
                item.action,
                "",
                "Evidence:",
            ]
        )

        for evidence in item.evidence:
            lines.append(
                f"- {evidence.evidence_type} "
                f"[{evidence.status}], "
                f"reference {evidence.reference_id}: "
                f"{evidence.detail}"
            )

        lines.append("")
        lines.append("Sources:")

        if item.source_filenames:
            lines.extend(
                f"- {filename}"
                for filename
                in item.source_filenames
            )
        else:
            lines.append(
                "- No document source lineage recorded"
            )

    if plan.remaining_minutes > 0:
        lines.extend(
            [
                "",
                (
                    "Unallocated time: "
                    f"{plan.remaining_minutes} minutes"
                ),
                (
                    "Use this time to retry the highest-"
                    "priority check question or summarize "
                    "what you learned."
                ),
            ]
        )

    return "\n".join(lines)
