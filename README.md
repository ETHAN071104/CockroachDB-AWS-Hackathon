# Local Study Companion

Local Study Companion is a private, citation-first study workspace for PDF, PowerPoint, and text notes. It keeps the original terminal application and adds a synchronous FastAPI API plus a responsive React/Vite TypeScript interface.

Persistence is selectable. `PERSISTENCE_BACKEND=sqlite` keeps the legacy local SQLite plus Chroma layout. `PERSISTENCE_BACKEND=cockroach` stores relational state, document chunks/vectors, learner memories/vectors, durable workflows, and embedding jobs in CockroachDB. Embedding and language-model providers are created only when a workflow needs them, so startup, health checks, the dashboard, and cached GET requests do not invoke an LLM.

## What Milestone 7 includes

- Notebook and document management, including a virtual **Unsorted Documents** notebook.
- Byte-based browser uploads and the existing path-based CLI ingestion flow.
- Grounded retrieval at global, notebook, document-list, or extracted-topic scope.
- Cached document, notebook, and topic summaries with source staleness detection.
- Scoped topic extraction, chat, review, quizzes, adaptive plans, and coaching.
- Study sessions, outcome tracking, source lineage, progress reports, and integrity checks.
- Learner-memory CRUD, proposal decisions, and two-step consolidation.
- Safe local export of SQLite and both Chroma stores with a checksum manifest.
- Responsive, keyboard-accessible React UI with restrained GSAP route motion.

## Architecture

```text
React/Vite browser --HTTP /api--> backend/api/
                                         |
Root launch shims -----------------------+
  main.py, api/app.py                     v
                  backend/rag/  backend/memory/
                  backend/study/  backend/llm/
                         |          |
                         +-- repository interfaces
                              |-- SQLite + Chroma (legacy local mode)
                              `-- CockroachDB + VECTOR(384)
```

| Area | Responsibility |
| --- | --- |
| `main.py` | Backward-compatible terminal launcher; implementation lives in `backend/cli.py`. |
| `api/` | Backward-compatible shim preserving `uvicorn api.app:app`. |
| `backend/api/` | App factory, schemas, error mapping, route composition, dashboard, and export delivery. Routes remain thin and call synchronous domain services. |
| `backend/rag/` | Ingestion, notebook/document metadata, retrieval scopes, chat, citations, cached summaries, and topics. |
| `backend/memory/` | Learner-memory persistence, vector search, proposals, and consolidation. |
| `backend/study/` | Sessions, outcomes, quiz scoring, review, plans, coaching, reports, dashboard aggregation, and integrity checks. |
| `backend/llm/` | Lazy provider construction. |
| `frontend/` | React Router application, typed API client, request hooks, reusable UI, styles, and Vitest tests. |
| `tests/` | Python `unittest` unit, integration, API, regression, and end-to-end coverage. |
| `backend/repositories/cockroach/` | CockroachDB adapters, pooled connections, retrying UnitOfWork, blob storage, and vector retrieval. |
| `backend/infrastructure/cockroach/` | Health, Alembic runner, source validation, one-time migration, and verification commands. |
| `alembic/` | Version-controlled CockroachDB schema and deferred vector-index revisions. |
| `data/` | Legacy local SQLite and Chroma data plus migration sources. It is intentionally ignored by Git. |

FastAPI startup initializes/probes only the selected backend. Cockroach mode fails loudly if `DATABASE_URL` is absent and never falls back to local storage. It does not load the embedding model or call the language model. SQLite connections enable foreign keys, a busy timeout, and WAL mode, and are closed at their service boundaries.

## Prerequisites

- Python 3.11 or newer is recommended. The code requires Python 3.10+ syntax.
- Node.js `^20.19.0` or `>=22.12.0` for the current Vite toolchain.
- npm.
- Enough local disk for uploaded content, vector indexes, the embedding model cache, and exports.
- An API key and model name for an accepted LLM provider when using generative workflows.

The default embedding model is `sentence-transformers/all-MiniLM-L6-v2`. Its first real embedding request can download model files and therefore may require network access. Provider-backed summary, topic, chat, quiz, review, and coaching generation also requires network access unless the configured OpenAI-compatible endpoint is local.

## Setup

From the repository root in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Set-Location frontend
npm ci
Set-Location ..
```

On macOS or Linux, replace activation with `source .venv/bin/activate` and use `python3` when creating the environment.

Create a file named `.env` in the repository root, next to `requirements.txt` and the `backend/` directory. Do not place it inside `backend/` or `frontend/`.

Choose **one** of the following provider configurations.

### OpenRouter

```dotenv
LLM_PROVIDER=openrouter
LLM_API_KEY=sk-or-v1-replace-me
LLM_MODEL=openai/gpt-4.1-mini
LLM_REASONING_VISIBLE=false
```

`LLM_BASE_URL` is not required for `openrouter`; Agentbook uses the dedicated OpenRouter integration.

### Groq

```dotenv
LLM_PROVIDER=groq
LLM_API_KEY=gsk_replace-me
LLM_MODEL=replace-me
LLM_REASONING_VISIBLE=false
```

`LLM_BASE_URL` is not required for `groq`; Agentbook uses the dedicated Groq integration.

### OpenAI-compatible endpoint

Use this option for providers or local servers that expose an OpenAI-compatible API:

```dotenv
LLM_PROVIDER=openai_compatible
LLM_API_KEY=replace-me
LLM_MODEL=replace-me
LLM_BASE_URL=https://provider.example.com/v1
LLM_REASONING_VISIBLE=false
```

For a local OpenAI-compatible server, the base URL may look like:

```dotenv
LLM_BASE_URL=http://127.0.0.1:1234/v1
```

The configured model name must be available from the selected provider. Model identifiers differ between providers and can change, so copy the exact model ID from the provider's dashboard or documentation.

### Complete local `.env` example

The following non-secret settings are a practical starting point. Replace only the provider, API key, and model values as needed:

```dotenv
# LLM provider: openrouter, groq, or openai_compatible
LLM_PROVIDER=groq
LLM_API_KEY=replace-me
LLM_MODEL=replace-me
LLM_REASONING_VISIBLE=false

# Required only when LLM_PROVIDER=openai_compatible
# LLM_BASE_URL=https://provider.example.com/v1

# Embeddings and retrieval
PERSISTENCE_BACKEND=sqlite
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
EMBEDDING_DIMENSION=384
CHUNK_SIZE=1000
CHUNK_OVERLAP=200
RETRIEVAL_K=5

# Learner memory
MEMORY_RETRIEVAL_K=5
MAX_MEMORY_DISTANCE=1.15
MEMORY_DUPLICATE_MAX_DISTANCE=0.40
ENABLE_MEMORY_PROPOSALS=true
MEMORY_PROPOSAL_MIN_CONFIDENCE=0.75
MEMORY_PROPOSAL_MIN_IMPORTANCE=0.40
```

Configuration variables and defaults:

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `LLM_PROVIDER` | For generative workflows | None | Provider adapter: `openrouter`, `groq`, or `openai_compatible`. |
| `LLM_API_KEY` | For hosted providers | None | API key sent to the selected LLM provider. |
| `LLM_MODEL` | For generative workflows | None | Exact model identifier accepted by the selected provider. |
| `LLM_BASE_URL` | Only for `openai_compatible` | None | Base URL of an OpenAI-compatible API, normally ending in `/v1`. It is ignored by the dedicated OpenRouter and Groq integrations. |
| `LLM_REASONING_VISIBLE` | No | `false` | Whether supported providers expose reasoning text to internal callers. Keep disabled for the normal UI. |
| `STUDY_DATA_DIR` | No | `<project>/data` | Directory containing SQLite and both Chroma stores. |
| `PERSISTENCE_BACKEND` | No | `sqlite` | Selects `sqlite` or `cockroach`; there is no silent fallback. |
| `DATABASE_URL` | Cockroach mode | None | CockroachDB connection string using TLS `verify-full`. Never log or commit it. |
| `DATABASE_POOL_SIZE` | No | `5` | Base SQLAlchemy connection-pool size. |
| `DATABASE_MAX_OVERFLOW` | No | `5` | Additional temporary pool connections. |
| `DATABASE_CONNECT_TIMEOUT` | No | `15` | Connection timeout in seconds. |
| `DATABASE_MAX_TRANSACTION_RETRIES` | No | `5` | Maximum SQLSTATE `40001` transaction retries. |
| `DATABASE_RETRY_BASE_DELAY_MS` | No | `100` | Exponential-backoff base delay. |
| `EMBEDDING_MODEL` | No | `sentence-transformers/all-MiniLM-L6-v2` | Hugging Face embedding model. |
| `EMBEDDING_DIMENSION` | No | `384` | Must match both the model and `VECTOR(384)` schema. |
| `ENABLE_VECTOR_INDEX` | No | `true` | Enables the documented distributed vector-index rollout. |
| `CHUNK_SIZE` | No | `1000` | Character-oriented document chunk size. |
| `CHUNK_OVERLAP` | No | `200` | Chunk overlap. |
| `RETRIEVAL_K` | No | `5` | Maximum document chunks used for ordinary retrieval. |
| `MEMORY_RETRIEVAL_K` | No | `5` | Maximum learner memories retrieved. |
| `MAX_MEMORY_DISTANCE` | No | `1.15` | Memory retrieval distance ceiling. |
| `ENABLE_MEMORY_PROPOSALS` | No | `true` | Enables optional post-chat memory proposals. |
| `MEMORY_PROPOSAL_MIN_CONFIDENCE` | No | `0.75` | Proposal confidence threshold. |
| `MEMORY_PROPOSAL_MIN_IMPORTANCE` | No | `0.40` | Proposal importance threshold. |
| `MEMORY_DUPLICATE_MAX_DISTANCE` | No | `0.40` | Similarity threshold used during duplicate-memory handling. |
| `MAX_UPLOAD_BYTES` | No | `52428800` | Upload ceiling in bytes (50 MiB). |

Never commit `.env`, `DATABASE_URL`, certificates containing private material, or provider secrets. `.env.example` contains placeholders only. A custom `STUDY_DATA_DIR` should point to a private, writable location.

### CockroachDB schema and one-time migration

Keep `PERSISTENCE_BACKEND=sqlite` while preparing the schema and importing legacy data. The safe order is:

```powershell
# Read-only connection/permission preflight
python -m backend.infrastructure.cockroach.migration_runner preflight

# Validate SQLite + both Chroma collections without changing either source or CockroachDB
python -m backend.infrastructure.cockroach.migrate --dry-run

# Create relational/vector-column schema, but defer vector indexes
python -m backend.infrastructure.cockroach.migration_runner upgrade 0001_agentbook_cockroach_schema

# Import through deterministic UUIDv5 mappings; safe reruns use ON CONFLICT and a migration manifest
python -m backend.infrastructure.cockroach.migrate

# Add workspace-prefixed cosine vector indexes after vectors have been verified
python -m backend.infrastructure.cockroach.migration_runner upgrade head

# Compare source/destination counts, relationships, dimensions, revision, and indexes
python -m backend.infrastructure.cockroach.verify

# Compare legacy and Cockroach nearest-neighbor identities/order
python -m backend.infrastructure.cockroach.compare
```

Only after verification succeeds, set `PERSISTENCE_BACKEND=cockroach` and restart the API. The migration never deletes or edits `data/app.db`, `data/chroma`, or `data/memory_chroma`.

## Run the application

### Terminal app

```powershell
python main.py
```

The equivalent direct backend-module command is:

```powershell
python -m backend.cli
```

The CLI remains a supported entry point for document ingestion, grounded chat, learner-memory management, study reports, quizzes, review, planning, and coaching. Repository-backed workflows use the selected persistence mode; legacy-only maintenance/export commands remain intended for SQLite mode.

### API

```powershell
uvicorn backend.api.app:app --reload
```

The historical `uvicorn api.app:app --reload` command remains supported by a
thin compatibility shim.

Uvicorn binds to `127.0.0.1:8000` by default. Useful local endpoints:

- Health: `http://127.0.0.1:8000/api/health`
- OpenAPI UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

PowerShell health smoke test:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
```

Health checks probe only the selected storage backend and do not load embeddings or call an LLM.

### Frontend

Keep the API running, then open another terminal:

```powershell
Set-Location frontend
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to `http://127.0.0.1:8000`. The development CORS allowlist contains only the loopback Vite origins.

For a production frontend artifact:

```powershell
Set-Location frontend
npm run build
```

The generated `frontend/dist/` directory is intentionally ignored. FastAPI does not publish that directory; serve it with a suitable static server if packaging the app beyond local development.

## API conventions

The API is synchronous by design because the existing domain workflows are synchronous. Pydantic request/response models define public boundaries. All application errors use:

```json
{
  "error": {
    "code": "stable_machine_code",
    "message": "Safe user-facing message",
    "details": {}
  }
}
```

`details` is optional. Responses never intentionally expose uploaded file bytes, content hashes, filesystem paths, internal prompts, raw Chroma metadata, secrets, stack traces, or provider reasoning.

Major route groups:

| Route family | Purpose |
| --- | --- |
| `/api/health`, `/api/dashboard` | Storage health and deterministic dashboard data. |
| `/api/notebooks`, `/api/documents` | Notebook CRUD, uploads, assignment, search, counts, metadata edits, and deletion. |
| `/api/documents/{id}/summary`, `/api/notebooks/{id}/summary` | Cached summary reads and explicit generation. |
| `/api/topics`, `/api/topics/extract`, `/api/topics/{id}` | Cached topic listing/detail and explicit extraction. |
| `/api/topics/{id}/summary` | Cached topic-summary reads and explicit generation. |
| `/api/chat`, `/api/study/sessions`, `/api/study/interactions/{id}/outcome` | Grounded chat, session lifecycle, history, and outcome ratings. |
| `/api/memories` and proposal/consolidation subroutes | Learner-memory workflows. |
| `/api/study/actions/*` | Review queue, generated review, quizzes, adaptive plan, and coaching. |
| `/api/reports/*` | Session, progress, and quiz reports. |
| `/api/system/integrity`, `/api/system/export` | Storage diagnostics and safe export. |

The generated OpenAPI document is the authoritative field-level route reference.

## Documents and notebooks

### Supported input

| Type | What is extracted |
| --- | --- |
| `.pdf` | Readable page text with one-based page lineage. |
| `.txt` | Readable text, preferring UTF-8 with encoding detection fallback. |
| `.pptx` | Slide titles, placeholders, text boxes, tables, and recursively nested grouped-shape text, with one-based slide lineage. |

PowerPoint extraction removes repeated text within a slide and ignores empty slides. It does **not** interpret speaker notes, charts, diagrams, images, screenshots, OCR, or visual equations. Legacy `.ppt`, protected presentations, corrupt archives, password-protected PDFs, and scanned/image-only PDFs are unsupported.

Uploads are read as bytes and are limited to 50 MiB by default. The service rejects empty files, unsupported extensions, path traversal, unsafe or reserved filenames, obvious binary `.txt` content, malformed files, and inputs from which no readable text can be extracted. The CLI path wrapper applies the same ingestion pipeline.

Each stored chunk carries public retrieval lineage for document ID, MIME type, chunk index, and a separate page or slide number. Existing rows created before these nullable fields were introduced remain readable.

For a harmless first ingestion smoke test, use `examples/retrieval_distance.txt`.

### Notebook rules

- A document can belong to at most one notebook.
- A null notebook assignment appears in the virtual **Unsorted Documents** collection; it is not a physical notebook row.
- Assigning or moving a document changes SQLite membership only. It does not re-embed document chunks.
- Removing a document from a notebook returns it to Unsorted.
- A notebook can be deleted only when empty.
- Uploading duplicate content returns the existing document and does not silently move it to the requested notebook.
- Document deletion coordinates SQLite and Chroma changes with compensation. SQLite and Chroma cannot participate in one true cross-store transaction, so an interrupted failure can still require the integrity check.

## Retrieval, summaries, and topics

A retrieval request may select exactly one of:

- one notebook;
- a non-empty list of document IDs;
- one extracted topic; or
- no scope, meaning all indexed documents.

The server resolves and validates scope. Chroma filtering happens before top-k context construction. If an explicitly requested scope contains no evidence, the request fails safely; it never falls back to global documents. Notebook moves therefore need no vector rewrite. Topic retrieval is restricted to the exact cited `(document_id, chunk_index)` pairs saved for that topic.

Summary and topic generation are explicit operations:

- `GET` returns only a cached result and its `stale` flag.
- `POST` performs generation or regeneration.
- The dashboard and normal GET requests never trigger an LLM call.

Generation uses bounded hierarchical context, validated structured model output, and validated citations. The latest valid result stores source snapshots, generation time, and a fingerprint derived from the participating documents. A source change makes the cached view stale. Failed regeneration preserves the previous valid result.

## Chat, sessions, learner memory, and quizzes

### Chat and study history

Chat validates retrieval scope before work begins. The server serializes active-session creation and enforces one active session with a partial unique SQLite index. A successful turn stores the interaction and source lineage atomically as `unrated`, then may create a learner-memory proposal.

Persisted source lineage can include document ID, notebook ID, filename, MIME type, page number, slide number, chunk index, vector distance, and a bounded excerpt. Outcome ratings can later mark understanding state without rewriting the answer.

### Learner-memory decisions

The API uses server-held UUID registries instead of terminal prompts:

- `accept` saves the proposed memory.
- `replace` archives the selected existing memory and saves the replacement.
- `keep_both` saves the proposal separately.
- `reject` discards the proposal.
- `cancel` leaves the proposal pending.

Consolidation is also two-step: generate a registry-backed proposal, then accept or reject it. Pending memory proposals and pending consolidation proposals are process memory only and disappear when the API restarts.

### Quiz secrecy and scoring

Quiz generation returns only the question text and answer options. Correct options, explanations, internal source mappings, and trusted generated quiz state remain server-side until submission.

Submission accepts a contiguous prefix starting at question 1:

- a null option means the presented question was skipped;
- an omitted suffix means those questions were not presented and the attempt is aborted;
- non-contiguous or out-of-range answers are rejected.

The server derives correctness from its pending registry, calculates results, returns explanations and citations, and persists an attempt ID. Overall score is correct answers divided by all generated questions; answered accuracy is correct answers divided by answered questions. A pending quiz is single-use and disappears after submission or API restart.

## Storage, vectors, and transactions

Default runtime layout:

```text
data/
├── app.db          # documents, notebooks, caches, memory metadata, and study history
├── chroma/         # document embeddings
└── memory_chroma/  # learner-memory embeddings
```

SQLite migrations remain additive. CockroachDB uses Alembic, application-generated UUIDs, workspace-scoped compatibility `public_id` values, and `legacy_sqlite_id` for imported integer identities. Imported UUIDs are deterministic UUIDv5 values derived from workspace, source table, and legacy identity.

Document chunks are normalized in `document_chunks` with `VECTOR(384)`. Learner memory embeddings use a one-to-one `learner_memory_embeddings` table so memory lifecycle data remains independent from embedding-model/version changes. Both semantic queries use cosine distance (`<=>`), filter `workspace_id`, and apply deterministic secondary ordering. Distributed vector indexes prefix `workspace_id`:

```sql
CREATE VECTOR INDEX idx_document_chunks_workspace_embedding
ON document_chunks (workspace_id, embedding vector_cosine_ops);

CREATE VECTOR INDEX idx_memory_embeddings_workspace_embedding
ON learner_memory_embeddings (workspace_id, embedding vector_cosine_ops);
```

CockroachDB transactions run at serializable isolation. `CockroachUnitOfWork.run()` retries SQLSTATE `40001` with a fresh transaction, bounded exponential backoff, and jitter. File parsing, LLM calls, embedding generation, and HTTP calls remain outside retryable callbacks. Source rows and embedding jobs commit together; post-commit processing or `python -m backend.infrastructure.reconcile_vectors` finishes/retries embeddings after interruption.

Uploaded bytes are behind `BlobStorage`. The current Cockroach compatibility adapter stores them as `BYTES`, retaining the 50 MiB default upload limit. A deployment team can later add an S3-backed adapter without changing notebook/study services; S3 is not part of this repository phase.

Do not hand-edit SQLite or Chroma migration sources while the CLI, API, or migration verifier is running. The data directories, database files, WAL sidecars, logs, frontend build output, dependency folders, and temporary exports are ignored by Git. Ignoring a file does not untrack a copy already committed in repository history.

## Export

Download a local backup from:

```text
GET /api/system/export
```

In SQLite mode the exporter:

1. creates a consistent SQLite copy with SQLite's backup API;
2. allowlists expected files from the document and learner-memory Chroma stores;
3. rejects symlinks and paths outside the expected store roots;
4. writes a manifest with file sizes and SHA-256 checksums;
5. streams a ZIP response; and
6. removes the temporary export workspace after delivery or failure.

In Cockroach mode the same endpoint creates an allowlisted logical JSON snapshot of application tables and does not read SQLite/Chroma. Neither export contains `.env`, `DATABASE_URL`, provider credentials, logs, virtual environments, or source code. Exports contain private study data and are neither encrypted nor authenticated: store and transfer them accordingly. There is no restore UI in this milestone.

## Frontend design and behavior

The light-only interface uses:

- parchment background `#F5F1E8`;
- warm surface `#FFFCF7`;
- foreground `#1F2933`;
- sage primary `#2F5D50`;
- border `#D8D2C4`;
- bundled Crimson Pro headings and Atkinson Hyperlegible body text.

The app includes Dashboard, Chat, Notebooks, Notebook detail, Document detail, Topic workspace, Study Actions, Progress, Learner Memory, and System pages. Desktop uses a persistent/collapsible sidebar; smaller layouts use a top bar and accessible drawer.

The frontend has no Redux, Zustand, Tailwind, Material UI, or shadcn dependency. A centralized typed API client provides abortable requests, GET deduplication, and explicit invalidation. Focused hooks and context own shared state. Forms retain input after recoverable failures, and async actions expose loading, success, failure, retry, and duplicate-submission protection.

Native semantic controls and `<dialog>` provide keyboard behavior, focus trapping, and Escape dismissal. Controls target at least 44 px, focus indicators remain visible, semantic outcomes use text plus icons and accessible colors, and layouts account for long content and citations.

Route content uses GSAP `useGSAP` with scoped refs, automatic cleanup, `contextSafe`, and `gsap.matchMedia`. The normal reveal animates `autoAlpha` and 8 px of vertical movement for 220 ms with `power1.out`. Reduced-motion users receive visibility without movement. The app intentionally avoids ScrollTrigger, animated backgrounds, perpetual motion, and input-blocking animation.

## Testing

### Backend

Run compilation and the complete standard-library test suite from the repository root:

```powershell
python -m compileall backend api main.py tests
python -m unittest discover -s tests -p "test_*.py" -v
```

Backend tests isolate state with temporary SQLite/Chroma directories, fake embeddings, mocked LLMs, fresh pending registries, and cleared caches. Coverage includes migrations, structured API errors, notebooks, assignments, ingestion, scoped retrieval, intelligence caches, chat, sessions, learner-memory decisions, quiz secrecy/scoring, reports, integrity, rollback/compensation, and export exclusions.

After applying the live schema, run CockroachDB-marked integration tests explicitly:

```powershell
$env:RUN_LIVE_COCKROACH_TESTS='1'
python -m unittest tests.test_cockroach_persistence -v
Remove-Item Env:RUN_LIVE_COCKROACH_TESTS
```

After backend changes, manually launch `python main.py`, exercise the affected CLI path, and exit normally. Also start Uvicorn and verify `/api/health`.

### Frontend

```powershell
Set-Location frontend
npm test
npm run build
```

Vitest, Testing Library, and JSDOM cover routing, API failures, forms, dialogs/focus, loading and empty states, citations, outcome states, and quiz behavior. The build runs TypeScript checking before Vite packaging.

### Browser audit matrix

Test with the API and Vite dev server running:

| Viewport width | Expected navigation/layout |
| --- | --- |
| `1440px` | Full desktop layout and persistent sidebar. |
| `1024px` | Compact desktop/tablet layout without clipping. |
| `768px` | Tablet top bar/drawer and reflowed content. |
| `390px` | Single-column mobile layout with no horizontal overflow. |

At every size check the browser console, horizontal overflow, keyboard-only navigation, visible focus, long filenames/content, citations, dialogs, loading/empty/error/retry states, and duplicate-submit prevention. Repeat with `prefers-reduced-motion: reduce`. Confirm that quiz answers and explanations are absent before submission and that simulated API failures preserve recoverable form input.

### Manual end-to-end check

1. Start the API and frontend; verify health and the empty dashboard.
2. Create a notebook and upload representative PDF, TXT, and PPTX files.
3. Confirm Unsorted behavior, assignment, moving, search, duplicate handling, counts, and document metadata.
4. Generate and re-read summaries/topics; change membership or content and confirm stale status.
5. Chat at notebook, document, and topic scope; inspect citations and rate an outcome.
6. Decide a memory proposal and complete a two-step consolidation.
7. Generate a quiz, verify pre-submit secrecy, skip an item, submit, and inspect the persisted report.
8. Run review, planning, coaching, progress, dashboard, and integrity workflows.
9. Export the workspace and inspect the manifest, checksums, allowlisted contents, and temporary-file cleanup.
10. Re-run the terminal smoke path to confirm CLI compatibility.

## Security and limitations

- This is a single-user, loopback-only local MVP with no authentication or authorization. Do not expose Uvicorn directly to a LAN or the public internet.
- There is no CSRF layer, rate limiter, multi-user isolation, cloud synchronization, worker queue, streaming response, WebSocket, scheduler, notification system, or background generation.
- Provider requests send selected retrieved text to the configured LLM service. Review that provider's privacy terms before using sensitive notes.
- Embeddings and provider libraries are lazy-loaded, so the first generative/retrieval action can be slower than startup.
- API workflows are synchronous and may occupy a request until embedding or generation finishes.
- SQLite and Chroma deletion/export coordination remains compensating in legacy mode. Cockroach mode uses relational embedding jobs and reconciliation.
- Pending quizzes, learner-memory proposals, and consolidation proposals are durable workflow records and survive restart.
- CockroachDB `BYTES` upload storage is a compatibility implementation, not a substitute for production object storage at large scale.
- Ambiguous network failures during commit require checking the idempotent record/job identity before an operator retries.
- The export is a backup artifact, not an encrypted archive, and automated restore is out of scope.
- PPTX visual content and scanned PDFs require OCR or multimodal processing, which is out of scope.
- The UI is light-only; dark mode is not included.
- Frontend production hosting and API authentication are deployment responsibilities outside this milestone.

## License

See [LICENSE](LICENSE).
