# Public ID String Boundary Audit

Date: 2026-07-24

## Scope and invariant

This audit classifies identifier-shaped fields before changing the Agentbook
network contract. The required invariant is:

| Boundary | Representation |
|---|---|
| CockroachDB persistence | `INT8` public ID plus internal UUID primary key |
| SQLite compatibility persistence | `INTEGER` public ID |
| Python domain and repositories | `int` |
| API path, query, form, and JSON request boundaries | validated decimal string |
| API JSON responses | decimal string |
| React, TypeScript, routing, state, and request payloads | opaque string |

Valid public-ID text matches `^[1-9][0-9]*$`. Counts, indexes, scores, page
numbers, chunk indexes, option numbers, ranks, durations, confidence values,
timestamps, and vector distances remain numeric.

## Root cause confirmed before implementation

CockroachDB contains document public ID `3557348663300104065`. The existing
API emitted it as a JSON number, and JavaScript rounded it to
`3557348663300104000`. The document and its 17 chunks exist. The incorrect
frontend route therefore queried a different ID and correctly received a
not-found response.

The shared generator derives a positive 63-bit public integer from a UUID.
Consequently, every public entity using `new_public_identity()` can exceed
`Number.MAX_SAFE_INTEGER`; this is not document-specific.

## Classification table

| Entity/field | Classification | Database type | Python type | Current API type | Required API type | Frontend type |
|---|---|---|---|---|---|---|
| Notebook `id`, `notebook_id` | Public integer identity | `INT8` | `int` | integer | decimal string | `PublicId` |
| Document `id`, `document_id`, `document_ids` | Public integer identity | `INT8` | `int`, `list[int]` | integer / integer array | decimal string / string array | `PublicId`, `PublicId[]` |
| Study session `id`, `session_id` | Public integer identity | `INT8` | `int` | integer | decimal string | `PublicId` |
| Study interaction `id`, `interaction_id` | Public integer identity | `INT8` | `int` | integer | decimal string | `PublicId` |
| Quiz attempt `id`, `attempt_id` | Public integer identity | `INT8` | `int` | integer | decimal string | `PublicId` |
| Stored quiz question `id`, question-attempt reference | Public integer identity | `INT8` | `int` | integer | decimal string | `PublicId` |
| Learner memory `id`, `memory_id`, `memory_ids` | Public integer identity | `INT8` | `int`, `list[int]` | integer / integer array | decimal string / string array | `PublicId`, `PublicId[]` |
| Source lineage `document_id`, `notebook_id` | Public integer identity reference | referenced `INT8` public ID | `int \| None` | integer or null | decimal string or null | `PublicId \| null` |
| Quiz scope `resolved_document_ids` | Public integer identity references | referenced `INT8` public IDs | `list[int]` | integer array | string array | `PublicId[]` |
| Adaptation `memory_ids` | Public integer identity references | referenced `INT8` public IDs | `list[int]` | integer array | string array | `PublicId[]` |
| Learning signal `memory_id` | Public integer identity reference | referenced `INT8` public ID | `int \| None` | integer or null | decimal string or null | `PublicId \| null` |
| Memory proposal `existing_memory_id` | Public integer identity reference | referenced `INT8` public ID | `int \| None` | integer or null | decimal string or null | `PublicId \| null` |
| Review `interaction_id`, `session_id` | Public integer identity references | referenced `INT8` public IDs | `int` | integer | decimal string | `PublicId` |
| Review/plan `source_document_ids` | Public integer identity references | referenced `INT8` public IDs | `list[int]` | integer array | string array | `PublicId[]` |
| Review/adaptation `memory_ids` | Public integer identity references | referenced `INT8` public IDs | `list[int]` | integer array | string array | `PublicId[]` |
| Study-plan evidence `reference_id` | Public interaction or quiz-attempt identity | referenced `INT8` public ID | `int` | integer | decimal string | `PublicId` |
| Dashboard session/quiz `id` | Public integer identity | `INT8` | `int` | integer | decimal string | `PublicId` |
| Integrity issue `record_id` when numeric | Public integer identity | varies by audited table | `int \| str \| None` | integer/string/null | string/null | `string \| null` |
| Summary `scope_id` | Public ID for document/notebook; topic ID otherwise | mixed | `str` | string | string | `string` |
| Topic `id`, `topic_id` | Internal Agentbook topic UUID/string key | `UUID`/string | `str` | string | unchanged string | `string` |
| Presented quiz `quiz_id` | Durable pending-quiz UUID | `UUID`/string | `str` | string | unchanged string | `string` |
| Proposal `proposal_id` | Durable proposal UUID/string | `UUID`/string | `str` | string | unchanged string | `string` |
| Workflow/event `workflow_id`, `event_id` | Internal UUID/string identifier | `UUID`/string | `str` | string | unchanged string | `string` |
| Learning signal `id`, `learning_signal_ids` | Internal UUID/string identifier | `UUID`/string | `str` | string | unchanged string | `string` |
| Learning signal `source_id`, `source_question_id` | Polymorphic stored string | string | `str` | string | unchanged string | `string` |
| Guest session ID and bearer token | Security identifier/credential | UUID/string and digest | `str` | token/string metadata | unchanged; token behavior untouched | opaque string |
| Workspace ID | Internal UUID | `UUID` | `str`/`UUID` | not publicly accepted as ownership input | unchanged | not user-selectable |
| Document chunk internal ID | Internal UUID | `UUID` | `str`/`UUID` | not a public integer ID | unchanged | not exposed as public ID |
| Memory relationship public ID | Generated persistence identity not currently exposed | `INT8` | `int` | not exposed | remain internal unless exposed later | not present |
| Study/quiz source-row public ID | Generated persistence identity not currently exposed | `INT8` | `int` | not exposed | remain internal unless exposed later | not present |
| `document_count`, totals, limits | Real numeric value | `INT8`/derived | `int` | number | number | `number` |
| `page_number`, `slide_number`, `chunk_index` | Real numeric source position | `INT8` | `int` | number | number | `number` |
| question number and option fields | Real numeric quiz position | `INT8` | `int` | number | number | `number` |
| rank, priority, minutes, occurrence count | Real numeric value | `INT8`/derived | `int` | number | number | `number` |
| score, confidence, importance, distance | Real numeric value | `FLOAT8`/derived | `float` | number | number | `number` |
| provider model IDs and request IDs | External/non-Agentbook identifier | string | `str` | string | unchanged | `string` |

## Shared-generator entities

Direct `new_public_identity()` usage was found for:

1. Notebooks.
2. Documents.
3. Study sessions.
4. Study interactions.
5. Study-interaction source rows (not currently exposed directly).
6. Quiz attempts.
7. Quiz-question attempt rows.
8. Quiz-question source rows (not currently exposed directly).
9. Learner memories.
10. Memory-relationship rows (not currently exposed directly).

The API/frontend migration must cover the exposed entities and every nested
reference to them. Non-exposed generated row IDs remain internal.

## Backend boundary locations

- `backend/api/schemas.py`: library, scope, source, chat, quiz, session,
  interaction, memory, proposal, and adaptation contracts.
- `backend/api/dashboard_schemas.py`: dashboard session and quiz IDs.
- `backend/api/report_schemas.py`: reports, review, plan, coaching, and
  integrity identifiers.
- `backend/api/routes/notebooks_documents.py`: notebook/document paths, query
  filter, upload form assignment, and document assignment.
- `backend/api/routes/intelligence.py`: document/notebook summary and topic
  paths.
- `backend/api/routes/chat.py`: interaction/session paths.
- `backend/api/routes/quiz.py`: pending quiz UUID path remains string; stored
  attempt output becomes a public-ID string.
- `backend/api/routes/reports_study.py`: session/attempt paths and scoped review
  query parameters.
- `backend/api/routes/memory.py`: memory paths, decisions, and consolidation.
- `backend/api/errors.py` and `backend/api/error_catalog.py`: structured
  malformed/unsafe public-ID errors.
- `backend/api/export_service.py`: Cockroach logical-export rows and nested
  JSON evidence containing public integer references.

## Frontend boundary locations

- `frontend/src/api/types.ts`: all API DTO identities and scope arrays.
- `frontend/src/api/endpoints.ts`: ID arguments and route generation.
- `DocumentDetailPage`, `NotebookDetailPage`, `NotebooksPage`, `ChatPage`,
  `MemoryPage`, `StudyActionsPage`, and `TopicWorkspacePage`: route parsing,
  selection state, maps, sets, outcome actions, and request payloads.
- `SourceCard` and report/dashboard components: nested identity props and keys.
- Integration/unit tests currently use small numeric fixture IDs and must be
  migrated to strings.

Unsafe patterns confirmed before implementation include:

- `Number(documentId)` and `Number(notebookId)` on React Router parameters.
- `Number(scopeValue)` for document/notebook Chat scope.
- `Record<number, ...>` and `Set<number>` keyed by public IDs.
- API endpoint arguments typed as `number`.
- JSON request arrays typed as `number[]`.
- Path and query parameters typed as FastAPI `int`.

Numeric conversion for real numeric UI inputs such as question count,
available minutes, maximum items, confidence sliders, and option selection is
valid and must remain.

## Input compatibility decision

- Preferred inputs are decimal strings matching `^[1-9][0-9]*$`.
- Positive numeric JSON values are accepted temporarily only through
  `9007199254740991`.
- Larger numeric JSON values are rejected with
  `PUBLIC_ID_STRING_REQUIRED`.
- Malformed strings, booleans, zero, negative values, fractional values,
  exponential notation, arrays in scalar positions, and objects are rejected
  with `INVALID_PUBLIC_ID`.
- Path, query, and form inputs are documented as strings and parsed into exact
  Python integers before domain/repository calls.

## Pre-change live-data baseline

The baseline was collected with read-only SQL:

| Check | Result |
|---|---|
| Alembic revision | `0003_guest_sessions` |
| Notebooks | 4 |
| Documents | 8 |
| Document chunks | 294 |
| Study sessions | 4 |
| Study interactions | 6 |
| Quiz attempts | 6 |
| Quiz question attempts | 12 |
| Learner memories | 11 |
| Guest sessions | 6 |
| Public-ID fingerprint | `b1c47cded24323a7397661da22b27021d25d87f44004940572777a275779538b` |
| Known document present | yes |
| Known document ID/chunks | `3557348663300104065` / 17 |

No application data, schema object, vector, guest token, or migration state was
changed while producing this audit.
