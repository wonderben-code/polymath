"""Add browser-bound human publisher sessions without storing bootstrap credentials."""

from alembic import op

revision = "0011_operator_sessions"
down_revision = "0010_artifact_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE operator_sessions (
    token_hash VARCHAR(64) PRIMARY KEY,
    browser_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions (token_hash),
    credential_fingerprint VARCHAR(64) NOT NULL,
    actor_id VARCHAR(100) NOT NULL,
    display_name VARCHAR(120) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    revoked_at TIMESTAMPTZ,
    CONSTRAINT ck_operator_session_hash CHECK (token_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_operator_credential_fingerprint CHECK
        (credential_fingerprint ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_operator_actor_size CHECK (length(actor_id) BETWEEN 1 AND 100),
    CONSTRAINT ck_operator_display_size CHECK (length(display_name) BETWEEN 1 AND 120)
);
CREATE INDEX ix_operator_sessions_browser_session_hash ON operator_sessions (browser_session_hash);
CREATE INDEX ix_operator_sessions_expires_at ON operator_sessions (expires_at);
""")


def downgrade() -> None:
    # Session records may be referenced by immutable human approval receipts in
    # later migrations. Refuse to silently discard an authenticated audit trail.
    op.execute("""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM operator_sessions) THEN
        RAISE EXCEPTION 'Cannot discard saved operator authentication sessions';
    END IF;
END;
$$;
""")
    op.drop_table("operator_sessions")
