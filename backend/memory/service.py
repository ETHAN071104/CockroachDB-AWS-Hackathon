from __future__ import annotations

from dataclasses import dataclass

from langchain_core.documents import Document

from backend.application.dependencies import get_application_dependencies
from backend.application.vector_outbox import VectorOutboxService
from backend.memory.consolidator import MemoryConsolidationProposal
from backend.memory.database import StoredMemory
from backend.memory.vector_store import (
    delete_memory_vector,
    get_memory_vector_store,
    make_memory_vector_id,
)
from backend.rag.config import MAX_MEMORY_DISTANCE, MEMORY_RETRIEVAL_K
from backend.repositories.chroma import ChromaMemoryVectorRepository


@dataclass(frozen=True)
class MemoryConsolidationResult:
    source_memories: tuple[StoredMemory, ...]
    consolidated_memory: StoredMemory


@dataclass(frozen=True)
class MemoryReplacementResult:
    archived_memory: StoredMemory
    new_memory: StoredMemory


@dataclass(frozen=True)
class MemorySearchResult:
    memory_id: int
    memory_type: str
    content: str
    confidence: float
    importance: float
    distance: float


def memory_to_document(memory: StoredMemory) -> Document:
    return Document(
        page_content=memory.content,
        metadata=_memory_metadata(memory),
    )


def _memory_metadata(memory: StoredMemory) -> dict[str, object]:
    dependencies = get_application_dependencies()
    return {
        "memory_id": memory.id,
        "memory_type": memory.memory_type,
        "confidence": memory.confidence,
        "importance": memory.importance,
        "status": memory.status,
        "workspace_id": dependencies.workspace_id,
    }


def _memory_vector_repository() -> ChromaMemoryVectorRepository:
    # Keep the legacy factory as the compatibility seam used by tests and
    # local installations while business code depends on the repository API.
    return ChromaMemoryVectorRepository(get_memory_vector_store)


def _outbox_service() -> VectorOutboxService:
    dependencies = get_application_dependencies()
    return VectorOutboxService(
        repository=dependencies.vector_outbox,
        document_vectors=dependencies.document_vectors,
        memory_vectors=_memory_vector_repository(),
    )


def _enqueue_memory_upsert(memory: StoredMemory) -> None:
    dependencies = get_application_dependencies()
    job = dependencies.vector_outbox.enqueue(
        "memory",
        str(memory.id),
        "upsert",
        {"text": memory.content, "metadata": _memory_metadata(memory)},
    )
    service = _outbox_service()
    active_uow = dependencies.unit_of_work()
    # This helper is always called from the active root transaction. A nested
    # UoW joins it and attaches the callback to the root commit boundary.
    with active_uow as joined:
        joined.after_commit(lambda: service.process(job.id))


def _enqueue_memory_delete(memory_id: int) -> None:
    dependencies = get_application_dependencies()
    job = dependencies.vector_outbox.enqueue(
        "memory",
        str(memory_id),
        "delete",
        {},
    )
    service = _outbox_service()
    with dependencies.unit_of_work() as joined:
        joined.after_commit(lambda: service.process(job.id))


def add_memory(
    memory_type: str,
    content: str,
    confidence: float = 1.0,
    importance: float = 0.5,
) -> StoredMemory:
    dependencies = get_application_dependencies()
    with dependencies.unit_of_work():
        memory_id = dependencies.memories.insert(
            memory_type=memory_type,
            content=content,
            confidence=confidence,
            importance=importance,
        )
        memory = dependencies.memories.get(memory_id)
        if memory is None:
            raise RuntimeError("Memory was inserted but could not be loaded.")
        _enqueue_memory_upsert(memory)
    return memory


def search_memories(
    query: str,
    k: int = MEMORY_RETRIEVAL_K,
) -> list[MemorySearchResult]:
    cleaned_query = query.strip()
    if not cleaned_query:
        raise ValueError("Search query cannot be empty.")
    if k <= 0:
        raise ValueError("Search result count must be greater than zero.")

    dependencies = get_application_dependencies()
    results = _memory_vector_repository().search(
        cleaned_query,
        k,
        {"status": "active"},
    )
    search_results: list[MemorySearchResult] = []
    for document, distance in results:
        numeric_distance = float(distance)
        if numeric_distance > MAX_MEMORY_DISTANCE:
            continue
        try:
            memory_id = int(document.metadata.get("memory_id"))
        except (TypeError, ValueError):
            continue
        current_memory = dependencies.memories.get(memory_id)
        if current_memory is None or current_memory.status != "active":
            continue
        search_results.append(
            MemorySearchResult(
                memory_id=current_memory.id,
                memory_type=current_memory.memory_type,
                content=current_memory.content,
                confidence=current_memory.confidence,
                importance=current_memory.importance,
                distance=numeric_distance,
            )
        )
    return search_results


def get_all_memories(include_archived: bool = False) -> list[StoredMemory]:
    return get_application_dependencies().memories.list(
        include_archived=include_archived
    )


def update_memory(
    memory_id: int,
    memory_type: str,
    content: str,
    confidence: float,
    importance: float,
) -> StoredMemory:
    dependencies = get_application_dependencies()
    with dependencies.unit_of_work():
        if dependencies.memories.get(memory_id) is None:
            raise ValueError(f"Memory ID {memory_id} does not exist.")
        updated = dependencies.memories.update(
            memory_id=memory_id,
            memory_type=memory_type,
            content=content,
            confidence=confidence,
            importance=importance,
        )
        if not updated:
            raise RuntimeError(f"Memory ID {memory_id} could not be updated.")
        updated_memory = dependencies.memories.get(memory_id)
        if updated_memory is None:
            raise RuntimeError("Memory was updated but could not be loaded.")
        _enqueue_memory_upsert(updated_memory)
    return updated_memory


def archive_memory(memory_id: int) -> bool:
    dependencies = get_application_dependencies()
    with dependencies.unit_of_work():
        existing = dependencies.memories.get(memory_id)
        if existing is None:
            return False
        if existing.status == "archived":
            return True
        if not dependencies.memories.archive(memory_id):
            return False
        _enqueue_memory_delete(memory_id)
    return True


def restore_archived_memory(memory_id: int) -> StoredMemory:
    dependencies = get_application_dependencies()
    with dependencies.unit_of_work():
        existing = dependencies.memories.get(memory_id)
        if existing is None:
            raise ValueError(f"Memory ID {memory_id} does not exist.")
        if existing.status == "active":
            return existing
        if not dependencies.memories.activate(memory_id):
            raise RuntimeError(f"Memory ID {memory_id} could not be reactivated.")
        active_memory = dependencies.memories.get(memory_id)
        if active_memory is None:
            raise RuntimeError("Memory was reactivated but could not be loaded.")
        _enqueue_memory_upsert(active_memory)
    return active_memory


def validate_current_consolidation_sources(
    proposal: MemoryConsolidationProposal,
) -> list[StoredMemory]:
    snapshots = list(proposal.source_memories)
    if len(snapshots) < 2:
        raise ValueError("A consolidation requires at least two source memories.")
    source_ids = [memory.id for memory in snapshots]
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("A consolidation proposal contains duplicate source IDs.")

    current_memories = get_application_dependencies().memories.get_many(source_ids)
    if len(current_memories) != len(source_ids):
        raise RuntimeError("One or more consolidation source memories no longer exist.")
    current_by_id = {memory.id: memory for memory in current_memories}
    ordered: list[StoredMemory] = []
    for snapshot in snapshots:
        current = current_by_id[snapshot.id]
        if current.status != "active":
            raise RuntimeError(f"Memory ID {current.id} is no longer active.")
        if current.memory_type != snapshot.memory_type:
            raise RuntimeError(
                f"Memory ID {current.id} changed type after the proposal was generated."
            )
        if current.content != snapshot.content:
            raise RuntimeError(
                f"Memory ID {current.id} changed content after the proposal was generated."
            )
        if current.updated_at != snapshot.updated_at:
            raise RuntimeError(
                f"Memory ID {current.id} was updated after the proposal was generated."
            )
        ordered.append(current)

    if len({memory.memory_type for memory in ordered}) != 1:
        raise RuntimeError("The consolidation sources no longer share one memory type.")
    candidate = proposal.candidate
    if not candidate.should_consolidate:
        raise ValueError("A rejected consolidation proposal cannot be applied.")
    if candidate.memory_type != ordered[0].memory_type:
        raise ValueError(
            "The consolidated memory type does not match its source memories."
        )
    if not candidate.content.strip():
        raise ValueError("The consolidated memory content cannot be empty.")
    return ordered


def apply_memory_consolidation(
    proposal: MemoryConsolidationProposal,
) -> MemoryConsolidationResult:
    dependencies = get_application_dependencies()
    with dependencies.unit_of_work():
        sources = validate_current_consolidation_sources(proposal)
        candidate = proposal.candidate
        target = add_memory(
            candidate.memory_type,
            candidate.content,
            candidate.confidence,
            candidate.importance,
        )
        for source in sources:
            if not archive_memory(source.id):
                raise RuntimeError(f"Could not archive source memory ID {source.id}.")
        dependencies.memories.insert_relationships(
            source_memory_ids=[memory.id for memory in sources],
            target_memory_id=target.id,
            relationship_type="consolidated_into",
        )
    return MemoryConsolidationResult(tuple(sources), target)


def replace_memory_with_candidate(
    existing_memory_id: int,
    memory_type: str,
    content: str,
    confidence: float,
    importance: float,
) -> MemoryReplacementResult:
    dependencies = get_application_dependencies()
    with dependencies.unit_of_work():
        existing = dependencies.memories.get(existing_memory_id)
        if existing is None:
            raise ValueError(f"Memory ID {existing_memory_id} does not exist.")
        if existing.status != "active":
            raise ValueError(f"Memory ID {existing_memory_id} is not active.")
        if existing.memory_type != memory_type:
            raise ValueError("The replacement memory must have the same type.")
        new_memory = add_memory(
            memory_type,
            content,
            confidence,
            importance,
        )
        if not archive_memory(existing_memory_id):
            raise RuntimeError("The existing memory could not be archived.")
    return MemoryReplacementResult(existing, new_memory)


def delete_memory(memory_id: int) -> bool:
    dependencies = get_application_dependencies()
    with dependencies.unit_of_work():
        if dependencies.memories.get(memory_id) is None:
            return False
        if not dependencies.memories.delete(memory_id):
            return False
        _enqueue_memory_delete(memory_id)
    return True
