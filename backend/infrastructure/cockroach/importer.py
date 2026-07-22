from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine

from backend.domain import deterministic_legacy_uuid
from backend.infrastructure.cockroach.source import SourceSnapshot, VectorRecord
from backend.rag import config
from backend.rag.embeddings import vector_literal


TARGET_COUNTS = {
    "workspaces": "workspaces",
    "notebooks": "notebooks",
    "documents": "documents",
    "document_blobs": "document_blobs",
    "notebook_documents": "notebook_documents",
    "cached_intelligence": "cached_intelligence",
    "topics": "topics",
    "topic_sources": "topic_sources",
    "study_sessions": "study_sessions",
    "study_interactions": "study_interactions",
    "study_interaction_sources": "study_interaction_sources",
    "quiz_attempts": "quiz_attempts",
    "quiz_question_attempts": "quiz_question_attempts",
    "quiz_question_sources": "quiz_question_sources",
    "learning_signals": "learning_signals",
    "memories": "learner_memories",
    "memory_relationships": "memory_relationships",
    "workflow_states": "workflow_states",
    "adaptation_events": "adaptation_events",
    "vector_outbox": "embedding_jobs",
    "document_chunks": "document_chunks",
    "learner_memory_embeddings": "learner_memory_embeddings",
}


def import_snapshot(engine: Engine, snapshot: SourceSnapshot) -> dict[str, int]:
    if snapshot.issues:
        raise RuntimeError("Source validation has unresolved migration exceptions.")
    _require_schema(engine)
    with engine.begin() as connection:
        run_id = deterministic_legacy_uuid(
            "00000000-0000-4000-8000-000000000001", "migration_runs", snapshot.fingerprint
        )
        connection.execute(
            text(
                """
                INSERT INTO migration_runs (
                    id,source_fingerprint,status,manifest,created_at,updated_at
                ) VALUES (
                    :id,:fingerprint,'running',CAST(:manifest AS JSONB),now(),now()
                ) ON CONFLICT (id) DO UPDATE SET status='running',updated_at=now()
                """
            ),
            {
                "id": run_id,
                "fingerprint": snapshot.fingerprint,
                "manifest": json.dumps({"source_counts": snapshot.counts}, sort_keys=True),
            },
        )
        _import_relational(connection, snapshot, run_id)
        _import_document_vectors(connection, snapshot, run_id)
        _link_chunk_lineage(connection)
        _import_memory_vectors(connection, snapshot, run_id)
        connection.execute(
            text(
                """
                UPDATE migration_runs SET status='completed',updated_at=now(),completed_at=now()
                WHERE id=:id
                """
            ),
            {"id": run_id},
        )
    counts = destination_counts(engine)
    for source_name, expected in snapshot.counts.items():
        actual = counts.get(source_name)
        if actual != expected:
            raise RuntimeError(
                f"Destination count mismatch for {source_name}: expected {expected}, found {actual}."
            )
    return counts


def destination_counts(engine: Engine) -> dict[str, int]:
    counts: dict[str, int] = {}
    with engine.connect() as connection:
        for source, target in TARGET_COUNTS.items():
            counts[source] = int(connection.execute(text(f"SELECT count(*) FROM {target}")).scalar_one())
    return counts


def _require_schema(engine: Engine) -> None:
    names = set(inspect(engine).get_table_names())
    expected = set(TARGET_COUNTS.values()) | {"migration_runs", "migration_items"}
    missing = sorted(expected - names)
    if missing:
        raise RuntimeError(
            "CockroachDB schema is not ready. Missing tables: " + ", ".join(missing)
        )


def _import_relational(connection: Connection, snapshot: SourceSnapshot, run_id: UUID) -> None:
    rows = snapshot.rows
    for row in rows["workspaces"]:
        connection.execute(
            text(
                """
                INSERT INTO workspaces (id,name,created_at,updated_at)
                VALUES (:id,:name,:created_at,:updated_at) ON CONFLICT (id) DO NOTHING
                """
            ),
            row,
        )
        _item(connection, run_id, "workspaces", row["id"], UUID(row["id"]), row)
    _import_library(connection, rows, run_id)
    _import_intelligence(connection, rows, run_id)
    _import_study(connection, rows, run_id)
    _import_quizzes(connection, rows, run_id)
    _import_agentic(connection, rows, run_id)


def _import_library(connection: Connection, rows, run_id: UUID) -> None:
    for row in rows["notebooks"]:
        workspace = row["workspace_id"]
        record_id = _legacy(workspace, "notebooks", row["id"])
        connection.execute(
            text(
                """
                INSERT INTO notebooks (
                    id,workspace_id,public_id,legacy_sqlite_id,name,normalized_name,
                    description,created_at,updated_at
                ) VALUES (
                    :id,:workspace_id,:public_id,:legacy_id,:name,:normalized_name,
                    :description,:created_at,:updated_at
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(workspace),
                "public_id": row["id"], "legacy_id": row["id"],
                "name": row["name"], "normalized_name": str(row["name"]).casefold(),
                "description": row["description"], "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            },
        )
        _item(connection, run_id, "notebooks", row["id"], record_id, row)
    for row in rows["documents"]:
        workspace = row["workspace_id"]
        record_id = _legacy(workspace, "documents", row["id"])
        parameters = {
            "id": record_id, "workspace_id": UUID(workspace),
            "public_id": row["id"], "legacy_id": row["id"],
            "filename": row["filename"], "mime_type": row["mime_type"],
            "file_hash": row["file_hash"], "chunk_count": row["chunk_count"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        }
        connection.execute(
            text(
                """
                INSERT INTO documents (
                    id,workspace_id,public_id,legacy_sqlite_id,filename,mime_type,
                    file_hash,chunk_count,created_at,updated_at
                ) VALUES (
                    :id,:workspace_id,:public_id,:legacy_id,:filename,:mime_type,
                    :file_hash,:chunk_count,:created_at,:updated_at
                ) ON CONFLICT (id) DO NOTHING
                """
            ), parameters,
        )
        connection.execute(
            text(
                """
                INSERT INTO document_blobs (
                    document_id,workspace_id,data,size_bytes,content_hash,filename,
                    mime_type,created_at,updated_at
                ) VALUES (
                    :id,:workspace_id,:data,:size_bytes,:file_hash,:filename,
                    :mime_type,:created_at,:updated_at
                ) ON CONFLICT (document_id) DO NOTHING
                """
            ),
            {**parameters, "data": row["file_data"], "size_bytes": len(row["file_data"])},
        )
        _item(connection, run_id, "documents", row["id"], record_id, row)
    for row in rows["notebook_documents"]:
        workspace = row["workspace_id"]
        identity = f"{row['notebook_id']}:{row['document_id']}"
        record_id = _legacy(workspace, "notebook_documents", identity)
        connection.execute(
            text(
                """
                INSERT INTO notebook_documents (
                    id,workspace_id,notebook_id,document_id,assigned_at
                ) VALUES (:id,:workspace_id,:notebook_id,:document_id,:assigned_at)
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(workspace),
                "notebook_id": _legacy(workspace, "notebooks", row["notebook_id"]),
                "document_id": _legacy(workspace, "documents", row["document_id"]),
                "assigned_at": row["assigned_at"],
            },
        )
        _item(connection, run_id, "notebook_documents", identity, record_id, row)


def _import_intelligence(connection: Connection, rows, run_id: UUID) -> None:
    for row in rows["cached_intelligence"]:
        workspace = row["workspace_id"]
        record_id = _legacy(workspace, "cached_intelligence", row["id"])
        connection.execute(
            text(
                """
                INSERT INTO cached_intelligence (
                    id,workspace_id,legacy_sqlite_id,kind,scope_kind,scope_key,
                    result,source_snapshot,generated_at,fingerprint,created_at,updated_at
                ) VALUES (
                    :id,:workspace_id,:legacy_id,:kind,:scope_kind,:scope_key,
                    CAST(:result AS JSONB),CAST(:snapshot AS JSONB),:generated_at,
                    :fingerprint,:generated_at,:generated_at
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(workspace), "legacy_id": row["id"],
                "kind": row["kind"], "scope_kind": row["scope_kind"],
                "scope_key": row["scope_key"], "result": row["result_json"],
                "snapshot": row["source_snapshot_json"], "generated_at": row["generated_at"],
                "fingerprint": row["fingerprint"],
            },
        )
        _item(connection, run_id, "cached_intelligence", row["id"], record_id, row)
    for row in rows["topics"]:
        record_id = UUID(row["id"])
        connection.execute(
            text(
                """
                INSERT INTO topics (
                    id,workspace_id,name,description,extraction_scope_kind,
                    extraction_scope_key,generated_at,source_fingerprint,created_at,updated_at
                ) VALUES (
                    :id,:workspace_id,:name,:description,:scope_kind,:scope_key,
                    :generated_at,:fingerprint,:generated_at,:generated_at
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(row["workspace_id"]),
                "name": row["name"], "description": row["description"],
                "scope_kind": row["extraction_scope_kind"],
                "scope_key": row["extraction_scope_key"], "generated_at": row["generated_at"],
                "fingerprint": row["source_fingerprint"],
            },
        )
        _item(connection, run_id, "topics", row["id"], record_id, row)
    for row in rows["topic_sources"]:
        workspace = row["workspace_id"]
        identity = f"{row['topic_id']}:{row['source_index']}"
        record_id = _legacy(workspace, "topic_sources", identity)
        connection.execute(
            text(
                """
                INSERT INTO topic_sources (
                    id,workspace_id,topic_id,document_id,chunk_index,source_index,
                    filename,mime_type,page_number,slide_number,excerpt,distance,created_at
                ) VALUES (
                    :id,:workspace_id,:topic_id,:document_id,:chunk_index,:source_index,
                    :filename,:mime_type,:page_number,:slide_number,:excerpt,:distance,now()
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(workspace),
                "topic_id": UUID(row["topic_id"]),
                "document_id": _legacy(workspace, "documents", row["document_id"]),
                "chunk_index": row["chunk_index"], "source_index": row["source_index"],
                "filename": row["filename"], "mime_type": row["mime_type"],
                "page_number": row["page_number"], "slide_number": row["slide_number"],
                "excerpt": row["excerpt"], "distance": row["distance"],
            },
        )
        _item(connection, run_id, "topic_sources", identity, record_id, row)


def _import_study(connection: Connection, rows, run_id: UUID) -> None:
    for row in rows["study_sessions"]:
        workspace = row["workspace_id"]
        record_id = _legacy(workspace, "study_sessions", row["id"])
        connection.execute(
            text(
                """
                INSERT INTO study_sessions (
                    id,workspace_id,public_id,legacy_sqlite_id,status,started_at,ended_at,
                    created_at,updated_at,version
                ) VALUES (
                    :id,:workspace_id,:public_id,:legacy_id,:status,:started_at,:ended_at,
                    :started_at,COALESCE(:ended_at,:started_at),1
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(workspace),
                "public_id": row["id"], "legacy_id": row["id"], "status": row["status"],
                "started_at": row["started_at"], "ended_at": row["ended_at"],
            },
        )
        _item(connection, run_id, "study_sessions", row["id"], record_id, row)
    for row in rows["study_interactions"]:
        workspace = row["workspace_id"]
        record_id = _legacy(workspace, "study_interactions", row["id"])
        connection.execute(
            text(
                """
                INSERT INTO study_interactions (
                    id,workspace_id,public_id,legacy_sqlite_id,session_id,question,
                    answer,outcome,created_at,updated_at
                ) VALUES (
                    :id,:workspace_id,:public_id,:legacy_id,:session_id,:question,
                    :answer,:outcome,:created_at,:created_at
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(workspace),
                "public_id": row["id"], "legacy_id": row["id"],
                "session_id": _legacy(workspace, "study_sessions", row["session_id"]),
                "question": row["question"], "answer": row["answer"],
                "outcome": row["outcome"], "created_at": row["created_at"],
            },
        )
        _item(connection, run_id, "study_interactions", row["id"], record_id, row)
    for row in rows["study_interaction_sources"]:
        workspace = row["workspace_id"]
        record_id = _legacy(workspace, "study_interaction_sources", row["id"])
        document_id = (
            _legacy(workspace, "documents", row["document_id"])
            if row["document_id"] is not None else None
        )
        connection.execute(
            text(
                """
                INSERT INTO study_interaction_sources (
                    id,workspace_id,public_id,legacy_sqlite_id,interaction_id,document_id,
                    source_index,filename,page_number,chunk_index,distance,notebook_public_id,
                    mime_type,slide_number,excerpt,created_at
                ) VALUES (
                    :id,:workspace_id,:public_id,:legacy_id,:interaction_id,:document_id,
                    :source_index,:filename,:page_number,:chunk_index,:distance,:notebook_id,
                    :mime_type,:slide_number,:excerpt,now()
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(workspace),
                "public_id": row["id"], "legacy_id": row["id"],
                "interaction_id": _legacy(workspace, "study_interactions", row["interaction_id"]),
                "document_id": document_id, "source_index": row["source_index"],
                "filename": row["filename"], "page_number": row["page_number"],
                "chunk_index": row["chunk_index"], "distance": row["distance"],
                "notebook_id": row["notebook_id"], "mime_type": row["mime_type"],
                "slide_number": row["slide_number"], "excerpt": row["excerpt"],
            },
        )
        _item(connection, run_id, "study_interaction_sources", row["id"], record_id, row)


def _import_quizzes(connection: Connection, rows, run_id: UUID) -> None:
    for row in rows["quiz_attempts"]:
        workspace = row["workspace_id"]
        record_id = _legacy(workspace, "quiz_attempts", row["id"])
        connection.execute(
            text(
                """
                INSERT INTO quiz_attempts (
                    id,workspace_id,public_id,legacy_sqlite_id,requested_topic,quiz_topic,
                    status,total_questions,presented_questions,answered_questions,
                    skipped_questions,correct_answers,score_percentage,accuracy_percentage,
                    confidence,created_at,updated_at
                ) VALUES (
                    :id,:workspace_id,:public_id,:legacy_id,:requested_topic,:quiz_topic,
                    :status,:total_questions,:presented_questions,:answered_questions,
                    :skipped_questions,:correct_answers,:score_percentage,:accuracy_percentage,
                    :confidence,:created_at,:created_at
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                **row, "id": record_id, "workspace_id": UUID(workspace),
                "public_id": row["id"], "legacy_id": row["id"],
            },
        )
        _item(connection, run_id, "quiz_attempts", row["id"], record_id, row)
    for row in rows["quiz_question_attempts"]:
        workspace = row["workspace_id"]
        record_id = _legacy(workspace, "quiz_question_attempts", row["id"])
        connection.execute(
            text(
                """
                INSERT INTO quiz_question_attempts (
                    id,workspace_id,public_id,legacy_sqlite_id,quiz_attempt_id,question_number,
                    question,options,presented,selected_option,correct_option,is_correct,
                    skipped,explanation,created_at,updated_at
                ) VALUES (
                    :id,:workspace_id,:public_id,:legacy_id,:attempt_id,:question_number,
                    :question,CAST(:options AS JSONB),:presented,:selected_option,:correct_option,
                    :is_correct,:skipped,:explanation,:created_at,:created_at
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(workspace),
                "public_id": row["id"], "legacy_id": row["id"],
                "attempt_id": _legacy(workspace, "quiz_attempts", row["quiz_attempt_id"]),
                "question_number": row["question_number"], "question": row["question"],
                "options": row["options_json"], "presented": bool(row["presented"]),
                "selected_option": row["selected_option"], "correct_option": row["correct_option"],
                "is_correct": bool(row["is_correct"]), "skipped": bool(row["skipped"]),
                "explanation": row["explanation"], "created_at": _parent_created(rows["quiz_attempts"], row["quiz_attempt_id"]),
            },
        )
        _item(connection, run_id, "quiz_question_attempts", row["id"], record_id, row)
    for row in rows["quiz_question_sources"]:
        workspace = row["workspace_id"]
        record_id = _legacy(workspace, "quiz_question_sources", row["id"])
        connection.execute(
            text(
                """
                INSERT INTO quiz_question_sources (
                    id,workspace_id,public_id,legacy_sqlite_id,question_attempt_id,document_id,
                    source_index,filename,page_number,chunk_index,distance,notebook_public_id,
                    mime_type,slide_number,excerpt,created_at
                ) VALUES (
                    :id,:workspace_id,:public_id,:legacy_id,:question_id,:document_id,
                    :source_index,:filename,:page_number,:chunk_index,:distance,:notebook_id,
                    :mime_type,:slide_number,:excerpt,now()
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(workspace),
                "public_id": row["id"], "legacy_id": row["id"],
                "question_id": _legacy(workspace, "quiz_question_attempts", row["question_attempt_id"]),
                "document_id": (_legacy(workspace, "documents", row["document_id"]) if row["document_id"] else None),
                "source_index": row["source_index"], "filename": row["filename"],
                "page_number": row["page_number"], "chunk_index": row["chunk_index"],
                "distance": row["distance"], "notebook_id": row["notebook_id"],
                "mime_type": row["mime_type"], "slide_number": row["slide_number"],
                "excerpt": row["excerpt"],
            },
        )
        _item(connection, run_id, "quiz_question_sources", row["id"], record_id, row)


def _import_agentic(connection: Connection, rows, run_id: UUID) -> None:
    for row in rows["memories"]:
        workspace = row["workspace_id"]
        record_id = _legacy(workspace, "memories", row["id"])
        connection.execute(
            text(
                """
                INSERT INTO learner_memories (
                    id,workspace_id,public_id,legacy_sqlite_id,memory_type,content,
                    confidence,importance,status,created_at,updated_at
                ) VALUES (
                    :id,:workspace_id,:public_id,:legacy_id,:memory_type,:content,
                    :confidence,:importance,:status,:created_at,:updated_at
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                **row, "id": record_id, "workspace_id": UUID(workspace),
                "public_id": row["id"], "legacy_id": row["id"],
            },
        )
        _item(connection, run_id, "memories", row["id"], record_id, row)
    for row in rows["memory_relationships"]:
        workspace = row["workspace_id"]
        record_id = _legacy(workspace, "memory_relationships", row["id"])
        connection.execute(
            text(
                """
                INSERT INTO memory_relationships (
                    id,workspace_id,public_id,legacy_sqlite_id,source_memory_id,
                    target_memory_id,relationship_type,created_at
                ) VALUES (
                    :id,:workspace_id,:public_id,:legacy_id,:source_id,:target_id,
                    :relationship_type,:created_at
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(workspace),
                "public_id": row["id"], "legacy_id": row["id"],
                "source_id": _legacy(workspace, "memories", row["source_memory_id"]),
                "target_id": _legacy(workspace, "memories", row["target_memory_id"]),
                "relationship_type": row["relationship_type"], "created_at": row["created_at"],
            },
        )
        _item(connection, run_id, "memory_relationships", row["id"], record_id, row)
    for row in rows["learning_signals"]:
        workspace = row["workspace_id"]
        record_id = UUID(row["id"])
        connection.execute(
            text(
                """
                INSERT INTO learning_signals (
                    id,workspace_id,source_type,source_id,source_question_id,topic,
                    signal_type,statement,evidence,confidence,importance,occurrence_count,
                    payload,status,first_observed_at,last_observed_at,created_at,updated_at,
                    signal_key,memory_id,proposal_id
                ) VALUES (
                    :id,:workspace_id,:source_type,:source_id,:source_question_id,:topic,
                    :signal_type,:statement,CAST(:evidence AS JSONB),:confidence,:importance,
                    :occurrence_count,CAST(:payload AS JSONB),:status,:first_observed_at,
                    :last_observed_at,:created_at,:updated_at,:signal_key,:memory_id,:proposal_id
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(workspace),
                "source_type": row["source_type"], "source_id": row["source_id"],
                "source_question_id": row["source_question_id"], "topic": row["topic"],
                "signal_type": row["signal_type"], "statement": row["statement"],
                "evidence": row["evidence_json"], "confidence": row["confidence"],
                "importance": row["importance"], "occurrence_count": row["occurrence_count"],
                "payload": row["payload_json"], "status": row["status"],
                "first_observed_at": row["first_observed_at"],
                "last_observed_at": row["last_observed_at"], "created_at": row["created_at"],
                "updated_at": row["updated_at"], "signal_key": row["signal_key"],
                "memory_id": (_legacy(workspace, "memories", row["memory_id"]) if row["memory_id"] else None),
                "proposal_id": UUID(row["proposal_id"]) if row["proposal_id"] else None,
            },
        )
        _item(connection, run_id, "learning_signals", row["id"], record_id, row)
    for row in rows["workflow_states"]:
        record_id = UUID(row["id"])
        connection.execute(
            text(
                """
                INSERT INTO workflow_states (
                    id,workspace_id,workflow_type,payload,status,created_at,updated_at,
                    expires_at,version,decision_metadata
                ) VALUES (
                    :id,:workspace_id,:workflow_type,CAST(:payload AS JSONB),:status,
                    :created_at,:updated_at,:expires_at,:version,CAST(:metadata AS JSONB)
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(row["workspace_id"]),
                "workflow_type": row["workflow_type"], "payload": row["payload_json"],
                "status": row["status"], "created_at": row["created_at"],
                "updated_at": row["updated_at"], "expires_at": row["expires_at"],
                "version": row["version"], "metadata": row["decision_metadata_json"] or "null",
            },
        )
        _item(connection, run_id, "workflow_states", row["id"], record_id, row)
    for row in rows["adaptation_events"]:
        record_id = UUID(row["id"])
        connection.execute(
            text(
                """
                INSERT INTO adaptation_events (
                    id,workspace_id,workflow_type,request_id,memory_ids,
                    learning_signal_ids,applied_changes,reason,created_at
                ) VALUES (
                    :id,:workspace_id,:workflow_type,:request_id,CAST(:memory_ids AS JSONB),
                    CAST(:signal_ids AS JSONB),CAST(:changes AS JSONB),:reason,:created_at
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(row["workspace_id"]),
                "workflow_type": row["workflow_type"], "request_id": row["request_id"],
                "memory_ids": row["memory_ids_json"], "signal_ids": row["learning_signal_ids_json"],
                "changes": row["applied_changes_json"], "reason": row["reason"],
                "created_at": row["created_at"],
            },
        )
        _item(connection, run_id, "adaptation_events", row["id"], record_id, row)
    for row in rows["vector_outbox"]:
        record_id = UUID(row["id"])
        idempotency_key = f"legacy:{row['id']}"
        connection.execute(
            text(
                """
                INSERT INTO embedding_jobs (
                    id,workspace_id,entity_type,entity_id,operation,payload,status,
                    attempts,last_error,idempotency_key,created_at,updated_at
                ) VALUES (
                    :id,:workspace_id,:entity_type,:entity_id,:operation,
                    CAST(:payload AS JSONB),:status,:attempts,:last_error,:key,
                    :created_at,:updated_at
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(row["workspace_id"]),
                "entity_type": row["entity_type"], "entity_id": row["entity_id"],
                "operation": row["operation"], "payload": row["payload_json"],
                "status": row["status"], "attempts": row["attempts"],
                "last_error": row["last_error"], "key": idempotency_key,
                "created_at": row["created_at"], "updated_at": row["updated_at"],
            },
        )
        _item(connection, run_id, "vector_outbox", row["id"], record_id, row)


def _import_document_vectors(connection: Connection, snapshot: SourceSnapshot, run_id: UUID) -> None:
    document_rows = {int(row["id"]): row for row in snapshot.rows["documents"]}
    for record in snapshot.document_vectors:
        document_id = int(record.metadata["document_id"])
        chunk_index = int(record.metadata["chunk_index"])
        owner = document_rows[document_id]
        workspace = owner["workspace_id"]
        record_id = _legacy(workspace, "document_chunks", record.vector_id)
        now = owner["updated_at"]
        connection.execute(
            text(
                """
                INSERT INTO document_chunks (
                    id,workspace_id,document_id,chunk_index,content,page_number,
                    slide_number,filename_snapshot,mime_type,metadata,embedding,
                    embedding_model,embedding_version,content_hash,created_at,updated_at
                ) VALUES (
                    :id,:workspace_id,:document_id,:chunk_index,:content,:page_number,
                    :slide_number,:filename,:mime_type,CAST(:metadata AS JSONB),
                    CAST(:embedding AS VECTOR(384)),:model,:version,:content_hash,:now,:now
                ) ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": record_id, "workspace_id": UUID(workspace),
                "document_id": _legacy(workspace, "documents", document_id),
                "chunk_index": chunk_index, "content": record.document,
                "page_number": record.metadata.get("page_number"),
                "slide_number": record.metadata.get("slide_number"),
                "filename": record.metadata.get("filename", owner["filename"]),
                "mime_type": record.metadata.get("mime_type", owner["mime_type"]),
                "metadata": json.dumps(record.metadata, sort_keys=True),
                "embedding": vector_literal(record.embedding), "model": config.EMBEDDING_MODEL,
                "version": config.EMBEDDING_MODEL,
                "content_hash": hashlib.sha256(record.document.encode()).hexdigest(), "now": now,
            },
        )
        _item(connection, run_id, "document_chunks", record.vector_id, record_id, _vector_safe(record))


def _import_memory_vectors(connection: Connection, snapshot: SourceSnapshot, run_id: UUID) -> None:
    memory_rows = {int(row["id"]): row for row in snapshot.rows["memories"]}
    for record in snapshot.memory_vectors:
        memory_id = int(record.metadata["memory_id"])
        owner = memory_rows[memory_id]
        workspace = owner["workspace_id"]
        target_id = _legacy(workspace, "memories", memory_id)
        connection.execute(
            text(
                """
                INSERT INTO learner_memory_embeddings (
                    memory_id,workspace_id,embedding,embedding_model,embedding_version,
                    content_hash,retrieval_count,created_at,updated_at
                ) VALUES (
                    :memory_id,:workspace_id,CAST(:embedding AS VECTOR(384)),:model,
                    :version,:content_hash,0,:created_at,:updated_at
                ) ON CONFLICT (memory_id) DO NOTHING
                """
            ),
            {
                "memory_id": target_id, "workspace_id": UUID(workspace),
                "embedding": vector_literal(record.embedding), "model": config.EMBEDDING_MODEL,
                "version": config.EMBEDDING_MODEL,
                "content_hash": hashlib.sha256(record.document.encode()).hexdigest(),
                "created_at": owner["created_at"], "updated_at": owner["updated_at"],
            },
        )
        _item(connection, run_id, "learner_memory_embeddings", record.vector_id, target_id, _vector_safe(record))


def _link_chunk_lineage(connection: Connection) -> None:
    for table in ("topic_sources", "study_interaction_sources", "quiz_question_sources"):
        connection.execute(
            text(
                f"""
                UPDATE {table} AS source SET document_chunk_id = chunk.id
                FROM document_chunks AS chunk
                WHERE source.document_id=chunk.document_id
                  AND source.workspace_id=chunk.workspace_id
                  AND source.chunk_index=chunk.chunk_index
                  AND source.document_chunk_id IS NULL
                """
            )
        )


def _item(
    connection: Connection,
    run_id: UUID,
    table: str,
    identity: object,
    target_id: UUID,
    source: Any,
) -> None:
    checksum = hashlib.sha256(
        json.dumps(source, sort_keys=True, default=_json_default, separators=(",", ":")).encode()
    ).hexdigest()
    item_id = _legacy(str(run_id), "migration_items", f"{table}:{identity}")
    connection.execute(
        text(
            """
            INSERT INTO migration_items (
                id,run_id,source_table,source_identity,target_id,checksum,status,
                created_at,updated_at
            ) VALUES (
                :id,:run_id,:source_table,:source_identity,:target_id,:checksum,
                'migrated',now(),now()
            ) ON CONFLICT (id) DO UPDATE SET status='migrated',updated_at=now()
            """
        ),
        {
            "id": item_id, "run_id": run_id, "source_table": table,
            "source_identity": str(identity), "target_id": target_id, "checksum": checksum,
        },
    )


def _legacy(workspace: str, table: str, identity: object) -> UUID:
    return deterministic_legacy_uuid(workspace, table, identity)


def _parent_created(rows, parent_id: int) -> str:
    return str(next(row["created_at"] for row in rows if int(row["id"]) == int(parent_id)))


def _json_default(value: Any):
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    raise TypeError(type(value).__name__)


def _vector_safe(record: VectorRecord) -> dict[str, object]:
    return {
        "vector_id": record.vector_id,
        "document_sha256": hashlib.sha256(record.document.encode()).hexdigest(),
        "metadata": record.metadata,
        "embedding_sha256": hashlib.sha256(vector_literal(record.embedding).encode()).hexdigest(),
    }
