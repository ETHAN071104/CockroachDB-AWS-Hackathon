# Agentbook CockroachDB Implementation Plan

Date: 2026-07-22  
Status: implementation authorized; no critical CockroachDB capability blocker found  
Cutover guard: keep `PERSISTENCE_BACKEND=sqlite` until schema, adapters, live integration tests, migration verification, and rollback evidence pass

## 1. Current persistence architecture

Agentbook currently composes `ApplicationDependencies` from SQLite relational repositories and two Chroma vector repositories. The repository protocols cover workspaces, notebooks, documents, intelligence, dashboard reads, study sessions, quizzes, learner memories, learning signals, adaptation events, durable workflow state, vector jobs, vectors, and a Unit of Work.

The foundation refactor added a default workspace, workspace filters, durable pending quizzes/proposals, learning signals, adaptation events, a vector outbox, and transaction boundaries. The agentic loop uses these boundaries for its core writes.

The implementation is not yet fully backend-neutral:

- API startup initializes SQLite tables and probes both Chroma stores directly.
- document ingestion and deletion construct Chroma adapters directly for post-commit work;
- learner-memory search and mutation construct the memory Chroma adapter directly;
- document RAG and intelligence source enumeration construct the document Chroma adapter directly;
- scope resolution imports SQLite notebook helpers;
- health, export, integrity, parts of CLI, and several reporting helpers import SQLite modules directly;
- the current Unit of Work context-manager contract cannot replay an arbitrary transaction body after SQLSTATE `40001`;
- uploaded bytes are coupled to the `documents.file_data` SQLite column;
- public APIs and frontend types expose positive integer IDs for documents, notebooks, memories, sessions, interactions, quiz attempts, questions, and sources.

The verified local source inventory is:

- SQLite `data/app.db`: 19 application tables;
- Chroma `study_documents`: 22 unique records, all 384 dimensions;
- Chroma `learner_memories`: 4 unique records, all 384 dimensions;
- source artifacts remain the migration and rollback source and will never be deleted or modified by migration tooling.

## 2. Target CockroachDB architecture

`PERSISTENCE_BACKEND` selects one complete composition root:

- `sqlite`: current SQLite repositories, Chroma vectors, local blob compatibility, and legacy export/integrity behavior;
- `cockroach`: CockroachDB repositories, CockroachDB vector repositories, CockroachDB blob compatibility storage, CockroachDB health/integrity/export, and `CockroachUnitOfWork`.

No application service may select a driver, SQLite path, Chroma collection, or backend-specific repository. FastAPI and CLI initialize only the selected composition. Cockroach mode must fail before startup when configuration is incomplete and must never fall back to SQLite.

CockroachDB will be the single authoritative database for relational records, workflow state, jobs, document chunks, document embeddings, learner memories, and learner-memory embeddings. Embedding generation remains outside retryable SQL transactions and is coordinated by durable jobs.

## 3. Complete table mapping

| Current source | CockroachDB target | Key changes |
|---|---|---|
| `workspaces` | `workspaces` | UUID PK, name, timestamps |
| `notebooks` | `notebooks` | UUID PK, non-sequential public integer ID, `legacy_sqlite_id`, workspace-normalized name uniqueness |
| `documents` | `documents` | UUID PK, public/legacy IDs, workspace hash uniqueness, metadata and chunk count; no inline source bytes |
| `documents.file_data` | `document_blobs` | one-to-one document FK, `BYTES`, size/hash/media metadata behind `BlobStorage` |
| `notebook_documents` | `notebook_documents` | UUID PK plus tenant-safe document/notebook FKs and one-current-notebook uniqueness |
| `cached_intelligence` | `cached_intelligence` | UUID PK, JSONB result/source snapshot, scope uniqueness by workspace |
| `topics` | `topics` | UUID PK, workspace scope/fingerprint and timestamps |
| `study_documents` Chroma | `document_chunks` | UUID PK, document FK, ordinal, lineage snapshots, JSONB metadata, nullable `VECTOR(384)`, model/version/hash/timestamps |
| `topic_sources` | `topic_sources` | UUID PK, topic/document/chunk lineage plus immutable citation snapshot |
| `study_sessions` | `study_sessions` | UUID PK, public/legacy IDs, status/timestamps, one active session per workspace |
| `study_interactions` | `study_interactions` | UUID PK, public/legacy IDs, session FK, question/answer/outcome/timestamps |
| `study_interaction_sources` | `study_interaction_sources` | UUID PK, public/legacy IDs, parent FK, optional live chunk/document IDs plus durable snapshot |
| `quiz_attempts` | `quiz_attempts` | UUID PK, public/legacy IDs, score/status fields, timestamps |
| `quiz_question_attempts` | `quiz_question_attempts` | UUID PK, public/legacy IDs, attempt FK, options JSONB, correctness fields |
| `quiz_question_sources` | `quiz_question_sources` | UUID PK, public/legacy IDs, parent FK, optional live chunk/document IDs plus durable snapshot |
| `workflow_states` | `workflow_states` | UUID PK, JSONB payload/decision metadata, expiry, optimistic version, status checks |
| `learning_signals` | `learning_signals` | UUID PK, workspace/source/evidence JSONB, memory FK, aggregation key and timestamps |
| `memories` | `learner_memories` | UUID PK, public/legacy IDs, type/content/scores/status/timestamps |
| `memory_relationships` | `memory_relationships` | UUID PK, public/legacy IDs, source/target UUID FKs and uniqueness |
| `learner_memories` Chroma | `learner_memory_embeddings` | one-to-one memory FK, `VECTOR(384)`, model/version, retrieval metadata and timestamps |
| `adaptation_events` | `adaptation_events` | UUID PK, JSONB memory/signal IDs and applied changes, request and timestamp indexes |
| `vector_outbox` | `embedding_jobs` | UUID PK, entity identity, operation/status, JSONB payload, attempts/error, idempotency/supersession, claim timestamps |
| none | `migration_runs` | migration manifest/checkpoint, source fingerprints, status and timestamps for safe resume |
| none | `migration_items` | per-table source identity, target UUID, checksum/status/error for rerun and exception proof |

All user-scoped tables include `workspace_id`. Every FK includes an explicit delete policy. Citation tables preserve snapshot fields even when live lineage is nullable. Status, score, ordinal, and optimistic-version checks are explicit.

## 4. ID strategy

Every CockroachDB record uses an application-generated UUID primary key. Sequential/autoincrement primary keys are prohibited.

Entities whose public contract currently exposes an integer also store a unique positive `public_id INT8`:

- imported rows keep `public_id = legacy_sqlite_id`;
- new rows derive a non-sequential positive 63-bit public ID from the application-generated UUID;
- a uniqueness conflict causes UUID/public-ID regeneration before the transaction is committed;
- repositories translate public integers to UUIDs internally, so API and frontend contracts remain unchanged.

Entities already exposed as UUID strings retain UUID public contracts. New topic, workflow, signal, adaptation-event, job, and migration IDs are UUIDv4 unless the migration rules require UUIDv5.

## 5. Legacy-ID mapping

For every SQLite integer-key row, the target UUID is UUIDv5 over a fixed Agentbook migration namespace and the canonical string:

```text
workspace_id + ":" + source_table + ":" + legacy_sqlite_id
```

The original integer is stored in `legacy_sqlite_id` and is unique with `workspace_id`. Dry runs and real migrations use the same pure mapping function. Join rows with composite source keys use a canonical, length-delimited key assembled from the ordered source columns. Existing canonical UUID identifiers are preserved where doing so protects external references; otherwise a UUIDv5 mapping from the exact legacy string is recorded in `migration_items`.

Every relationship is mapped from the source identity map, never by row order. Migration verification checks citation, memory, topic-source, quiz-question/source, and notebook relationships explicitly.

## 6. Transaction strategy

`CockroachUnitOfWork` binds one SQLAlchemy connection/transaction through a context variable so nested repositories join the root transaction. It supports commit, rollback, and after-commit callbacks.

To provide real serializable retries, the Unit of Work contract gains a callback-based `run(work)` boundary:

- each attempt opens a fresh connection and transaction;
- SQLSTATE `40001` is retried up to `DATABASE_MAX_TRANSACTION_RETRIES`;
- delay uses exponential backoff from `DATABASE_RETRY_BASE_DELAY_MS` plus bounded jitter;
- ordinary failures roll back immediately;
- exhaustion re-raises the original database exception;
- retry count is observable without logging credentials or payloads;
- after-commit callbacks run exactly once after a confirmed commit;
- ambiguous commit outcomes require an idempotency key or durable operation identity before retry.

SQLite implements `run(work)` as a single attempt. Cockroach write workflows are refactored to callback boundaries. LLM calls, embedding generation, document parsing, Chroma access, external HTTP, and notifications never occur inside a retry callback.

## 7. VECTOR strategy

Document chunks and learner-memory embeddings use `VECTOR(384)`. Startup and migration validate `EMBEDDING_DIMENSION`; a configured dimension other than the migration/schema dimension fails loudly.

Choice B is used for learner memories: `learner_memory_embeddings` is a one-to-one table. This separates semantic-retrieval lifecycle and model/version/retrieval metrics from durable learner-state rules, permits an active memory to exist while an embedding job is pending, and avoids rewriting the learner-memory row for every retrieval metric update.

Raw parameterized psycopg/SQLAlchemy SQL is used for vector values and distance ordering. Embeddings are generated through the existing embedding model outside a transaction, validated for finite values and exact dimension, then stored transactionally with completion of the durable job.

Cosine distance (`<=>`) remains the application distance convention: lower is closer, preserving existing thresholds and response semantics. Stable UUID/public-ID tie breakers make equal-distance results deterministic where practical.

## 8. Vector-index strategy

Create cosine vector indexes after vector import and validation:

```sql
CREATE VECTOR INDEX idx_document_chunks_workspace_embedding
ON document_chunks (workspace_id, embedding vector_cosine_ops);

CREATE VECTOR INDEX idx_learner_memory_embeddings_workspace_embedding
ON learner_memory_embeddings (workspace_id, embedding vector_cosine_ops);
```

Every vector query includes an equality filter on `workspace_id`. Document queries additionally filter public document IDs or exact document/chunk pairs for notebook, document, and topic scopes. Memory queries join active learner memories and apply status/confidence/importance filters.

The live cluster parser already accepted the syntax shape. Implementation verification must still collect live index definitions, representative parameterized SQL, `EXPLAIN` output when the data volume permits index selection, top-k results, and cross-workspace isolation evidence. No blocking index build is run until import counts/dimensions pass and impact is documented.

## 9. File/blob storage compatibility strategy

Introduce `BlobStorage` with `store`, `read`, `delete`, and `metadata` methods. In SQLite mode, `SQLiteBlobStorage` preserves current local behavior. In Cockroach mode, `CockroachBlobStorage` stores bytes in `document_blobs` in the same transaction as document metadata.

This is a compatibility implementation, not a large-object architecture. CockroachDB `BYTES` is suitable only for small source files in this phase. The effective Cockroach upload limit will be documented and validated; larger production files require a later external object-storage adapter. No S3 or deployment code is included.

Notebook, RAG, study, and API services receive blob access only through dependencies. Export reads logical records and blob content through the abstraction rather than copying database or Chroma files.

## 10. Migration order

1. preflight configuration, source readability, destination emptiness/manifest state, model and vector dimensions;
2. workspaces;
3. notebooks;
4. documents and blobs;
5. notebook assignments;
6. cached intelligence;
7. topics;
8. document chunks without indexes;
9. topic sources;
10. study sessions;
11. study interactions and citations;
12. quiz attempts;
13. quiz questions and citations;
14. learning signals;
15. learner memories;
16. memory relationships;
17. workflow states/proposals;
18. adaptation events;
19. learner-memory embeddings;
20. pending embedding/outbox jobs;
21. count/FK/checksum/sample validation;
22. vector indexes;
23. dual-read behavioral verification.

The source SQLite and Chroma stores are always opened read-only by migration tooling. Destination inserts are idempotent and checkpointed. Invalid/orphan/duplicate/incompatible records are recorded and block completion rather than being skipped silently.

## 11. Adapter implementation order

1. typed configuration, dependency pins, connection pool, row/JSON/time/ID helpers;
2. UUID/public-ID utilities and repository contract extensions;
3. Cockroach Unit of Work and retry tests;
4. workspace, workflow, learning-signal, adaptation-event, and job repositories;
5. blob/document/notebook repositories;
6. document chunk/vector repository;
7. learner-memory/relationship/vector repositories;
8. study session, quiz, intelligence, and dashboard repositories;
9. health, integrity, export, reconciliation, API startup, and CLI composition;
10. removal of direct SQLite/Chroma construction from normal Cockroach workflows;
11. Alembic environment/revisions and generated-SQL review;
12. migration/verification and dual-read tooling.

## 12. API compatibility strategy

Requests and responses keep existing positive integer IDs through `public_id`. UUIDs remain internal except for endpoints already using UUID strings. Dataclass shapes, quiz scoring, trusted answer storage, citation semantics, memory rules, error codes, and JSON field names remain unchanged.

Repositories return the existing dataclasses or row-compatible records. Timestamp formatting stays ISO-8601 UTC at API boundaries. JSONB values are converted back to the same Python list/dict/tuple shapes. New configuration and operational endpoints may add fields but will not remove or rename existing ones.

An automated Cockroach composition test patches SQLite/Chroma constructors and local path access to fail, then verifies initialization and representative workflows do not touch them.

## 13. Rollback strategy

- preserve `data/app.db`, both Chroma directories, and a source fingerprint/manifest;
- never dual-write permanently;
- keep SQLite mode functional throughout implementation;
- run import only into migration-owned destination rows with deterministic IDs;
- before cutover, rollback is selecting `PERSISTENCE_BACKEND=sqlite` because legacy sources remain unchanged;
- after a Cockroach-only smoke test writes new data, rollback requires exporting those new records or explicitly accepting their absence in legacy mode; this is documented before cutover;
- schema rollback uses reviewed Alembic downgrade only in a disposable/empty verification database; production recovery prefers forward fixes and retained source data;
- vector index creation is deferred and independently removable without deleting vector rows.

## 14. Testing strategy

Testing layers remain separate:

- unit tests: settings validation, deterministic UUID/public-ID mapping, vector conversion, retry classification/backoff, row mapping;
- fake-adapter tests: unchanged agentic behavior and post-commit job recovery;
- SQLite compatibility tests: complete existing backend suite;
- repository contract tests: common behavior for SQLite and Cockroach repositories;
- live empty-database tests: Alembic upgrade, schema constraints, repository CRUD, rollback, workspace isolation, vector ordering/filtering, restart recovery, and no SQLite/Chroma access;
- migration tests: dry-run determinism, idempotency, resume, source/destination counts, FK/checksum/sample comparisons, and exception reporting;
- dual-read tests: document/memory top-k identity/order/distance/filter comparisons plus relational reports;
- full live loop: quiz submission to signal, proposal, accepted memory, embedding, future retrieval/adaptation, and adaptation event;
- frontend tests/build: ensure integer-ID and response compatibility;
- manual live smoke: real upload/quiz/memory/restart with SQLite/Chroma fingerprints unchanged.

No live success is reported unless the configured cluster is actually contacted. No vector-index usage is claimed without live evidence.

## 15. Known risks

1. Integer API compatibility needs a durable `public_id` mapping in addition to UUID PKs.
2. Existing protocols use `int` IDs and `Any` return types; incomplete row mapping can produce subtle compatibility errors.
3. A context-manager transaction cannot replay business code; write paths must adopt callback-based retry boundaries.
4. Some business and operational modules bypass dependency injection and could accidentally touch SQLite/Chroma in Cockroach mode.
5. Current 50 MiB uploads are too large for a blanket Cockroach `BYTES` policy; this phase must enforce/document a safer limit.
6. Existing local uniqueness is not consistently workspace-scoped; migration can reveal cross-workspace conflicts.
7. SQLite timestamps and JSON text may contain inconsistent historical values.
8. Topic/citation snapshots intentionally survive some source changes, while live FKs must not erase audit evidence.
9. Chroma distance ordering must be compared empirically with Cockroach cosine distance.
10. Opening Chroma through high-level APIs may update local metadata; migration tooling should prefer read-only snapshot copies where necessary.
11. Vector index builds can block writes on non-empty tables; import/index ordering and impact must be explicit.
12. Updating retrieval counters on every vector read can add contention; metrics updates must not compromise retrieval correctness.
13. Export and integrity currently assume filesystem snapshots and require backend-specific logical implementations.
14. Live cluster data must be proven empty or migration-owned before schema/data application.
15. Credentials must never appear in Alembic configuration, command lines, reports, manifests, logs, or exception text.

No verified CockroachDB v26.2.1 capability currently blocks the proposed design. The hard blockers are implementation/test gates, so implementation may proceed while the live cutover guard remains SQLite.

## 16. Files expected to change

Configuration and dependencies:

- `requirements.txt`
- `.env.example`
- `backend/rag/config.py` or a new persistence settings module
- `README.md`

Contracts and composition:

- `backend/domain/persistence.py`
- `backend/repositories/interfaces/protocols.py`
- `backend/repositories/interfaces/__init__.py`
- `backend/application/dependencies.py`
- `backend/application/vector_outbox.py`

New Cockroach infrastructure:

- `backend/repositories/cockroach/*`
- `backend/infrastructure/cockroach/*`
- `alembic.ini`
- `alembic/env.py`
- `alembic/versions/*`

Backend-neutral service cleanup:

- `backend/rag/ingestion.py`
- `backend/rag/document_service.py`
- `backend/rag/rag_service.py`
- `backend/rag/scope.py`
- `backend/rag/intelligence.py`
- `backend/memory/service.py`
- `backend/api/app.py`
- `backend/api/health.py`
- `backend/api/export_service.py`
- `backend/study/integrity.py`
- reporting/progress/recommendation modules that still import SQLite helpers
- `backend/cli.py`

Tests and deliverables:

- existing test fixtures where composition assumptions change
- new configuration, ID, retry, repository-contract, no-legacy-access, migration, vector, and live-loop tests
- `COCKROACHDB_MIGRATION_REPORT.md`
- `COCKROACHDB_MIGRATION_MANIFEST.json`
- `COCKROACHDB_MIGRATION_EXCEPTIONS.md`
- `COCKROACHDB_HANDOFF.md`
