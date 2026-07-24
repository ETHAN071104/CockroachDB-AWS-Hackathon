from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.public_ids import PublicId, PublicIdData, PublicIdInput


class ApiModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
    )


class ErrorBody(ApiModel):
    code: str = Field(min_length=1)
    title: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    next_action: str = Field(min_length=1)
    retryable: bool
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    message: str = Field(min_length=1)
    details: Any | None = None
    legacy_code: str | None = None


class ErrorResponse(ApiModel):
    error: ErrorBody


class ServiceHealth(ApiModel):
    status: Literal["ok", "error"]
    collection_present: bool | None = None


class HealthResponse(ApiModel):
    status: Literal["ok", "degraded"]
    version: str
    database: ServiceHealth
    documents_vector_store: ServiceHealth
    memory_vector_store: ServiceHealth
    llm_provider: str
    persistence_backend: str | None = None
    guest_sessions_configured: bool | None = None


class GuestWorkspaceResponse(ApiModel):
    name: str


class GuestSessionMetadataResponse(ApiModel):
    status: Literal["active", "revoked", "expired"]
    created_at: str
    last_seen_at: str | None = None
    expires_at: str | None = None


class GuestSessionCreateResponse(ApiModel):
    token: str = Field(min_length=43, max_length=200)
    session: GuestSessionMetadataResponse
    workspace: GuestWorkspaceResponse


class GuestSessionInspectResponse(ApiModel):
    status: Literal["active", "revoked", "expired"]
    workspace: GuestWorkspaceResponse
    created_at: str
    last_seen_at: str | None = None
    expires_at: str | None = None


class NotebookCreate(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


class NotebookUpdate(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def require_change(self) -> "NotebookUpdate":
        if self.name is None and self.description is None:
            raise ValueError("At least one notebook field is required.")
        return self


class NotebookResponse(ApiModel):
    id: PublicId | None
    name: str
    description: str
    document_count: int = Field(ge=0)
    created_at: str | None
    updated_at: str | None
    is_virtual: bool = False


class NotebookListResponse(ApiModel):
    items: list[NotebookResponse]
    total: int = Field(ge=0)
    unsorted: NotebookResponse


class DocumentResponse(ApiModel):
    id: PublicId
    filename: str
    mime_type: str
    chunk_count: int = Field(ge=0)
    created_at: str
    updated_at: str
    notebook_id: PublicId | None = None


class DocumentListResponse(ApiModel):
    items: list[DocumentResponse]
    total: int = Field(ge=0)


class DocumentAssignment(ApiModel):
    notebook_id: PublicIdInput | None


class DocumentUploadResponse(ApiModel):
    status: Literal["indexed", "duplicate"]
    duplicate: bool
    document: DocumentResponse


class DeleteResponse(ApiModel):
    deleted: bool


class RetrievalScopeRequest(ApiModel):
    notebook_id: PublicIdInput | None = None
    document_ids: list[PublicIdInput] | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    topic_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_exactly_one_scope(self) -> "RetrievalScopeRequest":
        choices = (
            self.notebook_id is not None,
            self.document_ids is not None,
            self.topic_id is not None,
        )
        if sum(choices) != 1:
            raise ValueError(
                "Exactly one of notebook_id, document_ids, or topic_id is required."
            )
        if self.document_ids is not None:
            if any(identifier <= 0 for identifier in self.document_ids):
                raise ValueError("Document IDs must be positive integers.")
            if len(self.document_ids) != len(set(self.document_ids)):
                raise ValueError("Document IDs must be unique.")
        return self


class SourceLineageResponse(ApiModel):
    index: int = Field(ge=1)
    document_id: PublicId | None = None
    notebook_id: PublicId | None = None
    filename: str
    mime_type: str | None = None
    page_number: int | None = None
    slide_number: int | None = None
    chunk_index: int | None = None
    distance: float | None = None
    excerpt: str


class SummaryKeyPointResponse(ApiModel):
    text: str
    source_indexes: list[int]


class SummaryContentResponse(ApiModel):
    title: str
    overview: str
    key_points: list[SummaryKeyPointResponse]
    confidence: float = Field(ge=0, le=1)


class SummaryResponse(ApiModel):
    kind: Literal["document", "notebook", "topic"]
    scope_id: str
    summary: SummaryContentResponse
    sources: list[SourceLineageResponse]
    generated_at: str
    stale: bool


class TopicResponse(ApiModel):
    id: str
    name: str
    description: str
    sources: list[SourceLineageResponse]
    generated_at: str
    stale: bool


class TopicListResponse(ApiModel):
    items: list[TopicResponse]
    total: int = Field(ge=0)


class TopicExtractionRequest(ApiModel):
    scope: RetrievalScopeRequest


class QuizGenerateRequest(ApiModel):
    topic: str = Field(min_length=1, max_length=300)
    question_count: int = Field(default=3, ge=1, le=10)
    scope: RetrievalScopeRequest | None = None
    notebook_id: PublicIdInput | None = None
    document_ids: list[PublicIdInput] | None = Field(
        default=None,
        min_length=1,
        max_length=100,
    )
    topic_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_optional_scope(self) -> "QuizGenerateRequest":
        top_level_choices = (
            self.notebook_id is not None,
            self.document_ids is not None,
            self.topic_id is not None,
        )
        if sum(top_level_choices) > 1:
            raise ValueError(
                "Choose at most one of notebook_id, document_ids, or topic_id."
            )
        if self.scope is not None and any(top_level_choices):
            raise ValueError(
                "Use either top-level scope fields or the compatibility scope object."
            )
        if self.document_ids is not None:
            if any(identifier <= 0 for identifier in self.document_ids):
                raise ValueError("Document IDs must be positive integers.")
            if len(self.document_ids) != len(set(self.document_ids)):
                raise ValueError("Document IDs must be unique.")
        return self


class PresentedQuizQuestionResponse(ApiModel):
    question_number: int = Field(ge=1)
    question: str
    options: list[str] = Field(min_length=4, max_length=4)


class QuizScopeResponse(ApiModel):
    type: Literal[
        "global",
        "notebook",
        "document",
        "documents",
        "topic",
        "adaptive-global",
        "adaptive-notebook",
        "adaptive-document",
        "adaptive-documents",
        "adaptive-topic",
    ]
    label: str
    document_count: int = Field(ge=1)
    personalized: bool
    resolved_document_ids: list[PublicId] = Field(min_length=1)
    description: str
    notebook_name: str | None = None
    document_name: str | None = None


class AdaptationResponse(ApiModel):
    adapted_using_learner_memory: bool
    targeted_topic: str | None = None
    difficulty: str | None = None
    reason: str
    memory_ids: list[PublicId] = Field(default_factory=list)
    learning_signal_ids: list[str] = Field(default_factory=list)
    applied_changes: PublicIdData = Field(default_factory=dict)
    event_id: str | None = None


class LearningSignalResponse(ApiModel):
    id: str
    source_type: str
    source_id: str
    source_question_id: str | None = None
    topic: str
    signal_type: str
    statement: str
    evidence: list[PublicIdData]
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    occurrence_count: int = Field(ge=1)
    status: str
    first_observed_at: str
    last_observed_at: str
    memory_id: PublicId | None = None
    proposal_id: str | None = None


class QuizMemoryProposalResponse(ApiModel):
    proposal_id: str
    memory_type: str
    content: str
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    allowed_decisions: list[str]
    reason: str
    evidence: list[PublicIdData]
    occurrence_count: int = Field(ge=1)
    created_at: str


class PresentedQuizResponse(ApiModel):
    quiz_id: str
    requested_topic: str
    topic: str
    confidence: float = Field(ge=0, le=1)
    questions: list[PresentedQuizQuestionResponse]
    scope: QuizScopeResponse
    adaptation: AdaptationResponse | None = None


class QuizAnswerRequest(ApiModel):
    question_number: int = Field(ge=1)
    selected_option: int | None = Field(ge=1, le=4)


class QuizSubmitRequest(ApiModel):
    responses: list[QuizAnswerRequest] = Field(max_length=10)


class QuizQuestionFeedbackResponse(ApiModel):
    question_number: int
    question: str
    selected_option: int | None
    correct_option: int
    is_correct: bool
    skipped: bool
    explanation: str
    sources: list[SourceLineageResponse]


class QuizSubmissionResponse(ApiModel):
    attempt_id: PublicId
    status: Literal["completed", "aborted"]
    total_questions: int
    presented_questions: int
    answered_questions: int
    skipped_questions: int
    correct_answers: int
    score_percentage: float
    accuracy_percentage: float | None
    feedback: list[QuizQuestionFeedbackResponse]
    learning_signals: list[LearningSignalResponse] = Field(default_factory=list)
    detected_weaknesses: list[str] = Field(default_factory=list)
    memory_proposals: list[QuizMemoryProposalResponse] = Field(default_factory=list)
    enrichment_workflow_id: str | None = None


MemoryType = Literal[
    "profile",
    "learning_state",
    "episodic",
    "procedural",
]
MemoryStatus = Literal["active", "archived"]
MemoryDecision = Literal[
    "accept",
    "replace",
    "keep_both",
    "reject",
    "cancel",
]
StudyOutcome = Literal[
    "unrated",
    "understood",
    "partial",
    "confused",
]


class ChatRequest(ApiModel):
    question: str = Field(min_length=1, max_length=4000)
    notebook_id: PublicIdInput | None = None
    document_ids: list[PublicIdInput] | None = Field(
        default=None,
        max_length=100,
    )
    topic_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_optional_scope(self) -> "ChatRequest":
        choices = (
            self.notebook_id is not None,
            self.document_ids is not None,
            self.topic_id is not None,
        )
        if sum(choices) > 1:
            raise ValueError(
                "Choose at most one of notebook_id, document_ids, or topic_id."
            )
        if self.document_ids is not None:
            if any(identifier <= 0 for identifier in self.document_ids):
                raise ValueError("Document IDs must be positive integers.")
            if len(self.document_ids) != len(set(self.document_ids)):
                raise ValueError("Document IDs must be unique.")
        return self


class StudySourceResponse(SourceLineageResponse):
    pass


class FeatureRedirectResponse(ApiModel):
    target: Literal["coaching", "study-plan"]
    title: str
    message: str
    action_label: str
    original_prompt: str
    suggested_prompt: str | None = None


class MemoryProposalResponse(ApiModel):
    proposal_id: str
    memory_type: MemoryType
    content: str
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    conflict_type: Literal["new", "refinement", "contradiction"]
    conflict_confidence: float = Field(ge=0, le=1)
    existing_memory_id: PublicId | None = None
    existing_memory_content: str | None = None
    allowed_decisions: list[MemoryDecision]
    reason: str
    created_at: str
    evidence: list[PublicIdData] = Field(default_factory=list)
    learning_signal_ids: list[str] = Field(default_factory=list)
    source_type: str | None = None
    source_id: str | None = None
    occurrence_count: int = Field(default=1, ge=1)
    signal_status: str | None = None


class ChatResponse(ApiModel):
    session_id: PublicId
    interaction_id: PublicId
    answer: str
    sources: list[StudySourceResponse]
    memory_proposal: MemoryProposalResponse | None = None
    type: Literal["answer", "feature_redirect"] = "answer"
    intent: Literal[
        "document_question",
        "weakness_analysis",
        "coaching_request",
        "study_plan_request",
        "unsupported_or_ambiguous",
    ] = "document_question"
    evidence_status: Literal[
        "grounded",
        "no_documents_indexed",
        "no_relevant_chunks",
        "retrieved_chunks_insufficient",
        "personal_performance_request",
        "planning_request",
        "citation_validation_failed",
        "unsupported_claims",
    ] = "grounded"
    redirect: FeatureRedirectResponse | None = None
    suggested_question: str | None = None


class InteractionOutcomeUpdate(ApiModel):
    outcome: StudyOutcome


class StudySessionResponse(ApiModel):
    id: PublicId
    status: Literal["active", "completed"]
    started_at: str
    ended_at: str | None = None


class StudySessionListResponse(ApiModel):
    items: list[StudySessionResponse]
    total: int = Field(ge=0)


class StudyInteractionResponse(ApiModel):
    id: PublicId
    session_id: PublicId
    question: str
    answer: str
    outcome: StudyOutcome
    created_at: str
    sources: list[StudySourceResponse]


class SessionDetailResponse(ApiModel):
    session: StudySessionResponse
    interactions: list[StudyInteractionResponse]


class MemoryCreate(ApiModel):
    memory_type: MemoryType
    content: str = Field(min_length=1, max_length=500)
    confidence: float = Field(default=1.0, ge=0, le=1)
    importance: float = Field(default=0.5, ge=0, le=1)


class MemoryUpdate(ApiModel):
    memory_type: MemoryType | None = None
    content: str | None = Field(default=None, min_length=1, max_length=500)
    confidence: float | None = Field(default=None, ge=0, le=1)
    importance: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def require_memory_change(self) -> "MemoryUpdate":
        if all(
            value is None
            for value in (
                self.memory_type,
                self.content,
                self.confidence,
                self.importance,
            )
        ):
            raise ValueError("At least one memory field is required.")
        return self


class MemoryResponse(ApiModel):
    id: PublicId
    memory_type: MemoryType
    content: str
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    status: MemoryStatus
    created_at: str
    updated_at: str
    evidence: list[PublicIdData] = Field(default_factory=list)
    source_quiz_id: str | None = None
    occurrence_count: int = Field(default=0, ge=0)
    improvement_state: str | None = None
    latest_use: dict[str, Any] | None = None


class MemoryListResponse(ApiModel):
    items: list[MemoryResponse]
    total: int = Field(ge=0)


class MemorySearchItemResponse(ApiModel):
    memory_id: PublicId
    memory_type: MemoryType
    content: str
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    distance: float = Field(ge=0)


class MemorySearchResponse(ApiModel):
    items: list[MemorySearchItemResponse]
    total: int = Field(ge=0)


class MemoryProposalDecisionRequest(ApiModel):
    decision: MemoryDecision
    replace_memory_id: PublicIdInput | None = None
    edited_content: str | None = Field(default=None, min_length=12, max_length=500)


class MemoryProposalDecisionResultResponse(ApiModel):
    proposal_id: str
    decision: MemoryDecision
    consumed: bool
    saved_memory: MemoryResponse | None = None
    archived_memory: MemoryResponse | None = None


class ConsolidationProposeRequest(ApiModel):
    memory_ids: list[PublicIdInput] = Field(min_length=2, max_length=50)

    @model_validator(mode="after")
    def validate_memory_ids(self) -> "ConsolidationProposeRequest":
        if any(identifier <= 0 for identifier in self.memory_ids):
            raise ValueError("Memory IDs must be positive integers.")
        if len(self.memory_ids) != len(set(self.memory_ids)):
            raise ValueError("Memory IDs must be unique.")
        return self


class ConsolidationApplyRequest(ApiModel):
    proposal_id: str = Field(min_length=1, max_length=64)


class ConsolidationProposalResponse(ApiModel):
    proposal_id: str
    should_consolidate: bool
    memory_type: str
    content: str
    confidence: float = Field(ge=0, le=1)
    importance: float = Field(ge=0, le=1)
    reason: str
    source_memories: list[MemoryResponse]
    created_at: str


class ConsolidationApplyResultResponse(ApiModel):
    proposal_id: str
    consolidated_memory: MemoryResponse
    archived_source_memories: list[MemoryResponse]
