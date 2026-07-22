from __future__ import annotations

import sqlite3
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings

from backend.rag.config import (
    CHROMA_COLLECTION,
    CHROMA_PATH,
    EMBEDDING_MODEL,
)
from backend.rag.embeddings import get_embedding_model


@lru_cache(maxsize=1)
def get_vector_store() -> "Chroma":
    from langchain_chroma import Chroma

    return Chroma(
        collection_name=CHROMA_COLLECTION,
        embedding_function=get_embedding_model(),
        persist_directory=str(CHROMA_PATH),
    )


def probe_vector_store() -> dict[str, Any]:
    """Verify Chroma persistence without loading embeddings."""
    return probe_chroma_store(CHROMA_PATH, CHROMA_COLLECTION)


def probe_chroma_store(
    persist_directory: Path,
    collection_name: str,
) -> dict[str, Any]:
    """Read Chroma metadata without opening or mutating Chroma."""
    database_path = persist_directory / "chroma.sqlite3"

    if not database_path.exists():
        return {
            "status": "ok",
            "collection_present": False,
        }

    database_uri = f"{database_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(database_uri, uri=True) as connection:
        row = connection.execute(
            "SELECT 1 FROM collections WHERE name = ? LIMIT 1",
            (collection_name,),
        ).fetchone()

    return {
        "status": "ok",
        "collection_present": row is not None,
    }


def delete_document_vectors(document_id: int) -> None:
    vector_store = get_vector_store()

    collection_data = vector_store.get(
        where={
            "document_id": document_id,
        }
    )

    ids = collection_data.get("ids", [])

    if ids:
        vector_store.delete(ids=ids)
