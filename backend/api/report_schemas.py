from __future__ import annotations

from typing import Literal

from pydantic import Field

from backend.api.public_ids import PublicId, PublicIdInput
from backend.api.schemas import (
    AdaptationResponse,
    ApiModel,
    RetrievalScopeRequest,
    SourceLineageResponse,
)


class OutcomeCountsResponse(ApiModel):
    understood: int = Field(ge=0)
    partial: int = Field(ge=0)
    confused: int = Field(ge=0)
    unrated: int = Field(ge=0)


class InteractionReportResponse(ApiModel):
    id: PublicId
    session_id: PublicId
    question: str
    answer: str
    outcome: Literal["unrated", "understood", "partial", "confused"]
    created_at: str
    sources: list[SourceLineageResponse]


class SessionReportResponse(ApiModel):
    id: PublicId
    status: Literal["active", "completed"]
    started_at: str
    ended_at: str | None
    interaction_count: int
    outcome_counts: OutcomeCountsResponse
    source_filenames: list[str]
    interactions: list[InteractionReportResponse]


class SessionSummaryContentResponse(ApiModel):
    overview: str
    strengths: list[str]
    review_topics: list[str]
    next_steps: list[str]
    confidence: float


class SessionSummaryResponse(ApiModel):
    session: SessionReportResponse
    summary: SessionSummaryContentResponse


class ProgressSessionResponse(ApiModel):
    session_id: PublicId
    started_at: str
    ended_at: str
    interaction_count: int
    outcome_counts: OutcomeCountsResponse


class ProgressReportResponse(ApiModel):
    sessions: list[ProgressSessionResponse]
    session_count: int
    total_questions: int
    rated_question_count: int
    understanding_rate: float | None
    outcome_counts: OutcomeCountsResponse
    source_filenames: list[str]


class StoredQuizAttemptResponse(ApiModel):
    id: PublicId
    requested_topic: str
    quiz_topic: str
    status: Literal["completed", "aborted"]
    total_questions: int
    presented_questions: int
    answered_questions: int
    skipped_questions: int
    correct_answers: int
    score_percentage: float
    accuracy_percentage: float | None
    confidence: float
    created_at: str


class StoredQuizQuestionResponse(ApiModel):
    id: PublicId
    question_number: int
    question: str
    options: list[str]
    presented: bool
    selected_option: int | None
    correct_option: int | None
    is_correct: bool
    skipped: bool
    status: Literal["not_presented", "skipped", "correct", "incorrect"]
    explanation: str | None
    sources: list[SourceLineageResponse]


class QuizAttemptReportResponse(ApiModel):
    attempt: StoredQuizAttemptResponse
    questions: list[StoredQuizQuestionResponse]


class QuizTopicPerformanceResponse(ApiModel):
    topic: str
    attempt_count: int
    total_questions: int
    answered_questions: int
    correct_answers: int
    score_percentage: float
    accuracy_percentage: float | None


class QuizPerformanceResponse(ApiModel):
    attempts: list[StoredQuizAttemptResponse]
    attempt_count: int
    completed_attempt_count: int
    aborted_attempt_count: int
    total_questions: int
    presented_questions: int
    answered_questions: int
    correct_answers: int
    overall_score_percentage: float
    answered_accuracy_percentage: float | None
    topic_performance: list[QuizTopicPerformanceResponse]
    source_filenames: list[str]


class ReviewRecommendationResponse(ApiModel):
    interaction_id: PublicId
    session_id: PublicId
    question: str
    outcome: Literal["partial", "confused"]
    priority_score: int
    unresolved_count: int
    source_filenames: list[str]
    source_document_ids: list[PublicId]
    created_at: str
    reason: str
    memory_ids: list[PublicId] = Field(default_factory=list)
    learning_signal_ids: list[str] = Field(default_factory=list)
    adaptation_reason: str | None = None


class ReviewQueueResponse(ApiModel):
    items: list[ReviewRecommendationResponse]
    total: int
    completed_session_count: int
    scanned_interaction_count: int
    adaptation: AdaptationResponse | None = None


class ReviewGenerateRequest(ApiModel):
    interaction_id: PublicIdInput
    scope: RetrievalScopeRequest | None = None


class ReviewActionResponse(ApiModel):
    recommendation: ReviewRecommendationResponse
    should_generate: bool
    review_mode: str
    topic: str
    explanation: str
    worked_example: str
    check_question: str
    expected_answer: str
    source_indexes: list[int]
    confidence: float
    reason: str
    sources: list[SourceLineageResponse]
    adaptation: AdaptationResponse | None = None


class StudyPlanRequest(ApiModel):
    total_minutes: int = Field(default=45, ge=10, le=240)
    max_items: int = Field(default=5, ge=1, le=20)
    session_limit: int | None = Field(default=None, ge=1)
    attempt_limit: int | None = Field(default=None, ge=1)
    scope: RetrievalScopeRequest | None = None


class StudyPlanEvidenceResponse(ApiModel):
    evidence_type: Literal["study_outcome", "quiz_result"]
    status: str
    reference_id: PublicId
    detail: str


class StudyPlanItemResponse(ApiModel):
    rank: int
    title: str
    action: str
    priority_score: int
    estimated_minutes: int
    evidence: list[StudyPlanEvidenceResponse]
    source_filenames: list[str]
    source_document_ids: list[PublicId]


class StudyPlanResponse(ApiModel):
    requested_minutes: int
    allocated_minutes: int
    remaining_minutes: int
    item_count: int
    completed_sessions_scanned: int
    interactions_scanned: int
    quiz_attempts_scanned: int
    items: list[StudyPlanItemResponse]
    adaptation: AdaptationResponse | None = None


class CoachingRequest(StudyPlanRequest):
    pass


class CoachingActivityResponse(ApiModel):
    plan_item: StudyPlanItemResponse
    should_generate: bool
    coaching_mode: str
    topic: str
    objective: str
    review_step: str
    practice_step: str
    reassessment_question: str
    expected_answer: str
    completion_criteria: str
    source_indexes: list[int]
    confidence: float
    reason: str
    sources: list[SourceLineageResponse]


class CoachingPlanResponse(ApiModel):
    plan: StudyPlanResponse
    generated_count: int
    rejected_count: int
    items: list[CoachingActivityResponse]
    adaptation: AdaptationResponse | None = None


class IntegrityIssueResponse(ApiModel):
    severity: Literal["error", "warning"]
    code: str
    message: str
    record_type: str
    record_id: str | None


class IntegrityResponse(ApiModel):
    passed: bool
    error_count: int
    warning_count: int
    table_counts: dict[str, int]
    issues: list[IntegrityIssueResponse]
