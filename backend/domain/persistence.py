from __future__ import annotations

from dataclasses import dataclass


DEFAULT_WORKSPACE_ID = "00000000-0000-4000-8000-000000000001"
DEFAULT_WORKSPACE_NAME = "Local workspace"


@dataclass(frozen=True)
class Workspace:
    id: str
    name: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkflowState:
    id: str
    workspace_id: str
    workflow_type: str
    payload: dict[str, object]
    status: str
    created_at: str
    updated_at: str
    expires_at: str
    version: int
    decision_metadata: dict[str, object] | None = None


@dataclass(frozen=True)
class VectorOutboxJob:
    id: str
    workspace_id: str
    entity_type: str
    entity_id: str
    operation: str
    payload: dict[str, object]
    status: str
    attempts: int
    created_at: str
    updated_at: str
    last_error: str | None = None


@dataclass(frozen=True)
class LearningSignal:
    id: str
    workspace_id: str
    source_type: str
    source_id: str
    source_question_id: str | None
    topic: str
    signal_type: str
    statement: str
    evidence: tuple[dict[str, object], ...]
    confidence: float
    importance: float
    occurrence_count: int
    payload: dict[str, object]
    status: str
    first_observed_at: str
    last_observed_at: str
    created_at: str
    updated_at: str
    signal_key: str | None = None
    memory_id: int | None = None
    proposal_id: str | None = None


@dataclass(frozen=True)
class AdaptationEvent:
    id: str
    workspace_id: str
    workflow_type: str
    request_id: str
    memory_ids: tuple[int, ...]
    learning_signal_ids: tuple[str, ...]
    applied_changes: dict[str, object]
    reason: str
    created_at: str


@dataclass(frozen=True)
class BlobMetadata:
    document_id: int
    filename: str
    mime_type: str
    size_bytes: int
    content_hash: str
    created_at: str
    updated_at: str
