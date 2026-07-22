# Agentbook Audit Report

Audit date: 2026-07-22  
Repository state: `main` at `593c4b2a78e6` (`2026-07-18 Revise .env setup instructions in README`)  
Scope: README, FastAPI application, RAG, memory, study workflows, LLM adapters, React frontend, tests, configuration, SQLite, both Chroma stores, live API, and rendered UI.  
Constraint observed: no application code, schema, dependency, or user data was modified. This report is the only repository file added.

## 1. Executive Summary

Agentbook is a substantial local-first study application, not a shell or a frontend mock. Notebook management, multi-format ingestion, scoped retrieval, citations, summaries, quizzes, manual learner-memory management, review, planning, coaching, dashboards, reports, integrity checks, and export all have real backend implementations. The React application covers most of those paths, and the deterministic test suite is strong for a hackathon project.

The central product claim is not yet true end to end. The implemented flow stops after quiz results are stored and aggregated. Quiz submission does **not** invoke misconception extraction, memory proposal creation, memory creation, or memory update. Learner memories are retrieved only by grounded chat and are injected into that chat prompt; quiz generation, review generation, coaching, and adaptive planning do not read learner memory. The current system is therefore best described as a grounded study application with a partially connected learner-memory subsystem, not yet a closed-loop agentic study companion.

The persistence layer is also not ready for a direct SQLite/Chroma-to-CockroachDB substitution. SQL and storage calls are imported directly throughout services, IDs and SQL rely on SQLite behavior, several multi-store mutations use compensating operations rather than one transaction, and proposal/pending-quiz state is process-local. A repository/unit-of-work boundary should be introduced before the database cutover.

### Overall verdict

| Area | Verdict |
|---|---|
| Core local study workflows | Mostly working |
| Grounding and lineage | Strong deterministic implementation |
| True quiz-to-memory agent loop | Missing connection; not genuine end to end |
| Learner-memory use | Real for later chat only; absent from quiz/review/coaching/plan |
| Frontend coverage | Broad, with important topic and report gaps |
| Automated verification | Good deterministic coverage; real provider/embedding/browser E2E coverage is incomplete |
| CockroachDB readiness | Not ready for a direct swap; suitable for an adapter-first migration |
| Production/cloud readiness | No: single-user, local-only, unauthenticated, no tenant isolation |

## 2. Current Architecture

```text
React 19 + Vite + TypeScript
        |
        | /api (Vite proxy in development)
        v
FastAPI route layer
        |
        +--> RAG / intelligence services
        |      +--> SQLite: documents, notebooks, cached summaries, topics
        |      +--> Chroma: document chunks + embeddings
        |      +--> hosted/local OpenAI-compatible LLM adapter
        |
        +--> Study services
        |      +--> SQLite: sessions, interactions, outcomes, quiz attempts + lineage
        |      +--> in-memory: pending quizzes
        |      +--> transient: review actions, plans, coaching, session summaries
        |
        +--> Learner-memory services
               +--> SQLite: memory records + relationships
               +--> Chroma: memory text + embeddings
               +--> in-memory: memory and consolidation proposals
```

### Runtime composition

- `main.py` exposes the FastAPI application built in `backend/api/app.py`.
- `backend/api/routes/*` contains HTTP contracts and translation to service/domain objects.
- `backend/rag/*` handles file validation/extraction, chunking, Chroma indexing, scoped retrieval, grounded chat, notebook/document/topic intelligence, and SQLite persistence.
- `backend/memory/*` handles memory records, semantic search, proposal/conflict workflows, consolidation, and its second Chroma collection.
- `backend/study/*` handles sessions, interactions, outcomes, quiz generation/scoring/history, review queues, adaptive plans, coaching, progress reports, and integrity checks.
- `backend/llm/*` provides provider selection and OpenAI-compatible model clients.
- `frontend/src/*` is a strict TypeScript SPA with handwritten API types and endpoint wrappers.

### Current deployment characteristics

- Local, single-user process; no authentication, authorization, user table, or tenant key.
- FastAPI and React dev server run separately. FastAPI does not serve the built frontend; production needs an explicit same-origin reverse proxy/static hosting arrangement.
- SQLite file: `data/app.db`.
- Document Chroma directory: `data/chroma`, collection `study_documents`.
- Learner-memory Chroma directory: `data/memory_chroma`, collection `learner_memories`.
- Default embedding model: `sentence-transformers/all-MiniLM-L6-v2`.
- Uploaded source bytes are stored in SQLite, not in a permanent upload directory.
- PDF/TXT extraction uses temporary files where needed; PPTX is read from bytes. Export creates and then cleans a temporary workspace.

### Live state observed during the audit

- `/api/health`: healthy, version `0.7.0`, SQLite healthy, both vector stores probe successfully, neither Chroma collection currently exists because no document or memory has been indexed in this live data set.
- `/api/dashboard`: 0 documents, 1 notebook, 0 memories, 0 sessions, 0 quiz attempts, 0 topics.
- `/api/system/integrity`: passed with 0 errors and 0 warnings.
- LLM provider, key, and model settings are configured. No paid/provider call was made during this audit; no real-provider success is claimed.

## 3. Feature Completion Matrix

Status describes the implemented product path, not production readiness. “Demo now” distinguishes deterministic/local demonstration from provider- or embedding-dependent execution.

| # | Feature | Status | Relevant files and main functions/classes | API endpoint(s) | Storage | Frontend connection | Demo now and gaps |
|---:|---|---|---|---|---|---|---|
| 1 | Notebook creation and management | Fully implemented | `backend/rag/notebooks.py`: `create_notebook`, list/search/update/delete, `assign_document_to_notebook`; `backend/api/routes/notebooks_documents.py`; `frontend/src/pages/NotebooksPage.tsx`, `NotebookDetailPage.tsx` | `GET/POST /api/notebooks`; `GET/PATCH/DELETE /api/notebooks/{id}`; document assignment/removal routes; unsorted routes | SQLite `notebooks`, `notebook_documents`, `documents` | Yes | Demonstrable now. Empty-only delete is enforced. Single-user only; integer local IDs. |
| 2 | PDF upload | Fully implemented | `backend/rag/ingestion.py:index_file_bytes`; `backend/rag/loaders.py:load_documents_from_bytes`; upload route and notebook pages | `POST /api/documents` | SQLite document BLOB + document Chroma chunks | Yes | Code and mocked integration tests verify it. Real first-run embedding/indexing was not exercised in the empty live data set. No OCR for scanned/image-only PDFs. |
| 3 | PPTX upload | Fully implemented | Same ingestion path; PPTX loader extracts slide text and tables | `POST /api/documents` | SQLite BLOB + document Chroma | Yes | Deterministic tests verify text/table extraction and slide lineage. Images, diagrams, speaker notes, and visual meaning are not extracted. |
| 4 | TXT upload | Fully implemented | Same ingestion path; TXT binary/text validation | `POST /api/documents` | SQLite BLOB + document Chroma | Yes | Deterministic tests verify it. Encoding behavior and binary rejection are handled. |
| 5 | Document extraction | Fully implemented | `backend/rag/loaders.py`, `chunking.py`, `ingestion.py`; metadata carries document, MIME, page/slide, chunk index | Upload endpoint | Transient parsed text; persisted chunk text/metadata/embedding in Chroma | Indirectly through upload | Verified with unit/integration tests. Extraction has no OCR and does not model PPTX visuals. |
| 6 | Document summary | Fully implemented | `backend/rag/intelligence.py:generate_summary`; cache/store helpers; `DocumentDetailPage.tsx` | `GET/POST /api/documents/{id}/summary` | SQLite `cached_intelligence`; reads document Chroma | Yes | UI supports generate/regenerate, cached data, stale warning, loading/error/empty states. Provider-dependent generation was not invoked. |
| 7 | Notebook summary | Fully implemented | Same intelligence service scoped by notebook; `NotebookDetailPage.tsx` | `GET/POST /api/notebooks/{id}/summary` | SQLite cache; reads notebook-filtered document Chroma | Yes | Same provider caveat. Scope tests pass and stale cache is preserved on regeneration failure. |
| 8 | Topic extraction | Backend only | `backend/rag/intelligence.py:extract_topics`; `intelligence_store.py`; route exists | `POST /api/topics/extract` | SQLite `topics`, `topic_sources`; reads document Chroma | No usable UI action | Endpoint wrapper exists at `frontend/src/api/endpoints.ts:248`, but no page calls it. Cannot be demonstrated from normal UI. |
| 9 | Topic summary | Partially implemented | `TopicWorkspacePage.tsx`; `generate_summary` with topic scope | `GET/POST /api/topics/{topic_id}/summary` | SQLite cache; reads exact `(document_id, chunk_index)` pairs | Orphaned route | Direct URL works if a topic exists, but no normal navigation links to `/topics/{id}` and extraction has no UI entry point. |
| 10 | Grounded chat with citations | Fully implemented | `backend/rag/rag_service.py:answer_question`; `backend/rag/chat_service.py:run_chat`; `frontend/src/pages/ChatPage.tsx` | `POST /api/chat`; session/outcome routes | Reads document Chroma and memory Chroma; writes SQLite sessions/interactions/source lineage | Yes | Structurally verified with mocked model/vector dependencies. Provider- and document-index-dependent for a real answer. Citation lineage is validated structurally, not fact-checked semantically. |
| 11 | Quiz generation | Fully implemented | `backend/study/quiz_generator.py:generate_grounded_quiz`; `quiz_api.py:generate_quiz_for_api`; `StudyActionsPage.tsx` | `POST /api/study/actions/quizzes/generate` | Reads document Chroma; writes pending quiz only to process memory | Yes | API hides answers/explanations before submit and supports all scopes. Provider-dependent. Pending quizzes disappear on restart and are capped at 128. |
| 12 | Quiz submission | Fully implemented | `backend/study/quiz_api.py:submit_quiz`; `quiz_history.py:save_quiz_run_result` | `POST /api/study/actions/quizzes/{quiz_id}/submit` | Reads in-memory pending quiz; writes three SQLite quiz tables | Yes | Trusted server quiz is scored; client cannot inject correctness. Restart between generate and submit loses the quiz. |
| 13 | Quiz scoring | Fully implemented | `backend/study/quiz_api.py:score_quiz` | Submission endpoint | Persisted in quiz attempt/question tables | Yes | Pure scorer tests pass, including skipped/unpresented handling. |
| 14 | Quiz result explanation | Fully implemented | Quiz generator creates answer/explanation; submission response redacts or reveals based on presentation state | Submission endpoint; quiz report endpoints | SQLite after submission | Yes | Tested for answer/explanation secrecy and aborted-attempt redaction. Explanations are generated at quiz generation time, not adapted after analyzing the learner answer. |
| 15 | Student weakness detection | Partially implemented | `backend/study/quiz_reporting.py`; `recommendations.py`; `planner.py` | Quiz performance, progress, review queue, plan endpoints | Reads SQLite quiz and chat outcomes | Yes, through progress/review/plan | Incorrect/skipped answers and `partial`/`confused` outcomes become gaps. There is no semantic misconception entity, diagnosis, confidence, cause analysis, or quiz-to-memory step. Exact normalized question text is used for some grouping. |
| 16 | Learner-memory creation | Partially implemented | `backend/memory/service.py:add_memory`; `extractor.py`; `proposals.py:create_memory_proposal`; `MemoryPage.tsx` | `POST /api/memories`; chat can return a proposal | SQLite `memories` + memory Chroma | Yes | Manual creation works. Chat may create a proposal after a response. Quiz performance never creates a proposal or memory. Extractor treats the user message—not the assistant answer—as evidence. |
| 17 | Learner-memory retrieval | Fully implemented | `backend/memory/service.py:search_memories`; `backend/rag/rag_service.py:answer_question` | `POST /api/memories/search`; implicit in `/api/chat` | Reads memory Chroma, then SQLite records | Search UI and chat | Semantic retrieval is genuinely wired into later chat. It is not used by quiz, review, coach, or planner. |
| 18 | Learner-memory update | Partially implemented | `backend/memory/service.py:update_memory`; memory routes and page | `PATCH /api/memories/{id}` | SQLite then memory Chroma | Yes | Normal path works. If vector re-indexing fails after SQLite commit, SQL and Chroma diverge; there is no rollback/outbox/reconciliation. |
| 19 | Memory proposal approval or rejection | Fully implemented | `backend/memory/proposals.py`; conflict detector; proposal UI in chat/memory flows | `POST /api/memories/proposals/{proposal_id}/decision` | Proposal registry in process memory; accepted result goes to SQLite + Chroma | Yes | Accept/reject/cancel/replace/keep-both are tested. Proposals expire on restart, are bounded, and are not auditable/persisted. |
| 20 | Memory consolidation | Partially implemented | `backend/memory/consolidator.py:propose_memory_consolidation`; `consolidation_registry.py`; service apply logic | propose/apply endpoints | Proposal in process memory; final memories/relationships in SQLite + memory Chroma | Yes | Two-step stale-safe workflow is tested. Still provider-dependent, process-local before apply, and exposed to cross-store consistency failures. |
| 21 | Adaptive review | Partially implemented | `backend/study/recommendations.py:build_review_queue`; `reviewer.py:generate_review_action`; `StudyActionsPage.tsx` | `GET /api/study/actions/review-queue`; `POST /api/study/actions/review` | Reads SQLite study interactions/outcomes; generated action is transient; retrieves document Chroma | Yes | Empty and generated states exist. Review queue is driven by chat outcomes, not learner memory; quiz gaps are not part of this queue. Generated review actions are not persisted. |
| 22 | Adaptive study plan | Partially implemented | `backend/study/planner.py:build_adaptive_study_plan`; `StudyActionsPage.tsx` | `POST /api/study/actions/plan` | Reads SQLite outcomes/quiz history; result transient | Yes | Deterministic, scope-aware plan tested. It does not read learner memory and is not stored/versioned. |
| 23 | Coaching | Partially implemented | `backend/study/coach.py:generate_coaching_plan`; `StudyActionsPage.tsx` | `POST /api/study/actions/coaching-plan` | Reads transient plan + document Chroma; result transient | Yes | Provider-dependent and connected to UI. Does not read learner memory; coaching history is lost. |
| 24 | Dashboard | Fully implemented | `backend/study/dashboard.py:build_dashboard`; `DashboardPage.tsx` | `GET /api/dashboard` | Deterministic SQLite reads | Yes | Live and browser-verified, including empty state and responsive layout. |
| 25 | Progress reports | Partially implemented | `backend/study/reporting.py`; `quiz_reporting.py`; `ProgressPage.tsx` | Study session/progress and quiz report/performance endpoints | SQLite reads | Main progress UI yes | Main aggregates and session lists are connected. Individual quiz-attempt detail and generated session summary have API wrappers but no full UI flow. No persisted longitudinal report snapshots. |
| 26 | Frontend-backend integration | Partially implemented | `frontend/src/api/endpoints.ts`, handwritten `types.ts`, page hooks; all backend routes | 47 OpenAPI paths | N/A | Broad but incomplete | Core flows are integrated and strict build passes. Topic extraction is unused, topic workspace is orphaned, session-summary and some drilldown wrappers are unused. Dev proxy/same-origin assumption is undocumented for production deployment. |
| 27 | Data export | Fully implemented | `backend/api/export_service.py:build_study_export`; `SystemPage.tsx` | `GET /api/system/export` | SQLite backup + allowlisted copies of both Chroma directories + manifest in temp ZIP | Yes | Tests verify cleanup, allowlist, manifest, and generic error response. No restore/import workflow, encryption, authentication, or tenant scoping. |
| 28 | Automated tests | Partially implemented | `tests/*.py`; `frontend/src/test/*.test.tsx` | TestClient and UI integration tests | Temporary SQLite and faked vector/model services | N/A | 71 backend tests pass (1 skipped), 19 frontend tests pass, build passes. There is no real LLM contract test, real embedding/Chroma end-to-end test, browser E2E suite, CockroachDB test, or proof that memory changes model output. |

## 4. Verified End-to-End Flow

### Intended primary flow trace

| Step | Entry point and implementation | Input | Output | Reads | Writes | API | Frontend | Connected? |
|---|---|---|---|---|---|---|---|---|
| Upload material | Upload form → `index_file_bytes` → `load_documents_from_bytes` → chunk/index | Multipart PDF/PPTX/TXT, optional notebook | Document record and chunk count | Existing hash/notebook; file bytes during extraction | SQLite `documents`/assignment + document Chroma | `POST /api/documents` | Notebooks and notebook detail pages | Yes |
| Generate quiz | Quiz form → `generate_quiz_for_api` → `generate_grounded_quiz` | Retrieval scope, topic, count | Sanitized pending quiz without answers | Scoped document Chroma; LLM | In-memory pending-quiz registry only | `POST /api/study/actions/quizzes/generate` | Study Actions / Quiz | Yes, provider dependent |
| Submit answers | `submit_quiz` → `score_quiz` → `save_quiz_run_result` | Quiz UUID and presented answer prefix | Trusted scores, statuses, explanations, citations | Pending in-memory quiz | `quiz_attempts`, `quiz_question_attempts`, `quiz_question_sources` | `POST /api/study/actions/quizzes/{id}/submit` | Study Actions / Quiz | Yes |
| Analyze performance | Quiz reporting and planner aggregators | Stored question outcomes | Counts, percentages, incorrect/skipped review items | Quiz attempt tables | None beyond raw attempt already stored | Report, progress, plan endpoints | Progress and Study Actions | Yes, deterministic |
| Detect misconception | No dedicated implementation | Incorrect/skipped question or chat outcome | Only a gap/review reason based on status and text grouping | Quiz/chat history | None | Indirect via reports/plan | Indirect | **No genuine misconception detection** |
| Create/update learner memory | Manual API or post-chat proposal only | Manual memory fields, or chat user message + assistant response | Stored memory or proposal | Existing memories/conflicts | SQLite + memory Chroma on accept/manual create | Memory and proposal endpoints | Memory page/chat proposal UI | **Not connected to quiz** |
| Retrieve memory later | `search_memories` called inside `answer_question` | Later chat question | Up to 3 active semantic memory matches | Memory Chroma + SQLite | None | Implicit in `POST /api/chat` | Chat | Yes, chat only |
| Change future behavior | `RAG_PROMPT` includes memory context for style, depth, examples, emphasis | Retrieved memories plus grounded sources | LLM answer | Same as above | Chat interaction/source lineage | `POST /api/chat` | Chat | Partially: prompt is influenced; quiz/review/coach/plan are not |

### What the automated E2E test proves

`tests/test_backend_e2e.py::BackendEndToEndTest::test_complete_backend_workflow` runs an isolated workflow successfully with temporary storage and mocked model/vector dependencies. It proves route/service orchestration and persistence shape, but not network model behavior, real embeddings, or a quiz-to-memory loop.

### What could be demonstrated in the live app

- Dashboard, notebooks, chat setup, quiz setup, memory management, review empty state, and system/integrity/export screens render and navigate.
- The live API health/dashboard/integrity endpoints return coherent deterministic data.
- Responsive checks at 390, 768, 1024, and 1440 CSS pixels found no horizontal document overflow.
- The mobile navigation opens as a labeled dialog and exposes all primary destinations.
- No browser warnings or errors were logged in the inspected empty-state journey.
- A real generation/upload workflow was not executed: the live data set has no documents or vector collections, and calling the configured external LLM could incur external cost. The audit therefore does not claim a real-provider or real-embedding demonstration.

## 5. Agentic Memory Assessment

### Verdict

**The true agentic-memory loop is not implemented end to end.**

The memory subsystem is more than display-only: active learner memories are semantically retrieved in `backend/rag/rag_service.py:answer_question` and inserted into the grounded-chat prompt. That gives the LLM information intended to alter explanation style, depth, examples, and emphasis. This is a real integration point.

However, the intended learning loop is disconnected in three decisive places:

1. `backend/study/quiz_api.py:submit_quiz` persists quiz results and removes the pending quiz, but never calls the memory extractor, proposal registry, `add_memory`, or `update_memory`.
2. “Weakness detection” records incorrect/skipped items and chat outcome labels; it does not infer or persist a misconception suitable for learner memory.
3. `search_memories` is imported by grounded chat only. Quiz generation, adaptive review, planner, and coach retrieve document evidence but do not retrieve learner memories.

### Actual memory-producing paths

- Manual memory creation from `MemoryPage` → `POST /api/memories`.
- After successful chat, `run_chat` may call `create_memory_proposal(user_message, assistant_answer)`.
- Proposal acceptance or conflict resolution may add/replace/archive records.
- Consolidation can merge active memories after a two-step proposal/apply flow.

The chat extractor deliberately uses the user message as evidence and not the assistant answer. That is a defensible anti-hallucination choice, but it means a generated explanation itself cannot prove learning, misunderstanding, or a durable preference.

### Actual memory-consuming paths

| Workflow | Reads learner memory? | Effect |
|---|---:|---|
| Grounded chat explanation | Yes | Memory text is inserted into the prompt; intended to change teaching style/depth/examples/emphasis |
| Quiz generation | No | Quiz is based on topic/scope and retrieved study chunks only |
| Quiz result explanation | No | Explanation is generated with the quiz before learner answers are submitted |
| Adaptive review | No | Uses chat outcome history and document sources |
| Adaptive study plan | No | Uses chat outcome and quiz-gap aggregates |
| Coaching | No | Uses transient plan and document sources |
| Progress/dashboard | No semantic use | Counts and displays memory totals only |

### Required behavior before calling the product agentic

1. Introduce a persisted `learning_signal` or `misconception` record derived from trusted quiz/question outcomes and explicit chat outcome feedback.
2. Define confidence, evidence, provenance, recency, and approval rules before converting a signal into learner memory.
3. Create or update memory in a transactionally reliable workflow after quiz submission, preferably via a durable outbox/job rather than inside the request.
4. Retrieve relevant memories in quiz generation, review generation, coaching, and planning, with explicit prompt/algorithm fields showing how each memory changed the result.
5. Add tests that compare otherwise identical future requests with and without a memory and assert an observable planning/prompt/output difference.
6. Preserve human approval for sensitive or uncertain inferred memories and make the evidence visible.

## 6. Storage Inventory

### A. Structured relational data

| Current table | Primary/foreign keys | Stored data | Current writer/readers | CockroachDB target |
|---|---|---|---|---|
| `documents` | Integer autoincrement PK; unique file hash | Filename, MIME, source hash, source BLOB, chunk count, timestamps | Ingestion, document APIs, export, integrity | `documents` with UUID PK, unique hash, `BYTES` only as an initial compatibility choice; consider blob abstraction before scale |
| `notebooks` | Integer PK; case-insensitive unique name | Name, description, timestamps | Notebook service/UI | `notebooks` with UUID PK, normalized-name unique index, tenant/user key |
| `notebook_documents` | Document ID PK; notebook/document FKs cascade | One current notebook assignment per document | Notebook service | Join/assignment table with tenant-safe composite constraints |
| `cached_intelligence` | Integer PK; unique kind/scope tuple | Summary JSON, source snapshot JSON, generated time, fingerprint | Intelligence service | JSONB cache table with explicit scope columns and expiry/version metadata |
| `topics` | UUID text PK | Topic name/description, extraction scope, fingerprint | Intelligence service | UUID PK; tenant/scope FKs; normalized search fields |
| `topic_sources` | Composite topic/document/chunk PK; topic/document FKs cascade | Exact source chunk lineage + snapshot metadata | Intelligence service | FK to relational document chunks; preserve immutable citation snapshot where deletion semantics require it |
| `memories` | Integer PK | Type, content, confidence, importance, status, timestamps | Memory service | UUID PK, typed/check-constrained fields, tenant/user FK, optional embedding version |
| `memory_relationships` | Integer PK; memory FKs; unique relationship | Consolidation lineage | Memory/consolidation service | UUID/composite key, explicit `ON DELETE` policy, tenant-safe FKs |
| `study_sessions` | Integer PK; partial unique active-status index | Active/completed state, timestamps | Chat/session/report services | UUID PK, user/tenant key; uniqueness by owner plus active status or a separate active-session pointer |
| `study_interactions` | Integer PK; session FK cascade | Question, answer, outcome, timestamps | Chat, progress, review, plan | UUID PK/FK, enum/check for outcome, timestamps as `TIMESTAMPTZ` |
| `study_interaction_sources` | Integer PK; interaction FK only | Citation snapshot, document/notebook IDs, chunk/page/slide | Chat/report/integrity | FK to chunks where live lineage is required plus immutable source snapshot for deleted documents |
| `quiz_attempts` | Integer PK | Quiz scope, topic, status, score, timestamps | Quiz history/report/plan | UUID PK; tenant/user/scope columns; `TIMESTAMPTZ` |
| `quiz_question_attempts` | Integer PK; attempt FK cascade | Question/options, trusted answer, user answer, explanation, correctness/status | Quiz submission/report/plan | UUID PK/FK; options as JSONB; question ordering constraint |
| `quiz_question_sources` | Integer PK; question-attempt FK only | Citation snapshot and optional document/notebook lineage | Quiz submission/report/integrity | FK to chunks where appropriate plus immutable snapshot columns |

The following product concepts are **not persisted**: pending quizzes, memory proposals, consolidation proposals, generated session summaries, review actions, adaptive study plans, coaching plans, and a distinct misconception/learning-signal model. Reports are computed views, not stored report entities.

### B. Vector data

| Collection | ID format | Payload/metadata | Current operations | Suggested CockroachDB replacement |
|---|---|---|---|---|
| `study_documents` | `document-{id}-chunk-{index}` | Chunk text, embedding, document ID, filename, MIME, chunk index, page/slide | Add on ingestion; scoped cosine-like similarity search; delete/snapshot/restore on document deletion | `document_chunks` table with UUID/text chunk ID, document FK, ordinal, text, metadata columns/JSONB, model/version, `VECTOR(384)` if the current model is retained, and cosine vector index with tenant/document prefix filters |
| `learner_memories` | `memory-{id}` | Memory content, embedding, memory ID/type/confidence/importance/status | Add/search/delete/re-add during create/update/archive/consolidate/delete | `memory_embeddings` one-to-one table or embedding columns on `memories`, with memory FK, model/version, `VECTOR(384)`, active-status/tenant prefix strategy, and cosine vector index |

The current model card states that `all-MiniLM-L6-v2` produces 384-dimensional vectors. CockroachDB supports fixed-size `VECTOR` columns and cosine vector indexes suitable for RAG. Verify dimensions and embedding version in a migration preflight rather than hard-coding silently. Sources: [CockroachDB VECTOR](https://www.cockroachlabs.com/docs/stable/vector), [CockroachDB vector indexes](https://www.cockroachlabs.com/docs/stable/vector-indexes), [model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2).

### C. Local files

| Path/artifact | Purpose | Lifecycle | Migration recommendation |
|---|---|---|---|
| `data/app.db` | All structured records and original source BLOBs | Durable local SQLite | Replace through repository-backed CockroachDB schema and one-time importer |
| `data/chroma/*` | Document vector store | Durable local Chroma | Export chunks/metadata/embeddings, validate counts/checksums, import to `document_chunks` |
| `data/memory_chroma/*` | Memory vector store | Durable local Chroma | Import to memory embedding table; reconcile every vector to an active/archive SQL record |
| Temporary extraction files | PDF/TXT parser bridge | Deleted after use | Keep temporary-file adapter; use secure temp directory and cleanup guarantees |
| Temporary export workspace/ZIP | Snapshot and downloadable archive | Deleted after response/background cleanup | Redesign export around logical CockroachDB dump/query plus vector rows; retain allowlist/checksum manifest |
| Frontend `dist/*` | Static build output | Regenerated | No database migration; choose hosting/reverse-proxy strategy separately |

### Persistence code map

| File | Function/class area | Current storage | Data and operation | Difficulty | Suggested replacement |
|---|---|---|---|---|---|
| `backend/rag/config.py` | Paths/collection constants | Environment + local paths | Database and Chroma locations, model/provider config | Medium | Typed settings containing database URL, schema, vector dimensions/version; no path assumptions in service code |
| `backend/rag/database.py` | `get_connection`, `initialize_database`, document CRUD | SQLite/sqlite3 | Connection lifecycle, DDL, documents, notebooks, caches, topics | High | SQLAlchemy/psycopg repository + migrations + retrying unit of work |
| `backend/rag/notebooks.py` | Notebook/search/assignment functions | SQLite | Notebook and assignment SQL | Medium | Notebook/document repositories |
| `backend/rag/intelligence_store.py` | Summary/topic cache and lineage | SQLite | JSON text, topic source pairs, stale fingerprints | High | JSONB-backed intelligence repository and chunk FKs |
| `backend/rag/ingestion.py` | `index_file_bytes` | SQLite + Chroma | Source insert, chunk vector add, compensating cleanup | High | One SQL transaction for document/chunks/outbox; background embedding worker if needed |
| `backend/rag/document_service.py` | `delete_document` | SQLite + Chroma | Vector snapshot/delete and SQL delete compensation | High | One CockroachDB transaction if vectors are colocated; otherwise durable outbox/saga |
| `backend/rag/vector_store.py` | lazy Chroma and probe/delete | Chroma + direct internal Chroma SQLite probe | Document semantic storage and health | High | Vector repository using SQL `ORDER BY embedding <=> query`; supported health query, not internal-table probing |
| `backend/rag/rag_service.py` | scoped retrieval and `answer_question` | Chroma + memory service | Document and learner-memory reads | High | Inject document/memory retrieval interfaces; preserve prompt/domain code |
| `backend/memory/database.py` | memory/relationship CRUD | SQLite | Memory records and consolidation lineage | High | Memory repository with explicit FK/delete policy and migrations |
| `backend/memory/vector_store.py` | lazy memory Chroma | Chroma | Memory vector IDs/add/delete/probe | High | Memory vector repository in CockroachDB |
| `backend/memory/service.py` | add/search/update/archive/replace/delete | SQLite + Chroma | Cross-store orchestration | Very high | Transactional repository/unit of work; vectors in same SQL transaction or outbox |
| `backend/memory/proposals.py` | proposal registry | Process memory | Pending decisions and conflict target | Medium | Persist proposal, evidence, version, expiry, decision, actor |
| `backend/memory/consolidation_registry.py` | consolidation registry | Process memory | Pending merge proposal | Medium | Persist proposal with source-memory versions and optimistic concurrency |
| `backend/study/database.py` | sessions/interactions/quiz DDL and CRUD | SQLite | Study history and lineage | Very high | Study/quiz repositories, transaction retry wrapper, dialect-neutral SQL |
| `backend/study/quiz_api.py` | pending quiz registry | Process memory | Server-trusted unsubmitted quiz | Medium | Persist pending quiz + expiry/version, or issue a signed encrypted token with replay controls |
| `backend/study/quiz_history.py` | `save_quiz_run_result` | SQLite | Atomic attempt/question/source writes | High | Retrying CockroachDB unit of work |
| `backend/study/*reporting.py`, `dashboard.py`, `recommendations.py`, `planner.py` | query/aggregate functions | SQLite reads | Derived reports, queues, plan evidence | Medium/high | Read repositories or SQL views; retain pure aggregation after decoupling row shapes |
| `backend/api/export_service.py` | `build_study_export` | SQLite backup + Chroma filesystem | Consistent local snapshot and manifest | Very high | Logical tenant export from CockroachDB tables, chunk/vector rows, and optional blob provider |
| `backend/study/integrity.py` | `run_study_integrity_check` | SQLite + Chroma | Counts, lineage and cross-store checks | High | SQL constraint/consistency checks and CockroachDB vector orphan queries |
| `backend/cli.py` | many direct service/database calls | All current stores | Duplicates product workflow orchestration | High | Keep CLI UX but route it through the same injected application services |

## 7. SQLite Usage Map

### Connection and transaction behavior

- `backend/rag/database.py:get_connection` opens a new `sqlite3.Connection`, enables foreign keys and a busy timeout, commits on normal context exit, rolls back on exception, and closes the connection.
- Initialization enables WAL and calls additive migration logic from RAG, notebook/intelligence, memory, and study modules.
- Several critical methods use `BEGIN IMMEDIATE` or rely on SQLite locking/partial uniqueness for concurrency, especially the one-active-session invariant.
- Services often call multiple repository-like functions that each open and commit their own connection. This prevents a single transaction from covering the complete business operation.

### SQLite-specific assumptions to remove

| Assumption | Examples | Migration impact |
|---|---|---|
| `?` placeholders and raw `sqlite3.Row` | Throughout all database/service query modules | Every query/caller row mapping changes unless hidden behind repositories |
| `INTEGER PRIMARY KEY AUTOINCREMENT` + `lastrowid` | Documents, notebooks, memories, sessions, quiz records | Replace IDs and return semantics; app/client/types assume numbers for most entities |
| `PRAGMA` | Foreign keys, busy timeout, WAL, table-info migrations | Replace connection bootstrap and migration introspection |
| `COLLATE NOCASE` | Notebook uniqueness/search; topic search/order | Define normalized case-insensitive semantics with `CITEXT`, computed normalized column, or explicit collation strategy |
| `LIKE ... ESCAPE ... COLLATE NOCASE` | Notebook/document/topic search | Rewrite and test query semantics/index use |
| SQLite backup API | Export | Cannot snapshot CockroachDB by copying a file |
| `sqlite3.Binary` / BLOB | Original uploads | CockroachDB `BYTES` works, but current 50 MiB upload limit conflicts with CockroachDB guidance to keep individual BYTES values small; add a blob abstraction before scale ([BYTES docs](https://www.cockroachlabs.com/docs/stable/bytes)) |
| JSON serialized into TEXT | Caches, options, snapshots | Use JSONB with explicit validation/versioning ([JSONB docs](https://www.cockroachlabs.com/docs/stable/jsonb)) |
| Timestamp strings | All tables | Use `TIMESTAMPTZ` and normalize UTC boundaries |
| Partial unique active-session index | Study sessions | Recreate per user/tenant and test CockroachDB support/behavior under concurrency |
| Dynamic `IN (?,...)` construction | Scoped document/chunk queries | Convert to driver-safe array/bind expansion; keep values parameterized |
| Compensating cross-store rollback | Ingestion, document deletion, memory mutations | Replace with one SQL transaction once vectors are colocated, or a durable outbox |

CockroachDB defaults to serializable transactions and may return retryable `SQLSTATE 40001` errors that need client-side retry handling for multi-statement transactions. The current context manager has rollback but no retry protocol. Sources: [transactions](https://www.cockroachlabs.com/docs/stable/transactions), [retry error reference](https://www.cockroachlabs.com/docs/stable/transaction-retry-error-reference).

### Foreign-key and lineage observations

- Notebook assignments and topic sources have useful cascade constraints.
- `memory_relationships` references memories without an explicit delete cascade; deleting a related memory can fail after its vector has already been deleted.
- `study_interaction_sources` and `quiz_question_sources` constrain the parent interaction/question but do not constrain stored document/notebook IDs. This permits durable citation snapshots after source deletion, but the intent is implicit and integrity cannot be enforced by the database.
- Topic sources cascade on document deletion, potentially leaving a topic with no sources; no automatic topic cleanup is performed.
- All data lacks a user/tenant ownership key, making safe cloud migration impossible without a schema decision.

## 8. Chroma Usage Map

### Document vectors

- Construction: `backend/rag/vector_store.py:get_vector_store` lazily creates LangChain Chroma using `data/chroma`, collection `study_documents`, and the shared embedding model.
- Write: `backend/rag/ingestion.py:index_file_bytes` calls `add_documents` with stable IDs `document-{id}-chunk-{index}`.
- Read: `backend/rag/rag_service.py` performs similarity search with metadata filters for global/notebook/document scopes; topic scope resolves exact source pairs. Intelligence, quiz, reviewer, and coach reuse scoped retrieval.
- Delete: `backend/rag/document_service.py` captures a Chroma snapshot, deletes vectors, then deletes SQLite data and attempts restoration on failure.
- Health: `probe_chroma_store` reads Chroma's internal SQLite `collections` table directly. That is brittle across Chroma versions and has no CockroachDB analogue.

### Learner-memory vectors

- Construction: `backend/memory/vector_store.py:get_memory_vector_store` uses `data/memory_chroma`, collection `learner_memories`, with the same embedding model.
- Write/read: `backend/memory/service.py` calls `add_documents` and `similarity_search_with_score`; metadata stores memory ID/type/confidence/importance/status.
- Delete/rewrite: update, archive, replace, consolidate, and delete remove/re-add IDs such as `memory-{id}`.

### Confirmed consistency defects

1. **Memory update divergence:** SQLite is updated before deleting/re-adding the Chroma vector. A vector failure leaves new SQL content with stale or missing vector content.
2. **Memory delete divergence:** the vector is deleted before the SQLite record. If SQL deletion fails, including because of a relationship FK, the record remains but cannot be semantically retrieved.
3. **Document ingest/delete are compensated, not atomic:** crash windows remain between stores even where exception handling tries to restore one side.
4. **Archive/consolidation still span stores:** careful compensation reduces ordinary failures but cannot guarantee crash consistency.

Colocating embeddings with records in CockroachDB can eliminate these classes of split-brain behavior when every related change is made in one retryable transaction.

## 9. Frontend-Backend Integration Review

### Connected surfaces

- Dashboard, chat, notebooks, notebook detail, document detail, study actions, progress, memory, and system pages are routed in `frontend/src/App.tsx`.
- Loading, error, empty, stale-cache, confirmation, and success states are generally present on the main flows.
- Upload constraints and supported formats are visible before selection.
- Chat exposes scope selection and source citations; quiz hides trusted answers until submission.
- Native headings, labels, landmark elements, a skip link, focus styling, a labeled mobile navigation dialog, and reduced-motion styling are positive accessibility foundations.
- Strict TypeScript compilation and the production build succeed.

### Missing or incomplete wiring

1. `topicApi.extract` exists but is unused by pages. Topic extraction is backend-only in practice.
2. `TopicWorkspacePage` is routed at `/topics/:topicId`, but repository search found no normal navigation link to that path. It is an orphaned page.
3. Session-summary, individual quiz-attempt detail, and some memory/search wrappers exist without complete page journeys.
4. Main progress works, but backend detail/report capabilities are not all discoverable from the UI.
5. Frontend response types are handwritten rather than generated/checked from OpenAPI. Current build passes, but contract drift can reach runtime.
6. The System export link assumes `/api` is same-origin in production. The repository contains a Vite dev proxy but no FastAPI static hosting or deployment reverse-proxy implementation.

### Visual and responsive evidence

Captured screenshots are outside the repository so the audit does not add product assets:

- `C:/Users/Asus/.codex/visualizations/2026/07/22/019f87c4-74c1-7263-b8e8-09039efddca0/agentbook-audit/01-dashboard-desktop.jpg`
- `C:/Users/Asus/.codex/visualizations/2026/07/22/019f87c4-74c1-7263-b8e8-09039efddca0/agentbook-audit/02-notebooks-desktop.jpg`
- `C:/Users/Asus/.codex/visualizations/2026/07/22/019f87c4-74c1-7263-b8e8-09039efddca0/agentbook-audit/05-quiz-setup-desktop.jpg`
- `C:/Users/Asus/.codex/visualizations/2026/07/22/019f87c4-74c1-7263-b8e8-09039efddca0/agentbook-audit/07-chat-desktop.jpg`
- `C:/Users/Asus/.codex/visualizations/2026/07/22/019f87c4-74c1-7263-b8e8-09039efddca0/agentbook-audit/09-dashboard-mobile.jpg`
- `C:/Users/Asus/.codex/visualizations/2026/07/22/019f87c4-74c1-7263-b8e8-09039efddca0/agentbook-audit/10-mobile-nav-open.jpg`

Observed strengths: calm visual hierarchy, clear empty states, consistent cards/forms, strong readable type, and a functional mobile drawer. Observed product-level issue: the interface presents a coherent learning workspace, but the key “topics” path is undiscoverable and the visual product gives no indication that learner memory affects only chat. Full keyboard traversal, screen-reader output, color-contrast measurement, populated dense states, and provider-error visual states were not completely audited; no claim of WCAG conformance is made.

## 10. Test Results

All commands were run on 2026-07-22 from the checked-out repository.

### Python import/syntax check

```powershell
.\.venv\Scripts\python.exe -m compileall backend api main.py tests
```

Result: exit code 0; all listed packages and tests compiled.

### Complete Python/API/integration suite

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

Result: exit code 0. `Ran 71 tests ... OK (skipped=1)`.

- The skip is `test_symlink_database_is_rejected_and_temp_files_are_cleaned`, because symlinks were unavailable on this Windows environment.
- A Starlette deprecation warning reports that the current `httpx` integration should move to `httpx2`.
- Test groups cover application/error foundations, SQLite connection safety, chat/session/memory APIs, end-to-end orchestration, export, ingestion, notebook/document management, quiz trust/scoring/redaction, scoped intelligence/retrieval, and scoped planner evidence.

### Isolated scripted primary-workflow smoke

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_backend_e2e.BackendEndToEndTest.test_complete_backend_workflow -v
```

Result: exit code 0; 1 workflow test passed. It uses isolated temporary state and mocked external dependencies.

### Frontend tests

```powershell
npm.cmd test
```

Result: exit code 0; 7 test files and 19 tests passed.

### Frontend type and production build

```powershell
npm.cmd run build
```

Result: exit code 0; `tsc --noEmit` and Vite build succeeded, 1,819 modules transformed. Main JavaScript bundle: 430.91 kB (133.15 kB gzip); CSS: 36.86 kB (7.04 kB gzip).

### Live deterministic smoke

Read-only calls to `/api/health`, `/api/dashboard`, and `/api/system/integrity` succeeded. Integrity reported `passed: true`, 0 errors, and 0 warnings.

### What is and is not verified

| Category | Result |
|---|---|
| Deterministic domain/API behavior | Strongly covered and passing |
| SQLite migrations/constraints/rollback | Covered in temporary test databases |
| Provider-dependent generation | Model calls are mocked; configured provider was not called during audit |
| Embedding-model-dependent indexing/search | Faked/mocked in tests; live collections were absent; not proven against the real model/Chroma |
| Browser E2E | Manual read-only inspection only; no automated Playwright/Cypress suite |
| Memory changes future model output | Not tested; only the prompt-injection wiring can be established from code |
| CockroachDB | No schema, adapter, or integration test exists yet |

## 11. Bugs and Incomplete Features

### Critical — product truth and migration blockers

1. **Quiz-to-memory loop is absent.** Quiz submission never creates a learning signal, misconception, memory proposal, or memory update.
2. **Learner memory affects chat only.** Quiz, review, coaching, and study-plan services do not consume it.
3. **No users, authentication, or tenant isolation.** Moving this schema to a shared/cloud CockroachDB cluster would mix every user's documents, memories, reports, vectors, and exports.
4. **Cross-store writes can diverge.** Memory update/delete have confirmed failure orders that can split SQLite and Chroma.
5. **No database abstraction.** Services import global SQL/vector functions and configuration directly, so a direct database replacement has a large blast radius.

### High

6. Pending quizzes are process-local and disappear on restart/deploy; a generated quiz cannot reliably be submitted in a multi-worker service.
7. Memory and consolidation proposals are process-local, bounded, unaudited, and disappear on restart.
8. Current transaction code has no CockroachDB serialization-retry handling.
9. Most IDs are SQLite autoincrement integers. They are unsuitable for distributed/offline import without a deliberate compatibility mapping.
10. Memory delete removes its vector first and can then fail on relationship constraints, leaving a live SQL record without a vector.
11. Memory update commits SQL before vector replacement, leaving stale/missing vectors on failure.
12. Source-lineage FK semantics are inconsistent and implicit; some sources cascade away while study/quiz citation snapshots retain unconstrained IDs.
13. Original files up to 50 MiB are stored inside the relational database. CockroachDB supports `BYTES`, but its documentation recommends keeping values under 1 MiB for satisfactory performance. A blob abstraction is needed even if local/BYTES compatibility is used first.

### Medium

14. Topic extraction has no UI action and the topic workspace is orphaned.
15. Generated reviews, plans, coaching, and session summaries are transient; users cannot compare, resume, or audit them.
16. Weakness detection is outcome/status aggregation, not misconception diagnosis.
17. Quiz explanations are generated before the student's response and cannot address the specific wrong reasoning.
18. Frontend OpenAPI contracts are handwritten and can drift.
19. Health probing reads Chroma's internal SQLite schema directly.
20. Broad synchronous handlers perform blocking embeddings and hosted-model calls; concurrent requests can occupy server workers for long periods.
21. `requirements.txt` dependencies are not pinned, reducing reproducibility.
22. `backend/cli.py` duplicates substantial application workflow behavior and is tightly coupled to current persistence.
23. Backend capabilities without complete UI use include topic extraction, session summary, individual quiz attempt detail, and some wrapper functions.
24. No restore path exists for the export archive.

### Security and privacy

- Upload validation is comparatively strong: safe leaf filenames, extension/MIME/signature checks, size cap, duplicate hashing, corrupt/empty/binary rejection, and temporary-file cleanup.
- The app is loopback-oriented, but there is no auth, CSRF strategy, rate limiting, ownership enforcement, encryption policy, retention policy, or audit log.
- Retrieved document text is sent to the configured external provider. This privacy boundary is documented only operationally, not enforced per notebook/document.
- Prompt text instructs the model that retrieved excerpts are data, but uploaded study materials remain an indirect prompt-injection surface.
- Exception handlers return structured generic failures and avoid leaking stack traces in tested paths.
- Export returns all local user data and is unauthenticated; that is acceptable only while loopback-only and single-user assumptions hold.

## 12. CockroachDB Migration Readiness

### Readiness score

**Architecture: 4/10 for direct migration; 7/10 after repository and identity groundwork.**

CockroachDB can hold the relational records, JSON, source bytes, and embeddings, and its vector indexes support cosine distance appropriate for semantic retrieval. The blocker is not basic database capability; it is current application coupling, ownership semantics, transaction design, and transient workflow state.

### Tables to recreate

All 14 current tables must be represented: `documents`, `notebooks`, `notebook_documents`, `cached_intelligence`, `topics`, `topic_sources`, `memories`, `memory_relationships`, `study_sessions`, `study_interactions`, `study_interaction_sources`, `quiz_attempts`, `quiz_question_attempts`, and `quiz_question_sources`.

Recommended new tables: `users`/`tenants` (or at minimum `workspaces`), `document_chunks`, `memory_embeddings` if separate, `pending_quizzes`, `memory_proposals`, `memory_consolidation_proposals`, `learning_signals`/`misconceptions`, `generated_study_plans`, `generated_review_actions`, `generated_coaching_plans`, and a durable `outbox_jobs` table.

### Chroma collections to migrate

1. `study_documents` → `document_chunks` including text, 384-dimensional embedding if the current model is retained, metadata, source location, content/model version, and document/tenant keys.
2. `learner_memories` → `memory_embeddings` or vector columns on `memories`, including status/type metadata and embedding version.

Use CockroachDB cosine vector indexes and prefix columns for tenant/scope filtering where query plans support it. Vector-index creation/backfill on non-empty tables can temporarily block writes according to current CockroachDB documentation; create/backfill indexes deliberately during migration rather than as an afterthought.

### Services that can largely remain after dependency injection

- File type validation and text extraction.
- Chunking policy and metadata construction.
- Pydantic API schemas and error envelope.
- Prompt templates and JSON response validation.
- Pure quiz scorer.
- Pure report/priority/planning calculations, after they receive typed repository results rather than `sqlite3.Row`.
- Most React pages and UI state.
- Provider adapter layer.

### Services requiring interfaces/repositories

- Document/notebook/intelligence persistence.
- Document and memory vector search.
- Ingestion and document deletion unit of work.
- Memory create/update/archive/replace/delete/consolidate unit of work.
- Study sessions/interactions/outcomes.
- Quiz pending state and quiz-attempt persistence.
- Dashboard/report/planner query access.
- Integrity checks and export.

### Queries/functions requiring transaction redesign

- Document ingestion: document + assignment + chunks + embeddings.
- Document deletion: document + assignments/topics + chunk/vector rows.
- `get_or_create_active_study_session`: must preserve one active session per user under serializable contention.
- Atomic interaction + source writes.
- Quiz attempt + questions + sources.
- Memory create/update/archive/replace/delete.
- Consolidation: source archives, target creation, relationships, and embeddings.
- Proposal decisions: optimistic version check plus accepted mutation.

Every multi-statement transaction must be safe to retry on `40001`; callbacks must be idempotent and must not perform an external LLM call inside a retried transaction.

### Tests likely to break

- Every test patching SQLite paths or inspecting `PRAGMA`/WAL.
- Additive SQLite migration tests.
- Assertions on integer `lastrowid` IDs.
- Raw row/SQL expectations in notebook, intelligence, study, and memory tests.
- Export tests expecting a copied `app.db` and Chroma files.
- Integrity tests that compare Chroma and SQLite.
- Concurrency test for the partial unique active-session index.
- Vector fake interfaces if the new repository contract differs.
- Frontend tests/types if IDs change from number to UUID string.

Preserve the existing API contract temporarily with an ID compatibility layer if minimizing frontend churn is more important than immediately exposing UUIDs.

### Most serious migration risks

1. Migrating a flawed product loop faithfully and then having to redesign persisted semantics immediately afterward.
2. Adding CockroachDB before tenant/ownership keys, producing an unsafe shared schema.
3. Treating raw SQL replacement as the migration and missing retryable transaction behavior.
4. Losing or duplicating vectors because SQLite/Chroma reconciliation has not been proven before import.
5. Changing integer IDs without a deterministic legacy-ID mapping for all citation/relationship rows.
6. Importing source BLOBs into CockroachDB without a size/storage strategy.
7. Recreating Chroma search without verifying score direction, threshold, top-k/filter semantics, and topic exact-pair behavior.
8. Multi-worker deployment breaking all process-local registries.

## 13. Recommended Migration Order

1. **Freeze and specify behavior.** Document the real current API contracts, score semantics, citation lineage, and memory rules. Add an explicit test showing the current chat prompt receives memory.
2. **Fix the product data model first.** Define user/workspace ownership, learning signals/misconceptions, proposal evidence, memory versioning, and which workflows must consume memory.
3. **Introduce ports without changing storage.** Add repositories for document/notebook/intelligence, study/quiz, memory, document vectors, and memory vectors; add a unit-of-work interface. Keep SQLite/Chroma adapters initially.
4. **Make current writes reliable.** Fix memory update/delete ordering with reconciliation/outbox semantics and persist pending quiz/proposal state. This gives a trustworthy source data set to migrate.
5. **Choose IDs and compatibility.** Prefer application-generated UUIDv4/ULID-style UUIDs for new CockroachDB keys; CockroachDB recommends UUIDs over sequential `SERIAL` for distributed key distribution ([SERIAL guidance](https://www.cockroachlabs.com/docs/stable/serial)). Store `legacy_sqlite_id` during import.
6. **Create versioned CockroachDB migrations.** Recreate relational tables with `TIMESTAMPTZ`, `BOOL`, JSONB, explicit ownership/FKs/delete policies, checks, and indexes.
7. **Implement CockroachDB repositories and retrying unit of work.** Keep LLM/embedding calls outside retried transactions; use durable jobs/outbox for long external work.
8. **Migrate structured data first.** Snapshot SQLite, transform deterministically, import in FK order, and compare counts/checksums and sampled records.
9. **Migrate document chunks/vectors.** Create `document_chunks`, import text/metadata/embeddings, validate exact scope filtering and nearest-neighbor ordering, then build/enable vector indexes deliberately.
10. **Migrate learner-memory embeddings.** Reconcile every SQL memory against Chroma first, import with model/version, and verify archive/status filtering.
11. **Dual-run read verification.** For representative queries, compare old Chroma top-k/filters with CockroachDB vector results and old SQLite reports with new SQL reports.
12. **Cut over behind configuration.** Use feature flags per repository, observe errors/retries/latency, retain read-only rollback data, then retire SQLite/Chroma only after acceptance.
13. **Rebuild export/integrity.** Export logical tenant data and vector rows; replace filesystem probes with supported database health and consistency queries.

## 14. Files That Must Change

This is an impact map, not authorization to edit them now.

| File/group | Why it must change |
|---|---|
| `backend/rag/config.py` | Add CockroachDB URL/schema/pool/retry/vector configuration; remove service-level reliance on local paths |
| `backend/rag/database.py` | Replace sqlite3 connection, DDL, raw rows, migrations, IDs, and document persistence |
| `backend/rag/notebooks.py` | Replace raw SQLite SQL and row mapping |
| `backend/rag/intelligence_store.py` | Replace SQL, JSON text storage, scope/lineage FKs, and search collation |
| `backend/rag/ingestion.py` | Use repository/unit of work and CockroachDB chunk/vector writes or durable embedding jobs |
| `backend/rag/document_service.py` | Replace Chroma snapshot compensation with SQL transaction/outbox semantics |
| `backend/rag/vector_store.py` | Rewrite Chroma document vector implementation and health probe |
| `backend/rag/rag_service.py` | Inject retrieval interfaces and SQL vector filters; retain answer/prompt logic |
| `backend/memory/database.py` | Replace SQLite memory/relationship persistence and define delete/version semantics |
| `backend/memory/vector_store.py` | Replace Chroma memory vectors |
| `backend/memory/service.py` | Make memory changes atomic/retryable; fix update/delete divergence |
| `backend/memory/proposals.py` | Persist proposal/evidence/expiry/decision/version |
| `backend/memory/consolidation_registry.py` | Persist consolidation workflow and optimistic concurrency |
| `backend/study/database.py` | Replace all study/quiz SQLite DDL/queries/IDs/concurrency handling |
| `backend/study/quiz_api.py` | Persist pending quizzes and connect trusted performance to learning-signal processing |
| `backend/study/quiz_history.py` | Retrying atomic attempt/question/source writes |
| `backend/study/recommendations.py`, `planner.py`, `reviewer.py`, `coach.py` | Read learner memories/learning signals through interfaces and record how adaptation occurred |
| `backend/study/dashboard.py`, reporting modules | Query repositories/views rather than sqlite3 rows |
| `backend/study/integrity.py` | Replace SQLite/Chroma-specific checks |
| `backend/api/export_service.py` | Replace SQLite backup and Chroma file copying with logical CockroachDB export |
| `backend/api/app.py` | Initialize/pool the new database and injected services |
| `backend/cli.py` | Stop direct storage coupling; reuse application services |
| `frontend/src/api/types.ts` and affected pages | Update only if public IDs/contracts change; add topic/memory-adaptation UX |
| `tests/*.py`, frontend contract tests | Replace SQLite fixtures/assumptions; add CockroachDB, retry, vector, and true-loop coverage |
| `requirements.txt`, environment docs, README | Pin driver/migration dependencies and document the new configuration/operational model |

New files will likely be appropriate for repository protocols, SQLite/Chroma adapters, CockroachDB adapters, versioned migrations, durable job/outbox handling, and migration verification tooling.

## 15. Files That Should Not Change Yet

Until repository contracts and the target data model are agreed, avoid broad rewrites of:

- `frontend/src/styles/*`, shared UI components, typography, and layout: the present visual system is coherent and migration does not require a redesign.
- PDF/PPTX/TXT parsing and validation logic in `backend/rag/loaders.py` and the pure chunking rules, except where an injected storage boundary is needed.
- Pydantic request/response schemas and error envelopes unless ID/ownership/version fields require a planned API version.
- Pure quiz scoring and response-redaction logic.
- Prompt templates and provider adapters, except to pass explicit learner-memory context into more workflows after the behavior is specified.
- Existing tests as disposable files. First preserve them as compatibility tests, then add adapter contract tests and only replace assertions that intentionally change.
- SQLite and Chroma data directories. Keep immutable backups until row counts, hashes, relationships, and sampled vector-search results are verified after cutover.

Do not begin with UI redesign, AWS/object storage deployment, or deleting the old stores. Those do not solve the current agent-loop and transaction-boundary risks.

## 16. Final Verdict

Agentbook is credible hackathon software with a broad, testable local product and unusually thoughtful handling of scope, citation lineage, quiz answer trust, empty states, and safe uploads. The deterministic implementation is stronger than the headline gap might suggest.

The headline gap is nevertheless decisive: **the repository does not implement the intended quiz-performance-to-persistent-memory-to-future-multi-workflow loop.** Memories are not merely displayed—they can influence later chat—but they are created from manual/chat paths, never from quiz performance, and they do not influence future quizzes, review, coaching, or study plans. That prevents a “fully agentic study companion” verdict.

Do not directly replace SQLite and the two Chroma stores in the existing functions. First define ownership and learning-signal semantics, introduce repository/unit-of-work interfaces, make transient state durable, and correct cross-store consistency. Then CockroachDB is a good fit for consolidating structured data and both vector stores, with transaction retries, vector-query equivalence tests, and a staged data reconciliation/cutover.

### Concise checklist

#### Working now

- [x] Notebook CRUD, assignment, search, and empty-only delete
- [x] PDF/PPTX/TXT validation, extraction, chunking, and indexing code
- [x] Scoped grounded chat with citation lineage
- [x] Document/notebook summaries and cached/stale handling
- [x] Trusted quiz generation/submission/scoring/result redaction path
- [x] Manual memory CRUD/search and proposal decisions
- [x] Dashboard, system integrity, export, and main progress views
- [x] 71 backend tests (1 skipped), 19 frontend tests, syntax/type/build checks

#### Partially working

- [ ] Topic extraction/summary: backend exists; UI path is incomplete/orphaned
- [ ] Weakness detection: gap aggregation, not misconception diagnosis
- [ ] Learner-memory update/consolidation: normal path exists; cross-store atomicity does not
- [ ] Adaptive review/plan/coaching: real workflows, but transient and not memory-aware
- [ ] Progress reporting: main view connected; detail/summary coverage incomplete
- [ ] Agentic memory: real later-chat retrieval only

#### Missing

- [ ] Quiz outcome → misconception/learning signal
- [ ] Quiz outcome → memory proposal/create/update
- [ ] Memory retrieval in quiz/review/coaching/study plan
- [ ] Observable audit trail proving how memory changed an output
- [ ] Durable pending quiz/proposal/consolidation state
- [ ] Users/authentication/tenant isolation
- [ ] Real provider/embedding/browser/CockroachDB end-to-end test suites
- [ ] Export restore/import workflow

#### Must fix before CockroachDB

- [ ] Define tenant/user ownership and authorization model
- [ ] Define learning-signal, misconception, memory evidence/version/approval semantics
- [ ] Add repository and retrying unit-of-work boundaries
- [ ] Fix memory update/delete and other cross-store failure windows
- [ ] Choose ID migration and legacy mapping strategy
- [ ] Decide original-file storage abstraction and retention/privacy rules
- [ ] Reconcile SQLite with both Chroma stores before importing
- [ ] Persist process-local workflow state or replace it with safe durable tokens

#### Can migrate directly

- [x] Validated document metadata and notebook relationships after type/ID mapping
- [x] Cached summary/topic JSON after schema/version validation
- [x] Study/quiz history and immutable citation snapshots after ownership/FK decisions
- [x] Learner-memory records after reconciliation and versioning
- [x] Existing chunk text/metadata/embeddings after dimension/model checks
- [x] Pure extraction, scoring, validation, prompt, and frontend logic behind interfaces

#### Needs redesign

- [ ] Global sqlite3/Chroma service coupling
- [ ] Process-local quiz and proposal registries
- [ ] Cross-store compensating transactions
- [ ] Export and integrity implementations based on local files/internal Chroma SQLite
- [ ] Autoincrement/global single-user identity model
- [ ] Memory production/consumption loop across all adaptive workflows
- [ ] Provider/embedding jobs, retries, idempotency, and observability for distributed operation
