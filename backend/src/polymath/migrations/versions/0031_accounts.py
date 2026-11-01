"""Accounts: sign in with ORCID, GitHub or Google, or continue as a guest.

An account remembers one home browser session, whose bearer token is kept only
as AES-GCM ciphertext so that signing in on another device opens the same
work. Provider tokens are never stored; OAuth states only as hashes.
"""

from alembic import op

revision = "0031_accounts"
down_revision = "0030_visitor_publishing_levels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE account_oauth_states (
 state_hash VARCHAR(64) PRIMARY KEY,
 owner_session_hash VARCHAR(64) NOT NULL
  REFERENCES browser_sessions(token_hash) ON DELETE CASCADE,
 provider VARCHAR(16) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 expires_at TIMESTAMPTZ NOT NULL,
 CONSTRAINT ck_account_state_hash CHECK(state_hash ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_account_state_provider CHECK(provider IN ('orcid','github','google')),
 CONSTRAINT ck_account_state_lifetime CHECK
  (expires_at > created_at AND expires_at <= created_at + interval '15 minutes')
);
CREATE INDEX ix_account_oauth_states_owner ON account_oauth_states(owner_session_hash);
CREATE TABLE accounts (
 id UUID PRIMARY KEY,
 provider VARCHAR(16) NOT NULL,
 subject VARCHAR(128) NOT NULL,
 display_name VARCHAR(120) NOT NULL,
 orcid VARCHAR(19),
 home_session_hash VARCHAR(64)
  REFERENCES browser_sessions(token_hash) ON DELETE SET NULL,
 home_ciphertext BYTEA,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 signed_in_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_account_identity UNIQUE(provider, subject),
 CONSTRAINT uq_account_home UNIQUE(home_session_hash),
 CONSTRAINT ck_account_provider CHECK(provider IN ('orcid','github','google')),
 CONSTRAINT ck_account_home CHECK((home_session_hash IS NULL) = (home_ciphertext IS NULL)),
 CONSTRAINT ck_account_home_size CHECK
  (home_ciphertext IS NULL OR octet_length(home_ciphertext) BETWEEN 29 AND 512)
);
""")


def downgrade() -> None:
    op.execute("""
DROP TABLE accounts;
DROP TABLE account_oauth_states;
""")
