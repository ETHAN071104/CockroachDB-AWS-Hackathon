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
    signal_type: str
    source_type: str
    source_id: str
    payload: dict[str, object]
    status: str
    created_at: str
    updated_at: str
