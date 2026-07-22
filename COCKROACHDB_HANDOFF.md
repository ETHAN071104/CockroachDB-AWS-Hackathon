# CockroachDB Backend Handoff

Date: 2026-07-22

Current state: implementation and dry run pass; live permanent schema/data migration awaits renewed explicit authorization.

## Required environment

Set the following only in the repository-root `.env` or the deployment secret manager:

```dotenv
PERSISTENCE_BACKEND=sqlite
DATABASE_URL=
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=5
DATABASE_CONNECT_TIMEOUT=15
DATABASE_MAX_TRANSACTION_RETRIES=5
DATABASE_RETRY_BASE_DELAY_MS=100
EMBEDDING_DIMENSION=384
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
ENABLE_VECTOR_INDEX=true
```

Use a CockroachDB connection string with `sslmode=verify-full`. Never paste it into source, reports, commands that echo environment values, CI logs, screenshots, or issue trackers.

## Safe connection and schema sequence

From the repository root:

```powershell
python -m backend.infrastructure.cockroach.migration_runner preflight
python -m backend.infrastructure.cockroach.migrate --dry-run
python -m backend.infrastructure.cockroach.migration_runner upgrade 0001_agentbook_cockroach_schema
python -m backend.infrastructure.cockroach.migrate
python -m backend.infrastructure.cockroach.verify
python -m backend.infrastructure.cockroach.compare
python -m backend.infrastructure.cockroach.migration_runner upgrade head
python -m backend.infrastructure.cockroach.verify
```

Do not target `head` before document and learner-memory vectors have been imported and verified. Do not delete or modify the SQLite/Chroma sources. Inspect `COCKROACHDB_MIGRATION_MANIFEST.json` and `COCKROACHDB_MIGRATION_EXCEPTIONS.md` after every dry run/import.

## Starting the backend

After verification passes, change only:

```dotenv
PERSISTENCE_BACKEND=cockroach
```

Then start:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
```

Do not run two local servers on the same port. The health endpoint is `GET http://127.0.0.1:8000/api/health`. Cockroach mode fails startup instead of falling back when configuration/schema access is invalid.

## Tests

```powershell
python -m compileall backend alembic api main.py tests
python -m unittest discover -s tests -p "test_*.py" -v

$env:RUN_LIVE_COCKROACH_TESTS='1'
python -m unittest tests.test_cockroach_persistence -v
Remove-Item Env:RUN_LIVE_COCKROACH_TESTS

Set-Location frontend
npm.cmd test
npm.cmd run build
Set-Location ..
```

The full live agentic-loop test must be run with Cockroach composition and controlled fake model output so it proves persistence/vector behavior without making transaction callbacks perform external calls.

## Embedding jobs and failure inspection

Reconcile after a failed/interrupted embedding operation:

```powershell
python -m backend.infrastructure.reconcile_vectors
```

Inspect sanitized state through `embedding_jobs`: status, attempts, timestamps, entity type/ID, operation, and bounded `last_error`. Do not include provider payloads, credentials, or source text in operational tickets. A newer pending operation supersedes an older pending/failed operation for the same entity.

## Verifying vector retrieval

Both vector queries must include `workspace_id` and use cosine distance `<=>`. Verify:

- document scope, notebook-derived document scope, and exact topic chunk pairs;
- active learner-memory filtering;
- top-k order and deterministic tie ordering;
- vector dimension 384;
- index names `idx_document_chunks_workspace_embedding` and `idx_memory_embeddings_workspace_embedding`;
- `EXPLAIN` evidence after indexes are online.

Use `python -m backend.infrastructure.cockroach.verify` for count, relationship, revision, dimension, and index checks. The migration report must record actual representative query IDs/distances without recording private source content.

## Operational limitations

- CockroachDB `BYTES` is the current compatibility blob adapter and retains the application's 50 MiB default upload ceiling.
- Embedding/model execution is synchronous post-commit unless an operator runs reconciliation separately.
- Commit-time network ambiguity requires checking deterministic IDs/idempotency keys before retrying.
- The product is still single-user/no-auth unless a deployment layer provides protection.
- Exports are private unencrypted data.

## Work intentionally left for the deployment teammate

AWS, S3/object storage, deployment topology, secret-manager wiring, TLS certificate distribution, authentication/authorization, worker orchestration, observability, backup/restore operations, and production network policy are not implemented in this phase. A future S3 adapter should implement `BlobStorage`; notebook and study services should remain unchanged.
