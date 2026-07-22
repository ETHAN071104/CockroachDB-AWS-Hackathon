# CockroachDB Migration Report

Date: 2026-07-22

Overall migration status: WAITING FOR EXPLICIT LIVE-SCHEMA AUTHORIZATION

The CockroachDB backend, schema revisions, migration tooling, source validation, and local regression verification are implemented. The live cluster is reachable and still contains zero public tables. The permanent `0001` schema creation was not executed because the safety approval layer requires a new explicit user confirmation after an earlier instruction said not to run migrations. No source SQLite or Chroma data was changed.

## Architecture before and after

Before, relational application data lived in SQLite and document/learner-memory vectors lived in two Chroma collections. Durable workflow, LearningSignal, AdaptationEvent, and vector outbox records were local SQLite state.

The selectable target uses CockroachDB for:

- all relational records behind the existing repository protocols;
- uploaded bytes through `BlobStorage` and CockroachDB `BYTES` compatibility storage;
- `document_chunks.embedding VECTOR(384)`;
- one-to-one `learner_memory_embeddings.embedding VECTOR(384)`;
- durable workflow state, embedding jobs, LearningSignal, and AdaptationEvent records;
- cosine vector retrieval filtered by workspace.

`PERSISTENCE_BACKEND=sqlite` remains the configured default. Selecting `cockroach` requires `DATABASE_URL` and does not silently fall back.

## Schema

Alembic revision `0001_agentbook_cockroach_schema` defines normalized tables for workspaces, the document/notebook library, blobs, caches/topics/citations, study sessions/interactions, quizzes/questions/citations, learner memories/relationships, workflows, learning signals, adaptation events, embedding jobs, and migration bookkeeping. Records use application-generated UUID primary keys. Imported integer identities use deterministic UUIDv5 mappings, retain `legacy_sqlite_id`, and expose their original integer through workspace-scoped `public_id` compatibility fields.

Revision `0002_cockroach_vector_indexes` is intentionally deferred until vectors have been imported and verified.

## Repository adapters

CockroachDB implementations exist for:

- WorkspaceRepository
- NotebookRepository
- DocumentRepository
- BlobStorage
- IntelligenceRepository
- DashboardRepository
- StudySessionRepository
- QuizRepository
- LearnerMemoryRepository
- LearningSignalRepository
- WorkflowStateRepository
- AdaptationEventRepository
- DocumentVectorRepository
- MemoryVectorRepository
- VectorOutboxRepository
- UnitOfWork

FastAPI startup, health, export, integrity, retrieval scope, intelligence fingerprints, library routes, study routes, reports, and CLI repository-backed workflows choose the backend through the dependency container. In Cockroach mode, startup does not initialize SQLite or probe Chroma.

## UnitOfWork and retry behavior

`CockroachUnitOfWork.run()` detects SQLSTATE `40001`, rolls back, creates a fresh transaction, and retries with a bounded exponential backoff plus jitter. It exposes `retry_count` and re-raises the original serialization exception after exhaustion. Logs contain only the SQLSTATE and attempt number.

Document parsing, model calls, and embedding generation are outside retryable callbacks. Relational source changes and embedding jobs commit together. Post-commit processing performs embeddings, while `python -m backend.infrastructure.reconcile_vectors` retries pending, failed, or interrupted jobs.

## Document and learner-memory vector design

Document vectors are stored with content, full citation lineage, metadata JSONB, model/version, content hash, and `VECTOR(384)`. Query scopes cover global/workspace, notebook-derived document IDs, explicit document IDs, and exact topic `(document_id, chunk_index)` pairs. Queries use cosine distance `<=>`, top-k limits, and deterministic document/chunk tie ordering.

Learner memory uses a separate one-to-one embedding table so embedding lifecycle/model changes do not rewrite the memory record. Retrieval joins the memory owner, filters the workspace and active status, orders by cosine distance, and updates retrieval counters.

## Distributed Vector Index definitions

```sql
CREATE VECTOR INDEX idx_document_chunks_workspace_embedding
ON document_chunks (workspace_id, embedding vector_cosine_ops);

CREATE VECTOR INDEX idx_memory_embeddings_workspace_embedding
ON learner_memory_embeddings (workspace_id, embedding vector_cosine_ops);
```

The configured dimension is 384 and the application distance operator is `<=>`. Both index definitions and every other migration statement passed the live parser. Index creation and `EXPLAIN` evidence remain pending because permanent schema creation was not authorized.

## Migration tooling and results

Commands implemented:

```powershell
python -m backend.infrastructure.cockroach.migrate --dry-run
python -m backend.infrastructure.cockroach.migrate
python -m backend.infrastructure.cockroach.verify
```

The importer is non-destructive to its sources, validates both Chroma collections, uses deterministic relationships, records a source fingerprint and migration items, and supports safe reruns with deterministic IDs plus conflict handling.

Actual dry-run source counts:

| Source | Count |
|---|---:|
| workspaces | 1 |
| notebooks | 1 |
| documents / blobs | 1 / 1 |
| notebook assignments | 1 |
| document chunks/vectors | 22 |
| study sessions / interactions / sources | 2 / 1 / 5 |
| quiz attempts / questions / sources | 2 / 6 / 6 |
| learning signals | 4 |
| learner memories / embeddings / relationships | 4 / 4 / 0 |
| workflow states | 8 |
| adaptation events | 12 |
| legacy vector outbox jobs | 4 |
| cached intelligence / topics / topic sources | 0 / 0 / 0 |

Dry run result: PASS — 85 source objects validated, zero migration exceptions. All 26 stored embeddings are dimension 384 and unit-normalized (observed norms approximately 0.99999998–1.00000011). Legacy Chroma is configured for L2; for these normalized vectors, L2 and cosine produce equivalent rankings while numeric distance scales differ. The manifest records this conversion policy. The manifest status is `dry_run_passed`. No destination counts exist yet because no live import was allowed.

## Live-cluster evidence

- Repository-root `.env` was used without displaying its connection value.
- TLS mode: `verify-full`.
- Live preflight baseline: CockroachDB CCL v26.2.1.
- Latest public-table count before attempted schema apply: 0.
- Schema CREATE permission: passed.
- Full live parser check: 42/42 migration/index statements accepted through `SHOW SYNTAX`.
- Permanent schema creation: not executed; safety approval rejected the operation pending renewed explicit authorization.
- Data migration and vector-index creation: not executed.

## Tests actually run

- `python -m compileall -q backend alembic tests`: PASS.
- Backend `unittest` discovery: PASS — 85 tests, 2 conditional skips.
- Cockroach retry unit test: PASS, including two simulated `40001` retries with fresh transactions.
- Cockroach composition test: PASS; no SQLite/Chroma adapter instance is selected.
- Migration dry run: PASS — 85 validated source objects, zero exceptions.
- Live `SHOW SYNTAX`: PASS — 42 statements.
- Frontend Vitest: PASS — 7 files, 19 tests.
- Frontend production build: PASS — TypeScript and Vite build completed.
- Live repository contract tests: not run because the schema does not exist.
- Live Agentic Learning Loop: not run because the schema/data migration does not exist.
- Dual-read/vector-index usage comparison: not run for the same reason.

## Files changed

The implementation changes configuration/dependencies, repository protocols and adapters, backend composition and business storage boundaries, health/export/integrity/CLI paths, Alembic revisions, migration/verification tooling, tests, README, and the required plan/manifest/exceptions/handoff documents. The frontend source and visual design were not modified.

## Environment variables

Supported persistence variables are `PERSISTENCE_BACKEND`, `DATABASE_URL`, `DATABASE_POOL_SIZE`, `DATABASE_MAX_OVERFLOW`, `DATABASE_CONNECT_TIMEOUT`, `DATABASE_MAX_TRANSACTION_RETRIES`, `DATABASE_RETRY_BASE_DELAY_MS`, `EMBEDDING_DIMENSION`, `EMBEDDING_MODEL`, and `ENABLE_VECTOR_INDEX`. No real value is present in `.env.example` or this report.

## Remaining limitations and required next action

The current Cockroach file-blob adapter is suitable only as a compatibility boundary for the existing 50 MiB upload ceiling. Object storage, AWS, deployment, authentication, and frontend redesign remain outside scope.

To continue, the user must explicitly authorize creation of permanent Agentbook tables and indexes in the configured live cluster. After authorization, run revision `0001`, live repository/startup tests, the data import, verification and dual-read comparison, revision `0002`, vector `EXPLAIN`, cockroach-mode restart/no-local-write checks, and the full live Agentic Learning Loop. This report must then be updated with those actual results before the migration can be called complete.
