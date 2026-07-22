# Agentbook Persistence Foundation Refactor Report

Date: 2026-07-22  
Scope: persistence architecture and migration groundwork only  
Runtime retained: SQLite + Chroma  
Explicitly excluded: Figma, frontend redesign, AWS, CockroachDB migration, authentication, exam timetables, and notifications

## 1. Architecture before and after

### Before

Business workflows imported SQLite helper functions and Chroma factories directly. Each helper commonly owned its own connection/commit. Pending quizzes, memory proposals, and consolidation proposals lived in process-local dictionaries. Cross-store document and memory mutations used compensation logic, so a process failure or a failed compensation could leave SQLite and Chroma divergent.

```text
API / CLI
  -> business module
       -> SQLite helper / raw read model
       -> Chroma collection
       -> process-local workflow dictionary
```

### After

Application workflows depend on repository contracts and an explicit `ApplicationDependencies` composition root. SQLite and Chroma remain the concrete adapters. A shared SQLite UnitOfWork supplies transaction boundaries, durable workflow state replaces process memory, and vector mutations are represented by relational outbox jobs before Chroma is called.

```text
API / CLI initialization
  -> ApplicationDependencies
       -> repository Protocols
            -> SQLite adapters
            -> Chroma adapters
       -> SQLiteUnitOfWork
       -> WorkflowStateRepository
       -> VectorOutboxRepository -> post-commit Chroma synchronization
```

External LLM, document parsing, embedding, and Chroma calls do not execute inside the relational transaction. Chroma synchronization is attempted after commit. A failed attempt is visible to the caller and remains durable for reconciliation.

## 2. Repository interfaces created

The contracts are in `backend/repositories/interfaces/protocols.py`:

- `WorkspaceRepository`
- `NotebookRepository`
- `DocumentRepository`
- `IntelligenceRepository`
- `DashboardRepository`
- `StudySessionRepository`
- `QuizRepository`
- `LearnerMemoryRepository`
- `LearningSignalRepository`
- `WorkflowStateRepository`
- `DocumentVectorRepository`
- `MemoryVectorRepository`
- `VectorOutboxRepository`
- `UnitOfWork`

`RepositoryConflictError` converts adapter-specific constraint failures into an application-facing persistence error. The interfaces use application/domain values rather than exposing connections, SQL, filesystem paths, or Chroma collections.

The dependency composition root is `backend/application/dependencies.py`. FastAPI accepts an optional dependency bundle in `create_app(...)`, stores the active bundle on app state, and initializes the foundation schema during lifespan startup. CLI startup initializes the same application foundation. Existing default entry points remain compatible.

## 3. SQLite and Chroma adapters created

SQLite adapters:

- `backend/repositories/sqlite/connection.py`: connection ownership and active-transaction joining.
- `backend/repositories/sqlite/unit_of_work.py`: root/nested UnitOfWork implementation and post-commit callbacks.
- `backend/repositories/sqlite/adapters.py`: document, notebook, intelligence, study-session, quiz, and learner-memory adapters over the retained local persistence modules.
- `backend/repositories/sqlite/foundation.py`: workspace, workflow, learning-signal, and vector-outbox repositories plus additive schema migration.
- `backend/repositories/sqlite/dashboard.py`: workspace-scoped dashboard read adapter.

Chroma adapters:

- `ChromaDocumentVectorRepository`
- `ChromaMemoryVectorRepository`

The Chroma adapters own collection operations. Upserts are delete-then-add and therefore safe to replay. Business services do not call `add_documents`, `similarity_search_with_score`, collection `get`, or vector `delete` directly.

The existing persistence modules remain in place as compatibility-backed SQLite implementations. This avoided an unrelated file reorganization and preserved existing imports, migrations, test patches, and API behavior.

## 4. Workspace model

A default local workspace is created with ID:

```text
00000000-0000-4000-8000-000000000001
```

The additive migration creates `workspaces`, adds `workspace_id` to existing persisted entities, backfills null/empty ownership to the default workspace, and creates workspace indexes. Ownership is present on documents, notebooks, notebook assignments, intelligence caches, topics and topic sources, learner memories and lineage, study sessions/interactions/citations, quiz attempts/questions/sources, workflow states, learning signals, and vector outbox jobs.

Repository instances are workspace-bound. Document, memory, notebook, intelligence, dashboard, study, quiz, workflow, signal, and outbox reads apply that workspace boundary. RAG and intelligence retrieval also validate returned vector metadata against the workspace-scoped relational repository. This keeps historical Chroma records readable even though vectors created before this refactor do not contain `workspace_id` metadata.

No authentication or workspace-selection UI was added. Current API calls continue to use the default local workspace.

## 5. Persisted workflow states

The new `workflow_states` table contains:

- UUID primary key
- workspace ID
- workflow type
- JSON payload
- status
- created and updated timestamps
- expiry timestamp
- optimistic version
- optional decision metadata

The following former in-memory registries now use `WorkflowStateRepository`:

- pending generated quizzes (`pending_quiz`, 24-hour TTL)
- learner-memory proposals (`memory_proposal`, 7-day TTL)
- memory consolidation proposals (`memory_consolidation`, 7-day TTL)

Pending-state caps remain at 128 per workflow type. Cleanup marks expired pending records as `expired`. Quiz submission and proposal/consolidation decisions use expected-version updates, preventing two consumers from silently applying the same workflow state. Cancel keeps a memory proposal pending; reject/apply/submit creates a terminal state with decision metadata.

Serialized generated quizzes retain their authoritative source snapshot, correct answers, and explanations in SQLite but the pre-submit API still returns only the redacted presentation model. Existing scoring and redaction semantics were not changed.

## 6. UnitOfWork boundaries

`SQLiteUnitOfWork` uses `BEGIN IMMEDIATE` for one root write transaction. Nested application operations join the active connection. On failure, relational work and queued post-commit callbacks are rolled back together. The contract includes `commit`, `rollback`, and `after_commit`, leaving room for a future CockroachDB retrying implementation.

Explicit boundaries now cover:

- document ingestion
- document deletion
- quiz submission plus workflow consumption
- chat interaction plus citation lineage
- learner-memory create
- learner-memory update
- learner-memory archive, restore, and delete
- memory replacement
- memory consolidation and lineage
- memory proposal acceptance/replacement plus workflow decision

Document parsing and quiz/memory proposal generation happen before transactions. Chroma and embedding work is scheduled after relational commit, so a future retrying UnitOfWork will not repeat an LLM or embedding-provider request.

## 7. Outbox/reconciliation design

`vector_outbox` stores:

- job UUID and workspace
- entity type (`document` or `memory`)
- entity ID
- operation (`upsert` or `delete`)
- complete replay payload for upserts
- status (`pending`, `processing`, `completed`, `failed`)
- attempt count, last error, and timestamps

Mutation flow:

1. Change relational state and enqueue its vector operation in the same UnitOfWork.
2. Commit SQLite.
3. Attempt the idempotent Chroma operation in an `after_commit` callback.
4. Mark the job completed, or mark it failed and raise `VectorSynchronizationError`.
5. Reconciliation retries pending, failed, and interrupted-processing jobs.

A newer retryable operation for the same entity supersedes an older pending/failed operation. This prevents a late retry from restoring stale vector content after a newer update or delete. Completed jobs are idempotent and are not attempted twice.

The reconciliation service is `backend/application/vector_outbox.py`. The operator command is:

```powershell
.\.venv\Scripts\python.exe -m backend.infrastructure.reconcile_vectors
```

The request path never silently returns success after a post-commit Chroma failure. The relational change remains authoritative, the error contains the durable job ID, and the test suite proves that the job can repair the vector state later.

## 8. Files changed

New architecture and persistence files:

- `backend/domain/__init__.py`
- `backend/domain/persistence.py`
- `backend/application/__init__.py`
- `backend/application/dependencies.py`
- `backend/application/vector_outbox.py`
- `backend/infrastructure/__init__.py`
- `backend/infrastructure/reconcile_vectors.py`
- `backend/repositories/__init__.py`
- `backend/repositories/interfaces/__init__.py`
- `backend/repositories/interfaces/protocols.py`
- `backend/repositories/sqlite/__init__.py`
- `backend/repositories/sqlite/connection.py`
- `backend/repositories/sqlite/unit_of_work.py`
- `backend/repositories/sqlite/adapters.py`
- `backend/repositories/sqlite/foundation.py`
- `backend/repositories/sqlite/dashboard.py`
- `backend/repositories/chroma/__init__.py`
- `backend/repositories/chroma/adapters.py`
- `tests/test_persistence_foundation.py`
- `FOUNDATION_REFACTOR_REPORT.md`

Refactored integration files:

- `backend/api/app.py`
- `backend/api/routes/memory.py`
- `backend/cli.py`
- `backend/memory/consolidation_registry.py`
- `backend/memory/consolidator.py`
- `backend/memory/database.py`
- `backend/memory/proposals.py`
- `backend/memory/service.py`
- `backend/rag/chat_service.py`
- `backend/rag/database.py`
- `backend/rag/document_service.py`
- `backend/rag/ingestion.py`
- `backend/rag/intelligence.py`
- `backend/rag/intelligence_store.py`
- `backend/rag/notebooks.py`
- `backend/rag/rag_service.py`
- `backend/rag/scope.py`
- `backend/study/dashboard.py`
- `backend/study/database.py`
- `backend/study/progress.py`
- `backend/study/quiz_api.py`
- `backend/study/quiz_history.py`
- `backend/study/quiz_reporting.py`
- `backend/study/recommendations.py`
- `backend/study/reporting.py`

`AUDIT_REPORT.md` was preserved unchanged as the source audit artifact.

## 9. API compatibility

No route, request schema, response schema, scoring rule, citation shape, or frontend visual behavior was intentionally changed.

The existing flows remain covered by the original test suite: notebook CRUD, PDF/PPTX/TXT ingestion behavior, document assignment/deletion, summaries, topics, scoped retrieval, grounded chat and citations, study sessions/outcomes, quiz generation/submission/scoring/redaction, memory CRUD/proposals/consolidation, review, planning, coaching support paths, dashboard, reports, integrity checks, export, and CLI-compatible service entry points.

One failure behavior is deliberately safer: when SQLite has committed but Chroma synchronization fails, the API does not report a normal success. It returns the existing structured server error path while retaining a retryable outbox job. Successful-path response contracts are unchanged.

## 10. Test commands and actual results

All commands below were run from the checked-out repository on 2026-07-22.

### Backend

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

Actual result: exit code 0; 79 tests ran; 78 passed and 1 was skipped because symbolic links were unavailable on this Windows environment. The total includes 8 new persistence-foundation tests.

New coverage includes:

- repository CRUD and workspace isolation
- UnitOfWork rollback across multiple repositories
- pending quiz submission after dependency/application restart simulation
- memory and consolidation proposal retrieval/decision after restart simulation
- expired workflow cleanup
- failed outbox retry, attempt tracking, completion, and idempotent completed replay
- learner-memory update recovery
- learner-memory deletion recovery
- document ingestion recovery
- document deletion recovery

### Frontend

```powershell
cd frontend
npm.cmd test
```

Actual result: exit code 0; 7 test files passed; 19 tests passed.

### Frontend production build

```powershell
cd frontend
npm.cmd run build
```

Actual result: exit code 0; TypeScript `--noEmit` passed; Vite transformed 1,819 modules and produced the production bundle.

### Python compile check

```powershell
.\.venv\Scripts\python.exe -m compileall -q backend tests
```

Actual result: exit code 0; no compile errors.

`npm.cmd` was used because the machine's PowerShell execution policy blocks `npm.ps1`; this is an environment issue, not a project failure.

## 11. Remaining direct SQLite imports

Direct `sqlite3` imports now remain in persistence/infrastructure code, not mutation-oriented business services:

- `backend/repositories/sqlite/connection.py`
- `backend/repositories/sqlite/adapters.py`
- `backend/rag/database.py`
- `backend/rag/notebooks.py`
- `backend/rag/intelligence_store.py`
- `backend/memory/database.py`
- `backend/study/database.py`
- `backend/api/export_service.py`
- `backend/rag/vector_store.py` (legacy Chroma health probing of Chroma's local SQLite metadata)

The retained `rag`, `memory`, and `study` persistence modules are the compatibility-backed SQLite implementation used by the new adapters. `export_service.py` is intentionally SQLite-specific because this phase preserves the existing local backup/export contract. Health and integrity diagnostics also execute storage-specific read checks, but normal application business services no longer execute SQL or own connections.

Before a CockroachDB cutover, these compatibility implementations should be replaced by CockroachDB repository adapters and migration-managed schema definitions. Export, health, and integrity require separate CockroachDB implementations rather than dialect conditionals in business logic.

## 12. Remaining direct Chroma imports

Chroma construction and collection operations remain localized to:

- `backend/rag/vector_store.py`
- `backend/memory/vector_store.py`
- `backend/repositories/chroma/adapters.py`
- the application dependency composition root that wires those factories to the adapters

RAG, intelligence, ingestion, document deletion, and memory services retain factory symbols as compatibility/test injection seams, but collection operations are performed through `DocumentVectorRepository` or `MemoryVectorRepository`. A scan found no direct `add_documents`, similarity search, collection `get`, or vector `delete` calls in business modules outside the Chroma adapters/infrastructure modules.

Chroma remains the active vector store. No CockroachDB vector schema, driver, index, or migration was added.

## 13. Known limitations

- Dependency selection is process-wide for local single-user mode. FastAPI accepts an explicit bundle and exposes it on app state, but there is no per-request authenticated workspace resolver.
- SQLite integer primary keys and several legacy uniqueness constraints remain local-database oriented. Workspace filters prevent record mixing, but a full multi-tenant uniqueness redesign belongs in the CockroachDB schema phase.
- Outbox delivery is attempted synchronously after commit and retried by the reconciliation service/command. There is no background worker, exponential backoff, scheduling, alerting, or dead-letter queue yet.
- Reconciliation repairs recorded outbox operations. It does not full-scan arbitrary historical Chroma drift that predates the outbox.
- Historical Chroma vectors do not contain workspace metadata. Relational ownership validation prevents them from crossing repository boundaries; newly written vectors include workspace metadata.
- Workflow JSON payloads are versioned at the row/decision level, but there is no explicit payload-schema migration registry yet.
- Learning signals now have a repository and durable table as agent-loop groundwork, but no new misconception/exam/notification feature was introduced.
- SQLite/Chroma export, integrity, and health diagnostics remain storage-specific by design in this phase.
- External-provider behavior is still mocked in deterministic tests. No paid/live LLM or embedding request was made during this refactor.

## 14. Readiness for the agentic-loop phase

The persistence prerequisites for the next agentic-loop phase are in place:

- learning signals have a durable workspace-scoped repository;
- workflow state can survive restarts and supports expiry, versioning, and decisions;
- quiz and proposal outcomes can be consumed transactionally;
- memory changes and consolidation have explicit UnitOfWork boundaries;
- vector drift is visible and retryable instead of being hidden by compensation;
- repositories provide stable seams for later CockroachDB implementations.

The next phase can add an agentic learning loop on top of these contracts without coupling new domain logic to SQLite or Chroma. CockroachDB work should begin only after defining distributed IDs, tenant-aware uniqueness, migration tooling, a retrying CockroachDB UnitOfWork, and the vector/outbox worker deployment model.

## Completion checklist

- [x] No direct SQLite access from business services
- [x] No direct Chroma access from business services
- [x] Pending quizzes survive restart
- [x] Proposals survive restart
- [x] Workspace filtering enforced
- [x] Existing APIs remain functional
- [x] Existing tests pass
