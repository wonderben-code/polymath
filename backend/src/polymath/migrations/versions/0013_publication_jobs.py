"""Durable publication grants, recovery state, and immutable remote audits."""

from alembic import op

revision = "0013_publication_jobs"
down_revision = "0012_publication_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE publication_jobs (
    id UUID PRIMARY KEY,
    owner_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions (token_hash),
    manifest_id UUID NOT NULL REFERENCES publication_manifests (id),
    approval_id UUID NOT NULL REFERENCES publication_approvals (id),
    manifest_sha256 VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    work_kind VARCHAR(16) NOT NULL DEFAULT 'publish',
    lease_token BIGINT NOT NULL DEFAULT 0,
    lease_owner VARCHAR(128),
    lease_expires_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    remote_deposit_id VARCHAR(20),
    outcome JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason VARCHAR(100),
    stage VARCHAR(16) NOT NULL DEFAULT 'queued',
    event_sequence BIGINT NOT NULL DEFAULT 0,
    frozen BOOLEAN NOT NULL DEFAULT false,
    publication_reported BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_publication_job_manifest UNIQUE (manifest_id),
    CONSTRAINT ck_publication_job_status CHECK
        (status IN ('queued','running','interrupted','published','failed','cancelled')),
    CONSTRAINT ck_publication_job_kind CHECK (work_kind IN ('publish','reconcile')),
    CONSTRAINT ck_publication_job_hash CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_publication_job_lease_token CHECK (lease_token >= 0),
    CONSTRAINT ck_publication_job_event_sequence CHECK (event_sequence >= 0),
    CONSTRAINT ck_publication_job_stage CHECK
        (stage IN ('queued','create','upload','verify','publish','reconcile','complete')),
    CONSTRAINT ck_publication_job_outcome_size CHECK
        (octet_length(outcome::text) <= 524288 AND jsonb_typeof(outcome) = 'object')
);
CREATE INDEX ix_publication_jobs_status_created_at ON publication_jobs (status, created_at);
CREATE INDEX ix_publication_jobs_owner_created_at
ON publication_jobs (owner_session_hash, created_at);

CREATE TABLE publication_operations (
    id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES publication_jobs (id),
    stage VARCHAR(16) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'pending',
    request_hash VARCHAR(64) NOT NULL,
    lease_token BIGINT NOT NULL,
    approval_id UUID NOT NULL REFERENCES publication_approvals (id),
    manifest_sha256 VARCHAR(64) NOT NULL,
    observation JSONB,
    reason VARCHAR(100),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_publication_operation_stage UNIQUE (job_id, stage),
    CONSTRAINT ck_publication_operation_stage CHECK (stage IN ('create','upload','publish')),
    CONSTRAINT ck_publication_operation_status CHECK
        (status IN ('pending','observed','unknown','rejected','reconciled')),
    CONSTRAINT ck_publication_operation_hash CHECK (request_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_publication_operation_token CHECK (lease_token >= 1),
    CONSTRAINT ck_publication_operation_manifest_hash CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_publication_operation_observation_size CHECK
        (observation IS NULL OR (octet_length(observation::text) <= 524288
         AND jsonb_typeof(observation) = 'object'))
);

CREATE TABLE publication_operation_audit (
    id UUID PRIMARY KEY,
    operation_id UUID NOT NULL REFERENCES publication_operations (id),
    kind VARCHAR(16) NOT NULL,
    observation_sha256 VARCHAR(64) NOT NULL,
    observation JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_publication_operation_audit UNIQUE
        (operation_id, kind, observation_sha256),
    CONSTRAINT ck_publication_operation_audit_kind CHECK
        (kind IN ('observed','unknown','rejected','reconciled','conflict')),
    CONSTRAINT ck_publication_operation_audit_hash CHECK (observation_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_publication_operation_audit_size CHECK
        (octet_length(observation::text) <= 524288 AND jsonb_typeof(observation) = 'object')
);

CREATE TABLE publication_job_events (
    job_id UUID NOT NULL REFERENCES publication_jobs (id),
    sequence BIGINT NOT NULL,
    event_key VARCHAR(256) NOT NULL,
    kind VARCHAR(64) NOT NULL,
    summary TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, sequence),
    CONSTRAINT uq_publication_job_event UNIQUE (job_id, event_key),
    CONSTRAINT ck_publication_job_event_sequence CHECK (sequence > 0)
);

CREATE TABLE publication_remote_observations (
    id UUID PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES publication_jobs (id),
    lease_token BIGINT NOT NULL,
    observation_sha256 VARCHAR(64) NOT NULL,
    observation JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_publication_remote_observation UNIQUE (job_id, observation_sha256),
    CONSTRAINT ck_publication_remote_observation_hash CHECK
        (observation_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_publication_remote_observation_token CHECK (lease_token >= 1),
    CONSTRAINT ck_publication_remote_observation_size CHECK
        (octet_length(observation::text) <= 524288 AND jsonb_typeof(observation) = 'object')
);

CREATE FUNCTION polymath_publication_job_binding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Publication job history is immutable' USING ERRCODE = '23514';
    END IF;
    IF (NEW.id, NEW.owner_session_hash, NEW.manifest_id, NEW.manifest_sha256, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.owner_session_hash, OLD.manifest_id, OLD.manifest_sha256, OLD.created_at) THEN
        RAISE EXCEPTION 'Publication job inputs are immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.publication_reported AND NOT NEW.publication_reported THEN
        RAISE EXCEPTION 'A reported remote publication cannot be erased' USING ERRCODE = '23514';
    END IF;
    IF OLD.status = 'published' AND
       (NEW.status <> 'published' OR NEW.outcome IS DISTINCT FROM OLD.outcome) THEN
        RAISE EXCEPTION 'A verified publication cannot be rewritten' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER immutable_publication_job_binding BEFORE UPDATE OR DELETE ON publication_jobs
FOR EACH ROW EXECUTE FUNCTION polymath_publication_job_binding();

CREATE FUNCTION polymath_publication_operation_binding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'Publication operation history is immutable' USING ERRCODE = '23514';
    END IF;
    IF (NEW.id, NEW.job_id, NEW.stage, NEW.request_hash, NEW.lease_token,
        NEW.approval_id, NEW.manifest_sha256, NEW.created_at)
       IS DISTINCT FROM
       (OLD.id, OLD.job_id, OLD.stage, OLD.request_hash, OLD.lease_token,
        OLD.approval_id, OLD.manifest_sha256, OLD.created_at) THEN
        RAISE EXCEPTION 'Publication grant bindings are immutable' USING ERRCODE = '23514';
    END IF;
    IF OLD.status IN ('observed','reconciled','rejected') AND
       NEW.status IN ('pending','unknown') THEN
        RAISE EXCEPTION 'A known publication response cannot become a new grant'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER immutable_publication_operation_binding BEFORE UPDATE OR DELETE
ON publication_operations FOR EACH ROW EXECUTE FUNCTION polymath_publication_operation_binding();

CREATE TRIGGER immutable_publication_operation_audit BEFORE UPDATE OR DELETE
ON publication_operation_audit FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
CREATE TRIGGER immutable_publication_job_event BEFORE UPDATE OR DELETE
ON publication_job_events FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
CREATE TRIGGER immutable_publication_remote_observation BEFORE UPDATE OR DELETE
ON publication_remote_observations FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
""")


def downgrade() -> None:
    op.execute("""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM publication_jobs) OR
       EXISTS (SELECT 1 FROM publication_operations) OR
       EXISTS (SELECT 1 FROM publication_operation_audit) OR
       EXISTS (SELECT 1 FROM publication_job_events) OR
       EXISTS (SELECT 1 FROM publication_remote_observations) THEN
        RAISE EXCEPTION 'Cannot discard publication execution or reconciliation history';
    END IF;
END;
$$;
""")
    op.drop_table("publication_remote_observations")
    op.drop_table("publication_job_events")
    op.drop_table("publication_operation_audit")
    op.drop_table("publication_operations")
    op.drop_table("publication_jobs")
    op.execute("DROP FUNCTION polymath_publication_operation_binding()")
    op.execute("DROP FUNCTION polymath_publication_job_binding()")
