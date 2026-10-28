"""One-call native verification consent and immutable accounting receipts."""

from alembic import op

revision = "0020_meta_verification"
down_revision = "0019_meta_readiness"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE budget_accounts ADD COLUMN native_scope VARCHAR(24),
 ADD COLUMN native_manifest_sha256 VARCHAR(64),
 ADD COLUMN native_control_epoch BIGINT NOT NULL DEFAULT 0,
 ADD CONSTRAINT ck_budget_accounts_native_epoch CHECK(native_control_epoch >= 0),
 ADD CONSTRAINT ck_budget_accounts_native_scope
 CHECK(native_scope IS NULL OR native_scope='probe_only');
ALTER TABLE research_runs DROP CONSTRAINT ck_research_runs_task_kind;
ALTER TABLE research_runs ADD CONSTRAINT ck_research_runs_task_kind CHECK
 (task_kind IN ('research','formalise','write_summary','write_paper',
 'ingest_literature','reassess_finding','verify_meta'));
CREATE TABLE meta_probe_manifests (
 id UUID PRIMARY KEY, readiness_id UUID NOT NULL REFERENCES meta_readiness_proposals(id),
 account_id VARCHAR(120) NOT NULL REFERENCES budget_accounts(id),
 manifest_sha256 VARCHAR(64) NOT NULL UNIQUE, manifest JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE INDEX ix_meta_probe_manifests_account_created ON meta_probe_manifests(account_id,created_at);
CREATE TABLE meta_probe_approvals (
 id UUID PRIMARY KEY, manifest_id UUID NOT NULL UNIQUE REFERENCES meta_probe_manifests(id),
 manifest_sha256 VARCHAR(64) NOT NULL,
 operator_session_hash VARCHAR(64) NOT NULL REFERENCES operator_sessions(token_hash),
 browser_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions(token_hash),
 actor_id VARCHAR(100) NOT NULL, probe_policy_sha256 VARCHAR(64) NOT NULL,
 armed_epoch BIGINT NOT NULL, approved_at TIMESTAMPTZ NOT NULL, expires_at TIMESTAMPTZ NOT NULL);
CREATE TABLE meta_probe_requests (
 run_id UUID PRIMARY KEY REFERENCES research_runs(id),
 manifest_id UUID NOT NULL UNIQUE REFERENCES meta_probe_manifests(id),
 approval_id UUID NOT NULL UNIQUE REFERENCES meta_probe_approvals(id),
 account_id VARCHAR(120) NOT NULL REFERENCES budget_accounts(id),
 manifest_sha256 VARCHAR(64) NOT NULL, request_sha256 VARCHAR(64) NOT NULL,
 armed_epoch BIGINT NOT NULL);
CREATE TABLE meta_probe_revocations (
 approval_id UUID PRIMARY KEY REFERENCES meta_probe_approvals(id),
 actor_id VARCHAR(100) NOT NULL, revoked_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE meta_native_identity_bindings (
 kind VARCHAR(24) NOT NULL, binding_sha256 VARCHAR(64) NOT NULL,
 account_id VARCHAR(120) NOT NULL REFERENCES budget_accounts(id), actor_id VARCHAR(100) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY(kind,binding_sha256));
CREATE TABLE meta_probe_receipts (
 id UUID PRIMARY KEY, run_id UUID NOT NULL UNIQUE REFERENCES meta_probe_requests(run_id),
 manifest_sha256 VARCHAR(64) NOT NULL, observation_sha256 VARCHAR(64) NOT NULL,
 payload JSONB NOT NULL, payload_sha256 VARCHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now());
""")
    for table in (
        "meta_probe_manifests",
        "meta_probe_approvals",
        "meta_probe_requests",
        "meta_probe_revocations",
        "meta_native_identity_bindings",
        "meta_probe_receipts",
    ):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence()"
        )


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM meta_probe_manifests) OR
    EXISTS(SELECT 1 FROM meta_native_identity_bindings) OR
    EXISTS(SELECT 1 FROM research_runs WHERE task_kind='verify_meta') OR
    EXISTS(SELECT 1 FROM budget_accounts
      WHERE native_scope IS NOT NULL OR native_control_epoch<>0) THEN
  RAISE EXCEPTION 'Cannot discard native verification authority or history';
 END IF;
END; $$;
""")
    for table in (
        "meta_probe_receipts",
        "meta_probe_revocations",
        "meta_probe_requests",
        "meta_probe_approvals",
        "meta_native_identity_bindings",
        "meta_probe_manifests",
    ):
        op.drop_table(table)
    op.execute("""
ALTER TABLE research_runs DROP CONSTRAINT ck_research_runs_task_kind;
ALTER TABLE research_runs ADD CONSTRAINT ck_research_runs_task_kind CHECK
 (task_kind IN ('research','formalise','write_summary','write_paper',
 'ingest_literature','reassess_finding'));
ALTER TABLE budget_accounts DROP CONSTRAINT ck_budget_accounts_native_scope,
 DROP CONSTRAINT ck_budget_accounts_native_epoch,
 DROP COLUMN native_control_epoch, DROP COLUMN native_manifest_sha256, DROP COLUMN native_scope;
""")
