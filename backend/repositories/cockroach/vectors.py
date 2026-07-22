from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.documents import Document
from sqlalchemy import text

from backend.domain import DEFAULT_WORKSPACE_ID, deterministic_legacy_uuid
from backend.rag import config
from backend.rag.embeddings import encode_documents, encode_query, vector_literal
from backend.repositories.cockroach.connection import connection_scope
from backend.repositories.cockroach.helpers import content_sha256, json_text, utc_now, uuid_for_public


class CockroachDocumentVectorRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def stage_chunks(self, documents: list[Document], ids: list[str]) -> None:
        self._write_chunks(documents, ids, embeddings=None)

    def upsert_chunks(self, documents: list[Document], ids: list[str]) -> None:
        if len(documents) != len(ids):
            raise ValueError("Document chunk and ID counts differ.")
        embeddings = encode_documents([document.page_content for document in documents])
        self._write_chunks(documents, ids, embeddings=embeddings)

    def _write_chunks(
        self,
        documents: list[Document],
        ids: list[str],
        embeddings: list[list[float]] | None,
    ) -> None:
        if len(documents) != len(ids):
            raise ValueError("Document chunk and ID counts differ.")
        with connection_scope() as connection:
            for index, (document, external_id) in enumerate(zip(documents, ids, strict=True)):
                metadata = dict(document.metadata)
                public_document_id = int(metadata["document_id"])
                document_uuid = uuid_for_public(
                    "documents", self.workspace_id, public_document_id
                )
                if document_uuid is None:
                    raise KeyError(f"Document ID {public_document_id} does not exist.")
                chunk_index = int(metadata.get("chunk_index", index))
                record_id = deterministic_legacy_uuid(
                    self.workspace_id, "document_chunks", external_id
                )
                values = {
                    "id": record_id,
                    "workspace_id": UUID(self.workspace_id),
                    "document_id": document_uuid,
                    "chunk_index": chunk_index,
                    "content": document.page_content,
                    "page_number": metadata.get("page_number"),
                    "slide_number": metadata.get("slide_number"),
                    "filename": str(metadata.get("filename") or "Unknown file"),
                    "mime_type": str(metadata.get("mime_type") or "application/octet-stream"),
                    "metadata": json_text(metadata),
                    "embedding_model": config.EMBEDDING_MODEL,
                    "embedding_version": config.EMBEDDING_MODEL,
                    "content_hash": content_sha256(document.page_content),
                    "created_at": utc_now(),
                    "updated_at": utc_now(),
                }
                if embeddings is None:
                    connection.execute(
                        text(
                            """
                            INSERT INTO document_chunks (
                                id,workspace_id,document_id,chunk_index,content,
                                page_number,slide_number,filename_snapshot,mime_type,
                                metadata,embedding,embedding_model,embedding_version,
                                content_hash,created_at,updated_at
                            ) VALUES (
                                :id,:workspace_id,:document_id,:chunk_index,:content,
                                :page_number,:slide_number,:filename,:mime_type,
                                CAST(:metadata AS JSONB),NULL,:embedding_model,
                                :embedding_version,:content_hash,:created_at,:updated_at
                            ) ON CONFLICT (workspace_id,document_id,chunk_index) DO UPDATE SET
                                content=excluded.content,page_number=excluded.page_number,
                                slide_number=excluded.slide_number,
                                filename_snapshot=excluded.filename_snapshot,
                                mime_type=excluded.mime_type,metadata=excluded.metadata,
                                embedding=NULL,embedding_model=excluded.embedding_model,
                                embedding_version=excluded.embedding_version,
                                content_hash=excluded.content_hash,updated_at=excluded.updated_at
                            """
                        ), values,
                    )
                else:
                    values["embedding"] = vector_literal(embeddings[index])
                    connection.execute(
                        text(
                            """
                            INSERT INTO document_chunks (
                                id,workspace_id,document_id,chunk_index,content,
                                page_number,slide_number,filename_snapshot,mime_type,
                                metadata,embedding,embedding_model,embedding_version,
                                content_hash,created_at,updated_at
                            ) VALUES (
                                :id,:workspace_id,:document_id,:chunk_index,:content,
                                :page_number,:slide_number,:filename,:mime_type,
                                CAST(:metadata AS JSONB),CAST(:embedding AS VECTOR(384)),
                                :embedding_model,:embedding_version,:content_hash,
                                :created_at,:updated_at
                            ) ON CONFLICT (workspace_id,document_id,chunk_index) DO UPDATE SET
                                content=excluded.content,page_number=excluded.page_number,
                                slide_number=excluded.slide_number,
                                filename_snapshot=excluded.filename_snapshot,
                                mime_type=excluded.mime_type,metadata=excluded.metadata,
                                embedding=excluded.embedding,embedding_model=excluded.embedding_model,
                                embedding_version=excluded.embedding_version,
                                content_hash=excluded.content_hash,updated_at=excluded.updated_at
                            """
                        ), values,
                    )

    def delete_document(self, document_id: int) -> None:
        document_uuid = uuid_for_public("documents", self.workspace_id, document_id)
        if document_uuid is None:
            return
        with connection_scope() as connection:
            connection.execute(
                text(
                    "DELETE FROM document_chunks "
                    "WHERE workspace_id=:workspace_id AND document_id=:document_id"
                ), {"workspace_id": UUID(self.workspace_id), "document_id": document_uuid},
            )

    def search(
        self,
        query: str,
        k: int,
        metadata_filter: dict[str, object] | None = None,
    ) -> list[tuple[Document, float]]:
        embedding = vector_literal(encode_query(query))
        scope_sql, parameters = self._scope(metadata_filter)
        parameters.update(
            workspace_id=UUID(self.workspace_id), embedding=embedding, limit=int(k)
        )
        with connection_scope() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT c.*, d.public_id AS document_public_id,
                           c.embedding <=> CAST(:embedding AS VECTOR(384)) AS distance
                    FROM document_chunks c JOIN documents d ON d.id=c.document_id
                    WHERE c.workspace_id=:workspace_id AND c.embedding IS NOT NULL
                    """ + scope_sql
                    + " ORDER BY distance ASC, d.public_id ASC, c.chunk_index ASC LIMIT :limit"
                ), parameters,
            ).mappings().all()
        return [(self._document(row), float(row["distance"])) for row in rows]

    def list_chunks(
        self,
        metadata_filter: dict[str, object] | None = None,
    ) -> list[Document]:
        scope_sql, parameters = self._scope(metadata_filter)
        parameters["workspace_id"] = UUID(self.workspace_id)
        with connection_scope() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT c.*, d.public_id AS document_public_id
                    FROM document_chunks c JOIN documents d ON d.id=c.document_id
                    WHERE c.workspace_id=:workspace_id
                    """ + scope_sql
                    + " ORDER BY d.public_id ASC, c.chunk_index ASC"
                ), parameters,
            ).mappings().all()
        return [self._document(row) for row in rows]

    def _scope(self, metadata_filter: dict[str, object] | None):
        if not metadata_filter:
            return "", {}
        pairs = _topic_pairs(metadata_filter)
        if pairs:
            clauses = []
            parameters: dict[str, object] = {}
            for index, (document_id, chunk_index) in enumerate(pairs):
                clauses.append(
                    f"(d.public_id=:document_{index} AND c.chunk_index=:chunk_{index})"
                )
                parameters[f"document_{index}"] = document_id
                parameters[f"chunk_{index}"] = chunk_index
            return " AND (" + " OR ".join(clauses) + ")", parameters
        document_ids = _document_ids(metadata_filter)
        if document_ids is not None:
            return " AND d.public_id = ANY(:document_ids)", {"document_ids": document_ids}
        return "", {}

    @staticmethod
    def _document(row) -> Document:
        metadata = dict(row["metadata"] or {})
        metadata.update(
            document_id=int(row["document_public_id"]),
            chunk_index=int(row["chunk_index"]),
            filename=str(row["filename_snapshot"]),
            mime_type=str(row["mime_type"]),
        )
        if row["page_number"] is not None:
            metadata["page_number"] = int(row["page_number"])
        if row["slide_number"] is not None:
            metadata["slide_number"] = int(row["slide_number"])
        return Document(page_content=str(row["content"]), metadata=metadata)


class CockroachMemoryVectorRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def upsert(self, memory_id: int, text_value: str, metadata: dict[str, object]) -> None:
        embedding = vector_literal(encode_documents([text_value])[0])
        memory_uuid = uuid_for_public("learner_memories", self.workspace_id, memory_id)
        if memory_uuid is None:
            raise KeyError(f"Memory ID {memory_id} does not exist.")
        now = utc_now()
        with connection_scope() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO learner_memory_embeddings (
                        memory_id,workspace_id,embedding,embedding_model,
                        embedding_version,content_hash,retrieval_count,created_at,updated_at
                    ) VALUES (
                        :memory_id,:workspace_id,CAST(:embedding AS VECTOR(384)),
                        :embedding_model,:embedding_version,:content_hash,0,:created_at,:updated_at
                    ) ON CONFLICT (memory_id) DO UPDATE SET
                        embedding=excluded.embedding,embedding_model=excluded.embedding_model,
                        embedding_version=excluded.embedding_version,
                        content_hash=excluded.content_hash,updated_at=excluded.updated_at
                    WHERE learner_memory_embeddings.workspace_id=excluded.workspace_id
                    """
                ),
                {
                    "memory_id": memory_uuid, "workspace_id": UUID(self.workspace_id),
                    "embedding": embedding, "embedding_model": config.EMBEDDING_MODEL,
                    "embedding_version": config.EMBEDDING_MODEL,
                    "content_hash": content_sha256(text_value),
                    "created_at": now, "updated_at": now,
                },
            )

    def delete(self, memory_id: int) -> None:
        memory_uuid = uuid_for_public("learner_memories", self.workspace_id, memory_id)
        if memory_uuid is None:
            return
        with connection_scope() as connection:
            connection.execute(
                text(
                    "DELETE FROM learner_memory_embeddings "
                    "WHERE workspace_id=:workspace_id AND memory_id=:memory_id"
                ), {"workspace_id": UUID(self.workspace_id), "memory_id": memory_uuid},
            )

    def search(
        self,
        query: str,
        k: int,
        metadata_filter: dict[str, object] | None = None,
    ) -> list[tuple[Document, float]]:
        embedding = vector_literal(encode_query(query))
        clauses = ["e.workspace_id=:workspace_id"]
        parameters: dict[str, object] = {
            "workspace_id": UUID(self.workspace_id), "embedding": embedding, "limit": int(k)
        }
        for key in ("status", "memory_type"):
            if metadata_filter and key in metadata_filter:
                clauses.append(f"m.{key}=:{key}")
                parameters[key] = metadata_filter[key]
        with connection_scope() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT m.*, e.embedding <=> CAST(:embedding AS VECTOR(384)) AS distance
                    FROM learner_memory_embeddings e
                    JOIN learner_memories m ON m.id=e.memory_id
                    WHERE """ + " AND ".join(clauses)
                    + " ORDER BY distance ASC, m.public_id ASC LIMIT :limit"
                ), parameters,
            ).mappings().all()
            if rows:
                connection.execute(
                    text(
                        """
                        UPDATE learner_memory_embeddings
                        SET retrieval_count=retrieval_count+1,last_retrieved_at=now()
                        WHERE workspace_id=:workspace_id AND memory_id = ANY(:ids)
                        """
                    ),
                    {
                        "workspace_id": UUID(self.workspace_id),
                        "ids": [row["id"] for row in rows],
                    },
                )
        return [
            (
                Document(
                    page_content=str(row["content"]),
                    metadata={
                        "memory_id": int(row["public_id"]),
                        "memory_type": str(row["memory_type"]),
                        "confidence": float(row["confidence"]),
                        "importance": float(row["importance"]),
                        "status": str(row["status"]),
                        "workspace_id": self.workspace_id,
                    },
                ),
                float(row["distance"]),
            )
            for row in rows
        ]


def _document_ids(value: Any) -> list[int] | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("document_id")
    if isinstance(raw, int):
        return [raw]
    if isinstance(raw, dict):
        values = raw.get("$in")
        if isinstance(values, list):
            return [int(item) for item in values]
        equal = raw.get("$eq")
        if isinstance(equal, int):
            return [equal]
    return None


def _topic_pairs(value: Any) -> list[tuple[int, int]]:
    if not isinstance(value, dict):
        return []
    branches = value.get("$or")
    if not isinstance(branches, list):
        branches = [value]
    pairs = []
    for branch in branches:
        if not isinstance(branch, dict):
            continue
        terms = branch.get("$and")
        if not isinstance(terms, list):
            continue
        document_id = None
        chunk_index = None
        for term in terms:
            if not isinstance(term, dict):
                continue
            if "document_id" in term:
                ids = _document_ids(term)
                document_id = ids[0] if ids and len(ids) == 1 else None
            raw_chunk = term.get("chunk_index")
            if isinstance(raw_chunk, dict) and isinstance(raw_chunk.get("$eq"), int):
                chunk_index = int(raw_chunk["$eq"])
        if document_id is not None and chunk_index is not None:
            pairs.append((document_id, chunk_index))
    return pairs
