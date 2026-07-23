# AI Feature Routing Report

Date: 2026-07-23  
Scope: Study Chat, Coaching, and Study Plan responsibility boundaries  
Overall result: PASS

## Outcome

Agentbook now states the responsibility of each study feature and routes misplaced Study Chat requests without pretending that document retrieval is learner-performance analysis.

- Study Chat remains document-grounded question answering.
- Coaching remains the performance-evidence workflow for deciding what to review.
- Study Plan remains the deterministic workflow for ordering work and allocating time.
- Existing Chat, Coaching, and Study Plan endpoints remain available.
- No database schema, migration, persistence backend, Learner Memory meaning, or Learning Signal meaning was changed.

The pre-implementation trace and mismatch analysis is recorded in `AI_FEATURE_RESPONSIBILITY_AUDIT.md`.

## Backend changes

### Deterministic intent routing

`backend/rag/chat_intent.py` classifies only the current submitted Chat text. It does not inspect documents, Memories, Signals, quiz records, session history, or hidden model state.

Supported intents:

- `document_question`
- `weakness_analysis`
- `coaching_request`
- `study_plan_request`
- `unsupported_or_ambiguous`

English and Chinese phrase rules cover the requested weakness, quiz-performance, review-priority, study-plan, schedule, and time-order examples. No model fallback was added, so classification adds no model latency or new availability dependency. Planning rules take precedence when a prompt explicitly requests a plan or schedule. Ambiguous text remains in Chat instead of being redirected speculatively.

### Compatible Chat response extension

`POST /api/chat` retains its existing fields and adds:

- `type`: `answer` or `feature_redirect`
- `intent`
- `evidence_status`
- `redirect`
- `suggested_question`

A redirect preserves `answer`, `session_id`, `interaction_id`, `sources`, and `memory_proposal`, so existing clients can still read the response. The structured redirect contains the target, user-facing copy, action label, original submitted prompt, and an optional suggested prompt.

Weakness and review-priority requests return a Coaching suggestion. Explicit planning and scheduling requests return a Study Plan suggestion. These paths do not call document RAG, do not claim to analyze performance, and do not create a Learner Memory proposal from the redirect text. The original prompt is persisted and returned as data, not interpolated into trusted HTML or backend log content.

### Evidence distinctions and citation checks

Study Chat now exposes distinct evidence states for:

1. `no_documents_indexed`
2. `no_relevant_chunks`
3. `retrieved_chunks_insufficient`
4. `personal_performance_request`
5. `citation_validation_failed`
6. `unsupported_claims`

Successful document answers use `grounded`. The Chat prompt now requires a visible citation in every factual paragraph. The service checks that an answer contains source markers, that every marker references an available retrieved source, and that substantial factual paragraphs are not left without a visible citation. Failed checks return a safe explanation instead of presenting the model text as grounded.

This is a structural citation guard, not a semantic entailment engine. It prevents missing, invalid, and visibly uncited source claims, but it does not prove that every cited sentence is logically entailed by the excerpt.

Learner Memory remains a separate, non-citable personalization section in the RAG prompt. It is not formatted or returned as document evidence.

## Frontend changes

The UI now shows concise responsibility copy:

- Study Chat: asks questions about uploaded study materials and points weakness analysis to Coaching.
- Coaching: identifies review needs using quiz mistakes, Learning Signals, and Learner Memories.
- Study Plan: organizes what to learn next, in what order, and how much time to spend.

Study Chat keeps the active retrieval scope visible. Returned chunks are labelled `cited` only for a validated grounded answer; otherwise they are labelled `retrieved`. A retrieved-source count is no longer presented as proof that the chunks answer the question.

Structured redirects render a dedicated CTA card. Navigation occurs only after the user clicks `Open Coaching` or `Create Study Plan`. The target URL safely encodes the original prompt. The target screen displays that text as a carried question, but does not automatically submit it or treat it as performance evidence.

The same redirect rendering is available from the topic-scoped Chat surface.

## Compatibility and boundaries

- `POST /api/chat` remains the Chat endpoint and retains its original response fields.
- `POST /api/study/actions/coaching-plan` and `/api/study/coaching` remain unchanged.
- `POST /api/study/actions/plan` and `/api/study/plan` remain unchanged.
- Coaching and Study Plan were not merged into Chat.
- Ordinary document questions and ambiguous prompts remain in Chat.
- Existing grounded citation cards and outcome controls remain available for normal Chat answers.
- Redirect responses do not show answer-rating or Memory-proposal controls.
- No automatic navigation was introduced.
- No authentication, AWS, MCP, structured error-system, or Coaching retry/model work was started.
- No Alembic revision or data migration was created or executed.

## Files changed for this phase

Backend:

- `backend/rag/chat_intent.py`
- `backend/rag/chat_service.py`
- `backend/rag/rag_service.py`
- `backend/api/schemas.py`
- `backend/api/routes/chat.py`

Frontend:

- `frontend/src/api/types.ts`
- `frontend/src/components/FeatureRedirectCard.tsx`
- `frontend/src/components/index.ts`
- `frontend/src/pages/ChatPage.tsx`
- `frontend/src/pages/TopicWorkspacePage.tsx`
- `frontend/src/pages/StudyActionsPage.tsx`
- `frontend/src/styles/patterns.css`

Tests and reports:

- `tests/test_chat_feature_routing.py`
- `tests/test_api_study_memory.py`
- `frontend/src/test/feature-routing.integration.test.tsx`
- `AI_FEATURE_RESPONSIBILITY_AUDIT.md`
- `AI_FEATURE_ROUTING_REPORT.md`

Some listed shared files already contained uncommitted Quiz Scope work before this phase; those earlier changes were preserved.

## Test coverage added

- Ordinary document questions remain in Chat.
- Weakness questions route to Coaching.
- Coaching-priority questions route to Coaching.
- Planning and scheduling questions route to Study Plan.
- Ambiguous prompts do not redirect.
- English and Chinese deterministic examples classify correctly.
- Redirects do not call document RAG.
- Original prompts, including markup-like text, are preserved as inert data.
- Learner Memory is not formatted as document evidence.
- All evidence-status validation branches are distinct.
- An unindexed scope is detected without calling RAG.
- Feature descriptions and active Chat scope render.
- CTA navigation requires a click and reaches the correct tab.
- Carried prompt text is rendered without creating an HTML element from it.
- Existing grounded Chat citations still render as cited sources.
- Current Coaching and Study Plan endpoint shapes remain compatible.

## Verification results

All verification used `PERSISTENCE_BACKEND=sqlite` and an isolated temporary study-data directory. The temporary directory was removed after the run.

| Check | Result |
|---|---|
| Python `compileall` for `backend` and `tests` | PASS |
| Complete backend test suite | PASS - 106 tests, 5 skipped |
| Complete frontend test suite | PASS - 25 tests in 8 files |
| Frontend TypeScript and production build | PASS - 1,820 modules transformed |
| Credential scan of changed and untracked text files | PASS - no credential value printed |
| `git diff --check` | PASS |

The backend run emitted the existing Starlette `httpx` deprecation warning. It did not cause a test failure. No live CockroachDB object or production data was read or modified during this verification.

## Remaining limitations

- The classifier is intentionally phrase-based. New wording may remain in Chat until a rule is added; it will not silently invoke another model.
- Unsupported-claim detection is structural and citation-marker based, not a semantic source-entailment verifier.
- The carried prompt is informational on Coaching and Study Plan because those features use structured constraints and stored evidence rather than a free-text prompt contract.
