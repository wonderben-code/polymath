"""Owner-browser native research activation on the existing monetary ledger."""

from alembic import op

revision = "0021_meta_research"
down_revision = "0020_meta_verification"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE budget_accounts DROP CONSTRAINT ck_budget_accounts_native_scope;
ALTER TABLE budget_accounts ADD CONSTRAINT ck_budget_accounts_native_scope
 CHECK(native_scope IS NULL OR native_scope IN ('probe_only','research'));
CREATE TABLE meta_research_manifests (
 id UUID PRIMARY KEY, readiness_id UUID NOT NULL REFERENCES meta_readiness_proposals(id),
 canary_receipt_id UUID NOT NULL REFERENCES meta_probe_receipts(id),
 account_id VARCHAR(120) NOT NULL REFERENCES budget_accounts(id),
 manifest_sha256 VARCHAR(64) NOT NULL UNIQUE, manifest JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX ix_meta_research_manifests_account_created
 ON meta_research_manifests(account_id,created_at);
CREATE TABLE meta_research_approvals (
 id UUID PRIMARY KEY, manifest_id UUID NOT NULL UNIQUE REFERENCES meta_research_manifests(id),
 manifest_sha256 VARCHAR(64) NOT NULL,
 operator_session_hash VARCHAR(64) NOT NULL REFERENCES operator_sessions(token_hash),
 browser_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions(token_hash),
 actor_id VARCHAR(100) NOT NULL, research_policy_sha256 VARCHAR(64) NOT NULL,
 armed_epoch BIGINT NOT NULL, approved_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL);
CREATE TABLE meta_research_revocations (
 approval_id UUID PRIMARY KEY REFERENCES meta_research_approvals(id),
 actor_id VARCHAR(100) NOT NULL, revoked_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE meta_research_run_bindings (
 run_id UUID PRIMARY KEY REFERENCES research_runs(id),
 approval_id UUID NOT NULL REFERENCES meta_research_approvals(id),
 manifest_id UUID NOT NULL REFERENCES meta_research_manifests(id),
 manifest_sha256 VARCHAR(64) NOT NULL,
 account_id VARCHAR(120) NOT NULL REFERENCES budget_accounts(id),
 browser_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions(token_hash),
 armed_epoch BIGINT NOT NULL, task_kind VARCHAR(32) NOT NULL, mode VARCHAR(32) NOT NULL,
 input_sha256 VARCHAR(64) NOT NULL, profile JSONB NOT NULL, profile_sha256 VARCHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE meta_research_call_receipts (
 run_id UUID NOT NULL REFERENCES meta_research_run_bindings(run_id),
 request_id VARCHAR(128) NOT NULL,
 reservation_id UUID NOT NULL UNIQUE REFERENCES budget_reservations(id),
 manifest_sha256 VARCHAR(64) NOT NULL, request_sha256 VARCHAR(64) NOT NULL,
 observation_sha256 VARCHAR(64) NOT NULL, payload JSONB NOT NULL,
 payload_sha256 VARCHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(run_id,request_id));
CREATE TABLE meta_research_call_requests (
 run_id UUID NOT NULL REFERENCES meta_research_run_bindings(run_id),
 request_id VARCHAR(128) NOT NULL,
 reservation_id UUID NOT NULL UNIQUE REFERENCES budget_reservations(id), request JSONB NOT NULL,
 request_sha256 VARCHAR(64) NOT NULL, step_sha256 VARCHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(run_id,request_id));
""")
    for table in (
        "meta_research_manifests",
        "meta_research_approvals",
        "meta_research_revocations",
        "meta_research_run_bindings",
        "meta_research_call_receipts",
        "meta_research_call_requests",
    ):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence()"
        )


def downgrade():
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM meta_research_manifests) OR
    EXISTS(SELECT 1 FROM budget_accounts WHERE native_scope='research') THEN
  RAISE EXCEPTION 'Cannot discard native research authority or history';
 END IF;
END; $$;
""")
    for table in (
        "meta_research_call_requests",
        "meta_research_call_receipts",
        "meta_research_run_bindings",
        "meta_research_revocations",
        "meta_research_approvals",
        "meta_research_manifests",
    ):
        op.drop_table(table)
    op.execute("""
ALTER TABLE budget_accounts DROP CONSTRAINT ck_budget_accounts_native_scope;
ALTER TABLE budget_accounts ADD CONSTRAINT ck_budget_accounts_native_scope
 CHECK(native_scope IS NULL OR native_scope='probe_only');
""")
