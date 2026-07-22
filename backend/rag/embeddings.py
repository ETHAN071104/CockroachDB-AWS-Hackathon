from __future__ import annotations

import math
from functools import lru_cache
from typing import TYPE_CHECKING, Sequence

from backend.rag.config import EMBEDDING_DIMENSION, EMBEDDING_MODEL

if TYPE_CHECKING:
    from langchain_huggingface import HuggingFaceEmbeddings


@lru_cache(maxsize=1)
def get_embedding_model() -> "HuggingFaceEmbeddings":
    from langchain_huggingface import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
        encode_kwargs={"normalize_embeddings": True},
    )


def validate_embedding(values: Sequence[float]) -> list[float]:
    embedding = [float(value) for value in values]
    if len(embedding) != EMBEDDING_DIMENSION:
        raise ValueError(
            f"Embedding dimension {len(embedding)} does not match "
            f"configured dimension {EMBEDDING_DIMENSION}."
        )
    if not all(math.isfinite(value) for value in embedding):
        raise ValueError("Embedding contains a non-finite value.")
    return embedding


def encode_documents(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    return [
        validate_embedding(values)
        for values in get_embedding_model().embed_documents(texts)
    ]


def encode_query(query: str) -> list[float]:
    return validate_embedding(get_embedding_model().embed_query(query))


def vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(format(float(value), ".17g") for value in values) + "]"
