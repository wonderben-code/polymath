"""Studio concept art: images rendered by Meta's image model for an invention.

Each image is one 'render_image' run, so it is reserved and settled on the same
budget ledger as every other Meta call, and its bytes are stored once, here,
in the transaction that settles it (docs/STUDIO.md).
"""

from alembic import op

revision = "0035_studio_images"
down_revision = "0034_studio"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE research_runs DROP CONSTRAINT ck_research_runs_task_kind;
ALTER TABLE research_runs ADD CONSTRAINT ck_research_runs_task_kind CHECK
 (task_kind IN ('research','formalise','write_summary','write_paper',
 'ingest_literature','reassess_finding','verify_meta','render_image'));
CREATE TABLE studio_images (
 id UUID PRIMARY KEY,
 programme_id UUID NOT NULL REFERENCES studio_programmes(id),
 owner_session_hash VARCHAR(64) NOT NULL REFERENCES browser_sessions(token_hash),
 run_id UUID NOT NULL REFERENCES research_runs(id),
 prompt TEXT NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'rendering',
 error VARCHAR(64),
 mime VARCHAR(16),
 content BYTEA,
 content_sha256 VARCHAR(64),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 finished_at TIMESTAMPTZ,
 CONSTRAINT uq_studio_image_run UNIQUE(run_id),
 CONSTRAINT ck_studio_image_prompt CHECK(char_length(prompt) BETWEEN 1 AND 4000),
 CONSTRAINT ck_studio_image_status CHECK(status IN ('rendering','succeeded','refused','unknown')),
 CONSTRAINT ck_studio_image_mime CHECK
  (mime IS NULL OR mime IN ('image/webp','image/png','image/jpeg')),
 CONSTRAINT ck_studio_image_content CHECK
  ((status = 'succeeded') =
   (content IS NOT NULL AND mime IS NOT NULL AND content_sha256 IS NOT NULL)),
 CONSTRAINT ck_studio_image_size CHECK(content IS NULL OR octet_length(content) <= 8000000)
);
CREATE INDEX ix_studio_images_programme ON studio_images(programme_id,created_at);
CREATE INDEX ix_studio_images_owner ON studio_images(owner_session_hash,created_at);
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM research_runs WHERE task_kind='render_image') THEN
  RAISE EXCEPTION 'Cannot discard rendered image history';
 END IF;
END; $$;
DROP TABLE studio_images;
ALTER TABLE research_runs DROP CONSTRAINT ck_research_runs_task_kind;
ALTER TABLE research_runs ADD CONSTRAINT ck_research_runs_task_kind CHECK
 (task_kind IN ('research','formalise','write_summary','write_paper',
 'ingest_literature','reassess_finding','verify_meta'));
""")
