"""Publishing approval levels and the items the publishing autopilot handled.

A preference records the publisher's standing choice; each item records the
finding, the level and every artifact, manifest, approval and job linked to it,
so an automatic publication always has an exact explanation.
"""

from alembic import op

revision = "0028_publishing_autopilot"
down_revision = "0027_converge_programmes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE publishing_preferences (
 actor_id VARCHAR(100) PRIMARY KEY,
 level INTEGER NOT NULL DEFAULT 1,
 draft_destination VARCHAR(16) NOT NULL DEFAULT 'sandbox',
 write_kind VARCHAR(16) NOT NULL DEFAULT 'write_paper',
 full_text_only BOOLEAN NOT NULL DEFAULT false,
 daily_cap INTEGER NOT NULL DEFAULT 3,
 creators JSONB NOT NULL DEFAULT '[]'::jsonb,
 license VARCHAR(16) NOT NULL DEFAULT 'cc-by-4.0',
 keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
 enabled_at TIMESTAMPTZ,
 production_confirmed_at TIMESTAMPTZ,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT ck_publishing_level CHECK(level BETWEEN 1 AND 4),
 CONSTRAINT ck_publishing_draft_destination CHECK(draft_destination IN ('sandbox','production')),
 CONSTRAINT ck_publishing_write_kind CHECK(write_kind IN ('write_summary','write_paper')),
 CONSTRAINT ck_publishing_cap CHECK(daily_cap BETWEEN 1 AND 10),
 CONSTRAINT ck_publishing_license CHECK(license IN ('cc-by-4.0','cc0-1.0')),
 CONSTRAINT ck_publishing_creators CHECK
  (jsonb_typeof(creators) = 'array' AND jsonb_array_length(creators) <= 10),
 CONSTRAINT ck_publishing_keywords CHECK
  (jsonb_typeof(keywords) = 'array' AND jsonb_array_length(keywords) <= 20)
);
CREATE TABLE publishing_items (
 id UUID PRIMARY KEY,
 actor_id VARCHAR(100) NOT NULL,
 owner_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions(token_hash),
 result_id UUID NOT NULL REFERENCES evidence_results(id),
 level INTEGER NOT NULL,
 destination VARCHAR(16) NOT NULL,
 write_kind VARCHAR(16) NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'pending_write',
 artifact_run_id UUID UNIQUE REFERENCES research_runs(id),
 artifact_id UUID REFERENCES generated_artifacts(id),
 manifest_id UUID REFERENCES publication_manifests(id),
 approval_id UUID REFERENCES publication_approvals(id),
 job_id UUID REFERENCES publication_jobs(id),
 reason VARCHAR(64),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_publishing_item_result UNIQUE(owner_session_hash,result_id),
 CONSTRAINT ck_publishing_item_level CHECK(level BETWEEN 2 AND 4),
 CONSTRAINT ck_publishing_item_destination CHECK(destination IN ('sandbox','production')),
 CONSTRAINT ck_publishing_item_kind CHECK(write_kind IN ('write_summary','write_paper')),
 CONSTRAINT ck_publishing_item_status CHECK(status IN ('pending_write','writing','drafted',
  'ready','approved','queued','published','failed','skipped'))
);
CREATE INDEX ix_publishing_items_owner_created ON publishing_items(owner_session_hash,created_at);
CREATE INDEX ix_publishing_items_actor_created ON publishing_items(actor_id,created_at);
""")


def downgrade() -> None:
    op.execute("""
DROP TABLE publishing_items;
DROP TABLE publishing_preferences;
""")
