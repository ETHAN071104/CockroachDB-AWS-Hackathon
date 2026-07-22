from __future__ import annotations

import json
import sys
from uuid import UUID

import chromadb
from sqlalchemy import text

from backend.domain import DEFAULT_WORKSPACE_ID, deterministic_legacy_uuid
from backend.infrastructure.cockroach.importer import destination_counts
from backend.infrastructure.cockroach.source import VectorRecord, load_source_snapshot
from backend.rag import config
from backend.rag.embeddings import vector_literal
from backend.repositories.cockroach.connection import get_engine


def compare(sample_size: int = 3, top_k: int = 5) -> dict[str, object]:
    snapshot = load_source_snapshot()
    if snapshot.issues:
        raise RuntimeError("Source validation has migration exceptions.")
    counts = destination_counts(get_engine())
    count_matches = {
        name: counts.get(name, 0) >= expected
        for name, expected in snapshot.counts.items()
    }
    document_results = _compare_documents(snapshot, sample_size, top_k)
    memory_results = _compare_memories(snapshot, sample_size, top_k)
    lineage = _compare_document_lineage(snapshot)
    memory_filters = _compare_memory_filters(snapshot)
    workspace_isolation = _compare_workspace_isolation()
    relational = _compare_relational_behaviors(snapshot)
    topic_exact_source = (
        {"status": "conditionally_skipped", "reason": "Source manifest contains zero topics."}
        if not snapshot.rows["topics"]
        else _compare_topic_exact_sources(snapshot)
    )
    all_samples = [*document_results, *memory_results]
    passed = (
        all(count_matches.values())
        and all(bool(item["accepted"]) for item in all_samples)
        and bool(lineage["passed"])
        and bool(memory_filters["passed"])
        and bool(workspace_isolation["passed"])
        and bool(relational["passed"])
        and topic_exact_source.get("status") in {"pass", "conditionally_skipped"}
    )
    return {
        "status": "pass" if passed else "blocked",
        "policy": {
            "top_1_identity_must_match": True,
            "minimum_top_k_identity_overlap": 0.8,
            "distance_policy": (
                "Legacy Chroma L2 and Cockroach cosine distances may have different numeric "
                "scales; unit-normalized embeddings must preserve nearest-neighbor order."
            ),
        },
        "migration_baseline_count_preserved": count_matches,
        "document_samples": document_results,
        "memory_samples": memory_results,
        "document_page_slide_lineage": lineage,
        "memory_filtering_and_duplicates": memory_filters,
        "workspace_isolation": workspace_isolation,
        "relational_behavior": relational,
        "topic_exact_source": topic_exact_source,
        "credentials_recorded": False,
        "source_content_recorded": False,
    }


def _compare_documents(snapshot, sample_size: int, top_k: int):
    collection = chromadb.PersistentClient(path=str(config.CHROMA_PATH)).get_collection(
        config.CHROMA_COLLECTION
    )
    owners = {int(row["id"]): row for row in snapshot.rows["documents"]}
    uuid_to_source: dict[str, str] = {}
    for record in snapshot.document_vectors:
        owner = owners[int(record.metadata["document_id"])]
        target = deterministic_legacy_uuid(
            owner["workspace_id"], "document_chunks", record.vector_id
        )
        uuid_to_source[str(target)] = record.vector_id
    results = []
    samples = list(snapshot.document_vectors[:sample_size])
    for record in samples:
        results.append(
            _document_scope_comparison(
                collection, record, owners, uuid_to_source, top_k,
                scope_name="global", document_ids=None,
            )
        )
        document_id = int(record.metadata["document_id"])
        results.append(
            _document_scope_comparison(
                collection, record, owners, uuid_to_source, top_k,
                scope_name=f"document:{document_id}", document_ids=[document_id],
            )
        )
    notebook_documents: dict[int, list[int]] = {}
    for row in snapshot.rows["notebook_documents"]:
        notebook_documents.setdefault(int(row["notebook_id"]), []).append(int(row["document_id"]))
    if samples and notebook_documents:
        notebook_id = sorted(notebook_documents)[0]
        results.append(
            _document_scope_comparison(
                collection, samples[0], owners, uuid_to_source, top_k,
                scope_name=f"notebook:{notebook_id}",
                document_ids=sorted(notebook_documents[notebook_id]),
            )
        )
    return results


def _document_scope_comparison(
    collection, record: VectorRecord, owners, uuid_to_source, top_k: int,
    *, scope_name: str, document_ids: list[int] | None,
):
        query_args: dict[str, object] = {
            "query_embeddings": [list(record.embedding)], "n_results": top_k,
            "include": ["distances"],
        }
        if document_ids:
            query_args["where"] = {"document_id": {"$in": document_ids}}
        legacy = collection.query(
            **query_args,
        )
        legacy_ids = [str(value) for value in legacy["ids"][0]]
        clause = ""
        parameters: dict[str, object] = {
            "embedding": vector_literal(record.embedding),
            "workspace_id": UUID(owners[int(record.metadata["document_id"])]["workspace_id"]),
            "limit": top_k,
        }
        if document_ids:
            clause = " AND d.public_id = ANY(:document_ids)"
            parameters["document_ids"] = document_ids
        baseline_clause = " AND d.legacy_sqlite_id IS NOT NULL"
        with get_engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT c.id,c.embedding <=> CAST(:embedding AS VECTOR(384)) AS distance
                    FROM document_chunks c JOIN documents d ON d.id=c.document_id
                    WHERE c.workspace_id=:workspace_id
                    """ + baseline_clause + clause + " ORDER BY distance,c.id LIMIT :limit"
                ),
                parameters,
            ).mappings().all()
        target_ids = [uuid_to_source[str(row["id"])] for row in rows]
        comparison = _comparison(record.vector_id, legacy_ids, target_ids)
        comparison["scope"] = scope_name
        return comparison


def _compare_memories(snapshot, sample_size: int, top_k: int):
    collection = chromadb.PersistentClient(path=str(config.MEMORY_CHROMA_PATH)).get_collection(
        config.MEMORY_CHROMA_COLLECTION
    )
    memories = {int(row["id"]): row for row in snapshot.rows["memories"]}
    results = []
    for record in snapshot.memory_vectors[:sample_size]:
        legacy = collection.query(
            query_embeddings=[list(record.embedding)], n_results=top_k,
            where={"status": "active"}, include=["distances"],
        )
        legacy_ids = [str(value) for value in legacy["ids"][0]]
        memory_id = int(record.metadata["memory_id"])
        workspace = memories[memory_id]["workspace_id"]
        with get_engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT m.public_id,e.embedding <=> CAST(:embedding AS VECTOR(384)) AS distance
                    FROM learner_memory_embeddings e JOIN learner_memories m ON m.id=e.memory_id
                    WHERE e.workspace_id=:workspace_id AND m.status='active'
                      AND m.legacy_sqlite_id IS NOT NULL
                    ORDER BY distance,m.public_id LIMIT :limit
                    """
                ),
                {
                    "embedding": vector_literal(record.embedding),
                    "workspace_id": UUID(workspace), "limit": top_k,
                },
            ).mappings().all()
        target_ids = [f"memory-{int(row['public_id'])}" for row in rows]
        results.append(_comparison(record.vector_id, legacy_ids, target_ids))
    return results


def _comparison(query_id: str, legacy_ids: list[str], target_ids: list[str]):
    compared = max(min(len(legacy_ids), len(target_ids)), 1)
    overlap = len(set(legacy_ids) & set(target_ids)) / compared
    top_one = bool(legacy_ids and target_ids and legacy_ids[0] == target_ids[0])
    return {
        "query_id": query_id,
        "legacy_ids": legacy_ids,
        "cockroach_ids": target_ids,
        "top_1_match": top_one,
        "top_k_overlap": round(overlap, 4),
        "accepted": top_one and overlap >= 0.8,
    }


def _compare_document_lineage(snapshot: SourceSnapshot) -> dict[str, object]:
    documents = {int(row["id"]): row for row in snapshot.rows["documents"]}
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.id,c.page_number,c.slide_number,c.filename_snapshot,c.mime_type "
                "FROM document_chunks c JOIN documents d ON d.id=c.document_id "
                "WHERE d.legacy_sqlite_id IS NOT NULL"
            )
        ).mappings().all()
    actual = {str(row["id"]): row for row in rows}
    mismatches = 0
    for record in snapshot.document_vectors:
        owner = documents[int(record.metadata["document_id"])]
        target_id = deterministic_legacy_uuid(
            owner["workspace_id"], "document_chunks", record.vector_id
        )
        row = actual.get(str(target_id))
        if row is None:
            mismatches += 1
            continue
        expected_page = record.metadata.get("page_number")
        expected_slide = record.metadata.get("slide_number")
        if (
            row["page_number"] != expected_page
            or row["slide_number"] != expected_slide
            or str(row["filename_snapshot"]) != str(record.metadata.get("filename", owner["filename"]))
            or str(row["mime_type"]) != str(record.metadata.get("mime_type", owner["mime_type"]))
        ):
            mismatches += 1
    return {
        "passed": mismatches == 0 and len(actual) == len(snapshot.document_vectors),
        "checked_chunks": len(snapshot.document_vectors),
        "mismatch_count": mismatches,
    }


def _compare_memory_filters(snapshot: SourceSnapshot) -> dict[str, object]:
    source = list(snapshot.rows["memories"])
    source_active = sorted(int(row["id"]) for row in source if row["status"] == "active")
    source_archived = sorted(int(row["id"]) for row in source if row["status"] == "archived")
    source_threshold = sorted(
        int(row["id"])
        for row in source
        if float(row["confidence"]) >= 0.5 and float(row["importance"]) >= 0.5
    )
    source_duplicates = sorted(
        str(row["content"]).strip().casefold()
        for row in source
        if sum(
            1 for candidate in source
            if str(candidate["content"]).strip().casefold() == str(row["content"]).strip().casefold()
        ) > 1
    )
    with get_engine().connect() as connection:
        target_active = sorted(
            int(value) for value in connection.execute(
                text("SELECT public_id FROM learner_memories WHERE status='active' AND legacy_sqlite_id IS NOT NULL")
            ).scalars()
        )
        target_archived = sorted(
            int(value) for value in connection.execute(
                text("SELECT public_id FROM learner_memories WHERE status='archived' AND legacy_sqlite_id IS NOT NULL")
            ).scalars()
        )
        target_threshold = sorted(
            int(value) for value in connection.execute(
                text(
                    "SELECT public_id FROM learner_memories "
                    "WHERE confidence >= 0.5 AND importance >= 0.5 "
                    "AND legacy_sqlite_id IS NOT NULL"
                )
            ).scalars()
        )
        duplicate_rows = connection.execute(
            text(
                "SELECT lower(trim(content)) AS normalized,count(*) FROM learner_memories "
                "WHERE legacy_sqlite_id IS NOT NULL "
                "GROUP BY normalized HAVING count(*) > 1 ORDER BY normalized"
            )
        ).all()
    target_duplicates = sorted(
        str(normalized)
        for normalized, count in duplicate_rows
        for _ in range(int(count))
    )
    checks = {
        "active_filter": source_active == target_active,
        "archive_filter": source_archived == target_archived,
        "confidence_importance_filter": source_threshold == target_threshold,
        "normalized_duplicate_detection": source_duplicates == target_duplicates,
    }
    return {
        "passed": all(checks.values()),
        "checks": checks,
        "active_count": len(target_active),
        "archived_count": len(target_archived),
        "threshold_match_count": len(target_threshold),
        "duplicate_count": len(target_duplicates),
    }


def _compare_workspace_isolation() -> dict[str, object]:
    foreign_workspace = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
    tables = (
        "documents", "document_chunks", "learner_memories",
        "learner_memory_embeddings", "study_sessions", "quiz_attempts",
        "learning_signals", "workflow_states", "adaptation_events",
    )
    with get_engine().connect() as connection:
        foreign_counts = {
            table: int(
                connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE workspace_id=:workspace_id"),
                    {"workspace_id": foreign_workspace},
                ).scalar_one()
            )
            for table in tables
        }
        owned_counts = {
            table: int(
                connection.execute(
                    text(
                        f"SELECT count(*) FROM {table} "
                        "WHERE workspace_id != :workspace_id"
                    ),
                    {"workspace_id": UUID(DEFAULT_WORKSPACE_ID)},
                ).scalar_one()
            )
            for table in tables
        }
    passed = not any(foreign_counts.values()) and not any(owned_counts.values())
    return {
        "passed": passed,
        "foreign_workspace_visible_records": sum(foreign_counts.values()),
        "records_outside_default_workspace": sum(owned_counts.values()),
    }


def _compare_relational_behaviors(snapshot: SourceSnapshot) -> dict[str, object]:
    from backend.repositories.cockroach import (
        CockroachAdaptationEventRepository,
        CockroachDashboardRepository,
        CockroachDocumentRepository,
        CockroachLearnerMemoryRepository,
        CockroachLearningSignalRepository,
        CockroachNotebookRepository,
        CockroachQuizRepository,
        CockroachStudySessionRepository,
    )

    dashboard = CockroachDashboardRepository().build(100)
    expected_dashboard = {
        "documents": len(snapshot.rows["documents"]),
        "notebooks": len(snapshot.rows["notebooks"]),
        "study_sessions": len(snapshot.rows["study_sessions"]),
        "interactions": len(snapshot.rows["study_interactions"]),
        "quiz_attempts": len(snapshot.rows["quiz_attempts"]),
        "topics": len(snapshot.rows["topics"]),
    }
    dashboard_matches = all(
        int(dashboard["counts"][key]) >= value for key, value in expected_dashboard.items()
    )
    checks = {
        "dashboard_totals": dashboard_matches,
        "notebooks": set(int(row["id"]) for row in snapshot.rows["notebooks"])
        <= set(item.id for item in CockroachNotebookRepository().list()),
        "documents": set(int(row["id"]) for row in snapshot.rows["documents"])
        <= set(item.id for item in CockroachDocumentRepository().list()),
        "study_sessions": set(int(row["id"]) for row in snapshot.rows["study_sessions"])
        <= set(item.id for item in CockroachStudySessionRepository().list()),
        "quiz_reports": set(int(row["id"]) for row in snapshot.rows["quiz_attempts"])
        <= set(item.id for item in CockroachQuizRepository().list_attempts()),
        "learning_signals": set(str(row["id"]) for row in snapshot.rows["learning_signals"])
        <= set(item.id for item in CockroachLearningSignalRepository().list()),
        "learner_memories": set(int(row["id"]) for row in snapshot.rows["memories"])
        <= set(item.id for item in CockroachLearnerMemoryRepository().list(True)),
        "adaptation_events": set(str(row["id"]) for row in snapshot.rows["adaptation_events"])
        <= set(item.id for item in CockroachAdaptationEventRepository().list()),
    }
    with get_engine().connect() as connection:
        checks["workflow_states"] = set(
            str(value) for value in connection.execute(text("SELECT id FROM workflow_states")).scalars()
        ) >= set(str(row["id"]) for row in snapshot.rows["workflow_states"])
    return {"passed": all(checks.values()), "checks": checks}


def _compare_topic_exact_sources(snapshot: SourceSnapshot) -> dict[str, object]:
    expected = sorted(
        (str(row["topic_id"]), int(row["source_index"]), int(row["document_id"]), int(row["chunk_index"]))
        for row in snapshot.rows["topic_sources"]
    )
    with get_engine().connect() as connection:
        rows = connection.execute(
            text(
                "SELECT s.topic_id,s.source_index,d.public_id AS document_id,s.chunk_index "
                "FROM topic_sources s LEFT JOIN documents d ON d.id=s.document_id"
            )
        ).all()
    actual = sorted((str(topic), int(index), int(document), int(chunk)) for topic, index, document, chunk in rows)
    return {
        "status": "pass" if expected == actual else "blocked",
        "checked": len(expected),
        "mismatch_count": 0 if expected == actual else 1,
    }


def main() -> int:
    try:
        result = compare()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["status"] == "pass" else 2
    except Exception as error:
        print(f"Dual-read comparison failed safely ({type(error).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
