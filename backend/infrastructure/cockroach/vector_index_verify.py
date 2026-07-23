from __future__ import annotations

import json
from uuid import UUID

from sqlalchemy import text

from backend.domain import DEFAULT_WORKSPACE_ID
from backend.infrastructure.cockroach.source import load_source_snapshot
from backend.rag.embeddings import vector_literal
from backend.repositories.cockroach.connection import get_engine


INDEXES = {
    "document_chunks": "idx_document_chunks_workspace_embedding",
    "learner_memory_embeddings": "idx_memory_embeddings_workspace_embedding",
}


def _index_definition(connection, table: str, index_name: str) -> str:
    rows = connection.exec_driver_sql(f"SHOW CREATE TABLE {table}").mappings().all()
    statement = str(rows[0]["create_statement"])
    matches = [line.strip().rstrip(",") for line in statement.splitlines() if index_name in line]
    if len(matches) != 1:
        raise RuntimeError(f"Expected one definition for {index_name}.")
    return matches[0]


def _explain(connection, statement: str, parameters: dict[str, object]) -> tuple[list[str], bool]:
    lines = [
        str(row[0])
        for row in connection.execute(text("EXPLAIN " + statement), parameters).all()
    ]
    lowered = "\n".join(lines).casefold()
    return lines, any(name.casefold() in lowered for name in INDEXES.values())


def verify() -> dict[str, object]:
    snapshot = load_source_snapshot()
    if snapshot.issues or not snapshot.document_vectors or not snapshot.memory_vectors:
        raise RuntimeError("Validated document and memory vectors are required.")
    workspace = UUID(DEFAULT_WORKSPACE_ID)
    foreign_workspace = UUID("ffffffff-ffff-4fff-8fff-ffffffffffff")
    document_vector = vector_literal(snapshot.document_vectors[0].embedding)
    memory_vector = vector_literal(snapshot.memory_vectors[0].embedding)
    document_query = (
        "SELECT d.public_id,c.chunk_index,"
        "c.embedding <=> CAST(:embedding AS VECTOR(384)) AS distance "
        "FROM document_chunks c JOIN documents d "
        "ON d.id=c.document_id AND d.workspace_id=c.workspace_id "
        "WHERE c.workspace_id=:workspace_id ORDER BY distance,c.id LIMIT 5"
    )
    memory_query = (
        "SELECT m.public_id,"
        "e.embedding <=> CAST(:embedding AS VECTOR(384)) AS distance "
        "FROM learner_memory_embeddings e JOIN learner_memories m "
        "ON m.id=e.memory_id AND m.workspace_id=e.workspace_id "
        "WHERE e.workspace_id=:workspace_id AND m.status='active' "
        "ORDER BY distance,m.public_id LIMIT 5"
    )
    with get_engine().connect() as connection:
        revision = str(connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one())
        definitions = {
            name: _index_definition(connection, table, name)
            for table, name in INDEXES.items()
        }
        document_rows = connection.execute(
            text(document_query), {"embedding": document_vector, "workspace_id": workspace}
        ).mappings().all()
        memory_rows = connection.execute(
            text(memory_query), {"embedding": memory_vector, "workspace_id": workspace}
        ).mappings().all()
        foreign_document_count = int(
            connection.execute(
                text("SELECT count(*) FROM document_chunks WHERE workspace_id=:workspace_id"),
                {"workspace_id": foreign_workspace},
            ).scalar_one()
        )
        foreign_memory_count = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM learner_memory_embeddings "
                    "WHERE workspace_id=:workspace_id"
                ),
                {"workspace_id": foreign_workspace},
            ).scalar_one()
        )
        document_plan, document_index_used = _explain(
            connection, document_query,
            {"embedding": document_vector, "workspace_id": workspace},
        )
        memory_plan, memory_index_used = _explain(
            connection, memory_query,
            {"embedding": memory_vector, "workspace_id": workspace},
        )
    result = {
        "status": "pass",
        "alembic_revision": revision,
        "index_definitions": definitions,
        "document_query": {
            "shape": "workspace equality + cosine distance + top-k",
            "results": [
                {
                    "document_public_id": int(row["public_id"]),
                    "chunk_index": int(row["chunk_index"]),
                    "distance": float(row["distance"]),
                }
                for row in document_rows
            ],
            "explain": document_plan,
            "index_used": document_index_used,
        },
        "memory_query": {
            "shape": "workspace equality + active filter + cosine distance + top-k",
            "results": [
                {"memory_public_id": int(row["public_id"]), "distance": float(row["distance"])}
                for row in memory_rows
            ],
            "explain": memory_plan,
            "index_used": memory_index_used,
        },
        "cross_workspace": {
            "document_results": foreign_document_count,
            "memory_results": foreign_memory_count,
        },
        "credentials_recorded": False,
    }
    if (
        revision != "0003_guest_sessions"
        or len(definitions) != 2
        or not document_rows
        or not memory_rows
        or foreign_document_count
        or foreign_memory_count
    ):
        raise RuntimeError("Gate 5 vector-index verification failed.")
    return result


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
