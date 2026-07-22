from __future__ import annotations

from sqlalchemy import text

from backend.repositories.cockroach.connection import get_engine


def cockroach_health() -> dict[str, object]:
    """Return a credential-free CockroachDB readiness snapshot."""
    with get_engine().connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT version() AS version,
                       current_database() AS database_name,
                       (SELECT count(*) FROM information_schema.tables
                        WHERE table_schema='public') AS public_table_count
                """
            )
        ).mappings().one()
        revision = None
        if int(row["public_table_count"]) > 0:
            exists = connection.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name='alembic_version'"
                )
            ).scalar_one()
            if exists:
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
    return {
        "status": "ok",
        "version": str(row["version"]),
        "database_name": str(row["database_name"]),
        "public_table_count": int(row["public_table_count"]),
        "alembic_revision": revision,
        "credentials_recorded": False,
    }
