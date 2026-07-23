# Quiz Scope Implementation Report

Date: 2026-07-23  
Scope: Quiz Scope audit, normalization, user-visible source summary, and failure handling  
Result: PASS

## Outcome

Quiz generation now has one backend-neutral, workspace-safe `QuizScope` description. Every successful generation response identifies the effective knowledge boundary, its server-resolved label, the number and integer public IDs of included indexed documents, and whether learner personalization was actually applied.

The Study Actions quiz flow displays a compact source summary during setup, source resolution, the active quiz, results, generation errors, and the return-to-setup flow. Scoring, correct-answer secrecy, citation semantics, pending-quiz durability, Learner Memory rules, CockroachDB schema, and existing navigation remain unchanged.

No migration ran and no CockroachDB record was created, changed, or deleted during this task.

## Actual behavior before

- Document entry restricted vector retrieval to the selected document public ID.
- Notebook entry resolved current notebook members and restricted retrieval to those document public IDs.
- Topic entry restricted retrieval to its saved exact document/chunk pairs.
- No selector meant global eligibility across indexed documents owned by the active workspace; only top-ranked chunks were sent to the model.
- Upload recency and newest-document status did not affect ranking.
- Learner Memories and Learning Signals changed quiz emphasis and difficulty but could not expand the knowledge-source filter.
- The API returned adaptation information but no normalized knowledge-scope metadata.
- The frontend could silently prefer one of several URL selectors or discard malformed document IDs, which could accidentally turn a damaged URL into global retrieval.
- Empty notebooks, unindexed documents, and zero semantic matches collapsed into generic errors.
- Source scope was not kept visible through the quiz lifecycle.

The complete pre-change trace is in `QUIZ_SCOPE_AUDIT.md`.

## Behavior after

| Request | Effective behavior |
|---|---|
| No selector | Global scope across all indexed documents in the current workspace |
| `document_ids=[id]` | One document; the document must exist in the workspace and be indexed |
| Multiple `document_ids` | Only those selected, indexed workspace documents |
| `notebook_id` | Only indexed documents currently assigned to that workspace notebook |
| `topic_id` | Only the topic's saved exact source pairs and their workspace-owned documents |
| Relevant learner evidence | Scope type becomes adaptive while resolved knowledge document IDs remain unchanged |
| No relevant learner evidence | A standard grounded quiz is generated; no failure and no false adaptive label |
| Empty or unready scope | Generation stops before the quiz model call with a specific next action |
| Missing or cross-workspace ID | Returns a safe scope-not-found response; no global fallback |
| Ambiguous or malformed URL | Frontend blocks study requests and asks the user to choose a valid source |

## QuizScope API

`PresentedQuizResponse` preserves all previous fields and adds:

```json
{
  "scope": {
    "type": "document",
    "label": "CHAPTER 2 PROGRAMMING CONCEPTS.pdf",
    "document_count": 1,
    "personalized": false,
    "resolved_document_ids": [1],
    "description": "Questions use only the indexed document.",
    "notebook_name": null,
    "document_name": "CHAPTER 2 PROGRAMMING CONCEPTS.pdf"
  }
}
```

Supported types are `global`, `notebook`, `document`, `documents`, `topic`, and their `adaptive-` variants. Only integer public document IDs are returned; internal UUIDs are not exposed.

Top-level `document_ids=[]` is now rejected by request validation instead of becoming an empty selector. The compatibility `scope` object and existing top-level scope fields remain supported.

## Error behavior

- No indexed documents: `422 no_study_material` with an upload/index action.
- Empty notebook: `422 notebook_has_no_indexed_material` with an add/index action.
- Selected unindexed document: `422 document_not_ready`.
- Topic with no indexed sources: `422 topic_has_no_indexed_material`.
- Existing indexed scope with no relevant retrieved excerpts: `422 insufficient_evidence` with a scope-specific suggestion.
- Missing document, notebook, topic, or workspace mismatch: existing `404 scope_not_found` code with a specific safe message.
- No learner history: standard quiz metadata; personalization is not claimed.
- Learner evidence but no grounded material: the material error wins and no pending quiz is created.

No document or notebook error path falls back to global retrieval.

## UI changes

The existing visual system is reused. A compact Quiz source card now shows:

- effective scope label and type;
- included document count when server-confirmed;
- source-resolution status while generating;
- Standard quiz or Adaptive quiz only after server confirmation;
- a concise personalization explanation;
- the same scope on active questions and scored results.

The setup copy explains global eligibility and selected document/notebook/topic boundaries. Generation errors retain the setup and scope summary and link to the existing Notebooks and Documents page for choosing or uploading material. Ambiguous links display a blocking empty state instead of issuing a global request.

There is no direct quiz scope picker in the current product, so no non-functional Change scope control was invented. The existing document, notebook, and topic entry routes remain the supported selection paths.

## Files changed

### Product code

- `backend/study/quiz_scope.py` — normalized scope model, workspace-aware resolution, descriptions, and empty-scope errors.
- `backend/study/quiz_api.py` — resolves scope before quiz generation and attaches metadata to the presented quiz.
- `backend/api/schemas.py` — compatible response field and non-empty top-level document selector validation.
- `backend/api/routes/quiz.py` — response mapping and specific safe scope errors.
- `frontend/src/api/types.ts` — QuizScope response types.
- `frontend/src/pages/StudyActionsPage.tsx` — strict URL parsing and lifecycle-wide scope summary.
- `frontend/src/styles/patterns.css` — compact responsive scope-card styles using existing tokens.

### Tests and reports

- `tests/test_quiz_scope.py`
- `tests/test_quiz_api.py`
- `tests/test_report_quiz_audit.py`
- `tests/test_agentic_loop.py`
- `tests/test_persistence_foundation.py`
- `tests/test_backend_e2e.py`
- `frontend/src/test/study-flows.integration.test.tsx`
- `QUIZ_SCOPE_AUDIT.md`
- `QUIZ_SCOPE_IMPLEMENTATION_REPORT.md`

The three existing quiz test fixtures now mark their simulated source documents as indexed. The backend end-to-end fixture now initializes application repositories against its own temporary database instead of relying on the developer data directory.

## Verification

Executed against an isolated temporary SQLite data directory so the configured CockroachDB runtime and its records were not touched:

- Python compileall: PASS.
- Focused backend Quiz Scope/API/report tests: 18 tests, PASS.
- Backend regression tests for adjusted fixtures: 11 tests, PASS.
- Complete backend suite: 98 tests run, PASS, 5 environment-gated tests skipped.
- Focused frontend Study Actions integration tests: PASS.
- Complete frontend suite: 7 files, 22 tests, PASS.
- Frontend TypeScript check and Vite production build: PASS.
- `git diff --check`: PASS.
- Credential scan of changed and newly created source/report files: PASS; no credential was recorded.

Manual verification note: behavior was exercised through API and React integration tests covering setup, loading, active, result, reset, adaptive, empty, and ambiguous states. No screenshot artifact was created.

## Compatibility and invariants

- CockroachDB and SQLite repository paths use the same normalized metadata layer.
- CockroachDB workspace filtering and vector query filters were not changed.
- No database schema or migration file changed.
- No quiz scoring, correct-answer, explanation, citation, report, or pending-workflow serialization rule changed.
- Learner evidence remains personalization context, not factual quiz evidence.
- Existing response fields and legacy quiz endpoint aliases remain intact.

## Unresolved issues

- Before generation, global and notebook document counts are shown as pending because the current quiz API has no separate read-only scope-preview endpoint. The authoritative count appears as soon as generation returns. Adding a preview endpoint was intentionally avoided in this focused phase.
- The application still has no direct quiz scope picker. Users change scope through the existing document, notebook, and topic pages.
- The test runner emits an existing Starlette `TestClient` deprecation warning; it does not affect results.
- Live CockroachDB-only tests remain opt-in and were not run because this task explicitly prohibited changing live records. Existing CockroachDB query and persistence code was not modified.

No Chat routing, Coaching reliability, authentication, guest session, MCP, AWS, or unrelated work was started.
