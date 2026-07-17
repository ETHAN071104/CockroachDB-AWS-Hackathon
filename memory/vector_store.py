from __future__ import annotations

from functools import lru_cache

from langchain_chroma import Chroma

from rag.config import (
    MEMORY_CHROMA_COLLECTION,
    MEMORY_CHROMA_PATH,
)
from rag.vector_store import get_embedding_model


@lru_cache(maxsize=1)
def get_memory_vector_store() -> Chroma:
    return Chroma(
        collection_name=MEMORY_CHROMA_COLLECTION,
        embedding_function=get_embedding_model(),
        persist_directory=str(MEMORY_CHROMA_PATH),
    )


def make_memory_vector_id(memory_id: int) -> str:
    return f"memory-{memory_id}"


def delete_memory_vector(memory_id: int) -> None:
    vector_store = get_memory_vector_store()

    vector_store.delete(
        ids=[make_memory_vector_id(memory_id)]
    )