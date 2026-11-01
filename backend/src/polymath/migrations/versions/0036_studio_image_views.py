"""Studio art views: a gallery of five to seven pictures chosen for each invention."""

from alembic import op

revision = "0036_studio_image_views"
down_revision = "0035_studio_images"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
ALTER TABLE studio_images ADD COLUMN view VARCHAR(16) NOT NULL DEFAULT 'concept',
 ADD CONSTRAINT ck_studio_image_view CHECK
 (view IN ('concept','in_use','schematic','detail','sequence','scale','origins'));
""")


def downgrade() -> None:
    op.execute("ALTER TABLE studio_images DROP CONSTRAINT ck_studio_image_view, DROP COLUMN view;")
