# CockroachDB Backend Handoff

Date: 2026-07-23

Current state: PASS. The live schema is at revision
`0003_guest_sessions`; Guest Workspace request isolation and the earlier
CockroachDB migration, vector indexes, citation repair, and Agentic Learning
Loop are verified.

The repository-root `.env` selects `PERSISTENCE_BACKEND=cockroach`. No SQLite
or Chroma adapter participates in the public Cockroach runtime.

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
- The additive `0003_guest_sessions` revision created only the reviewed table,
  constraints, and two guest-session indexes.
- Guest credentials are 256-bit opaque values; CockroachDB stores only
  domain-separated HMAC-SHA256 digests.
- All user-data routers require a valid bearer session in public mode and bind
  a request-scoped repository bundle to the server-derived workspace.
- A live two-guest proof passed relational isolation, document-vector
  isolation, learner-memory-vector isolation, guessed-ID denial, workspace
  tampering denial, missing-credential denial, and repository/engine restart.
- The SQLite/Chroma fingerprint remains `401b389c323c1fd8358940aef6af1ef22821617a0728359a13ed21c95d8f8f43` with 85 source objects and zero validation exceptions.

## Final live state

The legacy/default workspace baseline remains unchanged with fingerprint
`fc41c2aef689c80f4e346a35733b38b29d817db7abe3c44983c10f69716eba56`.
Its expected workspace-filtered counts were rechecked before and after the live
guest proof. Guest proof rows exist only in two newly created private
workspaces.

The final regression added 13 durable records: one quiz attempt, question, citation, signal, memory, memory embedding, and embedding job; four workflow states; and two adaptation events. The TXT upload was not repeated because the fresh quiz regression directly verified the repaired runtime path.

## Verification commands

The original one-time migration previously passed its source-coupled verifier.
Phase 5 passed:

```powershell
python -m backend.infrastructure.cockroach.vector_index_verify
python -m backend.infrastructure.cockroach.guest_session_verify
python -m compileall -q backend alembic api main.py tests
python -m unittest discover -s tests
python -m unittest tests.test_live_cockroach_guest_isolation
npm test
npm run build
git diff --check
```

Current results: backend 120 tests pass with 6 conditional live tests skipped in
the ordinary SQLite run; the opt-in live Guest Workspace proof passes;
frontend 10 files and 32 tests pass; the production build passes.

The old `backend.infrastructure.cockroach.verify` command now stops safely
because the current local SQLite/Chroma snapshot has changed since its recorded
one-time migration run. Phase 5 did not rerun that migration or rewrite its
manifest. The live legacy workspace counts and Phase 5 schema/vector checks are
independently unchanged and passing.

## Runtime selection

Cockroach mode fails startup instead of falling back to SQLite or Chroma.
Public startup additionally requires `GUEST_SESSION_TOKEN_PEPPER` with at least
32 bytes and keeps `ALLOW_LEGACY_DEFAULT_WORKSPACE=false`. The frontend sends
the guest credential only to Agentbook API paths.

## Vector-index note

The following indexes exist and their workspace-filtered cosine query shapes pass:

- `idx_document_chunks_workspace_embedding`
- `idx_memory_embeddings_workspace_embedding`

Live `EXPLAIN` used ordinary scans because the final tables contain only 23 document vectors and 7 learner-memory vectors. Optimizer use of either vector index is therefore not claimed.

## Security and scope

- No credential is present in the reports or tracked changes.
- SQLite and Chroma remain unchanged rollback sources.
- Revision `0003_guest_sessions` was the only permanent schema change in this
  phase. No existing application row, legacy workspace, or vector index was
  altered or deleted.
- No raw guest token, stored digest, database credential, or private source
  content is recorded in reports.
- AWS, S3, deployment, authentication, notifications, and frontend redesign remain out of scope.
- The CockroachDB `BYTES` blob adapter remains a compatibility implementation under the existing upload limit.
