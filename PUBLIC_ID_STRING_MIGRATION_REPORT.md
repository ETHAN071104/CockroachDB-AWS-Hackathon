# Public ID String Migration Report

Date: 2026-07-24

Overall result: PASS

## 1. Root cause

CockroachDB correctly stored public identities as positive `INT8` values and
Python preserved them as arbitrary-precision integers. FastAPI previously
serialized those values as JSON numbers. JavaScript parsed them as IEEE-754
`Number` values, which cannot exactly represent every integer above
`9007199254740991`.

The failure was therefore at the HTTP/browser boundary, not in CockroachDB,
vector retrieval, workspace ownership, or document ingestion.

## 2. Exact regression example

| Stage | Value |
|---|---|
| CockroachDB and Python | `3557348663300104065` |
| Previous JavaScript value and route | `3557348663300104000` |
| Lost amount | `65` |
| Correct route after repair | `/documents/3557348663300104065` |

The existing affected record is
`CHAPTER 9 FUNCTION (PART 1-WEEK16).pdf`. It still has 17 stored chunks.

## 3. Why JavaScript lost precision

The value is greater than `Number.MAX_SAFE_INTEGER`. Once the old JSON number
was parsed, the lower bits were already lost; converting that rounded value to
a string or `BigInt` in React could not restore the original ID. The repair
therefore changes serialization before JavaScript receives the value.

## 4. API contract before

- CockroachDB: `INT8`.
- Python domain/repositories: `int`.
- JSON response IDs: number.
- request IDs: primarily integer schemas.
- frontend DTOs, state, and route helpers: primarily `number`.
- route parameters were converted through `Number(...)` in several pages.

## 5. API contract after

- CockroachDB: unchanged `INT8`.
- Python domain/repositories: unchanged exact `int`.
- path, query, form, and JSON public-ID inputs: canonical decimal strings.
- JSON public-ID outputs, including nested evidence and Cockroach logical
  exports: decimal strings.
- React/TypeScript public IDs: `PublicId`, defined as `string`.
- real numeric values such as counts, limits, indexes, ranks, durations,
  scores, confidence values, and distances remain numbers.

Valid text matches `^[1-9][0-9]*$`.

## 6. Backend implementation

`backend/api/public_ids.py` is the single network-boundary implementation. It
provides:

- `PublicId` response serialization from exact Python `int` to decimal text;
- `PublicIdInput` parsing and validation;
- `PublicIdData` serialization for public integer references inside
  intentionally untyped evidence/payload objects;
- OpenAPI string schema, pattern, and regression example;
- JavaScript-safe numeric transition compatibility;
- signed-`INT8` range enforcement.

All public-ID request and response fields in library, intelligence, chat,
study, quiz, report, dashboard, memory, review, plan, coaching, scope, source
lineage, adaptation, integrity, and logical-export boundaries use the shared
implementation.

`INVALID_PUBLIC_ID` handles malformed values.
`PUBLIC_ID_STRING_REQUIRED` rejects numeric JSON IDs greater than
`9007199254740991`. Structured responses retain title, reason, next action,
retryability, and request ID without echoing the rejected value.

## 7. Frontend implementation

The frontend now defines `PublicId = string` and keeps it opaque through:

- API DTOs and endpoint arguments;
- React Router parameters and generated paths;
- query keys;
- scope requests;
- component props and action callbacks;
- maps, sets, records, and selected values;
- notebook/document/session/report/memory navigation;
- source lineage and adaptation/report data.

ID-related `Number(...)` conversions were removed. Remaining conversions are
only for genuine numeric controls such as question count, minutes, maximum
items, and confidence/range values.

## 8. Entities audited

Public integer identities and references were migrated for notebooks,
documents, study sessions, study interactions, quiz attempts, stored quiz
questions, learner memories, source lineage, scope metadata, review records,
study-plan evidence, dashboard records, integrity records, memory proposals,
learning signals, adaptation evidence, and Cockroach logical exports.

Topic, pending-quiz, proposal, workflow, event, learning-signal, guest-session,
workspace, and provider identifiers remain their existing UUID/string
contracts. The complete classification is in `PUBLIC_ID_STRING_AUDIT.md`.

## 9. Input compatibility rules

- Preferred input: decimal string matching `^[1-9][0-9]*$`.
- Accepted temporary compatibility input: positive JSON integer no greater
  than `9007199254740991`.
- Unsafe numeric JSON input: rejected with
  `PUBLIC_ID_STRING_REQUIRED`.
- Empty, whitespace-padded, zero, negative, signed, fractional, exponential,
  hexadecimal, boolean, object, and scalar-position array values: rejected
  with `INVALID_PUBLIC_ID`.
- Valid input is converted to exact Python `int` before repository calls.

## 10. OpenAPI changes

Representative public IDs now appear as:

```yaml
type: string
pattern: ^[1-9][0-9]*$
examples:
  - "3557348663300104065"
```

An automated OpenAPI scan found zero integer schemas for fields or parameters
named `id`, ending in `_id`, or ending in `_ids`. UUID/string identifiers also
remain strings. Real numeric fields were not changed.

## 11. Files changed

Backend:

- `backend/api/public_ids.py`
- `backend/api/schemas.py`
- `backend/api/report_schemas.py`
- `backend/api/dashboard_schemas.py`
- `backend/api/errors.py`
- `backend/api/error_catalog.py`
- `backend/api/export_service.py`
- public-ID routes under `backend/api/routes/`

Frontend:

- `frontend/src/api/publicIds.ts`
- `frontend/src/api/types.ts`
- `frontend/src/api/endpoints.ts`
- `frontend/src/api/index.ts`
- `SourceCard` and affected pages under `frontend/src/pages/`
- public-ID and migrated fixture tests under `frontend/src/test/`

Tests and documentation:

- `tests/test_public_id_contract.py`
- affected API expectation tests under `tests/`
- `README.md`
- `PUBLIC_ID_STRING_AUDIT.md`
- this report

No Alembic file, persistence adapter, public-ID generator, `.env`, guest-token
code, or database schema was changed.

## 12. Tests executed

- Python compileall for `backend`, `api`, `main.py`, and `tests`.
- public-ID backend contract tests.
- complete backend `unittest` discovery suite with an isolated process-level
  `PERSISTENCE_BACKEND=sqlite` override.
- Cockroach repository unit tests.
- Guest Workspace and two-user relational-isolation tests.
- Agentic Learning Loop tests.
- Quiz, Chat, Coaching, Study Plan, scoped-planner, memory, report, export, and
  structured-error tests through the full suite.
- complete frontend Vitest suite.
- TypeScript check and Vite production build.
- automated OpenAPI public-ID schema scan.
- live Cockroach health, row-count, revision, Guest schema, original migration
  fingerprint, and vector-index verification.
- modified-diff credential scan and `git diff --check`.

## 13. Actual test results

| Check | Result |
|---|---|
| Python compileall | PASS |
| Public-ID backend contract | PASS, 12 tests |
| Complete backend suite | PASS, 132 tests; 6 explicit opt-in live mutation tests skipped |
| Focused repository/Guest/agentic/Quiz/Chat/plan suite | PASS, 45 tests; 1 opt-in live test skipped |
| Frontend Vitest suite | PASS, 11 files / 36 tests |
| TypeScript check | PASS |
| Vite production build | PASS |
| OpenAPI integer-public-ID scan | PASS, 0 violations |
| Cockroach runtime health | PASS, `persistence_backend=cockroach` |
| Cockroach composition excludes SQLite/Chroma adapters | PASS |
| Guest Session live schema verifier | PASS |
| Vector-index live verifier | PASS |
| Original completed migration fingerprint | PASS, still present and matching the migration report |
| Credential patterns in modified diff | PASS, 0 matches |
| Alembic files changed | PASS, 0 |
| `git diff --check` | PASS |

The source-import verifier was also invoked. Its current-local-source lookup
reported `Completed migration run is missing` because the ignored local
SQLite/Chroma source snapshot no longer has the same fingerprint as the
original imported snapshot. A direct read-only check confirmed that the
original completed migration fingerprint recorded in
`COCKROACHDB_MIGRATION_REPORT.md` is still present and unchanged. This does not
affect the active Cockroach runtime or this boundary-only repair.

## 14. End-to-end result

Authenticated browser verification opened:

`http://127.0.0.1:5173/documents/3557348663300104065`

The page rendered the correct existing document, reported 17 chunks, generated
its Study action link with the same exact ID, and produced no browser console
errors. It did not request or display the rounded
`3557348663300104000` value.

Automated frontend and FastAPI tests additionally prove that the exact ID is
preserved in the document request, parsed to the exact Python integer, passed
unchanged to the repository, and returned as a JSON string.

Document-scoped Chat, Quiz, Study Plan, and Coaching request tests confirm that
scope arrays contain strings. Existing full-flow tests confirm those workflows
remain functional without invoking live provider mutations against the known
document.

## 15. Existing-data integrity result

All live checks were read-only. No DDL, migration, upload, deletion,
regeneration, or vector write was executed.

| Live check | Before | After |
|---|---:|---:|
| Alembic revision | `0003_guest_sessions` | `0003_guest_sessions` |
| Notebooks | 4 | 4 |
| Documents | 8 | 8 |
| Document chunks | 294 | 294 |
| Study sessions | 4 | 4 |
| Study interactions | 6 | 6 |
| Quiz attempts | 6 | 6 |
| Quiz question attempts | 12 | 12 |
| Learner memories | 11 | 11 |
| Guest sessions | 6 | 6 |
| Known document rows | 1 | 1 |
| Known document chunks | 17 | 17 |

The original completed migration source fingerprint remains present. Both
workspace-prefixed vector index definitions remain present and the live vector
index verifier passed. No application table, index, public ID, UUID, document
chunk, embedding, learner memory, or guest credential was modified by this
repair.

The audit's pre-change public-ID digest remains recorded, but its one-off
canonicalization algorithm was not persisted. Integrity confirmation therefore
uses the reproducible row counts above, the exact known-ID lookup, the original
completed migration fingerprint, the live schema/index verifiers, and the fact
that every implementation and verification action was non-mutating.

## 16. Guest Workspace isolation result

Guest bearer-token behavior and request-scoped dependency binding were not
changed. Tests passed for:

- token digests and safe inspection;
- missing, invalid, expired, and revoked credentials;
- rejection of workspace overrides;
- two guest workspaces remaining relationally isolated across restart;
- cross-workspace public-ID lookups failing closed;
- Cockroach composition using workspace-scoped repositories.

The existing live Guest Session schema, constraints, indexes, row count, and
vector indexes passed the read-only verifier. A new live two-user fixture was
not created because the existing opt-in test leaves permanent disposable
workspace rows; avoiding that mutation was required by this repair.

## 17. Known limitations

- Safe numeric JSON compatibility is transitional. Clients should always send
  public IDs as strings.
- The local ignored SQLite/Chroma source snapshot has drifted from the
  historical imported snapshot, so the source-import verifier must be pointed
  at the original snapshot to reproduce its full historical comparison.
- The repair does not change the positive 63-bit public-ID generator; future
  values may continue to exceed JavaScript's safe-integer limit, which is now
  supported by the string contract.
- No live AI-provider generation was performed against the known document, so
  no quiz, session, memory, vector, or provider-side data was added.

## 18. Phase 6 MCP readiness

Yes. The public-ID production blocker is repaired, the exact existing record
opens, all public integer network/frontend boundaries are string-based, the
standard suites and production build pass, Cockroach remains on revision
`0003_guest_sessions`, and no database migration or data rewrite was required.

Phase 6 MCP work is safe to begin as a separate task. MCP work was not started
as part of this repair.
