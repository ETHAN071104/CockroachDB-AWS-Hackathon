# Coaching Pipeline Audit

Date: 2026-07-23  
Scope: current Agentbook Coaching request, evidence, generation, validation,
persistence, error, and frontend paths  
Audit status: COMPLETE — behavior was not changed during this audit

## Executive finding

The current Coaching implementation already makes one provider call per
study-plan item, but those calls are executed by one tuple comprehension. Any
exception aborts the comprehension, the route returns one aggregate error, and
all otherwise successful in-memory items are discarded.

Coaching items have no durable IDs and are not persisted. Only the deterministic
study inputs and the final `AdaptationEvent` exist outside the response. There
is therefore no partial-success contract, per-item retry, restart recovery, or
independent item observability.

The repository-root runtime configuration was read without displaying any
credential. The actual configuration is:

- persistence: `cockroach`;
- provider: `groq`;
- general model and effective Coaching fallback:
  `llama-3.3-70b-versatile`;
- `CHAT_MODEL`, `COACHING_MODEL`, `STUDY_PLAN_MODEL`, and
  `JSON_REPAIR_MODEL`: not currently defined;
- provider key: present, never displayed.

This differs from the supplied assumption that Coaching is using a Grok model
through OpenRouter. The current model is a fixed Meta Llama model served by
Groq. It is not `openrouter/free`, an auto/free router, or a `latest` alias.

## End-to-end flow

### 1. Frontend entry

- Route: `/study-actions?view=coaching`, with an optional carried `prompt`.
- Page: `frontend/src/pages/StudyActionsPage.tsx`.
- Initiating component: `CoachingWorkspace`.
- Form component: `PlanForm`.
- The form collects `total_minutes` and `max_items`; it also sends the active
  optional retrieval scope.
- `useAsyncAction` performs one request and stores one aggregate error.
- While the request runs, the entire form shows `Building plan…`.
- A failure is rendered through the shared structured `ErrorNotice`; Retry
  repeats the complete aggregate request.

### 2. API boundary

- Primary endpoint: `POST /api/study/actions/coaching-plan`.
- Compatibility endpoint: `POST /api/study/coaching`.
- Route: `backend/api/routes/reports_study.py::post_coaching`.
- Request model: `CoachingRequest`, which currently inherits
  `StudyPlanRequest`.

Current request body:

```json
{
  "total_minutes": 45,
  "max_items": 4,
  "session_limit": null,
  "attempt_limit": null,
  "scope": null
}
```

The route resolves the optional document/notebook/topic scope, builds one
deterministic adaptive study plan, calls `generate_coaching_plan`, records one
adaptation event after total success, and converts the in-memory result into
`CoachingPlanResponse`.

### 3. Scope and workspace resolution

`backend/rag/scope.py` resolves:

- global;
- notebook;
- explicit document IDs;
- topic source pairs.

All current Cockroach repositories are constructed with the configured
workspace ID. Document vector search adds
`c.workspace_id = :workspace_id`; relational notebook, document, quiz,
LearningSignal, Memory, and workflow repositories are also workspace-bound.
Cockroach mode composes Cockroach adapters and does not construct SQLite or
Chroma adapters.

The planner filters persisted quiz/study source lineage against the resolved
scope before selecting candidates. The item generation retrieval applies the
same resolved scope and then filters by the plan item's recorded source
filenames.

### 4. Quiz and study-history selection

`backend/study/planner.py::build_adaptive_study_plan` builds candidates from:

- completed study-session interactions whose latest outcome is `partial` or
  `confused`;
- completed quiz question outcomes whose latest state is `incorrect` or
  `skipped`.

Study history uses the workspace-bound `study_sessions` repository, then reads
each session's interactions and source lineage. Quiz history uses
`build_quiz_performance_report`, which reads the workspace-bound quiz attempts,
questions, and source lineage.

Candidates are normalized, deduplicated, ranked deterministically, and limited
by `max_items` and available minutes. A plan item contains rank, title,
recommended action, priority, minutes, evidence, source filenames, and public
document IDs.

### 5. LearningSignal and Learner Memory selection

`backend/application/learning_loop.py::build_adaptation_context` currently:

- reads every workspace-owned LearningSignal and retains active/improving
  records with lexical topic overlap;
- reads active workspace-owned Learner Memories;
- in Cockroach mode performs vector Memory retrieval for a non-empty topic;
- selects up to five relevant Signals and up to five Memories;
- formats their statements/content as personalization instructions.

These records influence explanation style and practice strategy. They are not
formatted as document citation sources. The current limits are five each, not
the requested per-item limit of three.

### 6. Document retrieval

`backend/study/coach.py::retrieve_coaching_sources` calls the shared
`retrieve_sources` function with:

- semantic query: the plan-item title;
- retrieval candidates: 10;
- returned sources: at most 6;
- active resolved scope;
- an additional source-filename filter when the plan item has lineage.

In Cockroach mode,
`CockroachDocumentVectorRepository.search` performs a workspace-filtered cosine
query over `document_chunks`, with optional document or exact topic-pair
filters. Returned `RetrievedSource` values contain filename, page/slide,
chunk index, public document ID, distance, and the private chunk text.

The current `RetrievedSource` contract does not expose the Cockroach
`document_chunks.id`, so Coaching cannot persist exact chunk UUID lineage
without extending this internal metadata mapping.

### 7. Prompt and provider request

Prompt builder: `backend/study/coach.py::COACHING_PROMPT`.

Each call contains:

- one plan item;
- its deterministic evidence summaries;
- the aggregate learner-adaptation instructions;
- up to six document excerpts.

The prompt says learner context is data, document excerpts are the only factual
grounding, visible numeric citations such as `[1]` are required, and one JSON
object must be returned.

Current provider request:

- provider: `groq`;
- model: `llama-3.3-70b-versatile`;
- temperature: `0`;
- maximum output tokens: `1500`;
- LangChain provider retries: `2`;
- non-streaming invocation;
- `response_format = {"type": "json_object"}`.

The business service calls `create_chat_model` outside any retryable Cockroach
UnitOfWork callback. Retrieval/persistence operations use their own repository
boundaries; the model invocation is not enclosed in a SQL transaction.

### 8. Parsing and local validation

The response is converted to text, then `extract_json_object` slices from the
first `{` to the last `}`. `PydanticOutputParser` parses
`GroundedCoachingActivity`, whose Pydantic configuration forbids extra fields
and enforces field types, lengths, confidence bounds, and the coaching-mode
literal.

`validate_coaching_activity` additionally requires:

- the correct generation mode;
- every required activity field to be non-empty;
- at least one citation;
- no duplicate source indexes;
- every index to exist in the supplied request sources;
- every declared source index to appear visibly in objective, review,
  practice, or expected-answer text.

The current code does not perform a repair request. Any parse, schema, or
citation exception aborts the whole plan.

### 9. Citation behavior

The model receives short numeric request-local indexes (`1`, `2`, etc.), not
Cockroach UUIDs. The prompt also exposes filenames, page/slide values, and
chunk indexes. It does not expose `document_chunks.id` or workspace UUID.

Returned numeric indexes are checked against the sources supplied to that item,
but the IDs are not the requested `S1`/`S2` form. Valid indexes are converted
back only to response source metadata. Coaching lineage is not persisted.

The current validation therefore prevents an unknown numeric citation from
being displayed as valid, but it does not persist an exact
`document_chunk_id`, and it has no durable cross-workspace lineage record to
verify after restart.

### 10. Persistence and workflow state

Current Coaching generation creates no Coaching record and uses no
`workflow_states` row. After every item succeeds, the route writes one
`AdaptationEvent`.

The existing `workflow_states` table can represent Coaching run and item
lifecycle without a schema migration:

- UUID ID and workspace ownership already exist;
- JSONB payload can hold the run/item contract, safe errors, lineage, generated
  activity, attempts, repair metadata, and observability;
- optimistic `version` updates can prevent concurrent retry completion;
- pending rows can represent pending/generating/retryable-failed items;
- completed and permanently failed rows can use existing terminal row statuses;
- `updated_at` and `expires_at` support explicit interrupted-item
  reconciliation.

Provider calls can remain outside all repository update transactions. A
per-item transition can be committed, followed by the provider call, followed
by a separate optimistic persistence update.

No permanent CockroachDB schema change is currently justified.

### 11. Error mapping and frontend failure behavior

The route maps scope/validation failures and passes unexpected generation
exceptions through the structured error mapper. Provider rate limits,
timeouts, unavailability, empty output, invalid JSON/schema, citation failure,
database failures, and unknown errors can therefore become safe error codes
with request IDs.

However, the error is currently aggregate. The frontend cannot receive a
successful sibling item alongside the failure. Its Retry button repeats
`POST /api/study/actions/coaching-plan`, regenerating every item.

## Native structured-output capability finding

The current code uses native JSON Object Mode, not native JSON Schema
Structured Outputs.

The installed provider path can syntactically send a `response_format`
parameter. OpenRouter supports strict `json_schema` and can require routed
providers to honor it through `provider.require_parameters = true`.

The exact current Groq model is the capability blocker:

- Groq's current documentation lists strict JSON Schema mode only for
  `openai/gpt-oss-20b` and `openai/gpt-oss-120b`;
- `llama-3.3-70b-versatile` is documented with JSON Object Mode;
- Groq states that other models must use JSON Object Mode and may not match a
  supplied schema.

Sources:

- https://console.groq.com/docs/structured-outputs
- https://console.groq.com/docs/model/llama-3.3-70b-versatile
- https://openrouter.ai/docs/guides/features/structured-outputs
- https://openrouter.ai/docs/guides/routing/provider-selection

Sending `strict: true` to the current model would not be a supported
configuration. Silently retaining prompt-only/JSON-object behavior would
violate this phase's strict structured-output requirement. Automatically
switching Provider or model would violate the explicit no-automatic-model-
change constraint.

## Explicit answers

1. **Are all Coaching items currently generated in one model request?**  
   No. Each item already receives an independent provider request, executed
   sequentially inside one tuple comprehension.

2. **Does one item failure fail the complete request?**  
   Yes. Any item exception aborts the comprehension and the route returns one
   aggregate error.

3. **Are Coaching items persisted independently?**  
   No. Coaching items are response-only objects.

4. **Can a failed item survive application restart?**  
   No.

5. **Can one item be retried without regenerating other items?**  
   No.

6. **Is native structured output currently used?**  
   Only JSON Object Mode. Strict JSON Schema Structured Outputs are not used.

7. **Is the response only requested through prompt text?**  
   No. The prompt requests JSON and Groq JSON Object Mode is also sent, but no
   strict schema is supplied to the provider.

8. **Are citation IDs validated against supplied evidence?**  
   Yes, for the current numeric request-local indexes. They are checked for
   existence, uniqueness, and visible use, but exact chunk lineage is not
   persisted.

9. **Does the model see database UUIDs?**  
   No. It sees plan evidence public integer references, filenames,
   page/slide/chunk positions, and request-local numeric source indexes.

10. **Are provider calls inside or outside retryable SQL transactions?**  
    Outside.

11. **Which exact Grok model ID is currently configured?**  
    No Grok model is configured. The exact effective Coaching fallback is
    Groq's fixed `llama-3.3-70b-versatile`.

12. **Is that model ID fixed, or an auto/free/latest alias?**  
    Fixed. It is not an auto/free/latest alias.

13. **Does the current provider path support
    `response_format/json_schema`?**  
    The adapter can send the parameter, but the exact configured model does
    not support strict JSON Schema mode. It supports JSON Object Mode.

14. **Does current frontend code support partial item states?**  
    No. It supports only one aggregate pending/error state and completed or
    model-rejected activity rendering.

## Required decision before behavior changes

The strict structured-output requirement and the no-model-change requirement
cannot both be satisfied with the verified current configuration.

The safe implementation path requires one of these externally authorized
states:

1. keep Groq but configure a fixed strict-capable model explicitly as
   `COACHING_MODEL`; or
2. configure the already intended fixed Grok model through OpenRouter and
   confirm that its model metadata includes `structured_outputs`; or
3. explicitly relax this phase to Groq JSON Object Mode plus local validation
   and one repair attempt.

No provider/model change was made by this audit.
