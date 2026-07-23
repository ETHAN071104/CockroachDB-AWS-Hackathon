from __future__ import annotations

from dataclasses import dataclass

from langchain_core.documents import Document

from backend.domain import VectorOutboxJob
from backend.repositories.interfaces import (
    DocumentVectorRepository,
    MemoryVectorRepository,
    VectorOutboxRepository,
)


class VectorSynchronizationError(RuntimeError):
    """Relational state committed, but its durable vector job failed."""

    def __init__(self, job_id: str, message: str) -> None:
        super().__init__(f"{message} Vector outbox job: {job_id}.")
        self.job_id = job_id


@dataclass(frozen=True)
class ReconciliationResult:
    attempted: int
    completed: int
    failed: int
    failed_job_ids: tuple[str, ...]


class VectorOutboxService:
    def __init__(
        self,
        repository: VectorOutboxRepository,
        document_vectors: DocumentVectorRepository,
        memory_vectors: MemoryVectorRepository,
    ) -> None:
        self.repository = repository
        self.document_vectors = document_vectors
        self.memory_vectors = memory_vectors

    def process(self, job_id: str) -> VectorOutboxJob:
        job = self.repository.get(job_id)
        if job is None:
            raise KeyError(f"Vector outbox job {job_id} does not exist.")
        if job.status == "completed":
            return job

        processing = self.repository.mark_processing(job_id)
        try:
            self._apply(processing)
        except Exception as error:
            self.repository.mark_failed(
                job_id,
                f"{type(error).__name__}: vector synchronization failed",
            )
            raise VectorSynchronizationError(
                job_id,
                "The relational change was saved, but vector synchronization failed.",
            ) from error
        return self.repository.mark_completed(job_id)

    def reconcile(self, limit: int = 100) -> ReconciliationResult:
        jobs = self.repository.list_retryable(limit=limit)
        completed = 0
        failed_ids: list[str] = []
        for job in jobs:
            try:
                self.process(job.id)
                completed += 1
            except VectorSynchronizationError:
                failed_ids.append(job.id)
        return ReconciliationResult(
            attempted=len(jobs),
            completed=completed,
            failed=len(failed_ids),
            failed_job_ids=tuple(failed_ids),
        )

    def _apply(self, job: VectorOutboxJob) -> None:
        if job.entity_type == "document":
            if job.operation == "delete":
                self.document_vectors.delete_document(int(job.entity_id))
                return
            chunks = job.payload.get("chunks")
            ids = job.payload.get("ids")
            if not isinstance(chunks, list) or not isinstance(ids, list):
                raise ValueError("Document vector outbox payload is incomplete.")
            documents: list[Document] = []
            for raw_chunk in chunks:
                if not isinstance(raw_chunk, dict):
                    raise ValueError("Document chunk payload is invalid.")
                text = raw_chunk.get("text")
                metadata = raw_chunk.get("metadata")
                if not isinstance(text, str) or not isinstance(metadata, dict):
                    raise ValueError("Document chunk payload is invalid.")
                documents.append(Document(page_content=text, metadata=metadata))
            self.document_vectors.upsert_chunks(documents, [str(value) for value in ids])
            return

        if job.entity_type == "memory":
            memory_id = int(job.entity_id)
            if job.operation == "delete":
                self.memory_vectors.delete(memory_id)
                return
            text = job.payload.get("text")
            metadata = job.payload.get("metadata")
            if not isinstance(text, str) or not isinstance(metadata, dict):
                raise ValueError("Memory vector outbox payload is incomplete.")
            self.memory_vectors.upsert(memory_id, text, metadata)
            return

        raise ValueError(f"Unsupported vector entity type: {job.entity_type}")


def build_default_outbox_service() -> VectorOutboxService:
    from backend.application.dependencies import get_application_dependencies

    dependencies = get_application_dependencies()
    return VectorOutboxService(
        repository=dependencies.vector_outbox,
        document_vectors=dependencies.document_vectors,
        memory_vectors=dependencies.memory_vectors,
    )


def reconcile_pending_vectors(limit: int = 100) -> ReconciliationResult:
    """Public reconciliation service for CLI, startup jobs, and operators."""
    return build_default_outbox_service().reconcile(limit=limit)
