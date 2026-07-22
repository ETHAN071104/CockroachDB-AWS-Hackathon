from __future__ import annotations

import json
import sys
from uuid import UUID

import chromadb
from sqlalchemy import text

from backend.domain import deterministic_legacy_uuid
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
        name: counts.get(name) == expected
        for name, expected in snapshot.counts.items()
    }
    document_results = _compare_documents(snapshot, sample_size, top_k)
    memory_results = _compare_memories(snapshot, sample_size, top_k)
    all_samples = [*document_results, *memory_results]
    passed = all(count_matches.values()) and all(bool(item["accepted"]) for item in all_samples)
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
        "relational_count_matches": count_matches,
        "document_samples": document_results,
        "memory_samples": memory_results,
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
        with get_engine().connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT c.id,c.embedding <=> CAST(:embedding AS VECTOR(384)) AS distance
                    FROM document_chunks c JOIN documents d ON d.id=c.document_id
                    WHERE c.workspace_id=:workspace_id
                    """ + clause + " ORDER BY distance,c.id LIMIT :limit"
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
