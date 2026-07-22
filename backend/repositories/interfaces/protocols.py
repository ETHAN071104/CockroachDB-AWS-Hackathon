from __future__ import annotations

from collections.abc import Callable
from types import TracebackType
from typing import Any, Protocol, TypeVar

from backend.domain import (
    AdaptationEvent,
    BlobMetadata,
    LearningSignal,
    VectorOutboxJob,
    WorkflowState,
    Workspace,
)


T = TypeVar("T")


class RepositoryConflictError(RuntimeError):
    """A uniqueness or optimistic-version constraint was violated."""


class UnitOfWork(Protocol):
    """Transaction boundary that can gain retry behavior in a later adapter."""

    def __enter__(self) -> "UnitOfWork": ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...

    def after_commit(self, callback: Callable[[], None]) -> None: ...

    def run(self, work: Callable[["UnitOfWork"], T]) -> T: ...

    @property
    def retry_count(self) -> int: ...


class NotebookRepository(Protocol):
    workspace_id: str

    def create(self, name: str, description: str = "") -> Any: ...

    def get(self, notebook_id: int) -> Any | None: ...

    def list(self, search: str | None = None) -> list[Any]: ...

    def update(
        self,
        notebook_id: int,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Any: ...

    def delete(self, notebook_id: int) -> bool: ...

    def assign_document(self, document_id: int, notebook_id: int) -> Any: ...

    def remove_document(self, document_id: int) -> bool: ...

    def count_documents(self, notebook_id: int | None) -> int: ...

    def get_document(self, document_id: int) -> Any | None: ...

    def list_documents(
        self,
        *,
        notebook_id: int | None = None,
        unsorted_only: bool = False,
        search: str | None = None,
    ) -> list[Any]: ...

    def get_document_notebook_id(self, document_id: int) -> int | None: ...


class DocumentRepository(Protocol):
    workspace_id: str

    def find_by_hash(self, file_hash: str) -> Any | None: ...

    def insert(
        self,
        filename: str,
        mime_type: str,
        file_hash: str,
        file_data: bytes,
    ) -> int: ...

    def get(self, document_id: int) -> Any | None: ...

    def get_file_data(self, document_id: int) -> tuple[str, bytes]: ...

    def update_chunk_count(self, document_id: int, chunk_count: int) -> None: ...

    def delete(self, document_id: int) -> bool: ...

    def list(self) -> list[Any]: ...


class BlobStorage(Protocol):
    workspace_id: str

    def store(
        self,
        document_id: int,
        filename: str,
        mime_type: str,
        content_hash: str,
        data: bytes,
    ) -> BlobMetadata: ...

    def read(self, document_id: int) -> bytes: ...

    def delete(self, document_id: int) -> bool: ...

    def metadata(self, document_id: int) -> BlobMetadata | None: ...


class IntelligenceRepository(Protocol):
    workspace_id: str

    def get_cached(self, kind: str, scope_kind: str, scope_key: str) -> Any | None: ...

    def replace_cached(self, **values: Any) -> Any: ...

    def replace_topics(self, **values: Any) -> list[Any]: ...

    def get_topic(self, topic_id: str) -> Any | None: ...

    def list_topics(self, **filters: Any) -> list[Any]: ...

    def fingerprint_for_scope(self, scope_kind: str, scope_key: object = None) -> str: ...


class DashboardRepository(Protocol):
    workspace_id: str

    def build(self, recent_limit: int) -> dict[str, Any]: ...


class StudySessionRepository(Protocol):
    workspace_id: str

    def get_or_create_active(self) -> Any: ...

    def get_active(self) -> Any | None: ...

    def insert_interaction_with_sources(self, **values: Any) -> tuple[Any, list[Any]]: ...

    def get(self, session_id: int) -> Any | None: ...

    def get_interaction(self, interaction_id: int) -> Any | None: ...

    def list(self) -> list[Any]: ...

    def list_interactions(self, session_id: int) -> list[Any]: ...

    def list_sources(self, interaction_id: int) -> list[Any]: ...

    def update_outcome(self, interaction_id: int, outcome: str) -> Any: ...

    def end(self, session_id: int) -> Any: ...


class QuizRepository(Protocol):
    workspace_id: str

    def save_run_result(self, result: Any) -> tuple[Any, list[Any]]: ...

    def get_attempt(self, attempt_id: int) -> Any | None: ...

    def list_attempts(self, limit: int | None = None) -> list[Any]: ...

    def list_questions(self, attempt_id: int) -> list[Any]: ...

    def list_sources(self, question_attempt_id: int) -> list[Any]: ...


class LearnerMemoryRepository(Protocol):
    workspace_id: str

    def insert(self, **values: Any) -> int: ...

    def get(self, memory_id: int) -> Any | None: ...

    def get_many(self, memory_ids: list[int]) -> list[Any]: ...

    def list(self, include_archived: bool = False) -> list[Any]: ...

    def update(self, **values: Any) -> bool: ...

    def archive(self, memory_id: int) -> bool: ...

    def activate(self, memory_id: int) -> bool: ...

    def delete(self, memory_id: int) -> bool: ...

    def insert_relationships(self, **values: Any) -> None: ...

    def delete_relationships_for_target(self, memory_id: int) -> int: ...


class LearningSignalRepository(Protocol):
    workspace_id: str

    def create(
        self,
        signal_type: str,
        source_type: str,
        source_id: str,
        payload: dict[str, object],
        status: str = "pending",
        *,
        source_question_id: str | None = None,
        topic: str = "",
        statement: str = "",
        evidence: tuple[dict[str, object], ...] = (),
        confidence: float = 0.5,
        importance: float = 0.5,
        occurrence_count: int = 1,
        first_observed_at: str | None = None,
        last_observed_at: str | None = None,
        signal_key: str | None = None,
        memory_id: int | None = None,
        proposal_id: str | None = None,
    ) -> LearningSignal: ...

    def get(self, signal_id: str) -> LearningSignal | None: ...

    def find_by_key(self, signal_key: str) -> LearningSignal | None: ...

    def update(self, signal_id: str, **values: Any) -> LearningSignal: ...

    def list(
        self,
        status: str | None = None,
        *,
        topic: str | None = None,
        signal_types: tuple[str, ...] | None = None,
    ) -> list[LearningSignal]: ...

    def link_memory(
        self,
        signal_ids: tuple[str, ...],
        memory_id: int,
        proposal_id: str | None = None,
    ) -> None: ...


class AdaptationEventRepository(Protocol):
    workspace_id: str

    def create(
        self,
        workflow_type: str,
        request_id: str,
        memory_ids: tuple[int, ...],
        learning_signal_ids: tuple[str, ...],
        applied_changes: dict[str, object],
        reason: str,
    ) -> AdaptationEvent: ...

    def get(self, event_id: str) -> AdaptationEvent | None: ...

    def list(
        self,
        workflow_type: str | None = None,
        limit: int | None = None,
    ) -> list[AdaptationEvent]: ...


class WorkflowStateRepository(Protocol):
    workspace_id: str

    def put(
        self,
        workflow_id: str,
        workflow_type: str,
        payload: dict[str, object],
        expires_at: str,
    ) -> WorkflowState: ...

    def get(
        self,
        workflow_id: str,
        workflow_type: str | None = None,
        *,
        include_terminal: bool = False,
    ) -> WorkflowState | None: ...

    def decide(
        self,
        workflow_id: str,
        expected_version: int,
        status: str,
        metadata: dict[str, object] | None = None,
    ) -> WorkflowState: ...

    def replace_payload(
        self,
        workflow_id: str,
        expected_version: int,
        payload: dict[str, object],
        expires_at: str,
    ) -> WorkflowState: ...

    def list_pending(self, workflow_type: str) -> list[WorkflowState]: ...

    def count_pending(self, workflow_type: str) -> int: ...

    def clear_type(self, workflow_type: str) -> int: ...

    def cleanup_expired(self) -> int: ...

    def trim_pending(self, workflow_type: str, maximum: int) -> int: ...


class DocumentVectorRepository(Protocol):
    def stage_chunks(self, documents: list[Any], ids: list[str]) -> None: ...

    def upsert_chunks(self, documents: list[Any], ids: list[str]) -> None: ...

    def delete_document(self, document_id: int) -> None: ...

    def search(
        self,
        query: str,
        k: int,
        metadata_filter: dict[str, object] | None = None,
    ) -> list[tuple[Any, float]]: ...

    def list_chunks(
        self,
        metadata_filter: dict[str, object] | None = None,
    ) -> list[Any]: ...


class MemoryVectorRepository(Protocol):
    def upsert(
        self,
        memory_id: int,
        text: str,
        metadata: dict[str, object],
    ) -> None: ...

    def delete(self, memory_id: int) -> None: ...

    def search(
        self,
        query: str,
        k: int,
        metadata_filter: dict[str, object] | None = None,
    ) -> list[tuple[Any, float]]: ...


class VectorOutboxRepository(Protocol):
    workspace_id: str

    def enqueue(
        self,
        entity_type: str,
        entity_id: str,
        operation: str,
        payload: dict[str, object],
    ) -> VectorOutboxJob: ...

    def get(self, job_id: str) -> VectorOutboxJob | None: ...

    def list_retryable(self, limit: int = 100) -> list[VectorOutboxJob]: ...

    def mark_processing(self, job_id: str) -> VectorOutboxJob: ...

    def mark_completed(self, job_id: str) -> VectorOutboxJob: ...

    def mark_failed(self, job_id: str, error: str) -> VectorOutboxJob: ...


class WorkspaceRepository(Protocol):
    def ensure_default(self) -> Workspace: ...

    def create(self, workspace_id: str, name: str) -> Workspace: ...

    def get(self, workspace_id: str) -> Workspace | None: ...
