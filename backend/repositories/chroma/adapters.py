from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.documents import Document


class ChromaDocumentVectorRepository:
    """Chroma implementation hidden behind the document-vector contract."""

    def __init__(self, store_factory: Callable[[], Any]) -> None:
        self._store_factory = store_factory

    def stage_chunks(self, documents: list[Document], ids: list[str]) -> None:
        # Chroma has no relational staging table. Its durable SQLite outbox
        # retains the desired chunk state until post-commit synchronization.
        del documents, ids

    def upsert_chunks(self, documents: list[Document], ids: list[str]) -> None:
        if not ids:
            return
        store = self._store_factory()
        # Delete-first makes a replayed outbox operation idempotent.
        store.delete(ids=ids)
        store.add_documents(documents=documents, ids=ids)

    def delete_document(self, document_id: int) -> None:
        store = self._store_factory()
        result = store.get(where={"document_id": int(document_id)})
        ids = [str(value) for value in (result.get("ids") or [])]
        if ids:
            store.delete(ids=ids)

    def search(
        self,
        query: str,
        k: int,
        metadata_filter: dict[str, object] | None = None,
    ) -> list[tuple[Document, float]]:
        store = self._store_factory()
        if metadata_filter is None:
            return store.similarity_search_with_score(query=query, k=k)
        return store.similarity_search_with_score(
            query=query,
            k=k,
            filter=metadata_filter,
        )

    def list_chunks(
        self,
        metadata_filter: dict[str, object] | None = None,
    ) -> list[Document]:
        store = self._store_factory()
        arguments: dict[str, Any] = {"include": ["documents", "metadatas"]}
        if metadata_filter is not None:
            arguments["where"] = metadata_filter
        try:
            raw = store.get(**arguments)
        except TypeError:
            arguments.pop("include", None)
            raw = store.get(**arguments)
        texts = list(raw.get("documents") or [])
        metadatas = list(raw.get("metadatas") or [])
        if len(texts) != len(metadatas):
            raise ValueError("Indexed source metadata is incomplete.")
        return [
            Document(
                page_content=str(text or ""),
                metadata=dict(metadata or {}),
            )
            for text, metadata in zip(texts, metadatas, strict=True)
        ]


class ChromaMemoryVectorRepository:
    """Chroma implementation hidden behind the learner-memory contract."""

    def __init__(self, store_factory: Callable[[], Any]) -> None:
        self._store_factory = store_factory

    @staticmethod
    def vector_id(memory_id: int) -> str:
        return f"memory-{int(memory_id)}"

    def upsert(
        self,
        memory_id: int,
        text: str,
        metadata: dict[str, object],
    ) -> None:
        store = self._store_factory()
        vector_id = self.vector_id(memory_id)
        store.delete(ids=[vector_id])
        store.add_documents(
            documents=[Document(page_content=text, metadata=dict(metadata))],
            ids=[vector_id],
        )

    def delete(self, memory_id: int) -> None:
        self._store_factory().delete(ids=[self.vector_id(memory_id)])

    def search(
        self,
        query: str,
        k: int,
        metadata_filter: dict[str, object] | None = None,
    ) -> list[tuple[Document, float]]:
        store = self._store_factory()
        if metadata_filter is None:
            return store.similarity_search_with_score(query=query, k=k)
        return store.similarity_search_with_score(
            query=query,
            k=k,
            filter=metadata_filter,
        )
