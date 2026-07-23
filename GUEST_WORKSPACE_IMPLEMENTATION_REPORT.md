# Guest Workspace Implementation Report

Date: 2026-07-23  
Overall result: PASS  
Live revision: `0003_guest_sessions`

## Migration and integrity gate

The authorized additive revision `0003_guest_sessions` was applied only after
the reviewed preflight matched the migration plan and the legacy/default
workspace baseline matched:

`fc41c2aef689c80f4e346a35733b38b29d817db7abe3c44983c10f69716eba56`

Immediate post-migration verification passed:

- the Alembic revision was exactly `0003_guest_sessions`;
- `guest_sessions` existed and initially contained 0 rows;
- all 12 reviewed columns and nullability rules matched;
- the UUID primary key, workspace foreign key, both unique constraints, all
  reviewed checks, and both reviewed indexes existed;
- the workspace foreign key used restrictive deletion behavior;
- both pre-existing vector indexes were unchanged;
- no other permanent object was introduced;
- every legacy/default workspace count and the baseline fingerprint were
  unchanged.

No downgrade, destructive DDL, SQLite/Chroma migration, or existing-row
modification was performed.

## Implemented security boundary

The public identity chain is:

```text
opaque bearer credential
  -> domain-separated HMAC-SHA256 digest
  -> active guest_sessions row
  -> server-owned workspace
  -> request-scoped ApplicationDependencies
  -> workspace-bound relational and vector repositories
```

Implemented behavior:

- 256-bit opaque token generation with a non-secret version prefix;
- a private HMAC pepper of at least 32 bytes;
- no raw token persistence and no digest exposure through public APIs;
- atomic workspace plus session creation inside a retry-safe UnitOfWork;
- `Idempotency-Key` validation, uniqueness, and conflict handling;
- `POST /api/guest-session` for public bootstrap;
- authenticated `GET /api/guest-session` for safe inspection;
- structured required, invalid, expired, revoked, conflict, creation-failed,
  and workspace-denied errors;
- bearer credentials accepted only through the `Authorization` header;
- rejection of token query parameters and workspace query/header overrides;
- extra request fields such as `workspace_id` rejected by strict schemas;
- all existing user-data route families protected in public mode;
- health and guest-session creation remain public and contain no private data;
- explicit local compatibility mode through
  `ALLOW_LEGACY_DEFAULT_WORKSPACE=true`, disabled by default;
- CORS support for `Authorization` and `Idempotency-Key` from approved origins.

## Persistence and vector isolation

`ApplicationDependencies` now carries a guest-session repository and can be
bound through a request-local context. Each authenticated request constructs
the complete repository bundle with the session's server-resolved workspace.

Both SQLite compatibility and CockroachDB adapters implement the guest-session
protocol. CockroachDB document and learner-memory vector joins now verify
workspace ownership on both sides of the relationship in addition to filtering
the vector table.

CockroachDB export and integrity reporting are workspace-scoped. Export omits
workspace identifiers, session authorization state, credentials, and digests.
The browser downloads the export through an authenticated request rather than
an unauthenticated link.

## Frontend behavior

Before rendering user-data routes, the React application:

1. reads the private guest credential from `localStorage`;
2. validates it with the Agentbook backend;
3. retains it during temporary network failures;
4. clears it only after a definitive invalid, expired, or revoked response;
5. offers **Continue as Guest** when no usable credential exists.

The typed API client adds the bearer credential only to relative Agentbook API
paths. Absolute external URLs never receive it, and the public creation request
does not send an old credential.

**Start a new study space** creates and switches to a fresh private workspace.
It does not delete or modify the previous workspace.

## Live two-guest proof

The authorized live proof created Guest A and Guest B with separate workspaces
and safe synthetic test material. It verified:

- distinct private workspaces and credentials;
- both sessions resolve after dependency and database-engine recreation;
- each guest lists only its own relational records;
- guessed public document and memory IDs return no cross-workspace record;
- document vector search returns only the authenticated workspace's chunk;
- learner-memory vector search returns only the authenticated workspace's
  memory;
- query and body workspace tampering is denied;
- missing credentials cannot access data routes;
- guessed legacy public IDs do not expose the legacy/default workspace;
- stored session values are digests rather than raw credentials;
- the legacy/default workspace counts are identical before and after the proof.

The final schema verifier reports 2 guest-session rows, corresponding only to
the two authorized live proof sessions. No raw credential or digest was
recorded.

## Verification

Passed:

- Python `compileall`;
- complete backend suite;
- guest-session repository/API/token tests;
- two-user relation isolation and workspace-tampering tests;
- opt-in live CockroachDB guest-isolation proof;
- existing Agentic Learning Loop tests as part of the complete suite;
- frontend Vitest suite;
- frontend TypeScript and Vite production build;
- live `0003_guest_sessions` migration/schema verification;
- live vector-index definition and workspace-filtered query verification;
- credential/token leakage scan;
- `git diff --check`.

## Remaining operational limits

- The creation limiter is per API process. A multi-instance deployment needs
  an upstream distributed rate limiter.
- Browser `localStorage` is appropriate for this anonymous Hackathon MVP but is
  not a replacement for hardened account authentication; origin-level script
  compromise can access it.
- There is no recovery flow after the user clears browser storage.
- Session rotation, revocation UI, accounts, roles, teams, AWS, and MCP remain
  outside Phase 5.
- The legacy source-coupled importer verifier safely reports that the current
  local SQLite/Chroma snapshot has no matching completed migration run. The
  source has changed since the historical one-time import; Phase 5 deliberately
  did not rerun that migration. This does not invalidate the separately passed
  live `0003` schema, legacy-workspace count, or vector-index checks.

Phase 6 MCP work is safe to begin only if it preserves this authenticated,
server-derived workspace boundary and never forwards the guest credential to
an MCP server.
