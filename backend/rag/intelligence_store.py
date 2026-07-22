from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence
from uuid import UUID, uuid4

from backend.domain import DEFAULT_WORKSPACE_ID

from backend.rag.database import get_connection, initialize_database


VALID_SCOPE_KINDS = frozenset(
    {"global", "notebook", "documents", "topic"}
)
EXTRACTION_SCOPE_KINDS = frozenset(
    {"global", "notebook", "documents"}
)
MAX_CACHE_KIND_LENGTH = 80
MAX_TOPIC_NAME_LENGTH = 160
MAX_TOPIC_DESCRIPTION_LENGTH = 2_000
MAX_SOURCE_EXCERPT_LENGTH = 2_000


class IntelligenceStoreError(ValueError):
    """Base error for invalid intelligence persistence operations."""


class IntelligenceScopeNotFoundError(LookupError):
    """Raised when a persisted scope target does not exist."""


class FingerprintMismatchError(IntelligenceStoreError):
    """Raised when source data changed while intelligence was generated."""


@dataclass(frozen=True)
class CachedIntelligence:
    kind: str
    scope_kind: str
    scope_key: str
    result: object
    source_snapshot: object
    generated_at: str
    fingerprint: str


@dataclass(frozen=True)
class TopicSourcePair:
    document_id: int
    chunk_index: int
    source_index: int
    filename: str
    mime_type: str
    page_number: int | None = None
    slide_number: int | None = None
    excerpt: str = ""
    distance: float | None = None


@dataclass(frozen=True)
class TopicInput:
    name: str
    sources: Sequence[TopicSourcePair]
    description: str = ""
    topic_id: str | None = None


@dataclass(frozen=True)
class Topic:
    id: str
    name: str
    description: str
    extraction_scope_kind: str
    extraction_scope_key: str
    generated_at: str
    source_fingerprint: str
    sources: tuple[TopicSourcePair, ...]

    @property
    def source_count(self) -> int:
        return len(self.sources)


@dataclass(frozen=True)
class _PreparedTopic:
    id: str
    name: str
    description: str
    sources: tuple[TopicSourcePair, ...]


def initialize_intelligence_database() -> None:
    """Initialize document, topic, and cache tables idempotently."""
    initialize_database()


def canonical_scope_key(
    scope_kind: str,
    value: object = None,
) -> str:
    normalized_kind = _normalize_scope_kind(scope_kind)

    if normalized_kind == "global":
        if value not in (None, "", "global"):
            raise IntelligenceStoreError(
                "Global scope cannot include an identifier."
            )
        return "global"

    if normalized_kind == "notebook":
        notebook_id = _positive_integer(value, "notebook ID")
        return str(notebook_id)

    if normalized_kind == "documents":
        document_ids = _normalize_document_ids(value)
        return ",".join(str(item) for item in document_ids)

    if value is None:
        raise IntelligenceStoreError("Topic scope requires a topic ID.")
    return str(_parse_uuid(str(value), "topic ID"))


def get_cached_intelligence(
    kind: str,
    scope_kind: str,
    scope_key: object = None,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> CachedIntelligence | None:
    normalized_kind = _normalize_cache_kind(kind)
    normalized_scope_kind = _normalize_scope_kind(scope_kind)
    normalized_scope_key = canonical_scope_key(
        normalized_scope_kind,
        scope_key,
    )

    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT
                kind,
                scope_kind,
                scope_key,
                result_json,
                source_snapshot_json,
                generated_at,
                fingerprint
            FROM cached_intelligence
            WHERE kind = ?
              AND scope_kind = ?
              AND scope_key = ?
              AND workspace_id = ?
            """,
            (
                normalized_kind,
                normalized_scope_kind,
                normalized_scope_key,
                workspace_id,
            ),
        ).fetchone()

    return _cache_from_row(row) if row is not None else None


def list_cached_intelligence(
    *,
    kind: str | None = None,
    scope_kind: str | None = None,
    scope_key: object = None,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[CachedIntelligence]:
    where_clauses: list[str] = ["workspace_id = ?"]
    parameters: list[object] = [workspace_id]

    if kind is not None:
        where_clauses.append("kind = ?")
        parameters.append(_normalize_cache_kind(kind))

    if scope_kind is not None:
        normalized_scope_kind = _normalize_scope_kind(scope_kind)
        where_clauses.append("scope_kind = ?")
        parameters.append(normalized_scope_kind)
        if scope_key is not None or normalized_scope_kind == "global":
            where_clauses.append("scope_key = ?")
            parameters.append(
                canonical_scope_key(normalized_scope_kind, scope_key)
            )
    elif scope_key is not None:
        raise IntelligenceStoreError(
            "scope_key requires scope_kind."
        )

    where_sql = (
        f"WHERE {' AND '.join(where_clauses)}"
        if where_clauses
        else ""
    )
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                kind,
                scope_kind,
                scope_key,
                result_json,
                source_snapshot_json,
                generated_at,
                fingerprint
            FROM cached_intelligence
            {where_sql}
            ORDER BY generated_at DESC, id DESC
            """,
            parameters,
        ).fetchall()

    return [_cache_from_row(row) for row in rows]


def replace_cached_intelligence(
    kind: str,
    scope_kind: str,
    scope_key: object,
    *,
    result: object,
    source_snapshot: object,
    fingerprint: str,
    generated_at: str | None = None,
    require_current_fingerprint: bool = True,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> CachedIntelligence:
    """Atomically replace latest cache after all values validate."""
    normalized_kind = _normalize_cache_kind(kind)
    normalized_scope_kind = _normalize_scope_kind(scope_kind)
    normalized_scope_key = canonical_scope_key(
        normalized_scope_kind,
        scope_key,
    )
    normalized_fingerprint = _normalize_fingerprint(fingerprint)
    normalized_generated_at = _normalize_timestamp(generated_at)
    result_json = _encode_json(result, "result")
    snapshot_json = _encode_json(source_snapshot, "source snapshot")

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if require_current_fingerprint:
            current_fingerprint = _fingerprint_for_scope_with_connection(
                connection,
                normalized_scope_kind,
                normalized_scope_key,
            )
            if current_fingerprint != normalized_fingerprint:
                raise FingerprintMismatchError(
                    "Sources changed during generation; cached result "
                    "was not replaced."
                )

        connection.execute(
            """
            INSERT INTO cached_intelligence (
                workspace_id,
                kind,
                scope_kind,
                scope_key,
                result_json,
                source_snapshot_json,
                generated_at,
                fingerprint
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(kind, scope_kind, scope_key) DO UPDATE SET
                result_json = excluded.result_json,
                source_snapshot_json = excluded.source_snapshot_json,
                generated_at = excluded.generated_at,
                fingerprint = excluded.fingerprint
            WHERE cached_intelligence.workspace_id = excluded.workspace_id
            """,
            (
                workspace_id,
                normalized_kind,
                normalized_scope_kind,
                normalized_scope_key,
                result_json,
                snapshot_json,
                normalized_generated_at,
                normalized_fingerprint,
            ),
        )

    cached = get_cached_intelligence(
        normalized_kind,
        normalized_scope_kind,
        normalized_scope_key,
        workspace_id=workspace_id,
    )
    if cached is None:
        raise RuntimeError("Replaced cache could not be loaded.")
    return cached


def delete_cached_intelligence(
    kind: str,
    scope_kind: str,
    scope_key: object = None,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> bool:
    normalized_kind = _normalize_cache_kind(kind)
    normalized_scope_kind = _normalize_scope_kind(scope_kind)
    normalized_scope_key = canonical_scope_key(
        normalized_scope_kind,
        scope_key,
    )
    with get_connection() as connection:
        cursor = connection.execute(
            """
            DELETE FROM cached_intelligence
            WHERE kind = ?
              AND scope_kind = ?
              AND scope_key = ?
              AND workspace_id = ?
            """,
            (
                normalized_kind,
                normalized_scope_kind,
                normalized_scope_key,
                workspace_id,
            ),
        )
        return cursor.rowcount > 0


def cache_is_stale(
    cached: CachedIntelligence,
    current_fingerprint: str,
) -> bool:
    return cached.fingerprint != _normalize_fingerprint(
        current_fingerprint
    )


def replace_topics_for_scope(
    scope_kind: str,
    scope_key: object,
    topics: Sequence[TopicInput],
    *,
    fingerprint: str,
    generated_at: str | None = None,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[Topic]:
    """Atomically replace one extraction scope and preserve old on error."""
    normalized_scope_kind = _normalize_scope_kind(scope_kind)
    if normalized_scope_kind not in EXTRACTION_SCOPE_KINDS:
        raise IntelligenceStoreError(
            "Topic extraction scope must be global, notebook, or documents."
        )
    normalized_scope_key = canonical_scope_key(
        normalized_scope_kind,
        scope_key,
    )
    normalized_fingerprint = _normalize_fingerprint(fingerprint)
    normalized_generated_at = _normalize_timestamp(generated_at)
    prepared_topics = _prepare_topics(topics)

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        current_fingerprint = _fingerprint_for_scope_with_connection(
            connection,
            normalized_scope_kind,
            normalized_scope_key,
        )
        if current_fingerprint != normalized_fingerprint:
            raise FingerprintMismatchError(
                "Sources changed during topic extraction; existing topics "
                "were preserved."
            )

        allowed_documents = _document_rows_for_scope(
            connection,
            normalized_scope_kind,
            normalized_scope_key,
        )
        validated_topics = tuple(
            _validate_topic_sources(topic, allowed_documents)
            for topic in prepared_topics
        )

        old_topic_rows = connection.execute(
            """
            SELECT id
            FROM topics
            WHERE extraction_scope_kind = ?
              AND extraction_scope_key = ?
              AND workspace_id = ?
            """,
            (normalized_scope_kind, normalized_scope_key, workspace_id),
        ).fetchall()
        old_topic_ids = [str(row["id"]) for row in old_topic_rows]
        if old_topic_ids:
            placeholders = ",".join("?" for _ in old_topic_ids)
            connection.execute(
                f"""
                DELETE FROM cached_intelligence
                WHERE scope_kind = 'topic'
                  AND scope_key IN ({placeholders})
                  AND workspace_id = ?
                """,
                [*old_topic_ids, workspace_id],
            )

        connection.execute(
            """
            DELETE FROM topics
            WHERE extraction_scope_kind = ?
              AND extraction_scope_key = ?
              AND workspace_id = ?
            """,
            (normalized_scope_kind, normalized_scope_key, workspace_id),
        )

        for topic in validated_topics:
            connection.execute(
                """
                INSERT INTO topics (
                    workspace_id,
                    id,
                    name,
                    description,
                    extraction_scope_kind,
                    extraction_scope_key,
                    generated_at,
                    source_fingerprint
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace_id,
                    topic.id,
                    topic.name,
                    topic.description,
                    normalized_scope_kind,
                    normalized_scope_key,
                    normalized_generated_at,
                    normalized_fingerprint,
                ),
            )
            connection.executemany(
                """
                INSERT INTO topic_sources (
                    workspace_id,
                    topic_id,
                    document_id,
                    chunk_index,
                    source_index,
                    filename,
                    mime_type,
                    page_number,
                    slide_number,
                    excerpt,
                    distance
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        workspace_id,
                        topic.id,
                        source.document_id,
                        source.chunk_index,
                        source.source_index,
                        source.filename,
                        source.mime_type,
                        source.page_number,
                        source.slide_number,
                        source.excerpt,
                        source.distance,
                    )
                    for source in topic.sources
                ],
            )

    return list_topics(
        scope_kind=normalized_scope_kind,
        scope_key=normalized_scope_key,
        workspace_id=workspace_id,
    )


def get_topic(
    topic_id: str,
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> Topic | None:
    normalized_id = str(_parse_uuid(topic_id, "topic ID"))
    topics = _load_topics(
        where_sql="WHERE t.id = ? AND t.workspace_id = ?",
        parameters=[normalized_id, workspace_id],
        workspace_id=workspace_id,
    )
    return topics[0] if topics else None


def list_topics(
    *,
    scope_kind: str | None = None,
    scope_key: object = None,
    search: str | None = None,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[Topic]:
    where_clauses: list[str] = ["t.workspace_id = ?"]
    parameters: list[object] = [workspace_id]

    if scope_kind is not None:
        normalized_scope_kind = _normalize_scope_kind(scope_kind)
        if normalized_scope_kind not in EXTRACTION_SCOPE_KINDS:
            raise IntelligenceStoreError(
                "Topic extraction scope must be global, notebook, or documents."
            )
        where_clauses.extend(
            [
                "t.extraction_scope_kind = ?",
                "t.extraction_scope_key = ?",
            ]
        )
        parameters.extend(
            [
                normalized_scope_kind,
                canonical_scope_key(normalized_scope_kind, scope_key),
            ]
        )
    elif scope_key is not None:
        raise IntelligenceStoreError("scope_key requires scope_kind.")

    if search is not None and search.strip():
        pattern = _literal_like_pattern(search.strip())
        where_clauses.append(
            "(t.name LIKE ? ESCAPE '\\' COLLATE NOCASE "
            "OR t.description LIKE ? ESCAPE '\\' COLLATE NOCASE)"
        )
        parameters.extend([pattern, pattern])

    where_sql = (
        f"WHERE {' AND '.join(where_clauses)}"
        if where_clauses
        else ""
    )
    return _load_topics(
        where_sql=where_sql,
        parameters=parameters,
        workspace_id=workspace_id,
    )


def delete_topic(topic_id: str) -> bool:
    normalized_id = str(_parse_uuid(topic_id, "topic ID"))
    with get_connection() as connection:
        connection.execute(
            """
            DELETE FROM cached_intelligence
            WHERE scope_kind = 'topic' AND scope_key = ?
            """,
            (normalized_id,),
        )
        cursor = connection.execute(
            "DELETE FROM topics WHERE id = ?",
            (normalized_id,),
        )
        return cursor.rowcount > 0


def get_topic_source_pairs(topic_id: str) -> tuple[TopicSourcePair, ...]:
    normalized_id = str(_parse_uuid(topic_id, "topic ID"))
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT
                document_id,
                chunk_index,
                source_index,
                filename,
                mime_type,
                page_number,
                slide_number,
                excerpt,
                distance
            FROM topic_sources
            WHERE topic_id = ?
            ORDER BY source_index ASC, document_id ASC, chunk_index ASC
            """,
            (normalized_id,),
        ).fetchall()
    return tuple(_topic_source_from_row(row) for row in rows)


def fingerprint_for_document(document_id: int) -> str:
    return fingerprint_for_documents([document_id])


def fingerprint_for_documents(document_ids: Iterable[int]) -> str:
    normalized_ids = _normalize_document_ids(list(document_ids))
    with get_connection() as connection:
        rows = _load_document_rows(connection, normalized_ids)
    return _fingerprint_document_rows(rows)


def fingerprint_for_notebook(notebook_id: int) -> str:
    scope_key = canonical_scope_key("notebook", notebook_id)
    with get_connection() as connection:
        return _fingerprint_for_scope_with_connection(
            connection,
            "notebook",
            scope_key,
        )


def fingerprint_for_topic(topic_id: str) -> str:
    scope_key = canonical_scope_key("topic", topic_id)
    with get_connection() as connection:
        return _fingerprint_for_scope_with_connection(
            connection,
            "topic",
            scope_key,
        )


def fingerprint_for_scope(
    scope_kind: str,
    scope_key: object = None,
) -> str:
    normalized_kind = _normalize_scope_kind(scope_kind)
    normalized_key = canonical_scope_key(normalized_kind, scope_key)
    with get_connection() as connection:
        return _fingerprint_for_scope_with_connection(
            connection,
            normalized_kind,
            normalized_key,
        )


def _load_topics(
    *,
    where_sql: str,
    parameters: list[object],
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> list[Topic]:
    with get_connection() as connection:
        topic_rows = connection.execute(
            f"""
            SELECT
                t.id,
                t.name,
                t.description,
                t.extraction_scope_kind,
                t.extraction_scope_key,
                t.generated_at,
                t.source_fingerprint
            FROM topics AS t
            {where_sql}
            ORDER BY t.name COLLATE NOCASE ASC, t.id ASC
            """,
            parameters,
        ).fetchall()

        topic_ids = [str(row["id"]) for row in topic_rows]
        source_rows: list[sqlite3.Row] = []
        if topic_ids:
            placeholders = ",".join("?" for _ in topic_ids)
            source_rows = connection.execute(
                f"""
                SELECT
                    topic_id,
                    document_id,
                    chunk_index,
                    source_index,
                    filename,
                    mime_type,
                    page_number,
                    slide_number,
                    excerpt,
                    distance
                FROM topic_sources
                WHERE topic_id IN ({placeholders})
                  AND workspace_id = ?
                ORDER BY source_index ASC, document_id ASC, chunk_index ASC
                """,
                [*topic_ids, workspace_id],
            ).fetchall()

    grouped_sources: dict[str, list[TopicSourcePair]] = {
        topic_id: [] for topic_id in topic_ids
    }
    for row in source_rows:
        grouped_sources[str(row["topic_id"])].append(
            _topic_source_from_row(row)
        )

    return [
        Topic(
            id=str(row["id"]),
            name=str(row["name"]),
            description=str(row["description"]),
            extraction_scope_kind=str(row["extraction_scope_kind"]),
            extraction_scope_key=str(row["extraction_scope_key"]),
            generated_at=str(row["generated_at"]),
            source_fingerprint=str(row["source_fingerprint"]),
            sources=tuple(grouped_sources[str(row["id"])]),
        )
        for row in topic_rows
    ]


def _prepare_topics(topics: Sequence[TopicInput]) -> tuple[_PreparedTopic, ...]:
    prepared: list[_PreparedTopic] = []
    seen_names: set[str] = set()
    seen_ids: set[str] = set()

    for topic in topics:
        name = _normalize_topic_name(topic.name)
        name_key = name.casefold()
        if name_key in seen_names:
            raise IntelligenceStoreError(
                f'Duplicate extracted topic name: "{name}".'
            )
        seen_names.add(name_key)

        topic_id = (
            str(uuid4())
            if topic.topic_id is None
            else str(_parse_uuid(topic.topic_id, "topic ID"))
        )
        if topic_id in seen_ids:
            raise IntelligenceStoreError(
                f"Duplicate extracted topic ID: {topic_id}."
            )
        seen_ids.add(topic_id)

        sources = tuple(topic.sources)
        if not sources:
            raise IntelligenceStoreError(
                f'Topic "{name}" must cite at least one source.'
            )
        source_indexes = sorted(source.source_index for source in sources)
        if source_indexes != list(range(1, len(sources) + 1)):
            raise IntelligenceStoreError(
                f'Topic "{name}" source indexes must be contiguous from 1.'
            )
        exact_pairs = {
            (source.document_id, source.chunk_index)
            for source in sources
        }
        if len(exact_pairs) != len(sources):
            raise IntelligenceStoreError(
                f'Topic "{name}" contains duplicate source pairs.'
            )

        prepared.append(
            _PreparedTopic(
                id=topic_id,
                name=name,
                description=_normalize_topic_description(
                    topic.description
                ),
                sources=tuple(
                    sorted(sources, key=lambda item: item.source_index)
                ),
            )
        )

    return tuple(prepared)


def _validate_topic_sources(
    topic: _PreparedTopic,
    document_rows: dict[int, sqlite3.Row],
) -> _PreparedTopic:
    validated_sources: list[TopicSourcePair] = []

    for source in topic.sources:
        if isinstance(source.document_id, bool) or source.document_id <= 0:
            raise IntelligenceStoreError(
                "Topic source document_id must be a positive integer."
            )
        document_row = document_rows.get(source.document_id)
        if document_row is None:
            raise IntelligenceStoreError(
                f"Document ID {source.document_id} is outside extraction scope."
            )
        if isinstance(source.chunk_index, bool) or source.chunk_index < 0:
            raise IntelligenceStoreError(
                "Topic source chunk_index must be zero or greater."
            )
        chunk_count = int(document_row["chunk_count"])
        if source.chunk_index >= chunk_count:
            raise IntelligenceStoreError(
                f"Chunk {source.chunk_index} does not exist in document "
                f"ID {source.document_id}."
            )
        if source.filename != str(document_row["filename"]):
            raise IntelligenceStoreError(
                "Topic source filename does not match stored document."
            )
        if source.mime_type != str(document_row["mime_type"]):
            raise IntelligenceStoreError(
                "Topic source MIME type does not match stored document."
            )

        page_number = _optional_positive_integer(
            source.page_number,
            "page number",
        )
        slide_number = _optional_positive_integer(
            source.slide_number,
            "slide number",
        )
        if page_number is not None and slide_number is not None:
            raise IntelligenceStoreError(
                "Topic source cannot have both page and slide numbers."
            )

        excerpt = source.excerpt.strip()
        if not excerpt:
            raise IntelligenceStoreError(
                "Topic source excerpt cannot be empty."
            )
        if len(excerpt) > MAX_SOURCE_EXCERPT_LENGTH:
            raise IntelligenceStoreError(
                "Topic source excerpt cannot exceed "
                f"{MAX_SOURCE_EXCERPT_LENGTH} characters."
            )

        distance = source.distance
        if distance is not None:
            distance = float(distance)
            if not math.isfinite(distance) or distance < 0:
                raise IntelligenceStoreError(
                    "Topic source distance must be a finite non-negative number."
                )

        validated_sources.append(
            TopicSourcePair(
                document_id=source.document_id,
                chunk_index=source.chunk_index,
                source_index=source.source_index,
                filename=source.filename,
                mime_type=source.mime_type,
                page_number=page_number,
                slide_number=slide_number,
                excerpt=excerpt,
                distance=distance,
            )
        )

    return _PreparedTopic(
        id=topic.id,
        name=topic.name,
        description=topic.description,
        sources=tuple(validated_sources),
    )


def _document_rows_for_scope(
    connection: sqlite3.Connection,
    scope_kind: str,
    scope_key: str,
) -> dict[int, sqlite3.Row]:
    if scope_kind == "global":
        rows = connection.execute(
            """
            SELECT id, filename, mime_type, file_hash, chunk_count
            FROM documents
            ORDER BY id ASC
            """
        ).fetchall()
        return {int(row["id"]): row for row in rows}

    if scope_kind == "notebook":
        notebook_id = int(scope_key)
        notebook = connection.execute(
            "SELECT 1 FROM notebooks WHERE id = ?",
            (notebook_id,),
        ).fetchone()
        if notebook is None:
            raise IntelligenceScopeNotFoundError(
                f"Notebook ID {notebook_id} does not exist."
            )
        rows = connection.execute(
            """
            SELECT d.id, d.filename, d.mime_type, d.file_hash, d.chunk_count
            FROM documents AS d
            JOIN notebook_documents AS nd
                ON nd.document_id = d.id
            WHERE nd.notebook_id = ?
            ORDER BY d.id ASC
            """,
            (notebook_id,),
        ).fetchall()
        return {int(row["id"]): row for row in rows}

    if scope_kind == "documents":
        return _load_document_rows(
            connection,
            _normalize_document_ids(scope_key),
        )

    raise IntelligenceStoreError(
        "Topic scope cannot be expanded as an extraction scope."
    )


def _load_document_rows(
    connection: sqlite3.Connection,
    document_ids: Sequence[int],
) -> dict[int, sqlite3.Row]:
    placeholders = ",".join("?" for _ in document_ids)
    rows = connection.execute(
        f"""
        SELECT id, filename, mime_type, file_hash, chunk_count
        FROM documents
        WHERE id IN ({placeholders})
        ORDER BY id ASC
        """,
        list(document_ids),
    ).fetchall()
    by_id = {int(row["id"]): row for row in rows}
    missing_ids = sorted(set(document_ids) - set(by_id))
    if missing_ids:
        raise IntelligenceScopeNotFoundError(
            "Document IDs do not exist: "
            + ", ".join(str(item) for item in missing_ids)
            + "."
        )
    return by_id


def _fingerprint_for_scope_with_connection(
    connection: sqlite3.Connection,
    scope_kind: str,
    scope_key: str,
) -> str:
    if scope_kind in EXTRACTION_SCOPE_KINDS:
        rows = _document_rows_for_scope(
            connection,
            scope_kind,
            scope_key,
        )
        return _fingerprint_document_rows(rows)

    topic_row = connection.execute(
        "SELECT 1 FROM topics WHERE id = ?",
        (scope_key,),
    ).fetchone()
    if topic_row is None:
        raise IntelligenceScopeNotFoundError(
            f"Topic ID {scope_key} does not exist."
        )
    rows = connection.execute(
        """
        SELECT
            ts.document_id,
            ts.chunk_index,
            d.file_hash
        FROM topic_sources AS ts
        JOIN documents AS d
            ON d.id = ts.document_id
        WHERE ts.topic_id = ?
        ORDER BY ts.document_id ASC, ts.chunk_index ASC
        """,
        (scope_key,),
    ).fetchall()
    payload = [
        [
            int(row["document_id"]),
            int(row["chunk_index"]),
            str(row["file_hash"]),
        ]
        for row in rows
    ]
    return _fingerprint_payload(payload)


def _fingerprint_document_rows(
    rows: dict[int, sqlite3.Row],
) -> str:
    payload = [
        [document_id, str(rows[document_id]["file_hash"])]
        for document_id in sorted(rows)
    ]
    return _fingerprint_payload(payload)


def _fingerprint_payload(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _cache_from_row(row: sqlite3.Row) -> CachedIntelligence:
    return CachedIntelligence(
        kind=str(row["kind"]),
        scope_kind=str(row["scope_kind"]),
        scope_key=str(row["scope_key"]),
        result=json.loads(str(row["result_json"])),
        source_snapshot=json.loads(str(row["source_snapshot_json"])),
        generated_at=str(row["generated_at"]),
        fingerprint=str(row["fingerprint"]),
    )


def _topic_source_from_row(row: sqlite3.Row) -> TopicSourcePair:
    return TopicSourcePair(
        document_id=int(row["document_id"]),
        chunk_index=int(row["chunk_index"]),
        source_index=int(row["source_index"]),
        filename=str(row["filename"]),
        mime_type=str(row["mime_type"]),
        page_number=(
            int(row["page_number"])
            if row["page_number"] is not None
            else None
        ),
        slide_number=(
            int(row["slide_number"])
            if row["slide_number"] is not None
            else None
        ),
        excerpt=str(row["excerpt"]),
        distance=(
            float(row["distance"])
            if row["distance"] is not None
            else None
        ),
    )


def _normalize_scope_kind(scope_kind: str) -> str:
    normalized = scope_kind.strip().lower()
    if normalized not in VALID_SCOPE_KINDS:
        supported = ", ".join(sorted(VALID_SCOPE_KINDS))
        raise IntelligenceStoreError(
            f"Invalid scope kind. Supported values: {supported}."
        )
    return normalized


def _normalize_cache_kind(kind: str) -> str:
    normalized = kind.strip().lower()
    if not normalized:
        raise IntelligenceStoreError("Cache kind cannot be empty.")
    if len(normalized) > MAX_CACHE_KIND_LENGTH:
        raise IntelligenceStoreError(
            f"Cache kind cannot exceed {MAX_CACHE_KIND_LENGTH} characters."
        )
    if not all(character.isalnum() or character in "_-" for character in normalized):
        raise IntelligenceStoreError(
            "Cache kind can contain only letters, numbers, underscores, and hyphens."
        )
    return normalized


def _normalize_document_ids(value: object) -> tuple[int, ...]:
    if isinstance(value, bool) or value is None:
        raise IntelligenceStoreError(
            "Document scope requires at least one document ID."
        )

    raw_values: Iterable[object]
    if isinstance(value, str):
        raw_values = value.split(",") if value.strip() else []
    elif isinstance(value, int):
        raw_values = [value]
    else:
        try:
            raw_values = iter(value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise IntelligenceStoreError(
                "Document scope requires document IDs."
            ) from exc

    normalized = sorted(
        {
            _positive_integer(item, "document ID")
            for item in raw_values
        }
    )
    if not normalized:
        raise IntelligenceStoreError(
            "Document scope requires at least one document ID."
        )
    return tuple(normalized)


def _positive_integer(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise IntelligenceStoreError(f"{label} must be a positive integer.")
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise IntelligenceStoreError(
            f"{label} must be a positive integer."
        ) from exc
    if number <= 0:
        raise IntelligenceStoreError(f"{label} must be a positive integer.")
    return number


def _optional_positive_integer(
    value: int | None,
    label: str,
) -> int | None:
    if value is None:
        return None
    return _positive_integer(value, label)


def _parse_uuid(value: str, label: str) -> UUID:
    try:
        return UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise IntelligenceStoreError(
            f"{label} must be a valid UUID."
        ) from exc


def _normalize_topic_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise IntelligenceStoreError("Topic name cannot be empty.")
    if len(normalized) > MAX_TOPIC_NAME_LENGTH:
        raise IntelligenceStoreError(
            f"Topic name cannot exceed {MAX_TOPIC_NAME_LENGTH} characters."
        )
    return normalized


def _normalize_topic_description(description: str) -> str:
    normalized = description.strip()
    if len(normalized) > MAX_TOPIC_DESCRIPTION_LENGTH:
        raise IntelligenceStoreError(
            "Topic description cannot exceed "
            f"{MAX_TOPIC_DESCRIPTION_LENGTH} characters."
        )
    return normalized


def _normalize_fingerprint(fingerprint: str) -> str:
    normalized = fingerprint.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef"
        for character in normalized
    ):
        raise IntelligenceStoreError(
            "Fingerprint must be a SHA-256 hexadecimal digest."
        )
    return normalized


def _normalize_timestamp(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).isoformat()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise IntelligenceStoreError(
            "generated_at must be an ISO-8601 timestamp."
        ) from exc
    if parsed.tzinfo is None:
        raise IntelligenceStoreError(
            "generated_at must include a timezone."
        )
    return parsed.astimezone(timezone.utc).isoformat()


def _encode_json(value: object, label: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise IntelligenceStoreError(
            f"{label} must contain valid JSON values."
        ) from exc


def _literal_like_pattern(value: str) -> str:
    escaped = (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"
