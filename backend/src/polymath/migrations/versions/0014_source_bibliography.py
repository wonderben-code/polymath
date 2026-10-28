"""Preserve optional provider-declared bibliography on immutable retrievals."""

from alembic import op

revision = "0014_source_bibliography"
down_revision = "0013_publication_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE evidence_source_retrievals ADD COLUMN bibliography JSONB;
ALTER TABLE evidence_source_retrievals ADD CONSTRAINT ck_source_bibliography CHECK (
    bibliography IS NULL OR (jsonb_typeof(bibliography) = 'object' AND
    octet_length(bibliography::text) <= 65536));
""")


def downgrade() -> None:
    op.execute("""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM evidence_source_retrievals WHERE bibliography IS NOT NULL) THEN
        RAISE EXCEPTION 'Cannot discard saved source bibliography';
    END IF;
END;
$$;
""")
    op.drop_constraint("ck_source_bibliography", "evidence_source_retrievals", type_="check")
    op.drop_column("evidence_source_retrievals", "bibliography")
