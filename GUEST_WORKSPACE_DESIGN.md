# Guest Workspace Isolation Design

Date: 2026-07-23  
Status: IMPLEMENTED AND VERIFIED  
Target: Hackathon-safe anonymous browser workspaces, not full user
authentication

Revision `0003_guest_sessions` is applied. The bearer-token dependency,
request-scoped repository bundle, frontend bootstrap, scoped export/integrity
behavior, and live two-guest relational/vector isolation proof now implement
this design. The explicit legacy compatibility switch remains disabled by
default.

## Security invariant

The only authoritative public-workspace flow is:

```text
Bearer guest token
  -> keyed token hash
  -> active guest_sessions row
  -> server-owned workspace_id
  -> request-scoped ApplicationDependencies
  -> workspace-bound relational and vector repositories
```

A workspace UUID, public document ID, request body, query parameter, or
frontend state is never an authorization credential.

## Threat model

### Protected assets

- uploaded document bytes and extracted chunks;
- embeddings and vector search results;
- notebooks and assignments;
- summaries, topics, and cached intelligence;
- Study Chat history and citations;
- Quiz questions, attempts, reports, and citations;
- Learning Signals and Learner Memories;
- Study Plan, Coaching, workflow, and adaptation state;
- dashboard, progress, integrity, and export output;
- the existing migrated/default workspace.

### Relevant attackers and failures

- another anonymous visitor guessing resource IDs or workspace UUIDs;
- a caller adding or changing `workspace_id` in JSON or query parameters;
- a stolen or replayed guest token;
- a database reader obtaining the `guest_sessions` table;
- browser storage loss;
- accidental Authorization-header logging;
- concurrent or retried Cockroach transactions;
- frontend retry after an ambiguous session-creation response;
- public requests falling through to the legacy workspace;
- unscoped background, export, integrity, relational, or vector queries;
- cross-site browser requests from an unapproved origin.

### Out of scope

- proving a real-world human identity;
- cross-device recovery and synchronization;
- protection after arbitrary JavaScript executes in the frontend origin;
- a distributed production abuse-prevention service;
- account recovery, roles, teams, or social login.

## Token design

### Raw credential

The backend generates one opaque token using `secrets.token_urlsafe(32)`.
`32` random bytes provide 256 bits of entropy before URL-safe encoding. A
non-secret version prefix may be added:

```text
agentbook_guest_v1_<base64url random value>
```

The token is:

- unrelated to workspace and session UUIDs;
- unrelated to timestamps or sequence counters;
- returned only by successful session creation or a separately authorized
  rotation operation;
- never returned by inspection, health, export, error, or listing endpoints;
- never written to application logs or analytics.

Token generation, session UUID generation, workspace UUID generation, and
token hashing happen once before the Cockroach retry callback. A retried
transaction reuses those fixed values and cannot create multiple valid tokens.

### Stored lookup hash

`GUEST_SESSION_TOKEN_PEPPER` is a server-held secret with at least 256 bits of
entropy. The stored lookup value is:

```text
HMAC-SHA256(
  key = GUEST_SESSION_TOKEN_PEPPER,
  message = "agentbook-guest-token-v1:" + raw_token
).hexdigest()
```

HMAC-SHA256 is appropriate here because the input token is already
high-entropy. It:

- prevents an offline database reader from using a stored hash directly as a
  bearer credential;
- supports deterministic, indexed lookup;
- avoids the unnecessary per-request cost of password hashing for a uniformly
  random 256-bit secret;
- provides domain separation for future credential types.

The repository performs lookup by the 64-character digest. A constant-time
comparison is used when comparing an already retrieved digest in application
code, although the indexed database equality lookup is the primary resolver.

The pepper is required when public Guest mode is enabled. It is never logged.
Changing it invalidates all existing guest tokens; rotation therefore requires
an explicit session migration/invalidation plan.

## Creation idempotency

The browser creates one high-entropy `Idempotency-Key` for each deliberate
session-creation attempt and keeps it only until that attempt resolves. The
backend stores:

```text
HMAC-SHA256(
  key = GUEST_SESSION_TOKEN_PEPPER,
  message = "agentbook-guest-creation-v1:" + idempotency_key
).hexdigest()
```

as `creation_key_hash`.

The key has a unique constraint. Concurrent submissions therefore create at
most one workspace/session pair.

The raw guest token is intentionally not stored, so a response lost after a
successful commit cannot be replayed from the database. A repeated
idempotency key returns `GUEST_SESSION_CONFLICT` without creating another
workspace and without returning a replacement credential. The frontend must
not silently create a new session after a network ambiguity; it offers retry
and an explicit “Start a new study space” decision.

This is a deliberate tradeoff: preventing duplicate persistent workspaces and
never storing a recoverable bearer token are more important than transparently
replaying a lost creation response.

## Proposed CockroachDB storage

One additive table is required:

| Column | Type | Purpose |
|---|---|---|
| `id` | `UUID` | Internal session identity |
| `workspace_id` | `UUID` | Server-owned workspace mapping |
| `token_hash` | `STRING` | HMAC-SHA256 hex digest, never the raw token |
| `creation_key_hash` | `STRING` | HMAC of creation idempotency key |
| `status` | `STRING` | `active`, `revoked`, or `expired` |
| `created_at` | `TIMESTAMPTZ` | Creation time |
| `updated_at` | `TIMESTAMPTZ` | Last state change |
| `last_seen_at` | `TIMESTAMPTZ` | Bounded activity update |
| `expires_at` | `TIMESTAMPTZ` | Optional expiration |
| `revoked_at` | `TIMESTAMPTZ` | Required only for revoked sessions |
| `version` | `INT8` | Positive optimistic version |
| `session_label` | `STRING` | Optional safe display label |

No IP address, user-agent string/hash, browser fingerprint, document content,
raw token, or token pepper is stored.

The workspace foreign key uses `ON DELETE RESTRICT`. The first MVP has no
workspace-delete operation. This makes accidental deletion harder and keeps
“Start a new study space” non-destructive.

Required schema protections:

- primary key on `id`;
- unique `token_hash`;
- unique `creation_key_hash`;
- valid status check;
- 64-character hash checks;
- positive version check;
- revoked status/revoked timestamp consistency;
- expiration later than creation when present;
- workspace/status index for internal lifecycle operations;
- partial active-expiration index for cleanup.

## Domain and repository boundaries

Add a persistence-neutral `GuestSession` domain record and
`GuestSessionRepository` protocol with:

- create;
- resolve an active token hash;
- find by creation-key hash for idempotency;
- get safe metadata by internal session ID;
- bounded last-seen update;
- revoke;
- expire;
- rotate only if separately implemented;
- internal test/admin list, never exposed publicly.

`GuestSessionRepository` is intentionally not workspace-bound because it is
the trusted component that resolves a credential into a workspace.

Implementations:

- `CockroachGuestSessionRepository` for public/runtime use;
- `SQLiteGuestSessionRepository` only for isolated legacy/unit tests.

Business services import the protocol, not SQLAlchemy, psycopg, or SQLite
drivers.

## Atomic session creation

The service prepares these fixed values outside a retryable transaction:

- raw token;
- token hash;
- creation-key hash;
- workspace UUID;
- guest-session UUID;
- timestamps and optional expiry.

One `UnitOfWork.run()` callback then:

1. checks/claims the idempotency hash;
2. creates `My Study Space`;
3. creates the guest-session mapping;
4. commits both or neither.

Both repositories join the active UnitOfWork connection through the existing
Cockroach connection context. Cockroach serialization retries reuse the fixed
token and UUID values.

## Session lifecycle

### Create

`POST /api/guest-session` is public, accepts no workspace identity, and
requires a valid bounded `Idempotency-Key`.

It returns the raw token once plus safe metadata. It does not return internal
session/workspace UUIDs or hashes.

### Inspect

`GET /api/guest-session` requires the Bearer token. It returns:

- status;
- safe workspace display name;
- creation time;
- last-seen time;
- optional expiry.

It never returns the raw token, token hash, creation hash, or internal UUID.

### Resolve

For each protected request:

1. reject token-bearing query parameters;
2. read one `Authorization: Bearer <token>` header;
3. validate format and a conservative maximum length;
4. HMAC the token;
5. resolve a matching session;
6. reject revoked or expired sessions;
7. transition an elapsed active session to `expired` safely;
8. load the mapped workspace internally;
9. build request-scoped dependencies for that workspace;
10. update `last_seen_at` at most once per five-minute window;
11. reset request context after the response.

No Authorization value is added to errors or logs.

### Expire and revoke

Expiration is optional and controlled by `GUEST_SESSION_TTL_DAYS`. An empty
value means no automatic expiry for the Hackathon demo. A positive integer
sets `expires_at` at creation.

Revocation is a service/repository capability used by tests and future admin
operations; it is not an anonymous public listing or management endpoint.

### Start a new study space

The frontend calls the same creation endpoint with a new idempotency key. After
success it atomically replaces `agentbook_guest_token` in localStorage,
clears API caches, and reloads the private application state.

The old session and workspace are not deleted or modified. The UI explains
that replacing or clearing browser storage may make the old space inaccessible.

No destructive reset action is part of this MVP.

## Request-scoped application dependencies

Introduce a request dependency context, preferably a `ContextVar`, with:

- an explicit `bind_application_dependencies()`/reset lifecycle;
- `get_application_dependencies()` returning the request bundle when present;
- an explicit legacy/operational accessor for migration tools and CLI;
- no implicit default fallback when
  `ALLOW_LEGACY_DEFAULT_WORKSPACE=false`.

Protected FastAPI routers receive a yielding `Depends` dependency that
authenticates the token and binds the workspace bundle for the complete route
and service call.

Operational tools must pass a workspace explicitly. The legacy workspace may
be selected only through an explicit local/test/maintenance path.

## Legacy/default workspace compatibility

Add:

```text
ALLOW_LEGACY_DEFAULT_WORKSPACE=false
```

Rules:

- public/deployment default is `false`;
- missing credentials never fall through to the default workspace when false;
- tests and controlled local development may set it to true explicitly;
- CLI/migration tooling uses an explicit legacy dependency constructor;
- application startup does not interpret `ensure_default()` as authorization;
- the migrated/default workspace and all existing rows remain untouched;
- existing data is never copied into a guest workspace automatically.

## Frontend bootstrap and storage

For the Hackathon MVP, store only the raw guest token under:

```text
agentbook_guest_token
```

in localStorage. This is documented as an XSS-sensitive MVP tradeoff.

Bootstrap states:

1. **No token:** show a lightweight welcome gate and “Continue as Guest”.
2. **Saved token:** call authenticated `GET /api/guest-session`.
3. **Valid:** configure the API client and render user-data routes.
4. **Invalid/revoked/expired:** clear the unusable token only after a definitive
   authenticated response and offer “Start a new study space”.
5. **Network/backend failure:** retain the token, do not create a new workspace,
   and offer “Try again”.
6. **Corrupt storage:** treat as invalid local state without displaying the
   value.

The API client attaches Bearer authentication only to same Agentbook API-base
requests. It never forwards the token to absolute/external URLs. User-data
screens do not render until bootstrap is resolved.

## UX copy

First use:

```text
Welcome to Agentbook

Your study space is private to this browser.
No account is required for this demo.

[Continue as Guest]
```

Persistent explanation:

```text
Private to this browser

This demo uses an anonymous study session. Clearing browser storage or
switching devices may remove access to this study space.
```

Invalid/expired states distinguish definitive credential failure from network
failure and use the existing structured-error component.

## Structured errors

| Code | HTTP | Retryable | Use |
|---|---:|---|---|
| `GUEST_SESSION_REQUIRED` | 401 | No | Protected request has no Bearer token |
| `GUEST_SESSION_INVALID` | 401 | No | Token format/hash does not resolve |
| `GUEST_SESSION_EXPIRED` | 401 | No | Session lifetime elapsed |
| `GUEST_SESSION_REVOKED` | 401 | No | Session was revoked |
| `GUEST_SESSION_CREATION_FAILED` | 503 | Yes | Session/workspace transaction could not complete |
| `GUEST_SESSION_CONFLICT` | 409 | No | Creation idempotency key was already committed |
| `WORKSPACE_ACCESS_DENIED` | 403 | No | Explicit workspace-identity tampering was rejected |
| `REQUEST_CONFLICT` | 409 | No | Optimistic lifecycle conflict |
| `DATABASE_UNAVAILABLE` | 503 | Yes | Persistence unavailable |
| `INTERNAL_ERROR` | 500 | No | Sanitized unexpected failure |

Cross-workspace resource-ID guesses normally resolve as
`RESOURCE_NOT_FOUND` within the caller's workspace so the other workspace's
existence is not disclosed. Explicit `workspace_id` fields/parameters can be
rejected as `WORKSPACE_ACCESS_DENIED` without echoing their value.

## Route matrix

All protected rows derive workspace from the Bearer guest session. No row
trusts client workspace identity.

| Route | Public/protected | Workspace source | Notes |
|---|---|---|---|
| `GET /api/health` | Public | None | Safe aggregate health only |
| `POST /api/guest-session` | Public | New server mapping | Returns raw token once |
| `GET /api/guest-session` | Protected | Bearer session | Safe metadata only |
| `GET /docs` | Deployment policy | None | Local on; public deploy may disable |
| `GET /redoc` | Deployment policy | None | Local on; public deploy may disable |
| `GET /openapi.json` | Deployment policy | None | Local on; public deploy may disable |
| `GET /docs/oauth2-redirect` | Deployment policy | None | Only if Swagger UI is enabled |
| `GET /api/dashboard` | Protected | Bearer session | Workspace dashboard |
| `GET /api/documents` | Protected | Bearer session | Workspace list |
| `POST /api/documents` | Protected | Bearer session | Upload |
| `POST /api/documents/upload` | Protected | Bearer session | Compatibility upload alias |
| `GET /api/documents/{document_id}` | Protected | Bearer session | Workspace-bound ID |
| `PATCH /api/documents/{document_id}/notebook` | Protected | Bearer session | Both resources workspace-bound |
| `DELETE /api/documents/{document_id}` | Protected | Bearer session | Current workspace only |
| `GET /api/notebooks` | Protected | Bearer session | Workspace list |
| `POST /api/notebooks` | Protected | Bearer session | Workspace create |
| `GET /api/notebooks/unsorted` | Protected | Bearer session | Virtual current-workspace view |
| `GET /api/notebooks/unsorted/documents` | Protected | Bearer session | Workspace list |
| `GET /api/notebooks/{notebook_id}` | Protected | Bearer session | Workspace-bound ID |
| `PATCH /api/notebooks/{notebook_id}` | Protected | Bearer session | Current workspace only |
| `DELETE /api/notebooks/{notebook_id}` | Protected | Bearer session | Current workspace only |
| `GET /api/notebooks/{notebook_id}/documents` | Protected | Bearer session | Workspace list |
| `POST /api/notebooks/{notebook_id}/documents/{document_id}` | Protected | Bearer session | Prevent cross-workspace assignment |
| `DELETE /api/notebooks/{notebook_id}/documents/{document_id}` | Protected | Bearer session | Current workspace only |
| `GET/POST /api/documents/{document_id}/summary` | Protected | Bearer session | Cached/generated intelligence |
| `GET/POST /api/notebooks/{notebook_id}/summary` | Protected | Bearer session | Cached/generated intelligence |
| `GET/POST /api/topics/{topic_id}/summary` | Protected | Bearer session | Cached/generated intelligence |
| `POST /api/topics/extract` | Protected | Bearer session | Scoped retrieval |
| `GET/POST /api/documents/{document_id}/topics` | Protected | Bearer session | Workspace intelligence |
| `GET/POST /api/notebooks/{notebook_id}/topics` | Protected | Bearer session | Workspace intelligence |
| `GET /api/topics` | Protected | Bearer session | Workspace list |
| `GET /api/topics/{topic_id}` | Protected | Bearer session | Workspace-bound ID |
| `POST /api/chat` | Protected | Bearer session | Workspace vectors/history |
| `PATCH /api/study/interactions/{interaction_id}/outcome` | Protected | Bearer session | Workspace history |
| `GET /api/study/sessions` | Protected | Bearer session | Workspace history |
| `GET /api/study/sessions/{session_id}` | Protected | Bearer session | Workspace-bound ID |
| `POST /api/study/sessions/active/end` | Protected | Bearer session | Workspace active session |
| `POST /api/study/actions/quizzes/generate` | Protected | Bearer session | Workspace retrieval/workflow |
| `POST /api/study/quiz` | Protected | Bearer session | Compatibility alias |
| `POST /api/study/actions/quizzes/{quiz_id}/submit` | Protected | Bearer session | Workspace workflow |
| `POST /api/study/quiz/{quiz_id}/submit` | Protected | Bearer session | Compatibility alias |
| `GET /api/reports/study/sessions` | Protected | Bearer session | Workspace report |
| `GET /api/reports/sessions` | Protected | Bearer session | Compatibility alias |
| `GET /api/reports/study/sessions/{session_id}` | Protected | Bearer session | Workspace report |
| `GET /api/reports/sessions/{session_id}` | Protected | Bearer session | Compatibility alias |
| `POST /api/reports/study/sessions/{session_id}/summary` | Protected | Bearer session | Workspace AI/report |
| `POST /api/reports/sessions/{session_id}/summary` | Protected | Bearer session | Compatibility alias |
| `GET /api/reports/study/progress` | Protected | Bearer session | Workspace report |
| `GET /api/reports/progress` | Protected | Bearer session | Compatibility alias |
| `GET /api/reports/quizzes/performance` | Protected | Bearer session | Workspace report |
| `GET /api/reports/quizzes` | Protected | Bearer session | Compatibility alias |
| `GET /api/reports/quizzes/{attempt_id}` | Protected | Bearer session | Workspace-bound ID |
| `GET /api/study/actions/review-queue` | Protected | Bearer session | Workspace history |
| `GET /api/review` | Protected | Bearer session | Compatibility alias |
| `POST /api/study/actions/review` | Protected | Bearer session | Workspace AI/history |
| `POST /api/review/generate` | Protected | Bearer session | Compatibility alias |
| `POST /api/study/actions/plan` | Protected | Bearer session | Workspace signals/memory |
| `POST /api/study/plan` | Protected | Bearer session | Compatibility alias |
| `POST /api/study/actions/coaching-plan` | Protected | Bearer session | Workspace signals/memory/vectors |
| `POST /api/study/coaching` | Protected | Bearer session | Compatibility alias |
| `GET /api/memories` | Protected | Bearer session | Workspace list |
| `POST /api/memories` | Protected | Bearer session | Workspace mutation |
| `GET /api/memories/search` | Protected | Bearer session | Workspace memory vectors |
| `GET /api/memories/{memory_id}` | Protected | Bearer session | Workspace-bound ID |
| `PATCH /api/memories/{memory_id}` | Protected | Bearer session | Workspace mutation |
| `POST /api/memories/{memory_id}/archive` | Protected | Bearer session | Workspace mutation |
| `DELETE /api/memories/{memory_id}` | Protected | Bearer session | Workspace mutation |
| `POST /api/memories/proposals/{proposal_id}/decision` | Protected | Bearer session | Workspace workflow |
| `POST /api/memories/consolidation/propose` | Protected | Bearer session | Workspace workflow |
| `POST /api/memories/consolidation/apply` | Protected | Bearer session | Workspace workflow |
| `POST /api/memories/consolidation/{proposal_id}/apply` | Protected | Bearer session | Compatibility alias |
| `GET /api/system/export` | Protected | Bearer session | Must filter every exported row |
| `GET /api/system/integrity` | Protected | Bearer session | Workspace-only diagnostics |
| `GET /api/integrity` | Protected | Bearer session | Compatibility alias |

## Export and integrity design

Cockroach export receives the authenticated `workspace_id` as a server-side
argument. Every owned table query includes that predicate. It excludes:

- `guest_sessions`;
- `migration_runs`;
- `migration_items`;
- other workspaces;
- hashes, credentials, and authorization metadata.

The export may include one safe workspace display record with no authorization
meaning. It must not include guest-session internals.

Public integrity checks are refactored to inspect only the resolved workspace.
Cluster-wide schema/referential checks remain internal operational commands.

## CORS and browser security

Local development allows only:

- `http://localhost:5173`;
- `http://127.0.0.1:5173`.

Add `Authorization` and `Idempotency-Key` to allowed headers. Keep wildcard
origins disabled and `allow_credentials=False` for Bearer localStorage mode.

`FRONTEND_ORIGIN` may add one explicit deployment origin. Production requires
HTTPS for both frontend and API.

A hardened same-site deployment should migrate the bearer token into an
HttpOnly, Secure, SameSite cookie with CSRF review. That is intentionally
outside this Hackathon phase.

## Abuse boundaries

Session creation accepts an empty/small body only, validates a bounded
idempotency header, and creates exactly one workspace/session per transaction.
There is no session enumeration endpoint.

A small instance-wide in-process creation limiter may cap accidental bursts in
local/demo operation without storing IP addresses. It is not a distributed
security control and must not be described as one. A public multi-instance
deployment requires an upstream/durable rate limit before broad exposure.

## Health and diagnostics

Health remains public and may add:

```json
{
  "guest_sessions": {
    "configured": true
  }
}
```

It does not include guest counts, hashes, workspace IDs, session timestamps,
database URLs, keys, or authorization state.

## Environment contract

Placeholders only:

```text
ALLOW_LEGACY_DEFAULT_WORKSPACE=false
GUEST_SESSION_TTL_DAYS=
GUEST_SESSION_TOKEN_PEPPER=
FRONTEND_ORIGIN=
```

Public mode fails safely at startup if the pepper is missing. Cockroach mode
continues to fail rather than falling back to SQLite or Chroma.

## Future production-auth migration

The guest session is an authentication principal with one workspace mapping.
A future account system can:

1. add a production principal/account table;
2. add an account/workspace membership model;
3. claim a guest workspace after explicit proof of the guest token;
4. revoke the guest session after transfer;
5. keep all current workspace-owned records unchanged.

Repository workspace binding and request-scoped dependencies remain reusable.

## Known limitations

- localStorage tokens are readable by same-origin JavaScript and depend on
  strong XSS prevention;
- anonymous sessions have no email/device recovery;
- clearing storage or changing browsers can permanently lose practical access;
- old workspaces are retained when starting a new space;
- a lost creation response cannot safely replay its raw token;
- pepper rotation invalidates sessions without a dedicated rotation plan;
- the MVP limiter is not distributed;
- this system provides workspace isolation, not verified human identity or
  full production authentication.

## Migration and implementation gate

This design requires one additive CockroachDB revision. The revision must be
generated and reviewed next, but must not be applied to the live cluster until
the user explicitly authorizes it.
