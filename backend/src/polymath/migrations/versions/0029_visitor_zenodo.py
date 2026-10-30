"""Connect Zenodo for every visitor: OAuth states, connections and publications.

Tokens are stored only as AES-GCM ciphertext bound to their browser session and
destination; OAuth states only as hashes. Everything belongs to one browser
session and disappears with it.
"""

from alembic import op

revision = "0029_visitor_zenodo"
down_revision = "0028_publishing_autopilot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE zenodo_oauth_states (
 state_hash VARCHAR(64) PRIMARY KEY,
 owner_session_hash VARCHAR(64) NOT NULL
  REFERENCES browser_sessions(token_hash) ON DELETE CASCADE,
 destination VARCHAR(16) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 expires_at TIMESTAMPTZ NOT NULL,
 CONSTRAINT ck_zenodo_state_hash CHECK(state_hash ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_zenodo_state_destination CHECK(destination IN ('sandbox','production')),
 CONSTRAINT ck_zenodo_state_lifetime CHECK
  (expires_at > created_at AND expires_at <= created_at + interval '15 minutes')
);
CREATE INDEX ix_zenodo_oauth_states_owner ON zenodo_oauth_states(owner_session_hash);
CREATE TABLE zenodo_connections (
 id UUID PRIMARY KEY,
 owner_session_hash VARCHAR(64) NOT NULL
  REFERENCES browser_sessions(token_hash) ON DELETE CASCADE,
 destination VARCHAR(16) NOT NULL,
 access_ciphertext BYTEA NOT NULL,
 refresh_ciphertext BYTEA,
 access_expires_at TIMESTAMPTZ,
 zenodo_user_id VARCHAR(64),
 connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_zenodo_connection UNIQUE(owner_session_hash, destination),
 CONSTRAINT ck_zenodo_connection_destination CHECK(destination IN ('sandbox','production')),
 CONSTRAINT ck_zenodo_connection_sizes CHECK
  (octet_length(access_ciphertext) BETWEEN 29 AND 8192
   AND (refresh_ciphertext IS NULL OR octet_length(refresh_ciphertext) BETWEEN 29 AND 8192))
);
CREATE TABLE visitor_publications (
 id UUID PRIMARY KEY,
 owner_session_hash VARCHAR(64) NOT NULL
  REFERENCES browser_sessions(token_hash) ON DELETE CASCADE,
 artifact_id UUID NOT NULL REFERENCES generated_artifacts(id),
 destination VARCHAR(16) NOT NULL,
 manifest JSONB NOT NULL,
 manifest_sha256 VARCHAR(64) NOT NULL,
 status VARCHAR(16) NOT NULL DEFAULT 'preview',
 create_started_at TIMESTAMPTZ,
 deposit_id BIGINT,
 doi VARCHAR(200),
 record_url VARCHAR(500),
 error_code VARCHAR(80),
 lease_owner VARCHAR(128),
 lease_expires_at TIMESTAMPTZ,
 confirmed_at TIMESTAMPTZ,
 published_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_visitor_publication UNIQUE(owner_session_hash, manifest_sha256),
 CONSTRAINT ck_visitor_publication_hash CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_visitor_publication_manifest CHECK(jsonb_typeof(manifest) = 'object'),
 CONSTRAINT ck_visitor_publication_destination CHECK(destination IN ('sandbox','production')),
 CONSTRAINT ck_visitor_publication_status CHECK(status IN ('preview','queued','running',
  'checking','published','failed','missing','reconnect')),
 CONSTRAINT ck_visitor_publication_deposit CHECK(deposit_id IS NULL OR deposit_id > 0),
 CONSTRAINT ck_visitor_publication_published CHECK
  (status <> 'published' OR (deposit_id IS NOT NULL AND published_at IS NOT NULL))
);
CREATE INDEX ix_visitor_publications_owner_created
 ON visitor_publications(owner_session_hash, created_at);
CREATE INDEX ix_visitor_publications_due ON visitor_publications(status, lease_expires_at);
""")


def downgrade() -> None:
    op.execute("""
DROP TABLE visitor_publications;
DROP TABLE zenodo_connections;
DROP TABLE zenodo_oauth_states;
""")
