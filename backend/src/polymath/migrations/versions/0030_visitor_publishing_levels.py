"""Publishing levels for visitors' own Zenodo connections.

Each browser session chooses how much Polymath may do with its own connection;
each item records the finding, the level and the write-up and publication it
led to, so an automatic publication always has an exact explanation.
"""

from alembic import op

revision = "0030_visitor_publishing_levels"
down_revision = "0029_visitor_zenodo"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE visitor_publishing_preferences (
 owner_session_hash VARCHAR(64) PRIMARY KEY
  REFERENCES browser_sessions(token_hash) ON DELETE CASCADE,
 level INTEGER NOT NULL DEFAULT 1,
 draft_destination VARCHAR(16) NOT NULL DEFAULT 'sandbox',
 write_kind VARCHAR(16) NOT NULL DEFAULT 'write_paper',
 full_text_only BOOLEAN NOT NULL DEFAULT false,
 daily_cap INTEGER NOT NULL DEFAULT 3,
 creators JSONB NOT NULL DEFAULT '[]'::jsonb,
 license VARCHAR(16) NOT NULL DEFAULT 'cc-by-4.0',
 enabled_at TIMESTAMPTZ,
 production_confirmed_at TIMESTAMPTZ,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT ck_visitor_publishing_level CHECK(level BETWEEN 1 AND 4),
 CONSTRAINT ck_visitor_publishing_draft CHECK(draft_destination IN ('sandbox','production')),
 CONSTRAINT ck_visitor_publishing_kind CHECK(write_kind IN ('write_summary','write_paper')),
 CONSTRAINT ck_visitor_publishing_cap CHECK(daily_cap BETWEEN 1 AND 10),
 CONSTRAINT ck_visitor_publishing_license CHECK(license IN ('cc-by-4.0','cc0-1.0')),
 CONSTRAINT ck_visitor_publishing_creators CHECK
  (jsonb_typeof(creators) = 'array' AND jsonb_array_length(creators) <= 10)
);
CREATE TABLE visitor_publishing_items (
 id UUID PRIMARY KEY,
 owner_session_hash VARCHAR(64) NOT NULL
  REFERENCES browser_sessions(token_hash) ON DELETE CASCADE,
 result_id UUID NOT NULL REFERENCES evidence_results(id),
 level INTEGER NOT NULL,
 destination VARCHAR(16) NOT NULL,
 write_kind VARCHAR(16) NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'pending_write',
 reason VARCHAR(80),
 artifact_run_id UUID,
 artifact_id UUID REFERENCES generated_artifacts(id),
 publication_id UUID REFERENCES visitor_publications(id) ON DELETE SET NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_visitor_publishing_item UNIQUE(owner_session_hash, result_id),
 CONSTRAINT ck_visitor_item_level CHECK(level BETWEEN 2 AND 4),
 CONSTRAINT ck_visitor_item_destination CHECK(destination IN ('sandbox','production')),
 CONSTRAINT ck_visitor_item_kind CHECK(write_kind IN ('write_summary','write_paper')),
 CONSTRAINT ck_visitor_item_status CHECK(status IN ('pending_write','writing','drafted',
  'ready','queued','published','failed','skipped'))
);
CREATE INDEX ix_visitor_publishing_items_owner_created
 ON visitor_publishing_items(owner_session_hash, created_at);
""")


def downgrade() -> None:
    op.execute("""
DROP TABLE visitor_publishing_items;
DROP TABLE visitor_publishing_preferences;
""")
