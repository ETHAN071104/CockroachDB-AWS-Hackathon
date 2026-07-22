# Agentbook Agentic Learning Loop Report

## 1. Final local agentic-memory flow

The local flow is now:

1. Study material is uploaded and indexed through the existing SQLite and Chroma adapters.
2. Quiz generation retrieves grounded document excerpts and reads relevant active learner memories and current learning signals.
3. The server keeps correct answers and explanations in the durable pending-quiz workflow until submission.
4. Quiz submission uses the trusted server quiz to score answers and atomically persists the attempt, every question outcome, citation lineage, deterministic learning signals, memory proposals, and an optional enrichment job.
5. A learning signal remains evidence, not confirmed memory. The learner must accept (and may edit) the durable proposal before a permanent memory is created.
6. Later quiz, review, coaching, and study-plan requests retrieve relevant active memories and unresolved/improving signals.
7. Each adaptive workflow returns visible adaptation metadata and persists an `AdaptationEvent` describing the records used, the observable changes, and a concise user-readable reason.

No CockroachDB migration, AWS integration, exam timetable, notification feature, Figma work, or visual redesign was introduced.

## 2. LearningSignal implementation

`LearningSignal` now persists the required fields:

- `workspace_id`
- `source_type`
- `source_id`
- `source_question_id`
- `topic`
- `signal_type`
- `statement`
- `evidence`
- `confidence`
- `importance`
- `occurrence_count`
- `status`
- `first_observed_at`
- `last_observed_at`

It also keeps a stable aggregation key and optional `memory_id`/`proposal_id` links. Supported signal types are `knowledge_gap`, `misconception`, `repeated_error`, `skipped_topic`, `low_confidence`, `mastery`, `preference`, and `learning_behavior`.

The SQLite migration is additive. Existing generic signal rows are retained and backfilled rather than dropped. The original `payload` contract remains available for compatibility.

## 3. Quiz-to-signal path

The quiz submission UnitOfWork now saves, in one relational transaction:

- the quiz attempt;
- all generated question records, including unpresented suffix questions;
- learner selections and trusted correctness;
- explanations and exact citation lineage;
- deterministic learning signals;
- durable memory-proposal workflow state;
- a durable optional LLM-enrichment workflow job;
- completion of the pending quiz workflow.

No LLM, embedding, or vector-provider call occurs inside this transaction. Provider work is either part of quiz generation before the transaction or deferred through durable workflow/outbox state.

Incorrect answers initially create a `knowledge_gap`; skipped questions create a `skipped_topic`; repeated non-duplicate evidence promotes the same signal to `repeated_error`. Evidence keys based on the stored attempt and question make reprocessing idempotent.

## 4. Signal-to-memory proposal rules

- One isolated incorrect answer creates a learning signal and an approval-required proposal, not active memory.
- Proposal IDs are deterministic UUIDs derived from workspace and normalized signal identity.
- Repeated evidence updates the existing signal and the same pending proposal.
- Duplicate evidence does not increase occurrence count or create another signal/proposal.
- Repeated errors increase confidence and importance within configured caps.
- Later correct evidence reduces weakness confidence and marks it `improving` or `resolved`.
- Every signal-backed proposal includes visible supporting quiz evidence.
- A rejected or completed proposal is terminal; the stable identity prevents accidental duplicate proposals.

## 5. Memory write/update path

Signal-backed proposals use the existing durable `WorkflowStateRepository`. The learner may accept, reject, or edit-and-accept a proposal. Acceptance writes a `learning_state` memory, completes the proposal workflow, and links the originating signal to the saved memory in the same UnitOfWork.

New evidence for a linked signal updates the existing memory's confidence/importance rather than creating a duplicate. Memory vector changes use the existing transactional outbox and run only after relational commit. A failed vector provider leaves relational data committed and the outbox job retryable.

## 6. Memory retrieval paths

The shared adaptation-context service reads:

- active relational learner memories;
- active or improving learning signals;
- normalized topic/text overlap for deterministic relevance;
- memory and signal IDs for audit lineage.

Grounded chat keeps its existing memory-aware behavior. New retrieval paths cover quiz generation, review generation/queue priority, deterministic study plans, and coaching generation.

## 7. Quiz adaptation

Quiz generation now supplies user-readable learner context to the grounded quiz prompt and exposes:

- whether learner memory/signals were used;
- targeted topic;
- changed difficulty;
- distractor policy;
- question type;
- targeted-question count;
- misconception-check behavior;
- memory/signal IDs and reason;
- AdaptationEvent ID.

The trusted answer registry, response redaction, grounding validation, and citation rules remain unchanged.

## 8. Review adaptation

Review recommendations read relevant learner state, boost evidence-backed items, retain deterministic ordering, and explain why the priority changed. Generated review activities receive adaptation instructions for simpler examples, repeated misconception coverage, and reduced repetition of mastered material while retaining document-only factual grounding.

## 9. Coaching adaptation

Coaching generation receives relevant learner state and changes the requested explanation style, depth, worked-example strategy, reassessment strategy, and encouragement guidance. The response reports the memory/signal records used and the observable coaching properties changed. Factual content must still be supported by retrieved document excerpts.

## 10. Study-plan adaptation

The deterministic plan now uses learner state to adjust:

- topic priority;
- review frequency;
- focused session duration;
- sequencing;
- estimated effort;
- recommendation wording.

When relevant state exists, candidate priority and focused practice time increase before normal time allocation. The returned plan includes the reason, IDs used, and changed property names.

## 11. AdaptationEvent audit trail

The new persisted `AdaptationEvent` contains:

- `id`
- `workspace_id`
- `workflow_type`
- `request_id`
- `memory_ids`
- `learning_signal_ids`
- `applied_changes`
- `reason`
- `created_at`

Quiz, review, coaching, and study-plan API workflows write events. Reasons are concise structured explanations; no hidden chain-of-thought is stored.

## 12. API changes

Quiz generation responses now include adaptation metadata. Quiz submission responses now include detected weaknesses, rich learning signals, supporting evidence, memory proposals, and the durable enrichment-workflow ID.

Memory-proposal decisions accept optional edited content. Memory and proposal responses expose evidence, source, occurrence count, improvement/resolution state, and latest adaptive use. Review, coaching, and study-plan responses include a shared adaptation object.

All additions are response-compatible: existing request fields and trusted scoring behavior remain valid.

## 13. Frontend changes

The existing design and components were retained. Minimal additions show:

- quiz-generation adaptation target, difficulty, and reason;
- detected weaknesses and learning signals after submission;
- supporting evidence, confidence, and occurrence count;
- proposal edit, accept, and reject controls;
- review, plan, and coaching recommendation reasons and changed properties;
- memory evidence, source quiz, occurrence count, improvement state, and latest use.

No unrelated page or visual system was redesigned.

## 14. Files changed

Core application and persistence:

- `backend/application/learning_loop.py`
- `backend/application/dependencies.py`
- `backend/domain/persistence.py`
- `backend/domain/__init__.py`
- `backend/repositories/interfaces/protocols.py`
- `backend/repositories/interfaces/__init__.py`
- `backend/repositories/sqlite/foundation.py`
- `backend/repositories/sqlite/__init__.py`
- `backend/memory/proposals.py`

Study workflows and API:

- `backend/study/quiz_api.py`
- `backend/study/quiz_generator.py`
- `backend/study/recommendations.py`
- `backend/study/reviewer.py`
- `backend/study/planner.py`
- `backend/study/coach.py`
- `backend/api/schemas.py`
- `backend/api/report_schemas.py`
- `backend/api/routes/quiz.py`
- `backend/api/routes/memory.py`
- `backend/api/routes/reports_study.py`

Frontend and tests:

- `frontend/src/api/types.ts`
- `frontend/src/pages/StudyActionsPage.tsx`
- `frontend/src/pages/MemoryPage.tsx`
- `tests/test_agentic_loop.py`
- `tests/test_scoped_planner_evidence.py` (isolates adaptation state from the live local database)
- `AGENTIC_LOOP_REPORT.md`

## 15. Test commands

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_agentic_loop -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
cd frontend
npm.cmd test -- --run
npm.cmd run build
cd ..
.\.venv\Scripts\python.exe -m compileall backend api main.py tests
git diff --check
```

## 16. Actual test results

- New complete agentic-loop suite: 2 passed.
- Complete backend suite: 81 tests run; 80 passed and 1 skipped because Windows symlinks were unavailable.
- Frontend suite: 7 test files passed, 19 tests passed.
- Frontend production build: TypeScript check and Vite build passed.
- Python compileall: passed.
- `git diff --check`: passed (Git emitted only expected Windows line-ending notices).

The complete-flow test proves quiz failure evidence persistence, proposal durability across dependency restart, repeated-evidence aggregation, edited acceptance, memory retrieval by a later identical-topic quiz, a differing adaptive result in an isolated workspace without memory, and a populated AdaptationEvent. A second test proves improvement/resolution behavior and recovery from a post-commit vector-provider failure.

## 17. Remaining limitations

- Signal confidence and relevance are deterministic heuristics, not statistically calibrated learner models.
- Relevance currently uses normalized lexical overlap; a future adapter may add semantic retrieval without changing application contracts.
- Optional LLM enrichment is durably queued but no background enrichment worker is enabled in this local phase.
- Adaptation events are returned with workflow responses and used for memory `latest_use`; a dedicated audit-history endpoint is not yet included.
- Local SQLite concurrency and Chroma operation remain appropriate for a single-user local deployment, not a distributed production service.

## 18. CockroachDB migration readiness

The application now depends on repository interfaces, explicit UnitOfWork boundaries, workspace ownership, durable workflows, and an outbox for vector side effects. LearningSignal and AdaptationEvent repositories follow the same boundary, and provider work remains outside retryable transactions.

A later CockroachDB phase can implement relational adapters for these interfaces and translate the additive SQLite DDL/indexes to Cockroach-compatible migrations. Transaction retry policy, JSON representation, timestamp types, and distributed worker claiming for workflow/outbox jobs still need production-specific design. No CockroachDB migration was started in this phase.
