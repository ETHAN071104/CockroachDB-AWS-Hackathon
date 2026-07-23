# Guest Workspace Isolation Audit

Date: 2026-07-23  
Scope: anonymous guest-session readiness, workspace resolution, relational and
vector isolation, public API exposure, frontend bootstrap, and migration
compatibility  
Audit status: COMPLETE — findings were remediated in Phase 5

## Executive finding

Agentbook already has a strong workspace-owned CockroachDB data model and its
Cockroach repository adapters normally bind one `workspace_id` at
construction. Document-vector search and learner-memory-vector search both add
that bound workspace to their SQL predicates.

The public API does not yet provide workspace isolation, however:

- every application data route is currently callable without authentication;
- `build_application_dependencies()` defaults to the fixed legacy workspace;
- `get_application_dependencies()` caches one global dependency bundle;
- application startup calls `ensure_default()` and keeps that legacy workspace
  active;
- request handlers and domain services obtain the global dependency bundle
  rather than a request-authenticated workspace bundle;
- Cockroach export reads every row from every allowlisted table;
- Cockroach integrity diagnostics inspect and report counts for the whole
  cluster schema rather than one authenticated workspace.

Consequently, the current API exposes the migrated/default workspace to any
caller. A browser cannot currently choose a `workspace_id`, but this is not a
security boundary because all callers are implicitly assigned the same fixed
workspace.

A dedicated `guest_sessions` table is required. Existing `workflow_states`
cannot safely replace it because workflow rows expire by workflow semantics,
have a different status model, and are scoped behind the very workspace
identity that the session must resolve.

## Sources inspected

The audit inspected:

- `WorkspaceRepository` and both workspace adapters;
- `ApplicationDependencies`, global dependency caching, and UnitOfWork
  connection binding;
- every registered FastAPI route, including aliases and documentation routes;
- Cockroach repository constructors and query predicates;
- document and learner-memory vector adapters;
- workflow state, learning-signal, adaptation-event, dashboard, export, and
  integrity paths;
- vector-outbox reconciliation and operational migration/verification tools;
- frontend API client, application entry point, routing shell, browser-storage
  usage, and CORS configuration;
- Alembic environment and revisions `0001` and `0002`;
- CockroachDB migration, handoff, manifest, and verification documentation;
- tests that explicitly or implicitly depend on the default workspace.

No credential value, `DATABASE_URL`, provider key, future guest token, or
authorization header was printed or recorded.

### Documentation drift

`COCKROACHDB_HANDOFF.md` still states that the repository-root runtime remains
on SQLite, and `.env.example` describes SQLite as the safe default. The current
verified task state and sanitized runtime check show CockroachDB as the active
backend. Those documents must be reconciled after migration authorization;
they must not be used to justify a public SQLite/Chroma fallback.

## 1. Current default workspace selection

The default workspace is a fixed application constant:

```text
00000000-0000-4000-8000-000000000001
```

Its display name is `Local workspace`.

`build_application_dependencies()` uses that value as its default argument.
Every repository in the resulting bundle is constructed with the same value.
`get_application_dependencies()` caches that bundle in
`_DEFAULT_DEPENDENCIES`.

`initialize_application_foundation()` calls
`dependencies.workspaces.ensure_default()` in both persistence modes. In
Cockroach mode this inserts the fixed workspace if absent and then cleans
expired workflow rows within the globally selected workspace.

Finding: the selection is global and hardcoded, not request-derived or
configuration-derived.

## 2. Current request dependency composition

FastAPI stores the global bundle on `application.state.dependencies`, but route
handlers do not receive it through `Depends`. Instead, routes and services call
`get_application_dependencies()` directly.

The same global lookup occurs inside Chat, ingestion, retrieval, intelligence,
Quiz, Learning Signal, Learner Memory, Study Plan, Coaching, reporting, and
vector-outbox services. Protecting only the outer route would therefore be
insufficient unless the service lookup is made request-aware or dependencies
are explicitly passed through every call.

The Cockroach UnitOfWork already uses `ContextVar`-bound connections. A similar
request-scoped dependency context can safely coexist with it, provided the
context is always reset after the request.

## 3. Client-authoritative workspace inputs

No current FastAPI request model, query parameter, frontend request body, or
frontend header supplies a `workspace_id`.

Current scope identifiers such as `document_ids`, `notebook_id`, `topic_id`,
quiz IDs, memory IDs, and session IDs are resource selectors, not workspace
credentials. They remain safe only when resolved through repositories bound to
the authenticated workspace.

Finding: there is no existing client `workspace_id` override to remove, but
tampering tests are still required to prevent a future compatibility field or
query parameter from becoming authoritative.

## 4. Route dependency findings

No current route derives workspace identity through a FastAPI authentication
dependency. All registered application routes except health and documentation
implicitly use the global default workspace.

Current routes with no workspace authentication requirement include:

- Dashboard: `GET /api/dashboard`.
- Documents: list, upload aliases, get, assign, and delete.
- Notebooks: list, create, virtual Unsorted, get, update, delete, document list,
  assign, and remove.
- Intelligence: document/notebook/topic summaries, topic extraction, topic
  list, and topic detail.
- Study Chat and study session/history routes.
- Quiz generation and submission routes, including compatibility aliases.
- Reports, progress, Quiz reports, review, Study Plan, and Coaching routes.
- Learner Memory list/create/search/update/archive/delete, proposal decisions,
  and consolidation routes.
- Integrity aliases and `GET /api/system/export`.

Current infrastructure-only public routes are:

- `GET /api/health`;
- `/docs`, `/redoc`, `/openapi.json`, and the Swagger OAuth redirect.

There is no version endpoint.

## 5. Required public/protected classification

The following should remain public:

- `GET /api/health`;
- the future `POST /api/guest-session`;
- API documentation only when explicitly allowed by deployment policy.

The future `GET /api/guest-session` must be authenticated because it inspects
the caller's session.

Every other existing `/api` route exposes or mutates user data, invokes a
user-specific AI workflow, exports data, or reveals storage state. All must be
guest-session protected in public mode. Operational migration and
reconciliation commands must remain command-line/internal operations, not
anonymous API routes.

## 6. Repository ownership enforcement

### Cockroach adapters

The following repositories bind `workspace_id` at construction and include it
in normal reads and writes:

- blobs, documents, and notebooks;
- cached intelligence, topics, and topic sources;
- study sessions, interactions, and interaction sources;
- Quiz attempts, Quiz questions, and Quiz sources;
- learner memories and memory relationships;
- workflow states;
- learning signals;
- adaptation events;
- embedding jobs/vector outbox;
- dashboard;
- document vectors;
- learner-memory vectors.

Public integer IDs are resolved through `(workspace_id, public_id)` lookups.
This prevents a guessed public ID from resolving in another bound workspace.

### Defense-in-depth gaps

- `CockroachNotebookRepository.delete()` counts notebook assignments by the
  globally unique notebook UUID without an additional workspace predicate.
  The preceding workspace-bound UUID lookup keeps this safe, but the query
  should still be explicitly workspace-filtered.
- Several joins rely on globally unique UUID relationships and filter only the
  owning side. They are safe with current UUID primary keys but should retain
  explicit workspace equality wherever practical.
- `CockroachWorkspaceRepository.get()` is intentionally unbound. It must remain
  internal and must never be exposed as an anonymous workspace-list or
  arbitrary-workspace lookup.

### SQLite compatibility paths

SQLite repositories also accept a workspace at construction and normally
filter by it. Many legacy database helper functions still default directly to
the fixed workspace. They are acceptable only in an explicit legacy/test mode
and must not be reachable as a fallback from public Cockroach requests.

## 7. Vector-query ownership enforcement

`CockroachDocumentVectorRepository.search()` uses:

```text
c.workspace_id = :workspace_id
```

before scope filters, cosine ordering, and the result limit. Chunk listing and
document-vector deletion use the same workspace binding.

`CockroachMemoryVectorRepository.search()` starts with:

```text
e.workspace_id = :workspace_id
```

and applies optional status/memory-type filters afterward. Retrieval-counter
updates also include the bound workspace.

Finding: both Cockroach vector paths already enforce workspace isolation. The
remaining risk is constructing either adapter with the global default instead
of the authenticated guest workspace.

Chroma adapters do not provide an equivalent database-enforced workspace
boundary. They must never be constructed when Cockroach public mode is active.

## 8. Workflow, dashboard, report, export, and integrity findings

- `workflow_states`, Learning Signals, AdaptationEvents, Quiz history, Study
  history, dashboard, and standard reports are repository-scoped and will be
  isolated once request-scoped dependencies are used.
- `GET /api/system/export` is currently unsafe for multiple workspaces in
  Cockroach mode. `_build_cockroach_export()` executes unfiltered
  `SELECT * FROM <table>` for every allowlisted table, including `workspaces`.
  A guest export must use the authenticated workspace, exclude internal
  migration/session tables, and filter every owned table.
- Cockroach integrity checks currently count and inspect whole tables without a
  workspace predicate. Multiple active study sessions across different
  workspaces can also be falsely reported as one integrity error. Public
  integrity responses must be workspace-scoped or the route must become
  internal-only.
- Health is appropriately aggregate and public, but it must only add a boolean
  guest-subsystem configuration signal; it must not reveal session or workspace
  counts.

## 9. Background and reconciliation commands

`reconcile_pending_vectors()` builds its service from the global dependency
bundle. The command-line entry point initializes the default workspace and
does not require an explicit workspace.

Migration comparison, live Agentic Loop tests, vector-index verification, and
legacy import tooling also explicitly target the fixed migrated workspace.
Those operational tools must keep an explicit, documented legacy workspace
path and must not inherit browser guest context.

Finding: background/reconciliation commands need an explicit workspace
argument or an intentional all-workspace internal loop. They must never use a
missing browser token to select the legacy workspace.

## 10. Frontend workspace context

The frontend currently has no session bootstrap and no use of `localStorage` or
`sessionStorage`.

The singleton API client sends `Accept` and, for JSON mutations,
`Content-Type`. It sends no `Authorization` header. The application renders all
routes immediately, so dashboard and other user-data requests can begin before
any identity decision.

The sidebar labels the product as a local-only workspace. There is no guest
session inspection, invalid-session recovery, start-new-space action, or
browser-local access explanation.

## 11. CORS findings

Current CORS configuration:

- permits only `http://localhost:5173` and `http://127.0.0.1:5173`;
- does not allow credentials;
- allows the required HTTP methods;
- allows only `Content-Type` and `Accept`;
- exposes only `X-Request-ID`.

Bearer authentication requires adding `Authorization` to allowed headers.
No wildcard origin is needed. `allow_credentials=False` remains appropriate
for the localStorage Bearer-token MVP. A future HttpOnly cookie deployment
would require a separate same-site/credentials review.

## 12. Tests coupled to the legacy workspace

The following test groups intentionally or implicitly use the default
dependency bundle:

- persistence foundation and restart tests;
- backend end-to-end and API foundation tests;
- Agentic Learning Loop and scoped planner tests;
- Quiz, Study Chat, report, memory, export, and structured-error tests;
- live Cockroach Agentic Loop and document-smoke tests;
- live Quiz lineage and migration verification.

`tests/test_live_cockroach_agentic_loop.py` and
`tests/test_live_cockroach_quiz_lineage.py` explicitly reference the fixed
workspace. Existing API tests call `create_app()` or configure global
dependencies without authentication.

These tests need an explicit legacy compatibility opt-in or authenticated test
sessions. Live migration verification must continue to address the legacy
workspace directly and independently of public API authentication.

## 13. Existing migration compatibility

The current Alembic chain is:

```text
0001_agentbook_cockroach_schema
  -> 0002_cockroach_vector_indexes
```

The schema already has `workspaces` and workspace ownership on application data
tables. It does not have a credential-to-workspace mapping.

Adding anonymous sessions requires a new, additive Alembic revision. Reusing or
altering existing application rows is unnecessary. The safe revision can be
limited to:

- one `guest_sessions` table;
- guest-session-only constraints;
- guest-session-only indexes;
- an Alembic head update.

The revision must not insert, update, delete, or reassign any existing
workspace or application row.

Existing verification utilities assume either revision `0001` or the `0002`
head and some treat additional tables as unexpected. They will need a
post-authorization update so the new additive table does not produce a false
migration-verification failure.

## Paths that could accidentally expose the default workspace

High-priority paths are:

1. `build_application_dependencies()` called without a workspace.
2. Cached `_DEFAULT_DEPENDENCIES` reused by concurrent public requests.
3. Startup `ensure_default()` interpreted as authorization.
4. Domain services that call `get_application_dependencies()` after only the
   route boundary was authenticated.
5. Cockroach export's unscoped table reads.
6. Cockroach integrity's unscoped counts and orphan checks.
7. Reconciliation/CLI commands that silently use the default bundle.
8. Tests or local fixtures that enable legacy fallback globally rather than
   per process/application.
9. Any future request model that accepts `workspace_id` without rejecting or
   ignoring it.

## Audit answers

1. **How is the default workspace selected?**  
   By the fixed `DEFAULT_WORKSPACE_ID` default argument used to build one global
   dependency bundle.

2. **Is it global, configured, or hardcoded?**  
   Global and hardcoded.

3. **Which endpoints accept `workspace_id` from the client?**  
   None currently.

4. **Which endpoints derive workspace through dependencies?**  
   None derive it through authenticated FastAPI dependencies. Most data routes
   indirectly use the global dependency bundle.

5. **Which routes currently have no workspace requirement?**  
   Every current route. Health and documentation are intentionally
   infrastructure-public; all other API routes are unintentionally
   default-workspace public.

6. **Which repository queries already enforce `workspace_id`?**  
   All normal Cockroach application repositories listed in section 6, with the
   noted defense-in-depth exceptions.

7. **Which vector queries already enforce `workspace_id`?**  
   Cockroach document and learner-memory vector search, listing, updates, and
   deletes.

8. **Which paths might accidentally use the default workspace?**  
   Global dependency creation/cache, startup, service-level lookups, export,
   integrity, reconciliation, CLI, and legacy helper defaults.

9. **Do background/reconciliation commands require an explicit workspace?**  
   No. They currently default to the legacy workspace and need an explicit
   operational selection.

10. **How does the frontend currently obtain workspace context?**  
    It does not. It sends no credential and the server supplies the global
    default implicitly.

11. **Which tests depend on the legacy default workspace?**  
    The persistence, API, feature, export, Agentic Loop, and live Cockroach
    groups summarized in section 12.

12. **Which endpoints should remain public?**  
    Health, future session creation, and optionally documentation according to
    deployment policy.

13. **Which endpoints must become guest-session protected?**  
    Every current data, AI workflow, report, export, and integrity endpoint,
    plus future session inspection.

## Implementation gate

The audit found no requirement to destructively modify existing data and no
repository limitation that prevents guest isolation.

It did confirm that a new permanent `guest_sessions` table is necessary.
Implementation must therefore proceed through the explicit migration approval
gate. No live migration was applied by this audit.
