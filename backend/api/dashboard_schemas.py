from __future__ import annotations

from typing import Literal

from pydantic import Field

from backend.api.public_ids import PublicId
from backend.api.schemas import ApiModel


class DashboardCountsResponse(ApiModel):
    documents: int = Field(ge=0)
    notebooks: int = Field(ge=0)
    unsorted_documents: int = Field(ge=0)
    active_memories: int = Field(ge=0)
    archived_memories: int = Field(ge=0)
    study_sessions: int = Field(ge=0)
    completed_sessions: int = Field(ge=0)
    interactions: int = Field(ge=0)
    quiz_attempts: int = Field(ge=0)
    topics: int = Field(ge=0)


class DashboardOutcomeCountsResponse(ApiModel):
    unrated: int = Field(ge=0)
    understood: int = Field(ge=0)
    partial: int = Field(ge=0)
    confused: int = Field(ge=0)


class DashboardSessionResponse(ApiModel):
    id: PublicId
    status: Literal["active", "completed"]
    started_at: str
    ended_at: str | None
    interaction_count: int = Field(ge=0)


class DashboardQuizAttemptResponse(ApiModel):
    id: PublicId
    quiz_topic: str
    status: Literal["completed", "aborted"]
    score_percentage: float = Field(ge=0, le=100)
    accuracy_percentage: float | None = Field(default=None, ge=0, le=100)
    created_at: str


class DashboardQuizStatsResponse(ApiModel):
    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    aborted: int = Field(ge=0)
    average_score_percentage: float | None = Field(default=None, ge=0, le=100)
    average_accuracy_percentage: float | None = Field(
        default=None,
        ge=0,
        le=100,
    )


class DashboardResponse(ApiModel):
    counts: DashboardCountsResponse
    active_session: DashboardSessionResponse | None
    recent_sessions: list[DashboardSessionResponse]
    outcomes: DashboardOutcomeCountsResponse
    quiz: DashboardQuizStatsResponse
    recent_quizzes: list[DashboardQuizAttemptResponse]
