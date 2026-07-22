from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.rag import config


SQLITE_TABLES = (
    "workspaces",
    "notebooks",
    "documents",
    "notebook_documents",
    "cached_intelligence",
    "topics",
    "topic_sources",
    "study_sessions",
    "study_interactions",
    "study_interaction_sources",
    "quiz_attempts",
    "quiz_question_attempts",
    "quiz_question_sources",
    "learning_signals",
    "memories",
    "memory_relationships",
    "workflow_states",
    "adaptation_events",
    "vector_outbox",
)


@dataclass(frozen=True)
class VectorRecord:
    collection: str
    vector_id: str
    document: str
    metadata: dict[str, Any]
    embedding: tuple[float, ...]


@dataclass(frozen=True)
class MigrationIssue:
    code: str
    source: str
    identity: str
    detail: str


@dataclass(frozen=True)
class SourceSnapshot:
    rows: dict[str, tuple[dict[str, Any], ...]]
    document_vectors: tuple[VectorRecord, ...]
    memory_vectors: tuple[VectorRecord, ...]
    issues: tuple[MigrationIssue, ...]
    fingerprint: str

    @property
    def counts(self) -> dict[str, int]:
        counts = {name: len(values) for name, values in self.rows.items()}
        counts["document_chunks"] = len(self.document_vectors)
        counts["learner_memory_embeddings"] = len(self.memory_vectors)
        counts["document_blobs"] = len(self.rows.get("documents", ()))
        return counts


def load_source_snapshot(
    sqlite_path: Path | None = None,
    document_chroma_path: Path | None = None,
    memory_chroma_path: Path | None = None,
) -> SourceSnapshot:
    database_path = (sqlite_path or config.DATABASE_PATH).resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"SQLite source does not exist: {database_path}")
    rows = _sqlite_rows(database_path)
    document_vectors, document_issues = _chroma_records(
        document_chroma_path or config.CHROMA_PATH,
        config.CHROMA_COLLECTION,
    )
    memory_vectors, memory_issues = _chroma_records(
        memory_chroma_path or config.MEMORY_CHROMA_PATH,
        config.MEMORY_CHROMA_COLLECTION,
    )
    issues = [*document_issues, *memory_issues]
    issues.extend(_validate(rows, document_vectors, memory_vectors))
    fingerprint_payload = {
        "sqlite": {
            table: {
                "count": len(values),
                "checksum": _rows_checksum(table, values),
            }
            for table, values in rows.items()
        },
        "vectors": {
            "document": _vector_checksum(document_vectors),
            "memory": _vector_checksum(memory_vectors),
        },
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return SourceSnapshot(
        rows=rows,
        document_vectors=tuple(document_vectors),
        memory_vectors=tuple(memory_vectors),
        issues=tuple(issues),
        fingerprint=fingerprint,
    )


def _sqlite_rows(database_path: Path) -> dict[str, tuple[dict[str, Any], ...]]:
    uri = f"file:{database_path.as_posix()}?mode=ro"
    result: dict[str, tuple[dict[str, Any], ...]] = {}
    with sqlite3.connect(uri, uri=True, timeout=5.0) as connection:
        connection.row_factory = sqlite3.Row
        available = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = sorted(set(SQLITE_TABLES) - available)
        if missing:
            raise RuntimeError("SQLite source is missing tables: " + ", ".join(missing))
        for table in SQLITE_TABLES:
            order = "rowid"
            values = connection.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
            result[table] = tuple(dict(row) for row in values)
    return result


def _chroma_records(path: Path, collection_name: str):
    import chromadb

    issues: list[MigrationIssue] = []
    if not path.exists():
        return [], [
            MigrationIssue("missing_collection", collection_name, "-", "Chroma path is missing.")
        ]
    try:
        collection = chromadb.PersistentClient(path=str(path)).get_collection(collection_name)
    except Exception:
        return [], [
            MigrationIssue(
                "missing_collection", collection_name, "-", "Chroma collection is unavailable."
            )
        ]
    payload = collection.get(include=["documents", "metadatas", "embeddings"])
    identifiers = list(payload.get("ids") or [])
    documents = list(payload.get("documents") or [])
    metadatas = list(payload.get("metadatas") or [])
    raw_embeddings = payload.get("embeddings")
    embeddings = list(raw_embeddings) if raw_embeddings is not None else []
    if not (len(identifiers) == len(documents) == len(metadatas) == len(embeddings)):
        issues.append(
            MigrationIssue(
                "collection_shape_mismatch",
                collection_name,
                "-",
                "Chroma IDs, documents, metadata, and embeddings have different counts.",
            )
        )
        return [], issues
    records: list[VectorRecord] = []
    seen: set[str] = set()
    for vector_id, document, metadata, embedding in zip(
        identifiers, documents, metadatas, embeddings, strict=True
    ):
        identity = str(vector_id)
        if identity in seen:
            issues.append(
                MigrationIssue("duplicate_vector_id", collection_name, identity, "Duplicate vector ID.")
            )
            continue
        seen.add(identity)
        vector = tuple(float(value) for value in embedding)
        if len(vector) != config.EMBEDDING_DIMENSION:
            issues.append(
                MigrationIssue(
                    "embedding_dimension",
                    collection_name,
                    identity,
                    f"Expected {config.EMBEDDING_DIMENSION} values; found {len(vector)}.",
                )
            )
        records.append(
            VectorRecord(
                collection=collection_name,
                vector_id=identity,
                document=str(document or ""),
                metadata=dict(metadata or {}),
                embedding=vector,
            )
        )
    return records, issues


def _validate(rows, document_vectors, memory_vectors):
    issues: list[MigrationIssue] = []
    for record in [*document_vectors, *memory_vectors]:
        norm = math.sqrt(sum(value * value for value in record.embedding))
        if not math.isfinite(norm) or abs(norm - 1.0) > 1e-3:
            issues.append(
                MigrationIssue(
                    "non_normalized_embedding", record.collection, record.vector_id,
                    "Embedding is not unit-normalized; cosine and legacy L2 order may differ.",
                )
            )
    document_rows = {int(row["id"]): row for row in rows["documents"]}
    memory_rows = {int(row["id"]): row for row in rows["memories"]}
    chunk_keys: set[tuple[int, int]] = set()
    chunks_per_document: dict[int, int] = {}
    for record in document_vectors:
        try:
            document_id = int(record.metadata["document_id"])
            chunk_index = int(record.metadata["chunk_index"])
        except (KeyError, TypeError, ValueError):
            issues.append(
                MigrationIssue(
                    "invalid_chunk_metadata",
                    record.collection,
                    record.vector_id,
                    "document_id or chunk_index is missing or invalid.",
                )
            )
            continue
        if document_id not in document_rows:
            issues.append(
                MigrationIssue(
                    "orphan_document_vector",
                    record.collection,
                    record.vector_id,
                    f"Document owner {document_id} is missing.",
                )
            )
        else:
            owner = document_rows[document_id]
            if str(record.metadata.get("filename", owner["filename"])) != str(owner["filename"]):
                issues.append(
                    MigrationIssue(
                        "document_filename_mismatch", record.collection, record.vector_id,
                        "Vector filename does not match the relational document.",
                    )
                )
            if str(record.metadata.get("mime_type", owner["mime_type"])) != str(owner["mime_type"]):
                issues.append(
                    MigrationIssue(
                        "document_mime_mismatch", record.collection, record.vector_id,
                        "Vector MIME type does not match the relational document.",
                    )
                )
        key = (document_id, chunk_index)
        if key in chunk_keys:
            issues.append(
                MigrationIssue(
                    "duplicate_chunk_identity",
                    record.collection,
                    record.vector_id,
                    f"Duplicate document/chunk identity {document_id}/{chunk_index}.",
                )
            )
        chunk_keys.add(key)
        chunks_per_document[document_id] = chunks_per_document.get(document_id, 0) + 1
    for document_id, row in document_rows.items():
        expected = int(row["chunk_count"])
        actual = chunks_per_document.get(document_id, 0)
        if expected != actual:
            issues.append(
                MigrationIssue(
                    "chunk_count_mismatch",
                    "documents",
                    str(document_id),
                    f"Relational chunk_count={expected}; Chroma count={actual}.",
                )
            )
    seen_memory_ids: set[int] = set()
    for record in memory_vectors:
        try:
            memory_id = int(record.metadata["memory_id"])
        except (KeyError, TypeError, ValueError):
            issues.append(
                MigrationIssue(
                    "invalid_memory_metadata",
                    record.collection,
                    record.vector_id,
                    "memory_id is missing or invalid.",
                )
            )
            continue
        if memory_id in seen_memory_ids:
            issues.append(
                MigrationIssue(
                    "duplicate_memory_vector",
                    record.collection,
                    record.vector_id,
                    f"Memory {memory_id} has more than one vector.",
                )
            )
        seen_memory_ids.add(memory_id)
        owner = memory_rows.get(memory_id)
        if owner is None:
            issues.append(
                MigrationIssue(
                    "orphan_memory_vector",
                    record.collection,
                    record.vector_id,
                    f"Memory owner {memory_id} is missing.",
                )
            )
        elif str(record.metadata.get("status", owner["status"])) != str(owner["status"]):
            issues.append(
                MigrationIssue(
                    "memory_status_mismatch",
                    record.collection,
                    record.vector_id,
                    "Vector status does not match relational memory status.",
                )
            )
        elif record.document != str(owner["content"]):
            issues.append(
                MigrationIssue(
                    "memory_content_mismatch", record.collection, record.vector_id,
                    "Vector document does not match relational memory content.",
                )
            )
        elif str(record.metadata.get("workspace_id", owner["workspace_id"])) != str(owner["workspace_id"]):
            issues.append(
                MigrationIssue(
                    "memory_workspace_mismatch", record.collection, record.vector_id,
                    "Vector workspace does not match relational memory ownership.",
                )
            )
    for memory_id, memory in memory_rows.items():
        if memory["status"] == "active" and memory_id not in seen_memory_ids:
            issues.append(
                MigrationIssue(
                    "missing_memory_vector",
                    "memories",
                    str(memory_id),
                    "Active memory has no Chroma vector.",
                )
            )
    return issues


def _rows_checksum(table: str, rows: tuple[dict[str, Any], ...]) -> str:
    safe_rows = []
    for row in rows:
        safe = {}
        for key, value in row.items():
            if isinstance(value, bytes):
                safe[key] = {"bytes_sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
            else:
                safe[key] = value
        safe_rows.append(safe)
    return hashlib.sha256(
        json.dumps([table, safe_rows], sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


def _vector_checksum(records: list[VectorRecord]) -> str:
    payload = [
        [
            record.vector_id,
            hashlib.sha256(record.document.encode()).hexdigest(),
            hashlib.sha256(json.dumps(record.metadata, sort_keys=True, default=str).encode()).hexdigest(),
            hashlib.sha256(json.dumps(record.embedding, separators=(",", ":")).encode()).hexdigest(),
        ]
        for record in sorted(records, key=lambda value: value.vector_id)
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
