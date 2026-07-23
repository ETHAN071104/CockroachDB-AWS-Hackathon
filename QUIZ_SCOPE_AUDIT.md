# Quiz Scope Audit

Date: 2026-07-23  
Scope: pre-implementation behavior audit  
Runtime backends reviewed: CockroachDB and SQLite/Chroma compatibility path

## Executive answer

Quiz knowledge is selected by an explicit retrieval scope when one is supplied. With no scope, retrieval is global within the active workspace. Global does **not** mean that every file is sent to the model; it means that the semantic top-k search may select chunks from any indexed document owned by the workspace. Upload recency and newest-document status do not affect ranking.

Every quiz generation also builds an adaptation context from relevant Learning Signals and active Learner Memories. That context can change difficulty, emphasis, distractors, or misconception checks, but it does not change the retrieval filter. A document- or notebook-scoped quiz therefore cannot use a document outside that knowledge scope merely because personalization evidence points elsewhere.

## Knowledge sources versus personalization sources

- **Knowledge source:** retrieved document chunks. These are the only factual context supplied for quiz content and the only records that can be cited.
- **Personalization source:** active Learner Memories and active/improving Learning Signals selected for relevance to the requested topic. They influence how the quiz tests the learner but are not factual citations.
- **Quiz history / weakness evidence:** incorrect and skipped submissions create or update Learning Signals and memory proposals after scoring. Those records can influence later quiz adaptation. Previous quiz attempts are not themselves added to the current document context.

## Entry-point matrix

| Entry point | Effective scope | Knowledge sources | Personalization sources |
|---|---|---|---|
| Document detail: **Study document** | One selected document | Semantic top-k chunks restricted to that document public ID | Relevant active Learner Memories and Learning Signals for the requested topic |
| Notebook detail: **Study notebook** | All current documents assigned to that notebook | Semantic top-k chunks restricted to the resolved notebook document public IDs | Relevant active Learner Memories and Learning Signals; they do not add documents |
| Topic workspace: **Study this topic** | Exact topic source pairs | Only the saved `(document_id, chunk_index)` pairs belonging to the topic | Relevant active Learner Memories and Learning Signals |
| App navigation or Dashboard: **Study actions** | Global | Semantic top-k chunks from any indexed document in the active workspace | Relevant active Learner Memories and Learning Signals |
| Adaptive / weakness-focused quiz | No separate route or request mode exists before this implementation | Same knowledge scope as the document, notebook, topic, or global request that started the quiz | Adaptation is automatically attempted for every quiz; relevant memories/signals can make the effective presentation adaptive |
| **Start another quiz** after results | The current page URL scope is retained | A new retrieval is executed against the same scope; current document/index state is used | Adaptation is rebuilt from current memories/signals, including newly created quiz evidence |
| Cancel then regenerate | The current page URL scope is retained | Same scope, fresh top-k retrieval | Fresh adaptation context |
| No explicit scope | Global | Same as the global row above | Relevant active Learner Memories and Learning Signals |

There is no persisted “retake this exact quiz” or report-level regenerate action. The result-screen action resets the current in-memory quiz and returns to setup without changing the route query parameters.

## Detailed trace

### 1. Specific document

| Stage | Actual behavior before implementation |
|---|---|
| Frontend route | `/documents/:documentId` |
| Initiating component | `DocumentDetailPage` |
| Navigation | `/study-actions?document_ids=<public-id>&scope_name=<filename>` |
| Quiz request | `POST /api/study/actions/quizzes/generate` with `topic`, `question_count`, `document_ids: [id]` |
| Compatibility field | The backend also accepts `scope: {document_ids:[id]}`; the frontend sends top-level fields |
| Scope resolver | `_scope_from_request()` creates `RetrievalScope(document_ids=(id,))`; `resolve_retrieval_scope()` verifies the document exists through the workspace-bound notebook repository |
| Resolved IDs | Exactly the selected integer public ID |
| SQLite/Chroma filter | `{"document_id":{"$in":[id]}}` is applied before similarity search |
| Cockroach filter | `d.public_id = ANY(:document_ids)` plus mandatory `c.workspace_id=:workspace_id` and `c.embedding IS NOT NULL` |
| Fallback | None. Missing documents fail; zero results reject quiz generation |

### 2. Notebook

| Stage | Actual behavior before implementation |
|---|---|
| Frontend route | `/notebooks/:notebookId` |
| Initiating component | `NotebookDetailPage` |
| Navigation | `/study-actions?notebook_id=<public-id>&scope_name=<notebook-name>` |
| Quiz request | `topic`, `question_count`, `notebook_id` |
| Scope resolver | Verifies the workspace-owned notebook, lists its current workspace-owned documents, and resolves their public IDs |
| Vector filter | Same document-ID filter as document scope, containing every current notebook member |
| Empty notebook | Resolves to an explicit empty non-global scope; retrieval returns no sources and never falls back globally |

### 3. Global

| Stage | Actual behavior before implementation |
|---|---|
| Frontend routes | Sidebar `/study-actions`; Dashboard link `/study-actions` |
| Initiating components | `AppShell`, `DashboardPage`, direct route navigation |
| Quiz request | Only `topic` and `question_count`; no `scope`, `document_ids`, `notebook_id`, or `topic_id` |
| Scope resolver | `scope=None` becomes `ResolvedRetrievalScope(kind="global")` |
| Vector filter | No document metadata filter. Cockroach still requires the repository workspace ID; SQLite/Chroma results receive a relational ownership check |
| Selection | Nearest chunks by vector distance, limited by retrieval count; deterministic document ID/chunk index tiebreaking in Cockroach |
| Recency | No `created_at`, upload time, or updated time participates |

### 4. Topic

The topic entry is an existing scoped route even though it is not one of the six requested normalized scope names. `TopicWorkspacePage` navigates with `topic_id`. The resolver loads the workspace-owned topic and produces exact document/chunk pairs. Cockroach generates an OR filter over `d.public_id` and `c.chunk_index`; Chroma receives the equivalent `$or` filter. An empty topic is an explicit empty scope and cannot fall back globally.

### 5. Adaptation and weakness focus

`generate_quiz_for_api()` calls `build_adaptation_context("quiz", topic)` before generation. The adaptation selector:

1. reads active/improving Learning Signals and active Learner Memories from workspace-bound repositories;
2. selects up to five relevant signals and memories using topic relevance (semantic memory retrieval in Cockroach mode, lexical filtering in SQLite mode);
3. derives observable quiz changes such as supportive/challenge difficulty, misconception checks, and targeted-topic emphasis;
4. supplies these changes as a separate `adaptation_instructions` prompt section.

Document retrieval still receives the original `RetrievalScope`. Adaptation has no code path that mutates that scope or supplies additional document IDs. With no relevant memory or signal, the generator receives “No learner-specific adaptation is available” and produces a standard grounded quiz.

### 6. Citations and pending state

The generator retrieves up to 10 chunks and sends at most 6 to the model. Every returned question must reference only available source indexes, and its explanation must contain a visible citation. The generated quiz and complete source snapshots are stored server-side as a durable pending workflow; the browser receives only questions and options. On submission, server-held correct options determine scoring. Feedback maps cited source indexes back to document/chunk lineage. Cockroach persistence resolves the exact workspace/document/chunk UUID before saving citation lineage.

## Workspace enforcement

- Cockroach document/notebook/topic lookup repositories bind every public-ID query to the configured `workspace_id`.
- Cockroach vector search always includes `c.workspace_id=:workspace_id`.
- SQLite repositories also receive the active workspace ID. Legacy Chroma rows are additionally checked against the authoritative relational document repository before being returned.
- A public ID owned only by another workspace is indistinguishable from a missing ID to the quiz scope resolver and cannot be retrieved.

## Explicit answers

### Does global mean all indexed documents?

Yes, as an eligibility boundary: any retrievable chunk from any active-workspace document may participate. The model receives only the highest-ranked retrieved chunks, not the full contents of every document.

### Does latest-uploaded material receive priority?

No. Neither global resolution nor vector ordering uses upload or update recency. New material participates only because it has indexed chunks that may be semantically relevant.

### Can adaptive selection include documents outside the chosen scope?

No. Personalization changes prompt instructions after the knowledge scope is established. It cannot expand document or notebook filters.

### What happens when no relevant document chunk is found?

Generation is rejected before creating a pending quiz. The API returns `422 insufficient_evidence` with the reason “No matching indexed document excerpts were found for this quiz topic.” Before this implementation, the reason does not distinguish no documents, an empty notebook, an unready document, or zero semantic matches.

### Can a quiz be created with an ambiguous or empty scope?

- Backend payloads with multiple top-level selectors, or both a compatibility `scope` object and top-level selectors, are rejected by schema validation.
- A compatibility `scope` object requires exactly one selector.
- Before this implementation, a top-level empty `document_ids: []` is accepted as an explicit empty scope; it returns no sources and does not fall back globally.
- Before this implementation, manually constructed frontend URLs can be ambiguous: the page silently prefers `topic_id`, then `notebook_id`, then valid `document_ids`. Invalid document query values are filtered out and can accidentally produce a global request. This is a frontend correctness gap, not backend retrieval fallback.

## Pre-implementation gaps

1. The API response exposes adaptation metadata but no normalized knowledge-scope metadata.
2. Scope labels in the page header come from untrusted URL display parameters rather than backend-resolved records.
3. Scope is not displayed consistently during setup, generation, active quiz, results, or reset.
4. “Adaptive” is not an explicit route; users cannot tell when normal generation actually applied learner evidence.
5. Empty and invalid scopes collapse into generic 404/422 messages.
6. Ambiguous or malformed frontend query parameters can silently select a different effective scope, including global.
7. Global eligibility, top-k selection, and the lack of recency priority are not explained in the UI.
