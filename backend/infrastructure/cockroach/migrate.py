from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from backend.infrastructure.cockroach.importer import import_snapshot
from backend.infrastructure.cockroach.source import SourceSnapshot, load_source_snapshot
from backend.rag import config
from backend.repositories.cockroach.connection import get_engine


ROOT = Path(__file__).resolve().parents[3]
MANIFEST_PATH = ROOT / "COCKROACHDB_MIGRATION_MANIFEST.json"
EXCEPTIONS_PATH = ROOT / "COCKROACHDB_MIGRATION_EXCEPTIONS.md"


def run(*, dry_run: bool) -> int:
    if not config.DATABASE_URL:
        raise RuntimeError("DATABASE_URL is required for CockroachDB migration.")
    snapshot = load_source_snapshot()
    _write_exceptions(snapshot)
    status = "blocked" if snapshot.issues else ("dry_run_passed" if dry_run else "running")
    _write_manifest(snapshot, status=status, destination_counts=None)
    if snapshot.issues:
        print(f"Migration {status}: {len(snapshot.issues)} validated exception(s).")
        return 2
    if dry_run:
        print(
            "Migration dry run passed: "
            f"{sum(snapshot.counts.values())} source records validated."
        )
        return 0
    destination_counts = import_snapshot(get_engine(), snapshot)
    _write_manifest(snapshot, status="migrated", destination_counts=destination_counts)
    print(
        "Migration completed: "
        f"{sum(destination_counts.values())} destination records verified by count."
    )
    return 0


def _write_manifest(
    snapshot: SourceSnapshot,
    *,
    status: str,
    destination_counts: dict[str, int] | None,
) -> None:
    manifest = {
        "format_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "source_fingerprint": snapshot.fingerprint,
        "embedding_dimension": config.EMBEDDING_DIMENSION,
        "embedding_model": config.EMBEDDING_MODEL,
        "source_vector_metric": "l2_on_unit_normalized_embeddings",
        "target_vector_metric": "cosine_distance",
        "source_counts": snapshot.counts,
        "destination_counts": destination_counts,
        "exception_count": len(snapshot.issues),
        "id_strategy": "UUIDv5(workspace_id, source_table, legacy_identity)",
        "source_mutated": False,
        "credentials_recorded": False,
    }
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _write_exceptions(snapshot: SourceSnapshot) -> None:
    lines = [
        "# CockroachDB Migration Exceptions",
        "",
        "This file contains sanitized validation exceptions. It never contains credentials or source content.",
        "",
    ]
    if not snapshot.issues:
        lines.extend(["No migration exceptions were detected.", ""])
    else:
        lines.extend(["| Code | Source | Identity | Detail |", "|---|---|---|---|"])
        for issue in snapshot.issues:
            fields = [issue.code, issue.source, issue.identity, issue.detail]
            lines.append("| " + " | ".join(value.replace("|", "\\|") for value in fields) + " |")
        lines.append("")
    EXCEPTIONS_PATH.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate SQLite and Chroma data to CockroachDB.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate source data and write a manifest without changing CockroachDB.",
    )
    args = parser.parse_args(argv)
    try:
        return run(dry_run=args.dry_run)
    except Exception as error:
        print(f"Migration failed safely ({type(error).__name__}).", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
