"""Preparation-only Meta configuration and budget administration audit."""

from alembic import op

revision = "0019_meta_readiness"
down_revision = "0018_public_sharing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE meta_readiness_proposals (
 id UUID PRIMARY KEY,
 account_id VARCHAR(120) NOT NULL REFERENCES budget_accounts(id),
 proposal_sha256 VARCHAR(64) NOT NULL,
 proposal JSONB NOT NULL,
 quote JSONB NOT NULL,
 quote_sha256 VARCHAR(64) NOT NULL,
 operator_session_hash VARCHAR(64) NOT NULL REFERENCES operator_sessions(token_hash),
 browser_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions(token_hash),
 actor_id VARCHAR(100) NOT NULL,
 native_status VARCHAR(16) NOT NULL DEFAULT 'not_run',
 execution_available BOOLEAN NOT NULL DEFAULT false,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_readiness_account_proposal UNIQUE(account_id,proposal_sha256),
 CONSTRAINT ck_readiness_proposal_hash CHECK(proposal_sha256 ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_readiness_quote_hash CHECK(quote_sha256 ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_readiness_proposal_size CHECK
  (jsonb_typeof(proposal) = 'object' AND octet_length(proposal::text) <= 262144),
 CONSTRAINT ck_readiness_quote_size CHECK
  (jsonb_typeof(quote) = 'object' AND octet_length(quote::text) <= 262144),
 CONSTRAINT ck_readiness_native_not_run CHECK(native_status = 'not_run'),
 CONSTRAINT ck_readiness_no_execution CHECK(NOT execution_available)
);
CREATE INDEX ix_readiness_proposals_account_created
 ON meta_readiness_proposals(account_id,created_at,id);
CREATE TABLE meta_readiness_audits (
 id UUID PRIMARY KEY,
 account_id VARCHAR(120) NOT NULL REFERENCES budget_accounts(id),
 proposal_id UUID REFERENCES meta_readiness_proposals(id),
 action VARCHAR(32) NOT NULL,
 operator_session_hash VARCHAR(64) NOT NULL REFERENCES operator_sessions(token_hash),
 browser_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions(token_hash),
 actor_id VARCHAR(100) NOT NULL,
 detail JSONB NOT NULL,
 detail_sha256 VARCHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT ck_readiness_audit_action CHECK(action IN ('proposal_prepared','safe_disabled')),
 CONSTRAINT ck_readiness_audit_hash CHECK(detail_sha256 ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_readiness_audit_size CHECK
  (jsonb_typeof(detail) = 'object' AND octet_length(detail::text) <= 16384)
);
CREATE INDEX ix_readiness_audits_account_created
 ON meta_readiness_audits(account_id,created_at,id);
CREATE TRIGGER immutable_meta_readiness_proposal BEFORE UPDATE OR DELETE ON meta_readiness_proposals
 FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
CREATE TRIGGER immutable_meta_readiness_audit BEFORE UPDATE OR DELETE ON meta_readiness_audits
 FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM meta_readiness_proposals) OR
    EXISTS(SELECT 1 FROM meta_readiness_audits) THEN
  RAISE EXCEPTION 'Cannot discard Meta readiness administration history';
 END IF;
END; $$;
""")
    op.drop_table("meta_readiness_audits")
    op.drop_table("meta_readiness_proposals")
