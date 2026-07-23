# Guest Workspace CockroachDB Migration Plan

Date: 2026-07-23  
Status: APPLIED AND VERIFIED  
Proposed revision: `0003_guest_sessions`  
Current live revision verified: `0003_guest_sessions`

## Purpose

Add the credential-to-workspace mapping required for anonymous Guest Workspace
isolation without modifying any existing workspace or application record.

The proposal is additive:

- one new `guest_sessions` table;
- two indexes on that new table;
- the normal Alembic revision metadata update.

No application behavior has been changed to depend on this table yet.

## Files

- Proposed revision:
  `alembic/versions/0003_guest_sessions.py`
- Security and request architecture:
  `GUEST_WORKSPACE_DESIGN.md`
- Source-code audit:
  `GUEST_WORKSPACE_AUDIT.md`

## Revision chain

```text
0001_agentbook_cockroach_schema
  -> 0002_cockroach_vector_indexes
  -> 0003_guest_sessions
```

`alembic heads` reports exactly one proposed head:

```text
0003_guest_sessions (head)
```

## Complete generated upgrade SQL

The following was generated offline from the proposed revision for the range
`0002_cockroach_vector_indexes:0003_guest_sessions`.

```sql
CREATE TABLE guest_sessions (
    id UUID PRIMARY KEY,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
    token_hash STRING NOT NULL,
    creation_key_hash STRING NOT NULL,
    status STRING NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    version INT8 NOT NULL DEFAULT 1,
    session_label STRING,
    CONSTRAINT uq_guest_sessions_token_hash UNIQUE (token_hash),
    CONSTRAINT uq_guest_sessions_creation_key_hash UNIQUE (creation_key_hash),
    CONSTRAINT ck_guest_sessions_token_hash_length
        CHECK (length(token_hash) = 64),
    CONSTRAINT ck_guest_sessions_creation_hash_length
        CHECK (length(creation_key_hash) = 64),
    CONSTRAINT ck_guest_sessions_status
        CHECK (status IN ('active', 'revoked', 'expired')),
    CONSTRAINT ck_guest_sessions_version
        CHECK (version > 0),
    CONSTRAINT ck_guest_sessions_updated_at
        CHECK (updated_at >= created_at),
    CONSTRAINT ck_guest_sessions_last_seen_at
        CHECK (last_seen_at IS NULL OR last_seen_at >= created_at),
    CONSTRAINT ck_guest_sessions_expires_at
        CHECK (expires_at IS NULL OR expires_at > created_at),
    CONSTRAINT ck_guest_sessions_revocation
        CHECK (
            (status = 'revoked' AND revoked_at IS NOT NULL)
            OR
            (status IN ('active', 'expired') AND revoked_at IS NULL)
        ),
    CONSTRAINT ck_guest_sessions_revoked_at
        CHECK (revoked_at IS NULL OR revoked_at >= created_at),
    CONSTRAINT ck_guest_sessions_label
        CHECK (
            session_label IS NULL
            OR (
                length(trim(session_label)) > 0
                AND length(session_label) <= 120
            )
        )
);

CREATE INDEX idx_guest_sessions_workspace_status
ON guest_sessions (workspace_id, status, created_at DESC);

CREATE INDEX idx_guest_sessions_active_expiry
ON guest_sessions (expires_at)
WHERE status = 'active' AND expires_at IS NOT NULL;

UPDATE alembic_version
SET version_num = '0003_guest_sessions'
WHERE version_num = '0002_cockroach_vector_indexes';
```

The final `UPDATE` changes only Alembic's own revision marker. It does not
modify an Agentbook application row.

## Schema review

### Objects created

- `guest_sessions`;
- `uq_guest_sessions_token_hash`;
- `uq_guest_sessions_creation_key_hash`;
- guest-session check constraints;
- `idx_guest_sessions_workspace_status`;
- `idx_guest_sessions_active_expiry`.

### Existing objects referenced but not changed

- `workspaces(id)` is referenced by a foreign key with deletion restricted.
- `alembic_version` receives the standard revision-marker update.

### Prohibited operations review

The upgrade contains:

- no `DROP`;
- no `TRUNCATE`;
- no `ALTER`;
- no `DELETE FROM`;
- no update to an application table;
- no insert into an existing application table;
- no migration, reassignment, or deletion of an existing row;
- no vector-index modification;
- no SQLite or Chroma operation.

`ON DELETE RESTRICT` is a foreign-key protection rule; it does not delete data.

The revision is deliberately forward-only. Its `downgrade()` refuses to run
instead of deleting the session table.

## Live non-destructive parser verification

The configured live CockroachDB cluster was checked without executing DDL.

Verified results:

- live Alembic revision was `0002_cockroach_vector_indexes`;
- `guest_sessions` was absent;
- `CREATE TABLE guest_sessions` was accepted through `SHOW SYNTAX`;
- the workspace/status index was accepted through `SHOW SYNTAX`;
- the partial active-expiry index was accepted through `SHOW SYNTAX`;
- permanent catalog fingerprint was identical before and after the checks.

No table, index, constraint, or row was created, altered, or deleted.

## Legacy/default workspace pre-migration baseline

The current legacy workspace was read without displaying row content. BYTES
values, document content, JSON payloads, and vectors contributed to a local
SHA-256 calculation but were not printed or copied into this report.

Combined legacy-workspace baseline:

```text
fc41c2aef689c80f4e346a35733b38b29d817db7abe3c44983c10f69716eba56
```

Counts:

| Data set | Count |
|---|---:|
| workspace record | 1 |
| notebooks | 1 |
| notebook assignments | 1 |
| documents | 2 |
| document blobs | 2 |
| document chunks/vectors | 23 |
| cached intelligence | 0 |
| topics | 0 |
| topic sources | 0 |
| study sessions | 2 |
| study interactions | 2 |
| study interaction sources | 5 |
| Quiz attempts | 5 |
| Quiz question attempts | 9 |
| Quiz question sources | 9 |
| learner memories | 7 |
| learner-memory embeddings | 7 |
| memory relationships | 0 |
| Learning Signals | 7 |
| workflow states | 20 |
| AdaptationEvents | 31 |
| embedding jobs | 8 |

The same workspace-filtered fingerprint procedure will run after the revision
and after implementation. Guest data is excluded from this baseline.

## Executed application procedure

1. Reconfirm repository-root configuration without displaying secrets.
2. Re-read the live Alembic revision and confirmed `guest_sessions` was
   absent.
3. Recompute the legacy/default workspace baseline.
4. Run the sanitized Cockroach migration preflight.
5. Applied only target revision `0003_guest_sessions`.
6. Verify:
   - revision is exactly `0003_guest_sessions`;
   - table columns and nullability;
   - primary, unique, foreign-key, and check constraints;
   - both proposed indexes;
   - table contains zero rows immediately after migration;
   - no other new permanent object exists.
7. Recompute the legacy/default workspace fingerprint and counts.
8. Stop if any legacy value differs.
9. Only after schema verification, implement the repository, authentication
   dependency, protected routes, scoped export/integrity, and frontend
   bootstrap.

The original SQLite/Chroma migration will not be rerun.

## Non-transactional DDL safety

The installed Cockroach Alembic dialect reports non-transactional DDL during
offline generation. Therefore:

- application code must not use the new subsystem until all three DDL
  statements and verification pass;
- a partial DDL failure must stop the process immediately;
- no automatic cleanup or destructive rollback is permitted;
- catalog state must be inspected before any retry;
- any corrective permanent DDL requires separate review and authorization.

The current live parser accepted all three statements, reducing but not
eliminating this operational risk.

## Verified post-migration state

Immediately after the authorized revision:

- `guest_sessions` exists and is empty;
- existing `workspaces` rows are unchanged;
- all document, Quiz, memory, signal, workflow, adaptation, embedding, and
  vector rows are unchanged;
- both existing cosine vector indexes are unchanged;
- runtime remains CockroachDB;
- no SQLite or Chroma adapter is used by the migration.

## Authorization result

The user explicitly authorized this additive revision and it was applied
successfully. That authorization did not cover destructive rollback, deletion
of any workspace, or unrelated schema changes; none of those actions occurred.
