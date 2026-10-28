"""Durable public sponsorship, immutable visitor admissions and resource leases."""

from alembic import op

revision = "0023_public_research"
down_revision = "0022_discovery_ladder"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
ALTER TABLE meta_research_approvals ADD CONSTRAINT uq_native_public_delegation_authority
 UNIQUE(id,manifest_sha256,operator_session_hash,browser_session_hash,actor_id,expires_at);
ALTER TABLE meta_research_run_bindings ADD CONSTRAINT uq_native_research_public_binding
 UNIQUE(run_id,approval_id,manifest_sha256,account_id,browser_session_hash);
CREATE TABLE public_research_delegations (
 approval_id UUID PRIMARY KEY, account_id VARCHAR(120) NOT NULL REFERENCES budget_accounts(id),
 manifest_sha256 VARCHAR(64) NOT NULL, authorization_mode VARCHAR(40) NOT NULL,
 sponsor_operator_session_hash VARCHAR(64) NOT NULL,
 sponsor_browser_session_hash VARCHAR(64) NOT NULL,
 sponsor_actor_id VARCHAR(100) NOT NULL, sponsor_policy_sha256 VARCHAR(64) NOT NULL,
 public_policy_sha256 VARCHAR(64) NOT NULL, valid_until TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_public_delegation_binding UNIQUE(approval_id,manifest_sha256,account_id),
 CONSTRAINT ck_public_delegation_mode CHECK(authorization_mode='durable_owner_delegation'),
 CONSTRAINT ck_public_delegation_hashes CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'
  AND sponsor_policy_sha256 ~ '^[0-9a-f]{64}$' AND public_policy_sha256 ~ '^[0-9a-f]{64}$'),
 FOREIGN KEY(approval_id,manifest_sha256,sponsor_operator_session_hash,
  sponsor_browser_session_hash,sponsor_actor_id,valid_until)
 REFERENCES meta_research_approvals(id,manifest_sha256,operator_session_hash,
  browser_session_hash,actor_id,expires_at));
CREATE INDEX ix_public_delegation_account ON public_research_delegations(account_id,created_at);
CREATE TABLE public_research_run_access (
 run_id UUID PRIMARY KEY, approval_id UUID NOT NULL, manifest_sha256 VARCHAR(64) NOT NULL,
 account_id VARCHAR(120) NOT NULL, requester_session_hash VARCHAR(64) NOT NULL,
 network_bucket VARCHAR(64) NOT NULL, public_policy_sha256 VARCHAR(64) NOT NULL,
 admitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT ck_public_run_access_hashes CHECK(network_bucket ~ '^[0-9a-f]{64}$'
  AND public_policy_sha256 ~ '^[0-9a-f]{64}$'),
 FOREIGN KEY(run_id,approval_id,manifest_sha256,account_id,requester_session_hash)
 REFERENCES meta_research_run_bindings
 (run_id,approval_id,manifest_sha256,account_id,browser_session_hash),
 FOREIGN KEY(approval_id,manifest_sha256,account_id)
 REFERENCES public_research_delegations(approval_id,manifest_sha256,account_id));
CREATE INDEX ix_public_access_account_admitted
 ON public_research_run_access(account_id,admitted_at);
CREATE INDEX ix_public_access_requester_admitted
 ON public_research_run_access(account_id,requester_session_hash,admitted_at);
CREATE INDEX ix_public_access_network ON public_research_run_access(account_id,network_bucket);
CREATE TABLE public_rate_buckets (
 bucket_kind VARCHAR(64) NOT NULL, subject_sha256 VARCHAR(64) NOT NULL,
 window_start TIMESTAMPTZ NOT NULL, count BIGINT NOT NULL DEFAULT 0,
 expires_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(bucket_kind,subject_sha256,window_start),
 CONSTRAINT ck_public_rate_count CHECK(count>=0));
CREATE INDEX ix_public_rate_expiry ON public_rate_buckets(expires_at);
CREATE TABLE public_stream_leases (
 id UUID PRIMARY KEY, owner_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions(token_hash),
 network_bucket VARCHAR(64) NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 expires_at TIMESTAMPTZ NOT NULL);
CREATE INDEX ix_public_stream_expiry ON public_stream_leases(expires_at);
CREATE INDEX ix_public_stream_owner ON public_stream_leases(owner_hash,expires_at);
CREATE INDEX ix_public_stream_network ON public_stream_leases(network_bucket,expires_at);
""")
    for table in ("public_research_delegations", "public_research_run_access"):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence()"
        )


def downgrade():
    op.execute("""DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM public_research_delegations) OR
    EXISTS(SELECT 1 FROM public_research_run_access) THEN
  RAISE EXCEPTION 'Cannot discard public research sponsorship or admission history';
 END IF; END; $$;""")
    for table in (
        "public_stream_leases",
        "public_rate_buckets",
        "public_research_run_access",
        "public_research_delegations",
    ):
        op.drop_table(table)
    op.drop_constraint(
        "uq_native_research_public_binding", "meta_research_run_bindings", type_="unique"
    )
    op.drop_constraint(
        "uq_native_public_delegation_authority", "meta_research_approvals", type_="unique"
    )
