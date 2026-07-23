from __future__ import annotations

import json

from sqlalchemy import text

from backend.repositories.cockroach.connection import get_engine


EXPECTED_COLUMNS = {
    "id": ("uuid", "NO"),
    "workspace_id": ("uuid", "NO"),
    "token_hash": ("text", "NO"),
    "creation_key_hash": ("text", "NO"),
    "status": ("text", "NO"),
    "created_at": ("timestamp with time zone", "NO"),
    "updated_at": ("timestamp with time zone", "NO"),
    "last_seen_at": ("timestamp with time zone", "YES"),
    "expires_at": ("timestamp with time zone", "YES"),
    "revoked_at": ("timestamp with time zone", "YES"),
    "version": ("bigint", "NO"),
    "session_label": ("text", "YES"),
}
EXPECTED_INDEXES = {
    "guest_sessions_pkey",
    "uq_guest_sessions_token_hash",
    "uq_guest_sessions_creation_key_hash",
    "idx_guest_sessions_workspace_status",
    "idx_guest_sessions_active_expiry",
}
EXPECTED_CHECKS = {
    "ck_guest_sessions_token_hash_length",
    "ck_guest_sessions_creation_hash_length",
    "ck_guest_sessions_status",
    "ck_guest_sessions_version",
    "ck_guest_sessions_updated_at",
    "ck_guest_sessions_last_seen_at",
    "ck_guest_sessions_expires_at",
    "ck_guest_sessions_revocation",
    "ck_guest_sessions_revoked_at",
    "ck_guest_sessions_label",
}
VECTOR_INDEXES = {
    "idx_document_chunks_workspace_embedding",
    "idx_memory_embeddings_workspace_embedding",
}


def verify() -> dict[str, object]:
    with get_engine().connect() as connection:
        revision = str(
            connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        )
        columns = {
            str(row["column_name"]): (
                str(row["data_type"]).casefold(),
                str(row["is_nullable"]).upper(),
            )
            for row in connection.execute(
                text(
                    "SELECT column_name,data_type,is_nullable "
                    "FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='guest_sessions'"
                )
            ).mappings()
        }
        constraints = {
            str(row["constraint_name"]): str(row["constraint_type"])
            for row in connection.execute(
                text(
                    "SELECT constraint_name,constraint_type "
                    "FROM information_schema.table_constraints "
                    "WHERE table_schema='public' AND table_name='guest_sessions'"
                )
            ).mappings()
        }
        indexes = {
            str(row["index_name"])
            for row in connection.exec_driver_sql(
                "SHOW INDEXES FROM guest_sessions"
            ).mappings()
        }
        vector_indexes = set()
        for table_name in (
            "document_chunks",
            "learner_memory_embeddings",
        ):
            vector_indexes.update(
                str(row["index_name"])
                for row in connection.exec_driver_sql(
                    f"SHOW INDEXES FROM {table_name}"
                ).mappings()
                if str(row["index_name"]) in VECTOR_INDEXES
            )
        delete_rule = connection.execute(
            text(
                """
                SELECT rc.delete_rule
                FROM information_schema.referential_constraints rc
                JOIN information_schema.table_constraints tc
                  ON tc.constraint_catalog=rc.constraint_catalog
                 AND tc.constraint_schema=rc.constraint_schema
                 AND tc.constraint_name=rc.constraint_name
                WHERE tc.table_schema='public'
                  AND tc.table_name='guest_sessions'
                """
            )
        ).scalar_one()
        session_count = int(
            connection.execute(
                text("SELECT count(*) FROM guest_sessions")
            ).scalar_one()
        )
    checks = {
        "revision_exact": revision == "0003_guest_sessions",
        "columns_exact": columns == EXPECTED_COLUMNS,
        "uuid_primary_key": constraints.get("guest_sessions_pkey") == "PRIMARY KEY",
        "token_hash_unique": constraints.get(
            "uq_guest_sessions_token_hash"
        ) == "UNIQUE",
        "creation_key_hash_unique": constraints.get(
            "uq_guest_sessions_creation_key_hash"
        ) == "UNIQUE",
        "check_constraints": EXPECTED_CHECKS.issubset(constraints),
        "indexes": EXPECTED_INDEXES.issubset(indexes),
        "workspace_fk_restrict": str(delete_rule).upper() in {
            "RESTRICT",
            "NO ACTION",
        },
        "vector_indexes_unchanged": vector_indexes == VECTOR_INDEXES,
    }
    if not all(checks.values()):
        raise RuntimeError(
            "Guest-session schema verification failed: "
            + json.dumps(checks, sort_keys=True)
        )
    return {
        "status": "pass",
        "revision": revision,
        "checks": checks,
        "guest_session_row_count": session_count,
        "credentials_recorded": False,
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
