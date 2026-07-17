from __future__ import annotations

from dataclasses import dataclass

from langchain_core.documents import Document

from memory.database import (
    StoredMemory,
    archive_memory_record,
    delete_memory_record,
    get_memory,
    insert_memory,
    list_memories,
    update_memory_record,
)
from memory.vector_store import (
    delete_memory_vector,
    get_memory_vector_store,
    make_memory_vector_id,
)
from rag.config import (
    MAX_MEMORY_DISTANCE,
    MEMORY_RETRIEVAL_K,
)

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
    """
    Convert a SQLite memory record into a LangChain Document
    that can be embedded and stored in Chroma.
    """
    return Document(
        page_content=memory.content,
        metadata={
            "memory_id": memory.id,
            "memory_type": memory.memory_type,
            "confidence": memory.confidence,
            "importance": memory.importance,
            "status": memory.status,
        },
    )


def add_memory(
    memory_type: str,
    content: str,
    confidence: float = 1.0,
    importance: float = 0.5,
) -> StoredMemory:
    """
    Add a memory to SQLite, then embed and store it in Chroma.
    """
    memory_id = insert_memory(
        memory_type=memory_type,
        content=content,
        confidence=confidence,
        importance=importance,
    )

    memory = get_memory(memory_id)

    if memory is None:
        delete_memory_record(memory_id)

        raise RuntimeError(
            "Memory was inserted into SQLite but could not be loaded."
        )

    try:
        vector_store = get_memory_vector_store()

        vector_store.add_documents(
            documents=[memory_to_document(memory)],
            ids=[make_memory_vector_id(memory.id)],
        )

        return memory

    except Exception:
        # Roll back SQLite if vector indexing fails.
        delete_memory_record(memory_id)
        raise


def search_memories(
    query: str,
    k: int = MEMORY_RETRIEVAL_K,
) -> list[MemorySearchResult]:
    """
    Search active memories using semantic similarity.
    """
    cleaned_query = query.strip()

    if not cleaned_query:
        raise ValueError("Search query cannot be empty.")

    if k <= 0:
        raise ValueError("Search result count must be greater than zero.")

    vector_store = get_memory_vector_store()

    results = vector_store.similarity_search_with_score(
        query=cleaned_query,
        k=k,
        filter={
            "status": "active",
        },
    )

    search_results: list[MemorySearchResult] = []

    for document, distance in results:
        numeric_distance = float(distance)

        if numeric_distance > MAX_MEMORY_DISTANCE:
            continue

        memory_id_value = document.metadata.get("memory_id")

        if memory_id_value is None:
            continue

        try:
            memory_id = int(memory_id_value)
        except (TypeError, ValueError):
            continue

        current_memory = get_memory(memory_id)

        if current_memory is None:
            continue

        if current_memory.status != "active":
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


def get_all_memories(
    include_archived: bool = False,
) -> list[StoredMemory]:
    """
    Return stored memories from SQLite.
    """
    return list_memories(
        include_archived=include_archived,
    )


def update_memory(
    memory_id: int,
    memory_type: str,
    content: str,
    confidence: float,
    importance: float,
) -> StoredMemory:
    """
    Update a memory in SQLite and replace its Chroma vector.
    """
    existing_memory = get_memory(memory_id)

    if existing_memory is None:
        raise ValueError(
            f"Memory ID {memory_id} does not exist."
        )

    updated = update_memory_record(
        memory_id=memory_id,
        memory_type=memory_type,
        content=content,
        confidence=confidence,
        importance=importance,
    )

    if not updated:
        raise RuntimeError(
            f"Memory ID {memory_id} could not be updated."
        )

    updated_memory = get_memory(memory_id)

    if updated_memory is None:
        raise RuntimeError(
            "Memory was updated but could not be loaded."
        )

    vector_store = get_memory_vector_store()
    vector_id = make_memory_vector_id(memory_id)

    try:
        vector_store.delete(
            ids=[vector_id],
        )

        vector_store.add_documents(
            documents=[memory_to_document(updated_memory)],
            ids=[vector_id],
        )

    except Exception as error:
        raise RuntimeError(
            "SQLite was updated, but the memory vector "
            f"could not be replaced: {error}"
        ) from error

    return updated_memory


def archive_memory(memory_id: int) -> bool:
    """
    Mark a memory as archived and remove it from semantic search.

    The vector is removed first. If the SQLite update fails,
    the vector is restored so both stores remain consistent.
    """
    existing_memory = get_memory(memory_id)

    if existing_memory is None:
        return False

    if existing_memory.status == "archived":
        return True

    vector_store = get_memory_vector_store()
    vector_id = make_memory_vector_id(memory_id)

    # Remove the active vector first.
    try:
        vector_store.delete(
            ids=[vector_id],
        )

    except Exception as error:
        raise RuntimeError(
            "The memory vector could not be removed, so the "
            f"SQLite record was not archived: {error}"
        ) from error

    # Archive the SQLite record.
    try:
        archived = archive_memory_record(memory_id)

    except Exception as error:
        try:
            vector_store.add_documents(
                documents=[
                    memory_to_document(existing_memory)
                ],
                ids=[vector_id],
            )

        except Exception as restore_error:
            raise RuntimeError(
                "SQLite archiving failed and the original "
                "memory vector could not be restored. "
                f"Archive error: {error}. "
                f"Restore error: {restore_error}"
            ) from error

        raise RuntimeError(
            "SQLite archiving failed. The original vector "
            "was restored."
        ) from error

    if not archived:
        try:
            vector_store.add_documents(
                documents=[
                    memory_to_document(existing_memory)
                ],
                ids=[vector_id],
            )

        except Exception as restore_error:
            raise RuntimeError(
                "The memory record was not archived and its "
                f"vector could not be restored: {restore_error}"
            ) from restore_error

        return False

    return True

def replace_memory_with_candidate(
    existing_memory_id: int,
    memory_type: str,
    content: str,
    confidence: float,
    importance: float,
) -> MemoryReplacementResult:
    """
    Save a new active memory and archive the existing memory.

    The old record remains in SQLite as history, but its vector
    is removed from active semantic retrieval.
    """
    existing_memory = get_memory(
        existing_memory_id
    )

    if existing_memory is None:
        raise ValueError(
            f"Memory ID {existing_memory_id} does not exist."
        )

    if existing_memory.status != "active":
        raise ValueError(
            f"Memory ID {existing_memory_id} is not active."
        )

    if existing_memory.memory_type != memory_type:
        raise ValueError(
            "The replacement memory must have the same type "
            "as the existing memory."
        )

    # Save the new memory first. If this fails, the existing
    # memory remains untouched.
    new_memory = add_memory(
        memory_type=memory_type,
        content=content,
        confidence=confidence,
        importance=importance,
    )

    try:
        archived = archive_memory(
            existing_memory_id
        )

        if not archived:
            raise RuntimeError(
                "The existing memory could not be archived."
            )

    except Exception as archive_error:
        # Remove the newly created memory if replacement fails.
        try:
            delete_memory(new_memory.id)

        except Exception as cleanup_error:
            raise RuntimeError(
                "Replacement failed and cleanup of the new "
                f"memory also failed. New memory ID: "
                f"{new_memory.id}. Archive error: "
                f"{archive_error}. Cleanup error: "
                f"{cleanup_error}"
            ) from archive_error

        raise RuntimeError(
            "Replacement failed. The newly created memory was "
            "removed and the existing memory was retained."
        ) from archive_error

    return MemoryReplacementResult(
        archived_memory=existing_memory,
        new_memory=new_memory,
    )

def delete_memory(memory_id: int) -> bool:
    """
    Permanently delete a memory from Chroma and SQLite.
    """
    existing_memory = get_memory(memory_id)

    if existing_memory is None:
        return False

    # Remove vector first. If this fails, retain the SQLite record.
    delete_memory_vector(memory_id)

    return delete_memory_record(memory_id)