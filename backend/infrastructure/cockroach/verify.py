from __future__ import annotations

import json
import sys

from sqlalchemy import text

from backend.infrastructure.cockroach.importer import destination_counts
from backend.infrastructure.cockroach.source import load_source_snapshot
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
    mismatches = {
        key: {"source": expected, "destination": counts.get(key)}
        for key, expected in snapshot.counts.items()
        if counts.get(key) != expected
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
    if mismatches:
        raise RuntimeError("Source and destination counts differ.")
    if any(orphan_counts.values()):
        raise RuntimeError("Destination referential verification found orphan records.")
    expected_dimension = config.EMBEDDING_DIMENSION
    for value in vector_dimensions.values():
        if value is not None and int(value) != expected_dimension:
            raise RuntimeError("Destination vector dimension mismatch.")
    return {
        "status": "pass",
        "alembic_revision": str(revision),
        "source_fingerprint_matches": migration["source_fingerprint"] == snapshot.fingerprint,
        "counts": counts,
        "orphan_counts": orphan_counts,
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


def main() -> int:
    try:
        print(json.dumps(verify(), indent=2, sort_keys=True, default=str))
        return 0
    except Exception as error:
        print(f"Migration verification failed safely ({type(error).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
