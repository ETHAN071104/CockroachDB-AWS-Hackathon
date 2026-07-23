# Structured Error System Audit

Date: 2026-07-23  
Scope: Agentbook HTTP API, AI generation paths, persistence/retrieval boundaries, and React error presentation

## Executive summary

Agentbook already wraps many route failures in an `{"error": ...}` object and hides stack traces from HTTP clients. The current object, however, contains only a route-specific code, one message, and optional details. It does not consistently explain the reason, next action, retryability, or request identifier.

The most visible gap is AI generation. Quiz, review, coaching, summary, and topic routes frequently catch a broad `RuntimeError` and replace it with one message such as “Grounded coaching generation failed.” The underlying exception chain often still contains useful categories such as provider rate limiting, timeout, invalid JSON, schema validation, or citation validation, but no safe classifier turns those categories into user guidance.

The generic FastAPI exception handler safely hides the exception text and stack trace, but returns the same 500 response for provider, vector, database, and unknown application failures. The frontend then renders `error.message` in a generic `Notice` or `ErrorState`, so it cannot decide when to show Retry, what alternate action to offer, or how to expose a support-safe request ID.

## Current backend envelope

Source: `backend/api/errors.py` and `backend/api/schemas.py`

Current shape:

```json
{
  "error": {
    "code": "coaching_generation_failed",
    "message": "Grounded coaching generation failed.",
    "details": null
  }
}
```

Current behavior:

- `ApiError` preserves an explicit HTTP status, code, message, and optional details.
- request validation returns 422 with sanitized field/type details and excludes submitted values.
- Starlette 404 errors return a generic safe not-found response.
- unknown exceptions return 500 without exception text or stack trace.
- unknown exceptions are logged only by method, route, and exception class.
- no request ID connects the browser response to the internal log record.
- route codes use mixed lowercase names and do not share a central definition.

## Error-source inventory

| Source | Current HTTP status | Current displayed message | Retryable now? | Useful hidden detail | Leakage assessment |
|---|---:|---|---|---|---|
| FastAPI request validation | 422 | “Request validation failed.” | Usually no | field and validation type | Low; submitted input is already removed |
| Starlette 404 | 404 | “The requested resource was not found.” | No | requested route | Low |
| Unhandled API exception | 500 | “An unexpected server error occurred.” | Unknown | exception class in log; causal chain in memory | HTTP-safe, but not actionable |
| Chat scope validation | 404/422 | scope not found or request message | Usually no | selected entity type | Low |
| Chat model/provider call | 500 when uncaught | unexpected server error | Often yes | provider exception/status in cause | HTTP-safe, category lost |
| Chat grounding validation | 200 evidence state | safe evidence-specific answer | Sometimes | evidence status | Deliberately a successful Chat contract, not an HTTP error |
| Quiz generation | 422/502 | insufficient evidence or generic generation failure | Depends | provider/parser/citation cause | Generic 502 hides category |
| Summary/topic generation | 409/422/502 | cache-safe generic failure | Depends | provider/parser/citation cause | Generic 502 hides category |
| Review generation | 404/502 | generic grounded review failure | Depends | provider/parser/citation cause | Generic 502 hides category |
| Coaching generation | 404/422/502/500 | generic coaching failure or unexpected error | Depends | provider/parser/citation cause | Generic response; current user-visible incident |
| Study Plan validation | 404/422 | generic invalid request/scope | Usually no | validation cause | Low |
| Empty Study Plan/Coaching input | 200 empty result | empty-state copy | No | scanned history counts | Not an HTTP failure by existing contract |
| SQLite/Cockroach repository failure | usually 500 | unexpected server error | Often yes | driver class/SQLSTATE | SQL text is not returned, but no stable category |
| Cockroach retry exhaustion | usually 500 | unexpected server error | Yes | retry/SQLSTATE evidence | No dedicated client guidance |
| Vector retrieval | usually 500 | unexpected server error | Often yes | vector adapter exception | No document text returned, category lost |
| Embedding/outbox job failure | workflow-specific | generic failure or durable failed job | Often yes | job ID, attempt count | persisted error strings may be broader than needed |
| Export creation | 500 | export could not be created | Often yes | safe cause class in log | HTTP-safe, no request ID |
| Memory/consolidation operations | 400/404/409/500 | route-specific message | Depends | service exception | some legacy route codes remain |

## Provider and structured-output failures

The shared model factory supports OpenRouter, Groq, and OpenAI-compatible providers. Provider libraries can expose HTTP status and exception categories, but routes do not map them.

Current distinguishable internal conditions include:

- connection/service unavailable
- 429 rate limiting
- timeout
- authentication/configuration rejection
- context length or payload too large
- empty model response
- missing JSON object
- JSON/Pydantic parser failure
- structured schema invariant failure
- refusal/content policy response
- citation index or visible-citation failure

Several generators currently include `Raw output:` followed by the complete model response inside raised exception messages:

- grounded quiz
- grounded review
- grounded coaching
- session summary
- memory extraction
- memory conflict detection
- memory consolidation

Those strings are not currently returned by the central HTTP handler, but retaining complete model output in production exception objects creates an unnecessary leakage risk if later logging or tracing captures exception messages.

## Frontend inventory

Source: `frontend/src/api/client.ts`, shared state/notice components, and page-level async handlers.

Current behavior:

- `ApiError` stores only status, code, message, and details.
- the client parses the current `error.message` shape.
- non-JSON responses fall back to status text or “Request failed.”
- network failures are reduced to `network_error`.
- `useAsyncAction` already retains the last arguments and exposes a working `retry()` method.
- most pages render `errorMessage(error)` inside `Notice`.
- `ErrorState` defaults to “Something went wrong” and can render a retry callback, but it receives only a message.
- there is no reusable presentation for title, reason, next action, retryability, and request ID.
- there is no application toast system.
- there is no React error boundary for render-time component failures.

High-value integration points:

- Study Chat submission
- topic-scoped Chat
- Quiz generation/submission
- Review generation
- Study Plan generation
- Coaching generation
- summary/topic extraction
- upload and Memory mutations

The current Coaching form uses `useAsyncAction`, so retry arguments are already available. It only needs a structured error parser and a reusable error card to expose that capability safely.

## Compatibility findings

- Existing frontend code expects `error.message`.
- Some external or legacy FastAPI paths may still return `{"detail": "message"}`.
- Existing tests and possible clients may rely on lowercase route-specific codes.
- Successful empty Study Plan and Coaching responses are part of the current API behavior and should not be converted into HTTP errors in this phase.
- Chat evidence states are successful, typed response states and should not be replaced with HTTP errors.

The transition should therefore:

1. add the full structured fields;
2. retain a safe `message` alias;
3. allow a `legacy_code` when a lowercase code is canonicalized;
4. make the frontend understand both the new envelope and legacy `detail`;
5. preserve existing HTTP status semantics;
6. document remaining legacy endpoints rather than changing every domain contract at once.

## Required implementation corrections

- Generate a safe request ID for every HTTP request and return it in the error body and response header.
- Define all required stable uppercase error codes in one catalog.
- Map provider, parsing, schema, citation, database, vector, and unknown exceptions centrally.
- Traverse exception causes so route wrappers do not erase provider categories.
- Log request ID, stable code, safe category/stage, status, and exception class without logging exception messages or payloads.
- remove raw model output from exception messages.
- extend the frontend `ApiError` and response parser with title, reason, next action, retryability, request ID, and legacy compatibility.
- add reusable structured error presentations and connect them to the main AI action surfaces.
- retain existing successful response shapes and HTTP statuses.
