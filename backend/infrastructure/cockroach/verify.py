from __future__ import annotations

import hashlib
import json
import sys
from typing import Any
from uuid import UUID

from sqlalchemy import text

from backend.domain import deterministic_legacy_uuid
from backend.infrastructure.cockroach.importer import (
    _json_default,
    _vector_safe,
    destination_counts,
)
from backend.infrastructure.cockroach.source import SourceSnapshot, load_source_snapshot
from backend.rag import config
from backend.repositories.cockroach.connection import get_engine


def verify() -> dict[str, object]:
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required for verification.")
    snapshot = load_source_snapshot()
    if snapshot.issues:
        raise RuntimeError("Source validation has unresolved migration exceptions.")
    engine = get_engine()
    counts = destination_counts(engine)
    shortfalls = {
        key: {"source": expected, "destination": counts.get(key)}
        for key, expected in snapshot.counts.items()
        if counts.get(key, 0) < expected
    }
    runtime_additions = {
        key: counts.get(key, 0) - expected
        for key, expected in snapshot.counts.items()
        if counts.get(key, 0) > expected
    }
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        migration = connection.execute(
            text(
                "SELECT status,source_fingerprint FROM migration_runs "
                "WHERE source_fingerprint=:fingerprint ORDER BY updated_at DESC LIMIT 1"
            ),
            {"fingerprint": snapshot.fingerprint},
        ).mappings().first()
        if migration is None or migration["status"] != "completed":
            raise RuntimeError("Completed migration run is missing.")
        orphan_counts = _orphan_counts(connection)
        vector_dimensions = connection.execute(
            text(
                """
                SELECT
                  (SELECT array_length(embedding::FLOAT8[],1) FROM document_chunks
                   WHERE embedding IS NOT NULL LIMIT 1) AS document_dimension,
                  (SELECT array_length(embedding::FLOAT8[],1) FROM learner_memory_embeddings
                   LIMIT 1) AS memory_dimension
                """
            )
        ).mappings().one()
        vector_indexes = connection.execute(
            text(
                """
                SELECT index_name FROM [SHOW INDEXES FROM document_chunks]
                WHERE index_name='idx_document_chunks_workspace_embedding'
                UNION ALL
                SELECT index_name FROM [SHOW INDEXES FROM learner_memory_embeddings]
                WHERE index_name='idx_memory_embeddings_workspace_embedding'
                """
            )
        ).scalars().all()
        item_verification = _verify_migration_items(connection, snapshot)
        mapping_verification = _verify_public_id_mappings(connection)
        workspace_orphans = _workspace_orphans(connection)
        lineage_mismatches = _lineage_mismatches(connection)
        blob_verification = _verify_blobs(connection, snapshot)
        vector_verification = _verify_vectors(connection, snapshot)
        embedding_job_verification = _verify_embedding_jobs(connection, snapshot)
    if shortfalls:
        raise RuntimeError("Destination has fewer records than the verified migration baseline.")
    if any(orphan_counts.values()):
        raise RuntimeError("Destination referential verification found orphan records.")
    if any(workspace_orphans.values()):
        raise RuntimeError("Destination workspace ownership verification found orphan records.")
    if any(lineage_mismatches.values()):
        raise RuntimeError("Destination citation lineage differs from imported chunks.")
    detailed_checks = {
        "migration_items": item_verification,
        "deterministic_public_id_mappings": mapping_verification,
        "document_blobs": blob_verification,
        "vectors": vector_verification,
        "embedding_jobs": embedding_job_verification,
    }
    if not all(bool(value.get("passed")) for value in detailed_checks.values()):
        raise RuntimeError("Destination detailed migration verification failed.")
    expected_dimension = config.EMBEDDING_DIMENSION
    for value in vector_dimensions.values():
        if value is not None and int(value) != expected_dimension:
            raise RuntimeError("Destination vector dimension mismatch.")
    return {
        "status": "pass",
        "alembic_revision": str(revision),
        "source_fingerprint_matches": migration["source_fingerprint"] == snapshot.fingerprint,
        "counts": counts,
        "migration_baseline_counts": snapshot.counts,
        "post_migration_runtime_additions": runtime_additions,
        "orphan_counts": orphan_counts,
        "workspace_orphan_counts": workspace_orphans,
        "citation_lineage_mismatches": lineage_mismatches,
        "migration_items": item_verification,
        "deterministic_public_id_mappings": mapping_verification,
        "document_blobs": blob_verification,
        "vectors": vector_verification,
        "embedding_jobs": embedding_job_verification,
        "document_vector_dimension": vector_dimensions["document_dimension"],
        "memory_vector_dimension": vector_dimensions["memory_dimension"],
        "vector_indexes": sorted(set(str(value) for value in vector_indexes)),
        "credentials_recorded": False,
    }


def _orphan_counts(connection) -> dict[str, int]:
    checks = {
        "notebook_documents": "SELECT count(*) FROM notebook_documents nd LEFT JOIN notebooks n ON n.id=nd.notebook_id LEFT JOIN documents d ON d.id=nd.document_id WHERE n.id IS NULL OR d.id IS NULL",
        "document_chunks": "SELECT count(*) FROM document_chunks c LEFT JOIN documents d ON d.id=c.document_id WHERE d.id IS NULL",
        "study_interactions": "SELECT count(*) FROM study_interactions i LEFT JOIN study_sessions s ON s.id=i.session_id WHERE s.id IS NULL",
        "quiz_questions": "SELECT count(*) FROM quiz_question_attempts q LEFT JOIN quiz_attempts a ON a.id=q.quiz_attempt_id WHERE a.id IS NULL",
        "memory_embeddings": "SELECT count(*) FROM learner_memory_embeddings e LEFT JOIN learner_memories m ON m.id=e.memory_id WHERE m.id IS NULL",
    }
    return {name: int(connection.execute(text(query)).scalar_one()) for name, query in checks.items()}


def _checksum(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=_json_default, separators=(",", ":")).encode()
    ).hexdigest()


def _expected_migration_items(snapshot: SourceSnapshot):
    expected: dict[tuple[str, str], tuple[str, str]] = {}

    def add(table: str, identity: object, target_id: UUID, value: Any) -> None:
        expected[(table, str(identity))] = (str(target_id), _checksum(value))

    direct_uuid_tables = {
        "workspaces", "topics", "learning_signals", "workflow_states",
        "adaptation_events", "vector_outbox",
    }
    deterministic_tables = {
        "notebooks", "documents", "cached_intelligence", "study_sessions",
        "study_interactions", "study_interaction_sources", "quiz_attempts",
        "quiz_question_attempts", "quiz_question_sources", "memories",
        "memory_relationships",
    }
    for table in direct_uuid_tables:
        for row in snapshot.rows[table]:
            add(table, row["id"], UUID(str(row["id"])), row)
    for table in deterministic_tables:
        for row in snapshot.rows[table]:
            add(
                table,
                row["id"],
                deterministic_legacy_uuid(row["workspace_id"], table, row["id"]),
                row,
            )
    for row in snapshot.rows["notebook_documents"]:
        identity = f"{row['notebook_id']}:{row['document_id']}"
        add(
            "notebook_documents", identity,
            deterministic_legacy_uuid(row["workspace_id"], "notebook_documents", identity),
            row,
        )
    for row in snapshot.rows["topic_sources"]:
        identity = f"{row['topic_id']}:{row['source_index']}"
        add(
            "topic_sources", identity,
            deterministic_legacy_uuid(row["workspace_id"], "topic_sources", identity),
            row,
        )
    documents = {int(row["id"]): row for row in snapshot.rows["documents"]}
    for record in snapshot.document_vectors:
        owner = documents[int(record.metadata["document_id"])]
        add(
            "document_chunks", record.vector_id,
            deterministic_legacy_uuid(owner["workspace_id"], "document_chunks", record.vector_id),
            _vector_safe(record),
        )
    memories = {int(row["id"]): row for row in snapshot.rows["memories"]}
    for record in snapshot.memory_vectors:
        owner = memories[int(record.metadata["memory_id"])]
        add(
            "learner_memory_embeddings", record.vector_id,
            deterministic_legacy_uuid(owner["workspace_id"], "memories", owner["id"]),
            _vector_safe(record),
        )
    return expected


def _verify_migration_items(connection, snapshot: SourceSnapshot) -> dict[str, object]:
    expected = _expected_migration_items(snapshot)
    rows = connection.execute(
        text(
            "SELECT source_table,source_identity,target_id,checksum,status "
            "FROM migration_items"
        )
    ).mappings().all()
    actual = {
        (str(row["source_table"]), str(row["source_identity"])): (
            str(row["target_id"]), str(row["checksum"]), str(row["status"])
        )
        for row in rows
    }
    mismatches = 0
    for key, (target_id, checksum) in expected.items():
        if actual.get(key) != (target_id, checksum, "migrated"):
            mismatches += 1
    unexpected = len(set(actual) - set(expected))
    return {
        "passed": mismatches == 0 and unexpected == 0,
        "expected_count": len(expected),
        "actual_count": len(actual),
        "mismatch_count": mismatches,
        "unexpected_count": unexpected,
    }


def _verify_public_id_mappings(connection) -> dict[str, object]:
    tables = {
        "notebooks": "notebooks",
        "documents": "documents",
        "study_sessions": "study_sessions",
        "study_interactions": "study_interactions",
        "study_interaction_sources": "study_interaction_sources",
        "quiz_attempts": "quiz_attempts",
        "quiz_question_attempts": "quiz_question_attempts",
        "quiz_question_sources": "quiz_question_sources",
        "learner_memories": "memories",
        "memory_relationships": "memory_relationships",
    }
    checked = 0
    runtime_records = 0
    mismatches = 0
    for target, source in tables.items():
        rows = connection.execute(
            text(f"SELECT id,workspace_id,public_id,legacy_sqlite_id FROM {target}")
        ).mappings()
        for row in rows:
            if row["legacy_sqlite_id"] is None:
                runtime_records += 1
                if int(row["public_id"]) <= 0:
                    mismatches += 1
                continue
            checked += 1
            expected = deterministic_legacy_uuid(
                str(row["workspace_id"]), source, int(row["public_id"])
            )
            if (
                str(row["id"]) != str(expected)
                or int(row["legacy_sqlite_id"]) != int(row["public_id"])
            ):
                mismatches += 1
    return {
        "passed": mismatches == 0,
        "checked_migrated_records": checked,
        "checked_runtime_records": runtime_records,
        "mismatch_count": mismatches,
    }


def _workspace_orphans(connection) -> dict[str, int]:
    tables = (
        "notebooks", "documents", "document_blobs", "notebook_documents",
        "cached_intelligence", "topics", "document_chunks", "topic_sources",
        "study_sessions", "study_interactions", "study_interaction_sources",
        "quiz_attempts", "quiz_question_attempts", "quiz_question_sources",
        "learner_memories", "memory_relationships", "learner_memory_embeddings",
        "workflow_states", "learning_signals", "adaptation_events", "embedding_jobs",
    )
    return {
        table: int(
            connection.execute(
                text(
                    f"SELECT count(*) FROM {table} value LEFT JOIN workspaces workspace "
                    "ON workspace.id=value.workspace_id WHERE workspace.id IS NULL"
                )
            ).scalar_one()
        )
        for table in tables
    }


def _lineage_mismatches(connection) -> dict[str, int]:
    return {
        table: int(
            connection.execute(
                text(
                    f"""
                    SELECT count(*) FROM {table} source
                    WHERE source.document_id IS NOT NULL AND (
                      source.document_chunk_id IS NULL OR NOT EXISTS (
                        SELECT 1 FROM document_chunks chunk
                        WHERE chunk.id=source.document_chunk_id
                          AND chunk.document_id=source.document_id
                          AND chunk.workspace_id=source.workspace_id
                          AND chunk.chunk_index=source.chunk_index
                      )
                    )
                    """
                )
            ).scalar_one()
        )
        for table in ("topic_sources", "study_interaction_sources", "quiz_question_sources")
    }


def _verify_blobs(connection, snapshot: SourceSnapshot) -> dict[str, object]:
    rows = connection.execute(
        text("SELECT document_id,data,size_bytes,content_hash FROM document_blobs")
    ).mappings().all()
    actual = {str(row["document_id"]): row for row in rows}
    mismatches = 0
    for source in snapshot.rows["documents"]:
        target_id = deterministic_legacy_uuid(
            source["workspace_id"], "documents", source["id"]
        )
        row = actual.get(str(target_id))
        content = bytes(source["file_data"])
        if (
            row is None
            or bytes(row["data"]) != content
            or int(row["size_bytes"]) != len(content)
            or str(row["content_hash"]) != str(source["file_hash"])
        ):
            mismatches += 1
    return {
        "passed": mismatches == 0 and len(actual) >= len(snapshot.rows["documents"]),
        "checked": len(snapshot.rows["documents"]),
        "runtime_additions": len(actual) - len(snapshot.rows["documents"]),
        "mismatch_count": mismatches,
    }


def _verify_vectors(connection, snapshot: SourceSnapshot) -> dict[str, object]:
    document_rows = {int(row["id"]): row for row in snapshot.rows["documents"]}
    actual_documents = {
        str(row["id"]): tuple(float(value) for value in row["embedding"])
        for row in connection.execute(
            text("SELECT id,embedding::FLOAT8[] AS embedding FROM document_chunks")
        ).mappings()
    }
    memory_rows = {int(row["id"]): row for row in snapshot.rows["memories"]}
    actual_memories = {
        str(row["memory_id"]): tuple(float(value) for value in row["embedding"])
        for row in connection.execute(
            text("SELECT memory_id,embedding::FLOAT8[] AS embedding FROM learner_memory_embeddings")
        ).mappings()
    }
    maximum_delta = 0.0
    mismatches = 0
    checked = 0
    for record in snapshot.document_vectors:
        owner = document_rows[int(record.metadata["document_id"])]
        target = deterministic_legacy_uuid(owner["workspace_id"], "document_chunks", record.vector_id)
        actual = actual_documents.get(str(target))
        checked += 1
        if actual is None or len(actual) != len(record.embedding):
            mismatches += 1
            continue
        delta = max(abs(left - right) for left, right in zip(actual, record.embedding, strict=True))
        maximum_delta = max(maximum_delta, delta)
        if delta > 1e-6:
            mismatches += 1
    for record in snapshot.memory_vectors:
        owner = memory_rows[int(record.metadata["memory_id"])]
        target = deterministic_legacy_uuid(owner["workspace_id"], "memories", owner["id"])
        actual = actual_memories.get(str(target))
        checked += 1
        if actual is None or len(actual) != len(record.embedding):
            mismatches += 1
            continue
        delta = max(abs(left - right) for left, right in zip(actual, record.embedding, strict=True))
        maximum_delta = max(maximum_delta, delta)
        if delta > 1e-6:
            mismatches += 1
    return {
        "passed": mismatches == 0,
        "checked": checked,
        "runtime_additions": len(actual_documents) + len(actual_memories) - checked,
        "dimension": config.EMBEDDING_DIMENSION,
        "maximum_absolute_delta": maximum_delta,
        "mismatch_count": mismatches,
    }


def _verify_embedding_jobs(connection, snapshot: SourceSnapshot) -> dict[str, object]:
    rows = connection.execute(
        text(
            "SELECT id,workspace_id,entity_type,entity_id,operation,payload,status,"
            "attempts,last_error,idempotency_key FROM embedding_jobs"
        )
    ).mappings().all()
    actual = {str(row["id"]): row for row in rows}
    mismatches = 0
    for source in snapshot.rows["vector_outbox"]:
        row = actual.get(str(source["id"]))
        expected_payload = json.loads(source["payload_json"])
        if (
            row is None
            or str(row["workspace_id"]) != str(source["workspace_id"])
            or str(row["entity_type"]) != str(source["entity_type"])
            or str(row["entity_id"]) != str(source["entity_id"])
            or str(row["operation"]) != str(source["operation"])
            or dict(row["payload"]) != expected_payload
            or str(row["status"]) != str(source["status"])
            or int(row["attempts"]) != int(source["attempts"])
            or row["last_error"] != source["last_error"]
            or str(row["idempotency_key"]) != f"legacy:{source['id']}"
        ):
            mismatches += 1
    return {
        "passed": mismatches == 0 and len(actual) >= len(snapshot.rows["vector_outbox"]),
        "checked": len(snapshot.rows["vector_outbox"]),
        "runtime_additions": len(actual) - len(snapshot.rows["vector_outbox"]),
        "mismatch_count": mismatches,
    }


def main() -> int:
    try:
        print(json.dumps(verify(), indent=2, sort_keys=True, default=str))
        return 0
    except Exception as error:
        print(f"Migration verification failed safely ({type(error).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
