from __future__ import annotations

import backend.rag.vector_store as vector_store_service

from backend.application.dependencies import get_application_dependencies
from backend.application.vector_outbox import (
    VectorOutboxService,
    VectorSynchronizationError,
)
from backend.rag.database import StoredDocument
from backend.repositories.chroma import ChromaDocumentVectorRepository


class DocumentDeletionError(RuntimeError):
    """Raised when a committed deletion still needs vector reconciliation."""


def delete_document(document_id: int) -> StoredDocument:
    """Delete relational state atomically and synchronize vectors via outbox."""
    if (
        not isinstance(document_id, int)
        or isinstance(document_id, bool)
        or document_id <= 0
    ):
        raise ValueError("Document ID must be a positive integer.")

    dependencies = get_application_dependencies()
    document = dependencies.documents.get(document_id)
    if document is None:
        raise ValueError(f"Document ID {document_id} does not exist.")

    outbox = VectorOutboxService(
        dependencies.vector_outbox,
        ChromaDocumentVectorRepository(vector_store_service.get_vector_store),
        dependencies.memory_vectors,
    )
    try:
        with dependencies.unit_of_work() as unit_of_work:
            if not dependencies.documents.delete(document_id):
                raise DocumentDeletionError(
                    "The document changed before it could be deleted."
                )
            job = dependencies.vector_outbox.enqueue(
                "document",
                str(document_id),
                "delete",
                {},
            )
            unit_of_work.after_commit(lambda: outbox.process(job.id))
    except VectorSynchronizationError as error:
        raise DocumentDeletionError(
            "The document was deleted, but vector cleanup is pending durable "
            f"reconciliation (job {error.job_id})."
        ) from error
    return document
