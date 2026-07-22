# CockroachDB Migration Report

Date: 2026-07-22

Overall migration status: PASS

The staged CockroachDB migration, targeted runtime citation-lineage repair, and post-fix regression are complete. The live database is at Alembic revision `0002_cockroach_vector_indexes`. The repository-root `.env` remains on `PERSISTENCE_BACKEND=sqlite`; no permanent runtime cutover was performed and no credential is recorded in this report.

## Final gate results

| Gate | Result | Sanitized evidence |
|---|---|---|
| Cloud preflight | PASS | TLS `verify-full`; CockroachDB CCL v26.2.1; VECTOR capabilities; SQLAlchemy/Alembic connectivity; required permissions |
| Revision 0001 | PASS | 24 application tables, UUID primary keys, workspace ownership constraints, foreign keys, and non-vector indexes verified |
| Source import | PASS | Complete 85-object baseline; 84/84 migration items; 28 deterministic mappings; blob bytes and 26 imported vectors matched |
| Dual-read comparison | PASS | Baseline document and memory samples had top-1 identity match and top-k overlap 1.0; isolation checks passed |
| Revision 0002 | PASS | Both workspace-prefixed cosine vector indexes exist and representative queries passed |
| Runtime citation fix | PASS | Runtime quiz citations now resolve exactly one owned chunk and persist `document_chunk_id` in the quiz transaction; invalid lineage fails and rolls back |
| Targeted backfill | PASS | Exactly two authorized runtime rows were repaired in one guarded transaction; zero other rows changed |
| Live regression | PASS | A fresh document-backed quiz citation immediately stored the correct chunk UUID and remained correct after restart |
| Final verification | PASS | Verification, dual-read comparison, vector-index verification, backend/frontend tests, credential scan, and diff checks passed |

## Targeted citation repair

The dry run selected exactly two rows with non-null `document_id` and `chunk_index` but null `document_chunk_id`. Both were the preserved Gate 6/7 runtime rows, both had exactly one workspace-owned chunk match, and no source content was emitted.

The apply step used one transaction, parameterized SQL, exact citation UUID guards, the still-null condition, and workspace/document/chunk equality guards. It required exactly two returned rows and rechecked the original six imported citations before commit.

| Citation ID | Resolved chunk ID | Result |
|---|---|---|
| `2a33dad9-95c4-4058-ab23-15b008cb6c85` | `cf5a3c78-3925-5fdd-9c54-fb16f8f6387c` | repaired and verified |
| `942cd1a9-1f16-4453-b3b0-9d845f2cb9f1` | `7e99ffeb-3a64-5165-8dc1-6e2301ee40ad` | repaired and verified |

No broad update, delete, table recreation, Alembic rerun, or data-migration rerun occurred. The six imported citations remained unchanged. After the fresh live regression, all 9 current `quiz_question_sources` rows have valid expected lineage.

## Preserved migration baseline

The SQLite/Chroma source fingerprint remains `401b389c323c1fd8358940aef6af1ef22821617a0728359a13ed21c95d8f8f43`. The source contains 85 objects and zero validation exceptions.

| Entity | Imported baseline | Final live count |
|---|---:|---:|
| workspaces | 1 | 1 |
| notebooks | 1 | 1 |
| documents / blobs | 1 / 1 | 2 / 2 |
| notebook assignments | 1 | 1 |
| document chunks / embeddings | 22 | 23 |
| study sessions / interactions / sources | 2 / 1 / 5 | 2 / 1 / 5 |
| quiz attempts / questions / sources | 2 / 6 / 6 | 5 / 9 / 9 |
| learning signals | 4 | 7 |
| learner memories / embeddings / relationships | 4 / 4 / 0 | 7 / 7 / 0 |
| workflow states | 8 | 20 |
| adaptation events | 12 | 18 |
| embedding jobs | 4 | 8 |
| cached intelligence / topics / topic sources | 0 / 0 / 0 | 0 / 0 / 0 |

The final live application-object count is 128. The 43 records above the 85-object baseline are the authorized Gate 6/7 and final regression records. The targeted two-row backfill changed only lineage UUID values and did not change record counts.

## Verification evidence

- Alembic revision: `0002_cockroach_vector_indexes`.
- Migration items: 84 expected, 84 actual, zero mismatches, zero unexpected.
- Source fingerprint: matched; SQLite and Chroma were unchanged.
- Imported vectors: 26 checked at dimension 384; maximum absolute delta 0; zero mismatches.
- Current citation-lineage mismatches: zero for quiz, study-interaction, and topic sources.
- Workspace and referential orphan checks: zero.
- Original imported quiz citations: 6/6 unchanged.
- Repaired citations: 2/2 point to their expected owned chunks.
- Fresh runtime citation: correct immediately and after backend restart.
- Vector indexes: `idx_document_chunks_workspace_embedding` and `idx_memory_embeddings_workspace_embedding` both exist.
- Cockroach mode live tests used failure sentinels for SQLite and Chroma; neither source was accessed.

Live `EXPLAIN` did not select the vector indexes on the small final dataset of 23 document vectors and 7 memory vectors. The definitions and query shapes are valid and live retrieval passed, but optimizer index use is not claimed.

## Test results

- Python compilation: PASS.
- Complete backend suite: 91 passed, 5 conditionally skipped.
- Cockroach repository and live citation-lineage suite: 8 passed.
- Controlled live CockroachDB Agentic Learning Loop: 1 passed.
- Frontend Vitest suite: 7 files, 19 tests passed.
- Frontend production build: PASS.
- Migration verification: PASS.
- Dual-read comparison: PASS.
- Vector-index verification: PASS.
- Credential scan: PASS.
- `git diff --check`: PASS.

The safe TXT smoke test had already exercised the same upload and quiz path with deterministic model/embedding stubs, so it was not rerun after the fresh live quiz regression. The final live loop created 13 additional durable test records; these are included in the 128-record total.

## Scope and runtime state

- The repository-root `.env` remains `PERSISTENCE_BACKEND=sqlite`.
- No SQLite row, Chroma record, or rollback-source file was modified.
- No permanent table or index was created, altered, or deleted during this repair task.
- No AWS, S3, deployment, authentication, notifications, or visual-design work was performed.
- Permanent CockroachDB cutover requires separate authorization.
