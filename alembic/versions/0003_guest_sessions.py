"""Add opaque anonymous guest-session mappings."""

from alembic import op


revision = "0003_guest_sessions"
down_revision = "0002_cockroach_vector_indexes"
branch_labels = None
depends_on = None


UPGRADE_STATEMENTS = (
    """
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
    )
    """,
    """
    CREATE INDEX idx_guest_sessions_workspace_status
    ON guest_sessions (workspace_id, status, created_at DESC)
    """,
    """
    CREATE INDEX idx_guest_sessions_active_expiry
    ON guest_sessions (expires_at)
    WHERE status = 'active' AND expires_at IS NOT NULL
    """,
)


def upgrade() -> None:
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    raise RuntimeError(
        "Revision 0003_guest_sessions is forward-only to protect live study data."
    )
