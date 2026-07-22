# CockroachDB Cloud Preflight Report

Date: 2026-07-22
Scope: non-destructive live-cluster readiness preflight
Overall result: PASS

## Verified results

- Repository-root `.env` loaded successfully.
- `DATABASE_URL` was available but never displayed.
- `PERSISTENCE_BACKEND=sqlite` was confirmed.
- TLS `verify-full` connection passed.
- Live version was CockroachDB CCL v26.2.1.
- `VECTOR(3)` cast passed.
- Fixed vector-dimension rejection passed.
- `VECTOR(384)` passed.
- Vector-index feature was enabled.
- Cosine vector-index syntax was accepted through `SHOW SYNTAX`.
- SQLAlchemy `cockroachdb+psycopg` connection passed.
- Alembic `MigrationContext` connection passed.
- Database `CONNECT` permission passed.
- Schema `USAGE` permission passed.
- Schema `CREATE` permission passed.
- Permanent catalog fingerprint was unchanged.
- No permanent table or index was created, altered, or deleted.
- No Alembic migration was executed.
- No data migration was executed.
- No credentials were recorded.
