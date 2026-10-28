"""Preserve exact human-reviewed publication manifests and approval history."""

from alembic import op

revision = "0012_publication_approval"
down_revision = "0011_operator_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE publication_manifests (
    id UUID PRIMARY KEY,
    owner_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions (token_hash),
    artifact_id UUID NOT NULL REFERENCES generated_artifacts (id),
    manifest JSONB NOT NULL,
    manifest_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_publication_manifest UNIQUE (owner_session_hash, manifest_sha256),
    CONSTRAINT ck_publication_manifest_hash CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_publication_manifest_object CHECK (jsonb_typeof(manifest) = 'object')
);
CREATE TABLE publication_approvals (
    id UUID PRIMARY KEY,
    manifest_id UUID NOT NULL REFERENCES publication_manifests (id),
    manifest_sha256 VARCHAR(64) NOT NULL,
    operator_session_hash VARCHAR(64) NOT NULL REFERENCES operator_sessions (token_hash),
    actor_id VARCHAR(100) NOT NULL,
    approved_at TIMESTAMPTZ NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT ck_publication_approval_hash CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_publication_approval_expiry CHECK (expires_at > approved_at),
    CONSTRAINT ck_publication_approval_lifetime CHECK
        (expires_at <= approved_at + interval '1 hour')
);
CREATE TABLE publication_revocations (
    approval_id UUID PRIMARY KEY REFERENCES publication_approvals (id),
    actor_id VARCHAR(100) NOT NULL,
    revoked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_publication_manifests_owner_created_at
ON publication_manifests (owner_session_hash, created_at);
CREATE INDEX ix_publication_approvals_manifest_approved_at
ON publication_approvals (manifest_id, approved_at);
CREATE TRIGGER immutable_publication_manifest BEFORE UPDATE OR DELETE ON publication_manifests
FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
CREATE TRIGGER immutable_publication_approval BEFORE UPDATE OR DELETE ON publication_approvals
FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
CREATE TRIGGER immutable_publication_revocation BEFORE UPDATE OR DELETE ON publication_revocations
FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
""")


def downgrade() -> None:
    op.execute("""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM publication_manifests) OR
       EXISTS (SELECT 1 FROM publication_approvals) OR
       EXISTS (SELECT 1 FROM publication_revocations) THEN
        RAISE EXCEPTION 'Cannot discard publication review and approval history';
    END IF;
END;
$$;
""")
    op.drop_table("publication_revocations")
    op.drop_table("publication_approvals")
    op.drop_table("publication_manifests")
