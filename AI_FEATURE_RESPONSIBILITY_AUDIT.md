# AI Feature Responsibility Audit

Date: 2026-07-23  
Scope: pre-implementation audit of Study Chat, Coaching, and Study Plan

## Executive summary

The three features already have different backend responsibilities, but the frontend does not explain those boundaries clearly and Study Chat does not detect requests that require learning-history evidence.

- **Study Chat** is document RAG. It retrieves document chunks inside the selected scope, optionally retrieves up to three relevant Learner Memories for explanation style, and calls a chat model. It does not retrieve Learning Signals, quiz performance, or previous session outcomes when answering.
- **Coaching** first builds the deterministic Study Plan from unresolved study outcomes and quiz gaps, augments prioritization using Learning Signals and Learner Memories, retrieves relevant document chunks, and then asks a model to produce cited review/practice/reassessment activities.
- **Study Plan** is deterministic. It ranks unresolved completed-session outcomes and incorrect/skipped quiz questions, allocates time, and optionally adjusts priority and duration when relevant Learning Signals or Learner Memories exist. It does not call a planning model and does not perform vector document retrieval.

Knowledge evidence and personalization evidence are already kept separate in prompts: document chunks are factual sources and Learner Memory is explicitly non-citable. The primary mismatch is routing. Personal-performance questions entered in Study Chat are currently sent to document retrieval and usually end as a generic insufficient-information answer.

## Responsibility matrix

| Feature | Intended decision | Actual primary evidence | Model use | Citations |
|---|---|---|---|---|
| Study Chat | Answer a question about uploaded material | Retrieved document chunks in global/notebook/document/topic scope | One document-grounded answer | Requested by prompt, but not programmatically validated before this phase |
| Coaching | Decide what to review and generate a grounded activity | Completed-session gaps, incorrect/skipped quiz questions, Learning Signals, Learner Memories, then document chunks | One structured activity per deterministic plan item | Required and programmatically validated |
| Study Plan | Order study work and allocate time | Completed-session gaps, incorrect/skipped quiz questions, scope-filtered stored source lineage, Learning Signals, Learner Memories | None; deterministic ranking/allocation | Not applicable; exposes evidence references and source filenames/IDs rather than prose citations |

## Study Chat

### Endpoint and request

- Endpoint: `POST /api/chat`.
- Request schema: `ChatRequest` with `question` and at most one of `notebook_id`, `document_ids`, or `topic_id`.
- Scope conversion: `backend.api.routes.chat._scope_from_chat_request()` creates `RetrievalScope`; no selector means global.
- Compatibility: integer public document/notebook IDs and the current topic UUID request field are preserved.

### Service and persistence

- Service: `backend.rag.chat_service.run_chat()`.
- The service validates the selected scope before creating session state.
- It obtains or creates an active study session, calls `rag_service.answer_question()`, and atomically stores the question, answer, and retrieved source lineage as an `unrated` interaction.
- After the grounded interaction commits, it may create a pending Learner Memory proposal from the user message and assistant answer. Proposal failure is optional and does not roll back the chat interaction.

### Prompt builder

- Prompt: `backend.rag.rag_service.RAG_PROMPT`.
- It contains two explicitly separated sections: Learner Memory and Document excerpts.
- It instructs the model to use Memory only for style, depth, examples, or emphasis; Memory cannot be factual evidence or a citation.
- It instructs the model to use only document excerpts for factual content and to return one exact insufficient-information sentence when evidence is inadequate.

### Data-source trace

| Source | Actual behavior |
|---|---|
| Document retrieval | `retrieve_sources()` resolves scope and performs semantic top-k vector search. Explicit empty scopes return no chunks and never fall back globally. Cockroach repositories retain workspace filtering. |
| Learner Memory | `search_memories(question, k=3)` runs only after document chunks were found. Failure is tolerated. Results enter a non-citable prompt section. |
| Learning Signals | Not queried for a Chat answer. |
| Quiz history | Not queried for a Chat answer. |
| Session history | No prior conversation history or completed-session analysis is supplied to the answer model. The current answer is only persisted afterward. |

### Citation requirements

- The prompt asks for visible `[1]`, `[2]` citations and tells the model not to cite unsupported claims.
- Before this phase, Chat does not parse or validate citation indexes and does not verify that an otherwise fluent answer contains a visible citation.
- The API returns all retrieved sources, not only sources actually cited in the answer.
- The frontend labels the returned count as “cited sources,” so a count such as five proves only that five chunks were retrieved, not that those chunks answer the question.

### Output schema

`ChatResponse` contains `session_id`, `interaction_id`, plain `answer`, `sources`, and an optional `memory_proposal`. It has no response type, detected intent, evidence state, or feature-routing metadata before this phase.

### Failure behavior

- No retrieved chunks: returns `I could not find sufficient information in the indexed files.` with an empty source list.
- Retrieved chunks but insufficient: the prompt asks the model to return the same sentence, making this indistinguishable from the no-chunk case to the frontend.
- Missing selected scope: `404 retrieval_scope_not_found`.
- Invalid request: `422 invalid_chat_request`.
- Empty/model failures outside these handled cases can surface as server errors.
- Citation validation failure and unsupported claims have no distinct state before this phase.

### Current frontend explanation

- Page heading: “Ask questions against your local sources. Every answer keeps its citation lineage.”
- The sidebar shows a real scope selector and a visible active source label.
- It explains that scope is applied before retrieval and empty scopes do not fall back.
- It does not say that weakness analysis belongs in Coaching.
- It renders the number of returned retrieval chunks as “cited sources,” even if the answer did not visibly cite them.

### Mismatch with intended responsibility

Study Chat has no performance-history inputs, yet it accepts questions such as “What are my weaknesses?” and sends them to document RAG. It cannot truthfully analyze the learner’s complete performance. Learner Memory may contain difficulty summaries, but the prompt correctly prevents Memory from becoming factual document evidence; Memory alone is also not a complete weakness assessment.

## Coaching

### Endpoint and request

- Endpoint: `POST /api/study/actions/coaching-plan`.
- Compatibility alias: `POST /api/study/coaching`.
- Request: available minutes, maximum items, optional session/attempt limits, and optional nested retrieval scope.

### Services and prompt builder

1. `build_adaptive_study_plan()` deterministically produces prioritized plan items.
2. `generate_coaching_plan()` builds a coaching adaptation context and calls `generate_coaching_item()` per plan item.
3. Prompt: `backend.study.coach.COACHING_PROMPT`.

The prompt receives a deterministic plan item, its stored performance evidence, learner-specific adaptation instructions, and retrieved document excerpts. It requires a Review → Practice → Reassess sequence and forbids outside knowledge.

### Data-source trace

| Source | Actual behavior |
|---|---|
| Document retrieval | Semantic retrieval uses each plan-item title, the requested scope, and then the item’s recorded source filenames. At most six chunks reach the coaching model. |
| Learner Memory | `build_adaptation_context("study_plan", …)` may affect plan priority/time, and `build_adaptation_context("coaching", …)` supplies coaching prompt instructions. Memory remains non-citable personalization. |
| Learning Signals | Read by both adaptation contexts when active/improving and relevant. They can change priority, difficulty, focus, or practice style but do not become factual citations. |
| Quiz history | `build_quiz_performance_report()` contributes incorrect and skipped questions, repetition counts, stored explanations, and stored source lineage. |
| Session history | `build_review_queue()` scans completed sessions and contributes `partial` and `confused` interactions with their source lineage. |

### Citation requirements

- Successful activities require at least one available source index.
- Every listed source index must appear visibly in the generated objective/review/practice/answer content.
- Invalid, duplicate, unavailable, or invisible citations raise a runtime generation error.
- Expected answers must be grounded in document excerpts.

### Output schema

`CoachingPlanResponse` contains the deterministic plan, generated/rejected counts, structured activity items, their cited sources, and adaptation metadata. Each activity exposes objective, review, practice, reassessment, expected answer, completion criteria, confidence, and reason.

### Failure behavior

- Missing scope: `404 scope_not_found`.
- Invalid request/scope: `422 invalid_scope`.
- Model/parser/citation validation failures: `502 coaching_generation_failed`.
- No matching chunks for an item: that item is returned as a structured rejected activity rather than silently using global material.
- No plan evidence: successful response with zero coaching items; no model call for activities.

### Current frontend explanation

The Coaching tab says it first builds a deterministic plan and generates cited practice when plan items exist. It does not explicitly name quiz mistakes, Learning Signals, and Learner Memories as the reason this is the correct place for weakness-based requests.

### Mismatch with intended responsibility

The backend largely matches the intended responsibility. The frontend boundary is too implicit. Also, Coaching uses relevant completed-session gaps rather than every completed interaction, and factual activity content still requires matching indexed chunks even when strong personalization evidence exists.

## Study Plan

### Endpoint and request

- Endpoint: `POST /api/study/actions/plan`.
- Compatibility alias: `POST /api/study/plan`.
- Request: total minutes, maximum items, optional session/attempt limits, and optional nested retrieval scope.

### Service and prompt builder

- Service: `backend.study.planner.build_adaptive_study_plan()`.
- There is no Study Plan model prompt. Candidate construction, deduplication, ranking, time allocation, and truncation are deterministic.

### Data-source trace

| Source | Actual behavior |
|---|---|
| Document retrieval | No vector retrieval. Requested scope filters recorded source lineage attached to candidate session/quiz evidence. |
| Learner Memory | Relevant active Memory is selected by `build_adaptation_context("study_plan", …)` and can increase candidate priority and desired minutes. It does not create a plan item without an underlying study/quiz candidate. |
| Learning Signals | Relevant active/improving signals enter the same adaptation context and can increase priority/time. |
| Quiz history | Incorrect and skipped questions are grouped by normalized question, scored by status and repetition, and retain attempt evidence/source lineage. |
| Session history | Partial and confused outcomes from completed sessions become candidates through the review queue. |

### Citation requirements

No generated factual prose requires citations because the planner is deterministic. Each item exposes evidence type/status/reference ID plus source filenames and public document IDs. Scope filtering is applied to stored document/chunk lineage before candidates are selected.

### Output schema

`StudyPlanResponse` exposes requested/allocated/remaining minutes, item count, scanned session/interaction/attempt counts, ranked items, evidence, source filenames/public IDs, and adaptation metadata.

### Failure behavior

- Missing scope: `404 scope_not_found`.
- Invalid constraints: `422 invalid_study_plan`.
- No unresolved evidence: successful empty plan.
- It never invents a generic plan from documents alone.

### Current frontend explanation

The tab calls itself “Adaptive study plan” and says it is deterministic from stored outcomes and quiz gaps. It does not concisely explain ordering, time allocation, available material, and history.

### Mismatch with intended responsibility

The actual planner matches ordering and time allocation. “Available material” means material connected to recorded gap evidence; the planner does not inventory every indexed document to create new study topics. Learning Signals and Memory adjust existing candidates rather than replace performance evidence.

## Cross-feature boundary findings

1. Study Chat is the only place with an interactive free-text question box, so users naturally ask performance and planning questions there even though its backend lacks those inputs.
2. Chat’s retrieved-source count is mislabeled as a citation guarantee.
3. Coaching is the correct destination for weakness/review-priority questions because it combines performance evidence with grounded source retrieval.
4. Study Plan is the correct destination for explicit ordering, scheduling, and time-allocation requests.
5. Neither Coaching nor Study Plan should be merged into Chat: their inputs, deterministic ranking, and output contracts are materially different.
6. Learner Memory cannot substitute for document chunks in Chat or Coaching and cannot create a Study Plan item without recorded gap evidence.
7. The current API has no structured route suggestion, so the frontend cannot offer a safe user-controlled CTA while preserving the original prompt.

## Required implementation corrections

- Classify only the current submitted Chat text using local deterministic rules.
- Return a compatible structured `feature_redirect` for performance/coaching/planning intent without running document RAG or claiming an answer.
- Persist the original prompt safely as the user’s interaction, but do not create a Learner Memory proposal from a redirect.
- Keep ordinary and ambiguous document questions in Chat.
- Add explicit evidence states and programmatic Chat citation checks without exposing Memory as factual support.
- Render a user-controlled CTA; never navigate automatically.
- Explain each feature’s responsibility with concise secondary copy and retain visible active Chat scope.
