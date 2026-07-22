from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text

from backend.domain import DEFAULT_WORKSPACE_ID, new_record_id
from backend.rag.intelligence_store import (
    EXTRACTION_SCOPE_KINDS,
    CachedIntelligence,
    FingerprintMismatchError,
    IntelligenceScopeNotFoundError,
    IntelligenceStoreError,
    Topic,
    TopicSourcePair,
    canonical_scope_key,
)
from backend.repositories.cockroach.connection import connection_scope
from backend.repositories.cockroach.helpers import iso, json_text, utc_now, uuid_for_public


class CockroachIntelligenceRepository:
    def __init__(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> None:
        self.workspace_id = workspace_id

    def get_cached(self, kind: str, scope_kind: str, scope_key: object) -> CachedIntelligence | None:
        normalized_kind = _kind(kind)
        normalized_scope = _scope_kind(scope_kind)
        normalized_key = canonical_scope_key(normalized_scope, scope_key)
        with connection_scope() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT * FROM cached_intelligence
                    WHERE workspace_id=:workspace_id AND kind=:kind
                      AND scope_kind=:scope_kind AND scope_key=:scope_key
                    """
                ),
                {
                    "workspace_id": UUID(self.workspace_id),
                    "kind": normalized_kind,
                    "scope_kind": normalized_scope,
                    "scope_key": normalized_key,
                },
            ).mappings().first()
        return _cached(row) if row else None

    def fingerprint_for_scope(self, scope_kind: str, scope_key: object = None) -> str:
        normalized_scope = _scope_kind(scope_kind)
        normalized_key = canonical_scope_key(normalized_scope, scope_key)
        return self._fingerprint_for_scope(normalized_scope, normalized_key)

    def replace_cached(self, **values: Any) -> CachedIntelligence:
        kind = _kind(values["kind"])
        scope_kind = _scope_kind(values["scope_kind"])
        scope_key = canonical_scope_key(scope_kind, values.get("scope_key"))
        fingerprint = str(values["fingerprint"]).strip().lower()
        if values.get("require_current_fingerprint", True):
            current = self._fingerprint_for_scope(scope_kind, scope_key)
            if current != fingerprint:
                raise FingerprintMismatchError(
                    "Sources changed during generation; cached result was not replaced."
                )
        now = utc_now()
        generated_at = values.get("generated_at") or now
        with connection_scope() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO cached_intelligence (
                        id,workspace_id,kind,scope_kind,scope_key,result,
                        source_snapshot,generated_at,fingerprint,created_at,updated_at
                    ) VALUES (
                        :id,:workspace_id,:kind,:scope_kind,:scope_key,
                        CAST(:result AS JSONB),CAST(:snapshot AS JSONB),:generated_at,
                        :fingerprint,:now,:now
                    ) ON CONFLICT (workspace_id,kind,scope_kind,scope_key) DO UPDATE SET
                        result=excluded.result,source_snapshot=excluded.source_snapshot,
                        generated_at=excluded.generated_at,fingerprint=excluded.fingerprint,
                        updated_at=excluded.updated_at
                    """
                ),
                {
                    "id": new_record_id(),
                    "workspace_id": UUID(self.workspace_id),
                    "kind": kind,
                    "scope_kind": scope_kind,
                    "scope_key": scope_key,
                    "result": json_text(values["result"]),
                    "snapshot": json_text(values["source_snapshot"]),
                    "generated_at": generated_at,
                    "fingerprint": fingerprint,
                    "now": now,
                },
            )
        cached = self.get_cached(kind, scope_kind, scope_key)
        assert cached is not None
        return cached

    def replace_topics(self, **values: Any) -> list[Topic]:
        scope_kind = _scope_kind(values["scope_kind"])
        if scope_kind not in EXTRACTION_SCOPE_KINDS:
            raise IntelligenceStoreError(
                "Topic extraction scope must be global, notebook, or documents."
            )
        scope_key = canonical_scope_key(scope_kind, values.get("scope_key"))
        fingerprint = str(values["fingerprint"]).strip().lower()
        if self._fingerprint_for_scope(scope_kind, scope_key) != fingerprint:
            raise FingerprintMismatchError(
                "Sources changed during topic extraction; existing topics were preserved."
            )
        documents = self._documents_for_scope(scope_kind, scope_key)
        prepared = [_prepare_topic(topic, documents) for topic in values["topics"]]
        generated_at = values.get("generated_at") or utc_now()
        now = utc_now()
        with connection_scope() as connection:
            old_ids = connection.execute(
                text(
                    """
                    SELECT id FROM topics WHERE workspace_id=:workspace_id
                      AND extraction_scope_kind=:scope_kind
                      AND extraction_scope_key=:scope_key
                    """
                ),
                {
                    "workspace_id": UUID(self.workspace_id),
                    "scope_kind": scope_kind,
                    "scope_key": scope_key,
                },
            ).scalars().all()
            if old_ids:
                connection.execute(
                    text(
                        "DELETE FROM cached_intelligence WHERE workspace_id=:workspace_id "
                        "AND scope_kind='topic' AND scope_key = ANY(:keys)"
                    ),
                    {
                        "workspace_id": UUID(self.workspace_id),
                        "keys": [str(value) for value in old_ids],
                    },
                )
            connection.execute(
                text(
                    """
                    DELETE FROM topics WHERE workspace_id=:workspace_id
                      AND extraction_scope_kind=:scope_kind
                      AND extraction_scope_key=:scope_key
                    """
                ),
                {
                    "workspace_id": UUID(self.workspace_id),
                    "scope_kind": scope_kind,
                    "scope_key": scope_key,
                },
            )
            for topic_id, name, description, sources in prepared:
                connection.execute(
                    text(
                        """
                        INSERT INTO topics (
                            id,workspace_id,name,description,extraction_scope_kind,
                            extraction_scope_key,generated_at,source_fingerprint,
                            created_at,updated_at
                        ) VALUES (
                            :id,:workspace_id,:name,:description,:scope_kind,:scope_key,
                            :generated_at,:fingerprint,:now,:now
                        )
                        """
                    ),
                    {
                        "id": topic_id,
                        "workspace_id": UUID(self.workspace_id),
                        "name": name,
                        "description": description,
                        "scope_kind": scope_kind,
                        "scope_key": scope_key,
                        "generated_at": generated_at,
                        "fingerprint": fingerprint,
                        "now": now,
                    },
                )
                for source in sources:
                    document_uuid = uuid_for_public(
                        "documents", self.workspace_id, source.document_id
                    )
                    chunk_uuid = connection.execute(
                        text(
                            """
                            SELECT id FROM document_chunks
                            WHERE workspace_id=:workspace_id AND document_id=:document_id
                              AND chunk_index=:chunk_index
                            """
                        ),
                        {
                            "workspace_id": UUID(self.workspace_id),
                            "document_id": document_uuid,
                            "chunk_index": source.chunk_index,
                        },
                    ).scalar_one_or_none()
                    connection.execute(
                        text(
                            """
                            INSERT INTO topic_sources (
                                id,workspace_id,topic_id,document_id,document_chunk_id,
                                chunk_index,source_index,filename,mime_type,page_number,
                                slide_number,excerpt,distance,created_at
                            ) VALUES (
                                :id,:workspace_id,:topic_id,:document_id,:chunk_id,
                                :chunk_index,:source_index,:filename,:mime_type,:page_number,
                                :slide_number,:excerpt,:distance,:created_at
                            )
                            """
                        ),
                        {
                            "id": new_record_id(),
                            "workspace_id": UUID(self.workspace_id),
                            "topic_id": topic_id,
                            "document_id": document_uuid,
                            "chunk_id": chunk_uuid,
                            "chunk_index": source.chunk_index,
                            "source_index": source.source_index,
                            "filename": source.filename,
                            "mime_type": source.mime_type,
                            "page_number": source.page_number,
                            "slide_number": source.slide_number,
                            "excerpt": source.excerpt,
                            "distance": source.distance,
                            "created_at": now,
                        },
                    )
        return self.list_topics(scope_kind=scope_kind, scope_key=scope_key)

    def get_topic(self, topic_id: str) -> Topic | None:
        try:
            normalized_id = UUID(str(topic_id))
        except ValueError as error:
            raise IntelligenceStoreError("Invalid topic ID.") from error
        topics = self._load_topics("t.id=:topic_id", {"topic_id": normalized_id})
        return topics[0] if topics else None

    def list_topics(self, **filters: Any) -> list[Topic]:
        clauses: list[str] = []
        parameters: dict[str, object] = {}
        if filters.get("scope_kind") is not None:
            scope_kind = _scope_kind(filters["scope_kind"])
            if scope_kind not in EXTRACTION_SCOPE_KINDS:
                raise IntelligenceStoreError(
                    "Topic extraction scope must be global, notebook, or documents."
                )
            clauses.extend(
                [
                    "t.extraction_scope_kind=:scope_kind",
                    "t.extraction_scope_key=:scope_key",
                ]
            )
            parameters.update(
                scope_kind=scope_kind,
                scope_key=canonical_scope_key(scope_kind, filters.get("scope_key")),
            )
        elif filters.get("scope_key") is not None:
            raise IntelligenceStoreError("scope_key requires scope_kind.")
        search = str(filters.get("search") or "").strip()
        if search:
            clauses.append("(t.name ILIKE :search OR t.description ILIKE :search)")
            parameters["search"] = f"%{search.replace('%', r'\%').replace('_', r'\_')}%"
        return self._load_topics(" AND ".join(clauses) or "true", parameters)

    def _load_topics(self, clause: str, parameters: dict[str, object]) -> list[Topic]:
        with connection_scope() as connection:
            rows = connection.execute(
                text(
                    "SELECT t.* FROM topics t WHERE t.workspace_id=:workspace_id AND "
                    + clause
                    + " ORDER BY lower(t.name),t.id"
                ),
                {"workspace_id": UUID(self.workspace_id), **parameters},
            ).mappings().all()
            topic_ids = [row["id"] for row in rows]
            sources = []
            if topic_ids:
                sources = connection.execute(
                    text(
                        """
                        SELECT s.*, d.public_id AS document_public_id
                        FROM topic_sources s LEFT JOIN documents d ON d.id=s.document_id
                        WHERE s.workspace_id=:workspace_id AND s.topic_id = ANY(:topic_ids)
                        ORDER BY s.topic_id,s.source_index,s.chunk_index
                        """
                    ),
                    {"workspace_id": UUID(self.workspace_id), "topic_ids": topic_ids},
                ).mappings().all()
        by_topic: dict[UUID, list[TopicSourcePair]] = {value: [] for value in topic_ids}
        for source in sources:
            if source["document_public_id"] is None:
                continue
            by_topic[source["topic_id"]].append(_source(source))
        return [
            Topic(
                id=str(row["id"]),
                name=str(row["name"]),
                description=str(row["description"]),
                extraction_scope_kind=str(row["extraction_scope_kind"]),
                extraction_scope_key=str(row["extraction_scope_key"]),
                generated_at=iso(row["generated_at"]),
                source_fingerprint=str(row["source_fingerprint"]),
                sources=tuple(by_topic[row["id"]]),
            )
            for row in rows
        ]

    def _documents_for_scope(self, scope_kind: str, scope_key: str) -> dict[int, Any]:
        query = "SELECT d.* FROM documents d WHERE d.workspace_id=:workspace_id"
        parameters: dict[str, object] = {"workspace_id": UUID(self.workspace_id)}
        if scope_kind == "notebook":
            query = (
                "SELECT d.* FROM documents d JOIN notebook_documents nd ON nd.document_id=d.id "
                "JOIN notebooks n ON n.id=nd.notebook_id WHERE d.workspace_id=:workspace_id "
                "AND n.public_id=:notebook_id"
            )
            parameters["notebook_id"] = int(scope_key)
        elif scope_kind == "documents":
            identifiers = [int(value) for value in scope_key.split(",")]
            query += " AND d.public_id = ANY(:document_ids)"
            parameters["document_ids"] = identifiers
        elif scope_kind != "global":
            raise IntelligenceStoreError("Invalid extraction scope.")
        query += " ORDER BY d.public_id"
        with connection_scope() as connection:
            if scope_kind == "notebook":
                exists = connection.execute(
                    text(
                        "SELECT 1 FROM notebooks WHERE workspace_id=:workspace_id "
                        "AND public_id=:notebook_id"
                    ),
                    parameters,
                ).first()
                if exists is None:
                    raise IntelligenceScopeNotFoundError(
                        f"Notebook ID {scope_key} does not exist."
                    )
            rows = connection.execute(text(query), parameters).mappings().all()
        result = {int(row["public_id"]): row for row in rows}
        if scope_kind == "documents":
            missing = sorted(set(parameters["document_ids"]) - set(result))  # type: ignore[arg-type]
            if missing:
                raise IntelligenceScopeNotFoundError(
                    "Document IDs do not exist: "
                    + ", ".join(str(value) for value in missing)
                    + "."
                )
        return result

    def _fingerprint_for_scope(self, scope_kind: str, scope_key: str) -> str:
        if scope_kind in EXTRACTION_SCOPE_KINDS:
            rows = self._documents_for_scope(scope_kind, scope_key)
            return _fingerprint(
                [[document_id, str(rows[document_id]["file_hash"])] for document_id in sorted(rows)]
            )
        topic = self.get_topic(scope_key)
        if topic is None:
            raise IntelligenceScopeNotFoundError(f"Topic ID {scope_key} does not exist.")
        documents = self._documents_for_scope(
            "documents", ",".join(str(source.document_id) for source in topic.sources)
        )
        return _fingerprint(
            [
                [source.document_id, source.chunk_index, str(documents[source.document_id]["file_hash"])]
                for source in sorted(topic.sources, key=lambda value: (value.document_id, value.chunk_index))
            ]
        )


def _kind(value: object) -> str:
    normalized = str(value).strip().lower()
    if not normalized or len(normalized) > 80:
        raise IntelligenceStoreError("Invalid cache kind.")
    return normalized


def _scope_kind(value: object) -> str:
    normalized = str(value).strip().lower()
    if normalized not in {"global", "notebook", "documents", "topic"}:
        raise IntelligenceStoreError("Invalid scope kind.")
    return normalized


def _cached(row: Any) -> CachedIntelligence:
    return CachedIntelligence(
        kind=str(row["kind"]), scope_kind=str(row["scope_kind"]),
        scope_key=str(row["scope_key"]), result=row["result"],
        source_snapshot=row["source_snapshot"], generated_at=iso(row["generated_at"]),
        fingerprint=str(row["fingerprint"]),
    )


def _source(row: Any) -> TopicSourcePair:
    return TopicSourcePair(
        document_id=int(row["document_public_id"]), chunk_index=int(row["chunk_index"]),
        source_index=int(row["source_index"]), filename=str(row["filename"]),
        mime_type=str(row["mime_type"]),
        page_number=int(row["page_number"]) if row["page_number"] is not None else None,
        slide_number=int(row["slide_number"]) if row["slide_number"] is not None else None,
        excerpt=str(row["excerpt"]),
        distance=float(row["distance"]) if row["distance"] is not None else None,
    )


def _prepare_topic(topic: Any, documents: dict[int, Any]):
    topic_id = UUID(str(topic.topic_id)) if topic.topic_id else new_record_id()
    name = str(topic.name).strip()
    description = str(topic.description).strip()
    if not name or len(name) > 160 or len(description) > 2_000:
        raise IntelligenceStoreError("Invalid topic name or description.")
    seen: set[int] = set()
    sources: list[TopicSourcePair] = []
    for source in topic.sources:
        if source.source_index <= 0 or source.source_index in seen:
            raise IntelligenceStoreError("Topic source indexes must be unique and positive.")
        seen.add(source.source_index)
        document = documents.get(source.document_id)
        if document is None:
            raise IntelligenceStoreError(
                f"Document ID {source.document_id} is outside extraction scope."
            )
        if source.chunk_index < 0 or source.chunk_index >= int(document["chunk_count"]):
            raise IntelligenceStoreError("Topic source chunk does not exist.")
        if source.filename != str(document["filename"]) or source.mime_type != str(document["mime_type"]):
            raise IntelligenceStoreError("Topic source metadata does not match stored document.")
        excerpt = source.excerpt.strip()
        if not excerpt or len(excerpt) > 2_000:
            raise IntelligenceStoreError("Invalid topic source excerpt.")
        sources.append(
            TopicSourcePair(
                document_id=source.document_id, chunk_index=source.chunk_index,
                source_index=source.source_index, filename=source.filename,
                mime_type=source.mime_type, page_number=source.page_number,
                slide_number=source.slide_number, excerpt=excerpt,
                distance=source.distance,
            )
        )
    return topic_id, name, description, tuple(sorted(sources, key=lambda item: item.source_index))


def _fingerprint(payload: object) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
