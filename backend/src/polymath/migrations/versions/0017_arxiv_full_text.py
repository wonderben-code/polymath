"""Immutable full-text bodies, owned indexes and exact saved Unicode windows."""

from alembic import op

revision = "0017_arxiv_full_text"
down_revision = "0016_finding_assessments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
CREATE TABLE arxiv_full_text_snapshots (
 id UUID PRIMARY KEY,
 arxiv_id VARCHAR(100) NOT NULL,
 arxiv_version INTEGER NOT NULL,
 url VARCHAR(2000) NOT NULL,
 source_text TEXT NOT NULL,
 snapshot_sha256 VARCHAR(64) NOT NULL,
 raw_html_sha256 VARCHAR(64) NOT NULL,
 extractor_version VARCHAR(100) NOT NULL,
 headings JSONB NOT NULL,
 headings_truncated BOOLEAN NOT NULL,
 limitations JSONB NOT NULL,
 provenance_sha256 VARCHAR(64) NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT uq_full_text_snapshot UNIQUE
  (arxiv_id, arxiv_version, extractor_version, raw_html_sha256, snapshot_sha256),
 CONSTRAINT ck_full_text_version CHECK(arxiv_version BETWEEN 1 AND 9999),
 CONSTRAINT ck_full_text_size CHECK(length(source_text) BETWEEN 1 AND 250000),
 CONSTRAINT ck_full_text_hash CHECK
  (snapshot_sha256 = encode(sha256(convert_to(source_text, 'UTF8')), 'hex')),
 CONSTRAINT ck_full_text_raw_hash CHECK(raw_html_sha256 ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_full_text_provenance_hash CHECK(provenance_sha256 ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_full_text_headings CHECK
  (jsonb_typeof(headings) = 'array' AND jsonb_array_length(headings) <= 80 AND
   octet_length(headings::text) <= 131072),
 CONSTRAINT ck_full_text_limitations CHECK
  (jsonb_typeof(limitations) = 'array' AND jsonb_array_length(limitations) BETWEEN 1 AND 8
   AND octet_length(limitations::text) <= 65536)
);
CREATE TABLE research_full_text_indexes (
 run_id UUID NOT NULL REFERENCES research_runs(id),
 source_id VARCHAR(200) NOT NULL,
 snapshot_id UUID NOT NULL REFERENCES arxiv_full_text_snapshots(id),
 reference JSONB NOT NULL,
 reference_sha256 VARCHAR(64) NOT NULL,
 retrieved_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(run_id,source_id),
 CONSTRAINT uq_run_full_text_snapshot UNIQUE(run_id,snapshot_id),
 CONSTRAINT ck_full_text_reference_hash CHECK(reference_sha256 ~ '^[0-9a-f]{64}$'),
 CONSTRAINT ck_full_text_reference_size CHECK
  (jsonb_typeof(reference) = 'object' AND octet_length(reference::text) <= 131072)
);
CREATE INDEX ix_full_text_indexes_snapshot ON research_full_text_indexes(snapshot_id);
ALTER TABLE evidence_source_retrievals ADD COLUMN coverage JSONB;
ALTER TABLE evidence_source_retrievals ADD COLUMN full_text_snapshot_id UUID
 REFERENCES arxiv_full_text_snapshots(id);
ALTER TABLE evidence_source_retrievals ADD CONSTRAINT ck_source_coverage CHECK
 ((coverage IS NULL AND full_text_snapshot_id IS NULL) OR
  (coverage IS NOT NULL AND full_text_snapshot_id IS NOT NULL AND
   jsonb_typeof(coverage) = 'object' AND octet_length(coverage::text) <= 4096 AND
   coverage->>'snapshot_id' = full_text_snapshot_id::text));
CREATE TRIGGER immutable_full_text_snapshot BEFORE UPDATE OR DELETE ON arxiv_full_text_snapshots
 FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
CREATE TRIGGER immutable_full_text_index BEFORE UPDATE OR DELETE ON research_full_text_indexes
 FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence();
CREATE FUNCTION polymath_full_text_window_integrity() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
 snapshot arxiv_full_text_snapshots%ROWTYPE;
 source evidence_source_versions%ROWTYPE;
 start_at INTEGER;
 end_at INTEGER;
BEGIN
 SELECT * INTO source FROM evidence_source_versions WHERE id = NEW.version_id;
 IF NEW.coverage IS NULL THEN
  IF source.source_id LIKE 'arxiv-html:%' THEN
   RAISE EXCEPTION 'Full-text locator requires coverage' USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
 END IF;
 SELECT * INTO snapshot FROM arxiv_full_text_snapshots WHERE id = NEW.full_text_snapshot_id;
 IF snapshot.id IS NULL OR source.id IS NULL OR
    (NEW.coverage->>'start') IS NULL OR (NEW.coverage->>'end') IS NULL OR
    (NEW.coverage->>'start') !~ '^(0|[1-9][0-9]{0,5})$' OR
    (NEW.coverage->>'end') !~ '^[1-9][0-9]{0,5}$' OR
    jsonb_typeof(NEW.coverage->'start') IS DISTINCT FROM 'number' OR
    jsonb_typeof(NEW.coverage->'end') IS DISTINCT FROM 'number' THEN
  RAISE EXCEPTION 'Invalid full-text coverage' USING ERRCODE = '23514';
 END IF;
 start_at := (NEW.coverage->>'start')::INTEGER;
 end_at := (NEW.coverage->>'end')::INTEGER;
 IF start_at < 0 OR end_at <= start_at OR end_at > length(snapshot.source_text) OR
    end_at - start_at > 8000 OR
    NEW.coverage IS DISTINCT FROM jsonb_build_object(
      'snapshot_id', snapshot.id::text, 'snapshot_sha256', snapshot.snapshot_sha256,
      'arxiv_id', snapshot.arxiv_id, 'arxiv_version', snapshot.arxiv_version,
      'start', start_at, 'end', end_at, 'total', length(snapshot.source_text)) OR
    source.source_text IS DISTINCT FROM
      substring(snapshot.source_text FROM start_at + 1 FOR end_at - start_at) OR
    source.content_kind <> 'full_text' OR
    source.source_id <> 'arxiv-html:' || snapshot.id::text || ':' || start_at || ':' || end_at OR
    NEW.url <> snapshot.url OR NEW.extraction_method <> snapshot.extractor_version OR
    NEW.bibliography->>'arxiv_id' IS DISTINCT FROM snapshot.arxiv_id OR
    NEW.bibliography->>'arxiv_version' IS DISTINCT FROM snapshot.arxiv_version::text THEN
  RAISE EXCEPTION 'Full-text window does not match snapshot' USING ERRCODE = '23514';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER full_text_window_integrity BEFORE INSERT ON evidence_source_retrievals
 FOR EACH ROW EXECUTE FUNCTION polymath_full_text_window_integrity();
""")


def downgrade() -> None:
    op.execute("""
DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM arxiv_full_text_snapshots) OR
    EXISTS(SELECT 1 FROM research_full_text_indexes) OR
    EXISTS(SELECT 1 FROM evidence_source_retrievals WHERE coverage IS NOT NULL) THEN
  RAISE EXCEPTION 'Cannot discard saved full-text evidence';
 END IF;
END; $$;
DROP TRIGGER full_text_window_integrity ON evidence_source_retrievals;
DROP FUNCTION polymath_full_text_window_integrity();
ALTER TABLE evidence_source_retrievals DROP CONSTRAINT ck_source_coverage;
ALTER TABLE evidence_source_retrievals DROP COLUMN full_text_snapshot_id;
ALTER TABLE evidence_source_retrievals DROP COLUMN coverage;
""")
    op.drop_table("research_full_text_indexes")
    op.drop_table("arxiv_full_text_snapshots")
