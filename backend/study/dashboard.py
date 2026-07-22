from __future__ import annotations

from dataclasses import dataclass

from backend.application.dependencies import get_application_dependencies


@dataclass(frozen=True)
class DashboardCounts:
    documents: int
    notebooks: int
    unsorted_documents: int
    active_memories: int
    archived_memories: int
    study_sessions: int
    completed_sessions: int
    interactions: int
    quiz_attempts: int
    topics: int


@dataclass(frozen=True)
class DashboardOutcomeCounts:
    unrated: int
    understood: int
    partial: int
    confused: int


@dataclass(frozen=True)
class DashboardSession:
    id: int
    status: str
    started_at: str
    ended_at: str | None
    interaction_count: int


@dataclass(frozen=True)
class DashboardQuizAttempt:
    id: int
    quiz_topic: str
    status: str
    score_percentage: float
    accuracy_percentage: float | None
    created_at: str


@dataclass(frozen=True)
class DashboardQuizStats:
    total: int
    completed: int
    aborted: int
    average_score_percentage: float | None
    average_accuracy_percentage: float | None


@dataclass(frozen=True)
class DashboardSnapshot:
    counts: DashboardCounts
    active_session: DashboardSession | None
    recent_sessions: tuple[DashboardSession, ...]
    outcomes: DashboardOutcomeCounts
    quiz: DashboardQuizStats
    recent_quizzes: tuple[DashboardQuizAttempt, ...]


def build_dashboard(recent_limit: int = 5) -> DashboardSnapshot:
    if isinstance(recent_limit, bool) or not 1 <= recent_limit <= 50:
        raise ValueError("recent_limit must be between 1 and 50.")
    dependencies = get_application_dependencies()
    raw = dependencies.dashboard.build(recent_limit)
    active = raw["active_session"]
    return DashboardSnapshot(
        counts=DashboardCounts(**raw["counts"]),
        active_session=DashboardSession(**active) if active is not None else None,
        recent_sessions=tuple(
            DashboardSession(**item) for item in raw["recent_sessions"]
        ),
        outcomes=DashboardOutcomeCounts(**raw["outcomes"]),
        quiz=DashboardQuizStats(**raw["quiz"]),
        recent_quizzes=tuple(
            DashboardQuizAttempt(**item) for item in raw["recent_quizzes"]
        ),
    )
