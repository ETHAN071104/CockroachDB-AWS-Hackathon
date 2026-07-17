from __future__ import annotations

from functools import lru_cache

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings

from rag.config import (
    CHROMA_COLLECTION,
    CHROMA_PATH,
    EMBEDDING_MODEL,
)


@lru_cache(maxsize=1)
def get_embedding_model() -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={
            "device": "cpu",
        },
        encode_kwargs={
            "normalize_embeddings": True,
        },
    )


@lru_cache(maxsize=1)
def get_vector_store() -> Chroma:
    return Chroma(
        collection_name=CHROMA_COLLECTION,
        embedding_function=get_embedding_model(),
        persist_directory=str(CHROMA_PATH),
    )


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