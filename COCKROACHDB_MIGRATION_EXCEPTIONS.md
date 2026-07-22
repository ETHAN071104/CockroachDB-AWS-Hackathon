# CockroachDB Migration Exceptions

Date: 2026-07-22

Migration import exception count: 0

Post-migration runtime verification exception count: 0

No imported SQLite or Chroma record was skipped, duplicated, orphaned, or altered. The complete 85-object baseline passed count, checksum, deterministic mapping, workspace, foreign-key, citation-lineage, blob, and vector validation.

## Resolved runtime finding

| Code | Affected rows | Resolution | Final verification |
|---|---:|---|---|
| `runtime_quiz_chunk_lineage_missing` | 2 | Runtime insertion now resolves one owned chunk and writes its UUID in the quiz transaction. The two authorized Gate 6/7 rows were repaired by an exact, guarded, single-transaction backfill. | Original 6 imported rows unchanged; repaired 2 correct; fresh runtime row correct; all 9 current rows valid |

There are no remaining migration or runtime-lineage exceptions. No credential or source content is recorded in this file.
