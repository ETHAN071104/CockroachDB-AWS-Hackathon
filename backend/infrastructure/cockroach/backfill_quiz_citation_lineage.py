from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from sqlalchemy import text

from backend.repositories.cockroach.connection import get_engine


CANDIDATE_SQL = text(
    """
    SELECT src.id,src.workspace_id,src.document_id,src.chunk_index,
           src.legacy_sqlite_id,q.legacy_sqlite_id AS question_legacy_id,
           attempt.legacy_sqlite_id AS attempt_legacy_id,
           attempt.requested_topic,
           (
             SELECT count(*) FROM document_chunks chunk
             WHERE chunk.workspace_id=src.workspace_id
               AND chunk.document_id=src.document_id
               AND chunk.chunk_index=src.chunk_index
           ) AS match_count,
           (
             SELECT chunk.id FROM document_chunks chunk
             WHERE chunk.workspace_id=src.workspace_id
               AND chunk.document_id=src.document_id
               AND chunk.chunk_index=src.chunk_index
             LIMIT 1
           ) AS resolved_chunk_id
    FROM quiz_question_sources src
    JOIN quiz_question_attempts q ON q.id=src.question_attempt_id
    JOIN quiz_attempts attempt ON attempt.id=q.quiz_attempt_id
    WHERE src.document_chunk_id IS NULL
      AND src.document_id IS NOT NULL
      AND src.chunk_index IS NOT NULL
    ORDER BY src.id
    """
)


def _candidates(connection) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(CANDIDATE_SQL).mappings().all()]


def _validate(rows: list[dict[str, Any]]) -> list[dict[str, object]]:
    if len(rows) != 2:
        raise RuntimeError("Targeted backfill requires exactly two candidate rows.")
    summary: list[dict[str, object]] = []
    for ordinal, row in enumerate(rows, start=1):
        runtime_row = (
            row["legacy_sqlite_id"] is None
            and row["question_legacy_id"] is None
            and row["attempt_legacy_id"] is None
            and (
                str(row["requested_topic"]).startswith("gate6live")
                or str(row["requested_topic"]).startswith("gate7txt")
            )
        )
        exact_match = int(row["match_count"]) == 1 and row["resolved_chunk_id"] is not None
        ownership = exact_match
        if not runtime_row or not exact_match or not ownership:
            raise RuntimeError("A targeted backfill candidate failed identity or ownership checks.")
        summary.append(
            {
                "ordinal": ordinal,
                "previously_identified_runtime_row": runtime_row,
                "exactly_one_chunk_match": exact_match,
                "ownership_check_passed": ownership,
            }
        )
    return summary


def dry_run() -> dict[str, object]:
    with get_engine().connect() as connection:
        rows = _candidates(connection)
        summary = _validate(rows)
    return {
        "status": "pass",
        "affected_row_count": len(rows),
        "rows": summary,
        "ownership_checks_passed": all(
            bool(item["ownership_check_passed"]) for item in summary
        ),
        "mutation_performed": False,
        "credentials_recorded": False,
        "source_content_recorded": False,
    }


def apply() -> dict[str, object]:
    with get_engine().begin() as connection:
        imported_before = tuple(
            connection.execute(
                text(
                    "SELECT id,document_chunk_id FROM quiz_question_sources "
                    "WHERE legacy_sqlite_id IS NOT NULL ORDER BY id"
                )
            ).all()
        )
        if len(imported_before) != 6 or any(row[1] is None for row in imported_before):
            raise RuntimeError("Imported baseline citation guard failed before backfill.")
        rows = _candidates(connection)
        _validate(rows)
        updated: list[dict[str, str]] = []
        for row in rows:
            result = connection.execute(
                text(
                    """
                    UPDATE quiz_question_sources
                    SET document_chunk_id=:document_chunk_id
                    WHERE id=:id
                      AND document_chunk_id IS NULL
                      AND workspace_id=:workspace_id
                      AND document_id=:document_id
                      AND chunk_index=:chunk_index
                    RETURNING id,document_chunk_id
                    """
                ),
                {
                    "id": row["id"],
                    "document_chunk_id": row["resolved_chunk_id"],
                    "workspace_id": row["workspace_id"],
                    "document_id": row["document_id"],
                    "chunk_index": row["chunk_index"],
                },
            ).mappings().all()
            if len(result) != 1:
                raise RuntimeError("A targeted citation row changed before update.")
            updated.append(
                {
                    "citation_id": str(result[0]["id"]),
                    "document_chunk_id": str(result[0]["document_chunk_id"]),
                }
            )
        if len(updated) != 2 or _candidates(connection):
            raise RuntimeError("Targeted backfill did not repair exactly two rows.")
        imported_after = tuple(
            connection.execute(
                text(
                    "SELECT id,document_chunk_id FROM quiz_question_sources "
                    "WHERE legacy_sqlite_id IS NOT NULL ORDER BY id"
                )
            ).all()
        )
        if imported_after != imported_before:
            raise RuntimeError("Imported baseline citations changed during backfill.")
    return {
        "status": "pass",
        "updated_row_count": len(updated),
        "updated": updated,
        "imported_baseline_unchanged": True,
        "credentials_recorded": False,
        "source_content_recorded": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run or apply the authorized two-row quiz citation lineage backfill."
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = apply() if args.apply else dry_run()
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as error:
        print(
            f"Targeted quiz citation backfill failed safely ({type(error).__name__}).",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
