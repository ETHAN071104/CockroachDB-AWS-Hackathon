# Structured Error System Implementation Report

Date: 2026-07-23  
Scope: backend error contracts, safe exception classification, frontend parsing and presentation, request correlation, and regression coverage  
Overall result: PASS

## Outcome

Agentbook now has one structured HTTP error contract and one frontend parsing
model for application failures. The main AI-assisted flows no longer reduce
provider, output-format, citation, persistence, or unknown failures to a vague
page-level message. They receive a stable code, a clear title and reason, a
next action, retry guidance, and a support-safe request ID.

The implementation did not add Coaching partial success, JSON repair, model
selection, authentication, AWS, or MCP features. It did not change the
database schema or run a data migration.

## Standard backend envelope

Errors use this response shape:

```json
{
  "error": {
    "code": "AI_PROVIDER_RATE_LIMITED",
    "title": "AI provider rate limit reached",
    "reason": "The configured provider is temporarily refusing additional requests.",
    "next_action": "Wait briefly and try again.",
    "retryable": true,
    "request_id": "32-character-safe-request-id",
    "details": null,
    "message": "Provider rate limited."
  }
}
```

`message` is a temporary safe compatibility alias. `legacy_code` is emitted
only when an older route-specific code was normalized to its canonical form.
Standard HTTP status behavior is retained: for example, validation remains
422, conflicts remain 409, provider rate limiting is 429, unavailable
dependencies are 503, timeouts are 504, invalid provider output is 502, and
unknown failures are 500.

## Stable error catalog

The central catalog implements all 27 required codes.

| Category | Codes |
|---|---|
| Study material and retrieval | `NO_INDEXED_DOCUMENTS`, `SCOPE_NOT_FOUND`, `SCOPE_EMPTY`, `DOCUMENT_NOT_READY`, `NO_RELEVANT_CHUNKS`, `INSUFFICIENT_GROUNDED_EVIDENCE`, `CITATION_VALIDATION_FAILED` |
| Learning history | `NO_LEARNING_HISTORY`, `NO_WEAKNESS_EVIDENCE`, `NO_COACHING_ITEMS`, `NO_STUDY_PLAN_INPUTS` |
| AI provider/output | `AI_PROVIDER_UNAVAILABLE`, `AI_PROVIDER_RATE_LIMITED`, `AI_PROVIDER_TIMEOUT`, `AI_EMPTY_RESPONSE`, `AI_INVALID_JSON`, `AI_SCHEMA_VALIDATION_FAILED`, `AI_REFUSAL`, `AI_CONTEXT_TOO_LARGE` |
| Persistence | `DATABASE_UNAVAILABLE`, `DATABASE_TRANSACTION_RETRY_EXHAUSTED`, `VECTOR_RETRIEVAL_FAILED`, `EMBEDDING_JOB_FAILED`, `WORKSPACE_ACCESS_DENIED` |
| Application | `VALIDATION_ERROR`, `REQUEST_CONFLICT`, `INTERNAL_ERROR` |

`RESOURCE_NOT_FOUND` and `EXPORT_FAILED` are also defined for existing
application-wide behavior.

## Exception classification

The central mapper traverses exception causes and contexts so a safe category
is retained even when a route wraps an underlying provider or parser failure.
It recognizes:

- provider 429, timeout, connection/service failure, rejected settings, and
  context-limit categories;
- empty output, invalid JSON, invalid structured schema, refusal, and citation
  validation;
- database unavailability, exhausted transaction retries, vector retrieval,
  embedding jobs, and workspace access;
- unknown failures, which always become `INTERNAL_ERROR`.

Chat, Quiz, Review, Coaching, Summary, and Topic generation routes use this
classification. The current Coaching failure path therefore returns an
actionable category instead of only “An unexpected server error occurred.”
This phase categorizes and presents the failure; it does not silently switch
models or repair malformed model output.

## Request IDs, logging, and security

- Every HTTP response receives an `X-Request-ID` header.
- Every error body contains the same safe request ID.
- Internal logs correlate request ID, code, HTTP status, method, route,
  exception class, provider status category, and validation stage.
- Exception messages, stack traces, provider payloads, SQL text, private
  document content, embeddings, authorization tokens, API keys, and database
  URLs are not logged by the structured handler.
- Optional details are bounded and recursively sanitized. Credential-shaped
  values and sensitive keys are redacted.
- AI generators no longer attach raw model output to raised exception
  messages.
- Durable vector-outbox failures retain only the exception type and a safe
  synchronization message.

## Frontend implementation

The shared API client now parses the structured envelope into `ApiError` fields
for status, code, title, reason, next action, retryability, request ID,
sanitized details, and legacy code.

Reusable presentation components were added:

- `ErrorNotice`
- `RetryableErrorCard`
- `InlineFieldError`
- `EmptyStateAction`

They display the title, non-duplicated reason, suggested action, a Retry button
only when the error is retryable, optional alternate actions, and an expandable
request ID for support. Sanitized backend details are deliberately not rendered
as user-facing content.

Structured presentation is connected to the highest-value AI action surfaces:

- Study Chat question submission;
- Quiz generation and submission;
- Review generation;
- Study Plan generation;
- Coaching generation;
- document, notebook, and topic summary generation;
- topic-scoped question submission.

Retry reuses the same stored action arguments. The Coaching integration test
confirms that Retry calls the same coaching endpoint and does not change the
request into another feature.

## Compatibility retained

- The frontend accepts the new envelope, the previous `error.message` body,
  legacy `{"detail": "message"}`, and a legacy top-level `message`.
- Existing lowercase route codes are canonicalized and may be exposed as
  `legacy_code` during the transition.
- Existing successful Chat evidence states remain HTTP 200 typed results.
- Empty Study Plan and Coaching outcomes remain HTTP 200 empty-state results;
  they were not converted into failures in this phase.
- Existing successful response bodies are unchanged.
- Lower-risk legacy presentation remains on Memory, library CRUD/upload,
  system health, dashboard load, review-queue load, and proposal-decision
  surfaces. Those paths still use the shared structured parser and safe reason,
  but do not yet render the complete structured error card.

## Principal files

Backend:

- `backend/api/error_catalog.py`
- `backend/api/errors.py`
- `backend/api/schemas.py`
- `backend/api/app.py`
- `backend/api/routes/chat.py`
- `backend/api/routes/quiz.py`
- `backend/api/routes/reports_study.py`
- `backend/api/routes/intelligence.py`
- AI generator modules under `backend/study/` and `backend/memory/`
- `backend/application/vector_outbox.py`

Frontend:

- `frontend/src/api/client.ts`
- `frontend/src/api/types.ts`
- `frontend/src/components/StructuredError.tsx`
- `frontend/src/components/index.ts`
- `frontend/src/styles/patterns.css`
- the integrated Chat, Study Actions, document, notebook, and topic pages

Audit and tests:

- `ERROR_SYSTEM_AUDIT.md`
- `tests/test_structured_errors.py`
- `frontend/src/test/structured-errors.test.tsx`
- updated backend API and frontend integration suites

## Verification

The final verification used local SQLite with an isolated study-data directory.
It did not contact CockroachDB or apply a migration.

| Check | Result |
|---|---|
| Python compileall | PASS |
| Backend test suite | PASS — 113 tests run, 5 conditionally skipped |
| Frontend test suite | PASS — 30 tests across 9 files |
| Frontend production TypeScript/Vite build | PASS — 1,821 modules transformed |
| Structured error catalog and major category tests | PASS |
| Provider rate-limit and Coaching retry integration | PASS |
| Request ID and no-secret/no-stack-trace tests | PASS |
| Credential scan of changed and untracked project files | PASS |
| `git diff --check` | PASS |

