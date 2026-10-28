"""Immutable opt-in public finding projections and revocations."""

from alembic import op

revision = "0018_public_sharing"
down_revision = "0017_arxiv_full_text"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE shared_findings (
 id UUID PRIMARY KEY,
 public_id VARCHAR(43) NOT NULL,
 owner_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions(token_hash),
 source_result_id UUID NOT NULL,
 source_run_id UUID NOT NULL,
 root_result_id UUID NOT NULL REFERENCES evidence_results(id),
 source_sha256 VARCHAR(64) NOT NULL,
 protocol VARCHAR(40) NOT NULL,
 projection JSONB NOT NULL,
 projection_sha256 VARCHAR(64) NOT NULL,
 idempotency_key UUID NOT NULL,
 request_sha256 VARCHAR(64) NOT NULL,
 expires_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(source_result_id, source_run_id) REFERENCES evidence_results(id,run_id),
 CONSTRAINT uq_shared_finding_public_id UNIQUE(public_id),
 CONSTRAINT uq_share_owner_request UNIQUE(owner_session_hash,idempotency_key),
 CONSTRAINT ck_share_public_id CHECK(public_id ~ '^[A-Za-z0-9_-]{43}$'),
 CONSTRAINT ck_share_source_hash CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_share_projection_hash CHECK(projection_sha256 ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_share_request_hash CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_share_protocol CHECK(protocol = 'polymath-public-finding-v1'),
 CONSTRAINT ck_share_projection_size CHECK
  (jsonb_typeof(projection) = 'object' AND octet_length(projection::text) <= 300000),
 CONSTRAINT ck_share_expiry CHECK(expires_at > created_at)
);
CREATE INDEX ix_shared_findings_owner_created
 ON shared_findings(owner_session_hash,created_at,id);
CREATE INDEX ix_shared_findings_expiry ON shared_findings(expires_at);
CREATE INDEX ix_shared_findings_source ON shared_findings(source_result_id);
CREATE TABLE shared_finding_revocations (
 share_id UUID PRIMARY KEY REFERENCES shared_findings(id),
 owner_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions(token_hash),
 revoked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TRIGGER immutable_shared_finding BEFORE UPDATE OR DELETE ON shared_findings
 FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
CREATE TRIGGER immutable_share_revocation BEFORE UPDATE OR DELETE ON shared_finding_revocations
 FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM shared_findings) OR EXISTS(SELECT 1 FROM shared_finding_revocations) THEN
  RAISE EXCEPTION 'Cannot discard public sharing history';
 END IF;
END; $$;
""")
    op.drop_table("shared_finding_revocations")
    op.drop_table("shared_findings")
