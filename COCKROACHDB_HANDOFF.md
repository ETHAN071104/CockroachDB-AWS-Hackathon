# CockroachDB Backend Handoff

Date: 2026-07-22

Current state: PASS. The live schema and data are at revision `0002_cockroach_vector_indexes`; the runtime quiz citation defect is fixed, the two authorized preserved rows are repaired, and final verification is complete.

The repository-root `.env` intentionally remains on `PERSISTENCE_BACKEND=sqlite`. A permanent CockroachDB cutover requires separate authorization.

## Completed work

- CockroachDB Cloud preflight passed with TLS `verify-full` against CockroachDB CCL v26.2.1.
- Revisions `0001_agentbook_cockroach_schema` and `0002_cockroach_vector_indexes` were applied and verified in the previously authorized migration stages.
- The complete 85-object SQLite/Chroma baseline was imported with zero migration exceptions.
- All 84 migration items, 28 deterministic mappings, workspace ownership checks, foreign keys, blob bytes, six imported quiz citations, and 26 imported vectors match.
- Both cosine vector indexes exist and live document/memory retrieval and workspace isolation pass.
- CockroachDB startup, health, dashboard, restart, durable workflows, semantic memory retrieval, controlled Agentic Learning Loop, and safe TXT smoke passed.
- Runtime quiz citation insertion now resolves the chunk by workspace, document public ID, and chunk index, requires exactly one owned match, and persists `document_chunk_id` in the same transaction.
- Missing, ambiguous, partial, and cross-workspace lineage fails safely and rolls back.
- Exactly two authorized runtime citations were repaired in one guarded transaction; no other row changed.
- A fresh live citation stored the correct chunk UUID immediately and remained correct after restart.
- All 9 current quiz citations have valid lineage; the six imported citations remain unchanged.
- The SQLite/Chroma fingerprint remains `401b389c323c1fd8358940aef6af1ef22821617a0728359a13ed21c95d8f8f43` with 85 source objects and zero validation exceptions.

## Final live state

The live destination contains 128 application records: the verified 85-object migration baseline plus 43 authorized runtime-test records. The final lineage mismatch count, referential orphan count, workspace orphan count, migration-item mismatch count, and imported-vector mismatch count are all zero.

The final regression added 13 durable records: one quiz attempt, question, citation, signal, memory, memory embedding, and embedding job; four workflow states; and two adaptation events. The TXT upload was not repeated because the fresh quiz regression directly verified the repaired runtime path.

## Verification commands

These final checks passed:

```powershell
python -m backend.infrastructure.cockroach.verify
python -m backend.infrastructure.cockroach.compare
python -m backend.infrastructure.cockroach.vector_index_verify
python -m compileall -q backend alembic api main.py tests
python -m unittest discover -s tests
npm test
npm run build
git diff --check
```

Results: backend 91 passed with 5 conditional skips; live repository/lineage 8 passed; controlled live Agentic Learning Loop 1 passed; frontend 7 files and 19 tests passed; production build passed.

## Runtime selection

For a separately authorized temporary Cockroach runtime check, set `PERSISTENCE_BACKEND=cockroach` in the process environment only. Cockroach mode fails startup instead of falling back to SQLite or Chroma. Do not edit the repository-root `.env` for permanent cutover without authorization.

## Vector-index note

The following indexes exist and their workspace-filtered cosine query shapes pass:

- `idx_document_chunks_workspace_embedding`
- `idx_memory_embeddings_workspace_embedding`

Live `EXPLAIN` used ordinary scans because the final tables contain only 23 document vectors and 7 learner-memory vectors. Optimizer use of either vector index is therefore not claimed.

## Security and scope

- No credential is present in the reports or tracked changes.
- SQLite and Chroma remain unchanged rollback sources.
- The repair created, altered, or deleted no permanent table or index and reran no migration.
- AWS, S3, deployment, authentication, notifications, and frontend redesign remain out of scope.
- The CockroachDB `BYTES` blob adapter remains a compatibility implementation under the existing upload limit.
