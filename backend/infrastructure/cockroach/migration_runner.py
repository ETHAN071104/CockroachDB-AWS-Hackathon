from __future__ import annotations

import argparse
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from backend.rag import config
from backend.repositories.cockroach.connection import cockroach_url, get_engine


ROOT = Path(__file__).resolve().parents[3]


def preflight() -> dict[str, object]:
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required.")
    url = cockroach_url()
    if url.query.get("sslmode") != "verify-full":
        raise RuntimeError("DATABASE_URL must use sslmode=verify-full.")
    with get_engine().connect() as connection:
        version = str(connection.execute(text("SELECT version()" )).scalar_one())
        database_name = str(connection.execute(text("SELECT current_database()" )).scalar_one())
        schema_create = bool(
            connection.execute(
                text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
            ).scalar_one()
        )
    if "CockroachDB" not in version or not schema_create:
        raise RuntimeError("CockroachDB identity or schema permission check failed.")
    return {
        "tls_mode": "verify-full",
        "version": version,
        "database_name": database_name,
        "schema_create": schema_create,
        "credentials_recorded": False,
    }


def upgrade(target: str) -> None:
    preflight()
    alembic_config = Config(str(ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(alembic_config, target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run sanitized CockroachDB schema operations.")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("preflight")
    upgrade_parser = subparsers.add_parser("upgrade")
    upgrade_parser.add_argument(
        "target", nargs="?", default="0001_agentbook_cockroach_schema",
        help="Alembic target. Use head only after vector data has been imported and verified.",
    )
    args = parser.parse_args(argv)
    try:
        if args.action == "preflight":
            result = preflight()
            print(
                "CockroachDB preflight passed: "
                f"TLS={result['tls_mode']}; schema CREATE={result['schema_create']}."
            )
        else:
            upgrade(args.target)
            print(f"Alembic upgrade completed: {args.target}.")
        return 0
    except Exception as error:
        print(f"CockroachDB operation failed safely ({type(error).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
