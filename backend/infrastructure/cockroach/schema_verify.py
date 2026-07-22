from __future__ import annotations

import json
import re

from sqlalchemy import text

# Alembic revision filenames begin with digits and cannot be imported with normal syntax.
def _revision_module():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[3] / "alembic" / "versions" / "0001_agentbook_cockroach_schema.py"
    spec = importlib.util.spec_from_file_location("agentbook_revision_0001", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load revision 0001.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


from backend.repositories.cockroach.connection import get_engine


EXPECTED_REVISION = "0001_agentbook_cockroach_schema"
VECTOR_INDEXES = {
    "idx_document_chunks_workspace_embedding",
    "idx_memory_embeddings_workspace_embedding",
}


def verify() -> dict[str, object]:
    revision_module = _revision_module()
    table_sql = tuple(revision_module.TABLES)
    expected_tables = {
        re.search(r"CREATE TABLE\s+(\w+)", statement, re.IGNORECASE).group(1)
        for statement in table_sql
    }
    expected_indexes = {
        re.search(r"CREATE (?:UNIQUE )?INDEX\s+(\w+)", statement, re.IGNORECASE).group(1)
        for statement in revision_module.INDEXES
    }
    expected_uuid_primary_keys: dict[str, str] = {}
    expected_workspace_tables: set[str] = set()
    compatibility_columns: dict[str, set[str]] = {}
    expected_foreign_keys = 0
    expected_checks = 0
    for statement in table_sql:
        table = re.search(r"CREATE TABLE\s+(\w+)", statement, re.IGNORECASE).group(1)
        primary = re.search(r"^\s*(\w+)\s+UUID\s+PRIMARY KEY", statement, re.MULTILINE | re.IGNORECASE)
        if primary:
            expected_uuid_primary_keys[table] = primary.group(1)
        if re.search(r"^\s*workspace_id\s+UUID\s+NOT NULL", statement, re.MULTILINE | re.IGNORECASE):
            expected_workspace_tables.add(table)
        columns = {
            column for column in ("public_id", "legacy_sqlite_id")
            if re.search(rf"^\s*{column}\s+", statement, re.MULTILINE | re.IGNORECASE)
        }
        if columns:
            compatibility_columns[table] = columns
        expected_foreign_keys += len(re.findall(r"\bREFERENCES\b", statement, re.IGNORECASE))
        expected_checks += len(re.findall(r"\bCHECK\s*\(", statement, re.IGNORECASE))

    with get_engine().connect() as connection:
        revision = str(connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one())
        actual_tables = {
            str(value)
            for value in connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_type='BASE TABLE'"
                )
            ).scalars()
        }
        column_rows = connection.execute(
            text(
                "SELECT table_name,column_name,data_type,is_nullable "
                "FROM information_schema.columns WHERE table_schema='public'"
            )
        ).mappings().all()
        columns = {
            (str(row["table_name"]), str(row["column_name"])): row
            for row in column_rows
        }
        primary_rows = connection.execute(
            text(
                """
                SELECT tc.table_name,kcu.column_name,c.data_type
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON kcu.constraint_catalog=tc.constraint_catalog
                 AND kcu.constraint_schema=tc.constraint_schema
                 AND kcu.constraint_name=tc.constraint_name
                JOIN information_schema.columns c
                  ON c.table_schema=tc.table_schema
                 AND c.table_name=tc.table_name
                 AND c.column_name=kcu.column_name
                WHERE tc.table_schema='public' AND tc.constraint_type='PRIMARY KEY'
                """
            )
        ).mappings().all()
        primary_keys = {
            (str(row["table_name"]), str(row["column_name"]), str(row["data_type"]).lower())
            for row in primary_rows
        }
        foreign_key_rows = connection.execute(
            text(
                "SELECT table_name,constraint_name FROM information_schema.table_constraints "
                "WHERE table_schema='public' AND constraint_type='FOREIGN KEY'"
            )
        ).all()
        check_rows = connection.execute(
            text(
                "SELECT table_name,constraint_name FROM information_schema.table_constraints "
                "WHERE table_schema='public' AND constraint_type='CHECK'"
            )
        ).all()
        index_names: set[str] = set()
        for table in sorted(expected_tables):
            escaped = table.replace('"', '""')
            index_names.update(
                str(row["index_name"])
                for row in connection.exec_driver_sql(
                    f'SHOW INDEXES FROM "{escaped}"'
                ).mappings()
            )

    missing_tables = sorted(expected_tables - actual_tables)
    unexpected_tables = sorted(actual_tables - expected_tables - {"alembic_version"})
    missing_uuid_primary_keys = sorted(
        f"{table}.{column}"
        for table, column in expected_uuid_primary_keys.items()
        if (table, column, "uuid") not in primary_keys
    )
    missing_workspace_columns = sorted(
        table for table in expected_workspace_tables
        if (table, "workspace_id") not in columns
        or str(columns[(table, "workspace_id")]["is_nullable"]).upper() != "NO"
    )
    missing_compatibility_columns = sorted(
        f"{table}.{column}"
        for table, required in compatibility_columns.items()
        for column in required
        if (table, column) not in columns
    )
    missing_indexes = sorted(expected_indexes - index_names)
    present_vector_indexes = sorted(VECTOR_INDEXES & index_names)
    checks = {
        "revision_exact": revision == EXPECTED_REVISION,
        "all_tables": not missing_tables and not unexpected_tables,
        "uuid_primary_keys": not missing_uuid_primary_keys,
        "workspace_ownership_columns": not missing_workspace_columns,
        "compatibility_columns": not missing_compatibility_columns,
        "foreign_keys": len(foreign_key_rows) == expected_foreign_keys,
        "check_constraints": len(check_rows) >= expected_checks,
        "revision_0001_indexes": not missing_indexes,
        "vector_indexes_absent": not present_vector_indexes,
    }
    if not all(checks.values()):
        raise RuntimeError(
            "Gate 2 schema verification failed: "
            + json.dumps(
                {
                    "checks": checks,
                    "missing_tables": missing_tables,
                    "unexpected_tables": unexpected_tables,
                    "missing_uuid_primary_keys": missing_uuid_primary_keys,
                    "missing_workspace_columns": missing_workspace_columns,
                    "missing_compatibility_columns": missing_compatibility_columns,
                    "missing_indexes": missing_indexes,
                    "present_vector_indexes": present_vector_indexes,
                    "expected_foreign_keys": expected_foreign_keys,
                    "actual_foreign_keys": len(foreign_key_rows),
                    "expected_minimum_checks": expected_checks,
                    "actual_checks": len(check_rows),
                },
                sort_keys=True,
            )
        )
    return {
        "status": "pass",
        "revision": revision,
        "application_table_count": len(expected_tables),
        "uuid_primary_key_count": len(expected_uuid_primary_keys),
        "workspace_owned_table_count": len(expected_workspace_tables),
        "foreign_key_count": len(foreign_key_rows),
        "check_constraint_count": len(check_rows),
        "revision_0001_index_count": len(expected_indexes),
        "vector_indexes_present": present_vector_indexes,
        "credentials_recorded": False,
    }


def main() -> int:
    print(json.dumps(verify(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
