from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path

from alembic.migration import MigrationContext
from sqlalchemy import text

from backend.rag import config
from backend.repositories.cockroach.connection import cockroach_url, get_engine


ROOT = Path(__file__).resolve().parents[3]
REPORT_PATH = ROOT / "PRE_COCKROACH_CLUSTER_REPORT.md"

INDEX_STATEMENTS = (
    "CREATE VECTOR INDEX idx_document_chunks_workspace_embedding "
    "ON document_chunks (workspace_id, embedding vector_cosine_ops)",
    "CREATE VECTOR INDEX idx_memory_embeddings_workspace_embedding "
    "ON learner_memory_embeddings (workspace_id, embedding vector_cosine_ops)",
)


def _catalog_fingerprint(connection) -> str:
    rows = connection.execute(
        text(
            """
            SELECT table_schema, table_name, table_type
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_schema, table_name, table_type
            """
        )
    ).all()
    serialized = json.dumps([list(row) for row in rows], separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _fixed_dimension_rejected(engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT '[1,2]'::VECTOR(3)")).scalar_one()
    except Exception:
        return True
    return False


def run() -> dict[str, object]:
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is unavailable.")
    if config.PERSISTENCE_BACKEND != "sqlite":
        raise RuntimeError("PERSISTENCE_BACKEND must remain sqlite during preflight.")
    if cockroach_url().query.get("sslmode") != "verify-full":
        raise RuntimeError("CockroachDB TLS mode must be verify-full.")

    engine = get_engine()
    with engine.connect() as connection:
        before = _catalog_fingerprint(connection)
        version = str(connection.execute(text("SELECT version()" )).scalar_one())
        vector_three = connection.execute(
            text("SELECT '[1,0,0]'::VECTOR(3) <=> '[0,1,0]'::VECTOR(3)")
        ).scalar_one()
        zero_vector = "[" + ",".join("0" for _ in range(384)) + "]"
        vector_384 = connection.execute(
            text("SELECT CAST(:value AS VECTOR(384)) IS NOT NULL"), {"value": zero_vector}
        ).scalar_one()
        vector_index_enabled = connection.execute(
            text("SHOW CLUSTER SETTING feature.vector_index.enabled")
        ).scalar_one()
        syntax_rows = []
        for statement in INDEX_STATEMENTS:
            escaped = statement.replace("'", "''")
            rows = connection.exec_driver_sql(
                f"SHOW SYNTAX '{escaped}'"
            ).mappings().all()
            syntax_rows.append(rows)
        database_name = str(connection.execute(text("SELECT current_database()" )).scalar_one())
        connect_permission = bool(
            connection.execute(
                text("SELECT has_database_privilege(current_user, :database_name, 'CONNECT')"),
                {"database_name": database_name},
            ).scalar_one()
        )
        usage_permission = bool(
            connection.execute(
                text("SELECT has_schema_privilege(current_user, 'public', 'USAGE')")
            ).scalar_one()
        )
        create_permission = bool(
            connection.execute(
                text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
            ).scalar_one()
        )
        MigrationContext.configure(connection)
        after = _catalog_fingerprint(connection)
        public_table_count = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_type='BASE TABLE'"
                )
            ).scalar_one()
        )

    fixed_rejected = _fixed_dimension_rejected(engine)
    syntax_accepted = all(
        rows and not any(str(row.get("field", "")).lower() == "error" for row in rows)
        for rows in syntax_rows
    )
    checks = {
        "tls_verify_full": True,
        "version": version,
        "vector_3_cast": float(vector_three) == 1.0,
        "fixed_dimension_rejected": fixed_rejected,
        "vector_384": bool(vector_384),
        "vector_index_enabled": bool(vector_index_enabled),
        "cosine_index_syntax": syntax_accepted,
        "sqlalchemy_connectivity": True,
        "alembic_migration_context": True,
        "database_connect_permission": connect_permission,
        "schema_usage_permission": usage_permission,
        "schema_create_permission": create_permission,
        "catalog_unchanged": before == after,
        "public_table_count": public_table_count,
    }
    passed = (
        "CockroachDB" in version
        and all(value for key, value in checks.items() if key not in {"version", "public_table_count"})
    )
    if not passed:
        raise RuntimeError("One or more live preflight checks failed.")
    return checks


def write_report(result: dict[str, object]) -> None:
    version = str(result["version"])
    short_version = "CockroachDB " + version.split("CockroachDB ", 1)[1].split(" ", 1)[0]
    lines = [
        "# CockroachDB Cloud Preflight Report",
        "",
        f"Date: {date.today().isoformat()}",
        "Scope: non-destructive live-cluster readiness preflight",
        "Overall result: PASS",
        "",
        "## Verified results",
        "",
        "- Repository-root `.env` loaded successfully.",
        "- `DATABASE_URL` was available but never displayed.",
        "- `PERSISTENCE_BACKEND=sqlite` was confirmed.",
        "- TLS `verify-full` connection passed.",
        f"- Live version was {short_version}.",
        "- `VECTOR(3)` cast passed.",
        "- Fixed vector-dimension rejection passed.",
        "- `VECTOR(384)` passed.",
        "- Vector-index feature was enabled.",
        "- Both proposed cosine vector-index statements were accepted through `SHOW SYNTAX`.",
        "- SQLAlchemy `cockroachdb+psycopg` connection passed.",
        "- Alembic `MigrationContext` connection passed without applying a revision.",
        "- Database `CONNECT` permission passed.",
        "- Schema `USAGE` permission passed.",
        "- Schema `CREATE` permission passed.",
        "- Permanent catalog fingerprint was unchanged.",
        f"- Public base-table count observed: {result['public_table_count']}.",
        "- No permanent table or index was created, altered, or deleted.",
        "- No Alembic migration was executed.",
        "- No data migration was executed.",
        "- No credentials were recorded.",
        "",
    ]
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    result = run()
    write_report(result)
    print(
        "Live preflight passed: TLS, vectors, parser, SQLAlchemy, Alembic, "
        f"permissions; public tables={result['public_table_count']}; catalog unchanged."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
