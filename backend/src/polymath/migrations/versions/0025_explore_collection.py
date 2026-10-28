"""Shared immutable stage owners and explicitly model-ranked Explore collections."""

from alembic import op
from sqlalchemy import inspect, text

revision = "0025_explore_collection"
down_revision = "0024_scientific_context"
branch_labels = None
depends_on = None

REPARENT = ("research_stage_contexts", "research_stage_observations", "research_stage_commits")
NEW_TABLES = (
    "research_explore_collection_intents",
    "research_stage_owners",
    "research_explore_rankings",
    "research_explore_ranking_members",
    "research_explore_collection_finishes",
    "research_explore_collection_stops",
)


def _retarget(table, old, new):
    connection = op.get_bind()
    matches = [
        fk
        for fk in inspect(connection).get_foreign_keys(table)
        if fk["referred_table"] == old and fk["constrained_columns"] == ["run_id"]
    ]
    if len(matches) != 1 or matches[0]["referred_columns"] != ["run_id"]:
        raise ValueError("stage_owner_fk_shape_changed")
    op.drop_constraint(matches[0]["name"], table, type_="foreignkey")
    name = f"{table}_run_id_fkey" if new == "research_discovery_intents" else f"fk_{table}_owner"
    op.create_foreign_key(name, table, new, ["run_id"], ["run_id"])


def upgrade():
    # Stop funding and the old worker/API generation before this migration.
    # No existing scientific payload, receipt, checkpoint or monetary row changes.
    op.execute(
        "LOCK TABLE research_discovery_intents, "
        + ", ".join(REPARENT)
        + " IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute("""DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM research_discovery_intents d JOIN research_runs r ON r.id=d.run_id
 WHERE r.mode<>'surprise' OR r.task_kind<>'research' OR
 d.protocol<>'polymath-surprise-ladder-v1' OR d.max_stages NOT BETWEEN 1 AND 4 OR
 d.max_depth NOT BETWEEN 1 AND 3 OR d.config_sha256<>encode(sha256(convert_to(
 '{"max_depth":' || d.max_depth::text || ',"max_stages":' || d.max_stages::text ||
 ',"protocol":"polymath-surprise-ladder-v1"}','UTF8')),'hex')) THEN
 RAISE EXCEPTION 'legacy_stage_intent_integrity_failed'; END IF; END; $$;""")
    op.execute("""ALTER TABLE research_discovery_intents
 ADD CONSTRAINT uq_surprise_owner_config UNIQUE(run_id,protocol,config_sha256),
 ADD CONSTRAINT ck_surprise_owner_config CHECK(
 protocol='polymath-surprise-ladder-v1' AND max_stages BETWEEN 1 AND 4 AND max_depth BETWEEN 1 AND 3
 AND config_sha256=encode(sha256(convert_to('{"max_depth":' || max_depth::text ||
 ',"max_stages":' || max_stages::text ||
',"protocol":"polymath-surprise-ladder-v1"}','UTF8')),'hex'));
 ALTER TABLE research_stage_commits ADD CONSTRAINT uq_stage_ranking_receipt
 UNIQUE(id,result_id,run_id,content_sha256,receipt_sha256);""")
    op.execute(r"""

CREATE TABLE research_explore_collection_intents (
    run_id UUID NOT NULL,
    mode VARCHAR(32) NOT NULL,
    task_kind VARCHAR(32) NOT NULL,
    protocol VARCHAR(64) NOT NULL,
    max_bridges INTEGER NOT NULL,
    config_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (run_id),
    FOREIGN KEY(run_id, mode, task_kind) REFERENCES research_runs (id, mode, task_kind),
    CONSTRAINT uq_explore_intent_config UNIQUE (run_id, protocol, config_sha256),
    CONSTRAINT ck_explore_intent_mode CHECK (mode='explore' AND task_kind='research'),
    CONSTRAINT ck_explore_intent_protocol CHECK (protocol='polymath-explore-collection-v1' AND
max_bridges BETWEEN 1 AND 4),
    CONSTRAINT ck_explore_intent_hash CHECK
(config_sha256=encode(sha256(convert_to('{"max_bridges":' || max_bridges::text ||
',"protocol":"polymath-explore-collection-v1"}','UTF8')),'hex'))
)

;

CREATE TABLE research_stage_owners (
    run_id UUID NOT NULL,
    mode VARCHAR(32) NOT NULL,
    task_kind VARCHAR(32) NOT NULL,
    protocol VARCHAR(64) NOT NULL,
    config_sha256 VARCHAR(64) NOT NULL,
    surprise_intent_run_id UUID,
    explore_intent_run_id UUID,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (run_id),
    FOREIGN KEY(run_id, mode, task_kind) REFERENCES research_runs (id, mode, task_kind),
    FOREIGN KEY(surprise_intent_run_id, protocol, config_sha256) REFERENCES
research_discovery_intents (run_id, protocol, config_sha256),
    FOREIGN KEY(explore_intent_run_id, protocol, config_sha256) REFERENCES
research_explore_collection_intents (run_id, protocol, config_sha256),
    CONSTRAINT ck_stage_owner_exact_intent CHECK (task_kind='research' AND ((mode='surprise' AND
protocol='polymath-surprise-ladder-v1' AND surprise_intent_run_id IS NOT NULL AND
surprise_intent_run_id=run_id AND explore_intent_run_id IS NULL) OR (mode='explore' AND
protocol='polymath-explore-collection-v1' AND explore_intent_run_id IS NOT NULL AND
explore_intent_run_id=run_id AND surprise_intent_run_id IS NULL)))
)

;

CREATE TABLE research_explore_rankings (
    id UUID NOT NULL,
    run_id UUID NOT NULL,
    model_request_id VARCHAR(128) NOT NULL,
    action_ordinal INTEGER NOT NULL,
    covered_through_ordinal INTEGER,
    ranked_count INTEGER NOT NULL,
    excluded_count INTEGER NOT NULL,
    packet JSONB NOT NULL,
    canonical_packet TEXT NOT NULL,
    packet_sha256 VARCHAR(64) NOT NULL,
    receipt JSONB NOT NULL,
    canonical_receipt TEXT NOT NULL,
    receipt_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (id),
    FOREIGN KEY(run_id) REFERENCES research_explore_collection_intents (run_id),
    FOREIGN KEY(run_id, model_request_id) REFERENCES
research_external_requests (run_id, request_id),
    CONSTRAINT uq_explore_rank_request UNIQUE (run_id, model_request_id),
    CONSTRAINT uq_explore_rank_action UNIQUE (run_id, action_ordinal),
    CONSTRAINT uq_explore_rank_run UNIQUE (id, run_id),
    CONSTRAINT uq_explore_rank_finish UNIQUE (id, run_id, model_request_id, action_ordinal,
ranked_count),
    CONSTRAINT ck_explore_rank_action CHECK (action_ordinal BETWEEN 1 AND 16 AND model_request_id ~
'^model-[0-9]{4}$'),
    CONSTRAINT ck_explore_rank_coverage CHECK (ranked_count BETWEEN 0 AND 4 AND excluded_count
BETWEEN 0 AND 4 AND ranked_count+excluded_count<=4 AND ((covered_through_ordinal IS NULL AND
ranked_count+excluded_count=0) OR (covered_through_ordinal IS NOT NULL AND
covered_through_ordinal BETWEEN 0 AND 3 AND
ranked_count+excluded_count=covered_through_ordinal+1))),
    CONSTRAINT ck_explore_rank_packet_bytes CHECK (jsonb_typeof(packet)='object' AND
octet_length(canonical_packet) BETWEEN 1 AND 262144 AND packet=canonical_packet::jsonb),
    CONSTRAINT ck_explore_rank_packet_hash CHECK
(packet_sha256=encode(sha256(convert_to(canonical_packet,'UTF8')),'hex')),
    CONSTRAINT ck_explore_rank_receipt_bytes CHECK (jsonb_typeof(receipt)='object' AND
octet_length(canonical_receipt) BETWEEN 1 AND 262144 AND receipt=canonical_receipt::jsonb),
    CONSTRAINT ck_explore_rank_receipt_hash CHECK
(receipt_sha256=encode(sha256(convert_to(canonical_receipt,'UTF8')),'hex'))
)

;

CREATE TABLE research_explore_ranking_members (
    ranking_id UUID NOT NULL,
    bucket VARCHAR(16) NOT NULL,
    position INTEGER NOT NULL,
    run_id UUID NOT NULL,
    stage_id UUID NOT NULL,
    result_id UUID NOT NULL,
    result_sha256 VARCHAR(64) NOT NULL,
    stage_receipt_sha256 VARCHAR(64) NOT NULL,
    explanation TEXT NOT NULL,
    PRIMARY KEY (ranking_id, bucket, position),
    FOREIGN KEY(ranking_id, run_id) REFERENCES research_explore_rankings (id, run_id),
    FOREIGN KEY(stage_id, result_id, run_id, result_sha256, stage_receipt_sha256) REFERENCES
research_stage_commits (id, result_id, run_id, content_sha256, receipt_sha256),
    CONSTRAINT uq_explore_rank_stage UNIQUE (ranking_id, stage_id),
    CONSTRAINT uq_explore_rank_member_finish UNIQUE (ranking_id, bucket, position, stage_id,
result_id, run_id, result_sha256),
    CONSTRAINT ck_explore_rank_member_position CHECK (bucket IN ('ranked','excluded') AND position
BETWEEN 0 AND 3),
    CONSTRAINT ck_explore_rank_explanation CHECK (length(explanation) BETWEEN 1 AND 2000)
)

;

CREATE TABLE research_explore_collection_finishes (
    run_id UUID NOT NULL,
    ranking_id UUID NOT NULL,
    model_request_id VARCHAR(128) NOT NULL,
    action_ordinal INTEGER NOT NULL,
    ranked_count INTEGER NOT NULL,
    selected_bucket VARCHAR(16),
    selected_position INTEGER,
    selected_stage_id UUID,
    selected_result_id UUID,
    selected_result_sha256 VARCHAR(64),
    packet JSONB NOT NULL,
    canonical_packet TEXT NOT NULL,
    packet_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (run_id),
    FOREIGN KEY(run_id) REFERENCES research_explore_collection_intents (run_id),
    FOREIGN KEY(ranking_id, run_id, model_request_id, action_ordinal, ranked_count) REFERENCES
research_explore_rankings (id, run_id, model_request_id, action_ordinal, ranked_count),
    FOREIGN KEY(ranking_id, selected_bucket, selected_position, selected_stage_id,
selected_result_id, run_id, selected_result_sha256) REFERENCES research_explore_ranking_members
(ranking_id, bucket, position, stage_id, result_id, run_id, result_sha256),
    CONSTRAINT uq_explore_finish_stop UNIQUE (run_id, ranking_id),
    CONSTRAINT ck_explore_finish_explicit_selection CHECK ((ranked_count=0 AND selected_bucket IS
NULL AND selected_position IS NULL AND selected_stage_id IS NULL AND selected_result_id IS NULL
AND selected_result_sha256 IS NULL) OR (ranked_count BETWEEN 1 AND 4 AND selected_bucket IS NOT
NULL AND selected_bucket='ranked' AND selected_position IS NOT NULL AND selected_position=0 AND
selected_stage_id IS NOT NULL AND selected_result_id IS NOT NULL AND selected_result_sha256 IS
NOT NULL)),
    CONSTRAINT ck_explore_finish_packet_bytes CHECK (jsonb_typeof(packet)='object' AND
octet_length(canonical_packet) BETWEEN 1 AND 262144 AND packet=canonical_packet::jsonb),
    CONSTRAINT ck_explore_finish_packet_hash CHECK
(packet_sha256=encode(sha256(convert_to(canonical_packet,'UTF8')),'hex'))
)

;

CREATE TABLE research_explore_collection_stops (
    run_id UUID NOT NULL,
    status VARCHAR(32) NOT NULL,
    finish_ranking_id UUID,
    payload JSONB NOT NULL,
    canonical_payload TEXT NOT NULL,
    payload_sha256 VARCHAR(64) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
    PRIMARY KEY (run_id),
    FOREIGN KEY(run_id) REFERENCES research_explore_collection_intents (run_id),
    FOREIGN KEY(run_id, finish_ranking_id) REFERENCES research_explore_collection_finishes (run_id,
ranking_id),
    CONSTRAINT ck_explore_stop_status CHECK (status IN
('completed','insufficient_evidence','limit_reached','failed') AND (status<>'completed' OR
finish_ranking_id IS NOT NULL)),
    CONSTRAINT ck_explore_stop_payload_bytes CHECK (jsonb_typeof(payload)='object' AND
octet_length(canonical_payload) BETWEEN 1 AND 262144 AND payload=canonical_payload::jsonb),
    CONSTRAINT ck_explore_stop_payload_hash CHECK
(payload_sha256=encode(sha256(convert_to(canonical_payload,'UTF8')),'hex'))
)

;
""")
    op.execute("""INSERT INTO research_stage_owners
 (run_id,mode,task_kind,protocol,config_sha256,surprise_intent_run_id)
 SELECT d.run_id,r.mode,r.task_kind,d.protocol,d.config_sha256,d.run_id
 FROM research_discovery_intents d JOIN research_runs r ON r.id=d.run_id""")
    for table in REPARENT:
        _retarget(table, "research_discovery_intents", "research_stage_owners")
    for table in NEW_TABLES:
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION polymath_immutable_evidence()"
        )
    connection = op.get_bind()
    schema = connection.dialect.identifier_preparer.quote_schema(
        connection.scalar(text("SELECT current_schema()"))
    )
    op.execute(f"""CREATE FUNCTION {schema}.polymath_validate_surprise_stage_intent()
 RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
 PERFORM 1 FROM {schema}.research_runs WHERE id=NEW.run_id
 AND mode='surprise' AND task_kind='research' FOR KEY SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'stage_owner_wrong_surprise_scope'
 USING ERRCODE='23514'; END IF; RETURN NEW; END $$;
 CREATE TRIGGER surprise_stage_intent_scope BEFORE INSERT ON {schema}.research_discovery_intents
 FOR EACH ROW EXECUTE FUNCTION {schema}.polymath_validate_surprise_stage_intent();""")


def downgrade():
    # Backfilled Surprise-only owners are derived metadata; Explore history is not.
    op.execute("""DO $$ BEGIN
 IF EXISTS(SELECT 1 FROM research_explore_collection_intents) OR
 EXISTS(SELECT 1 FROM research_explore_rankings) OR
 EXISTS(SELECT 1 FROM research_explore_ranking_members) OR
 EXISTS(SELECT 1 FROM research_explore_collection_finishes) OR
 EXISTS(SELECT 1 FROM research_explore_collection_stops) OR
 EXISTS(SELECT 1 FROM research_stage_owners WHERE mode='explore') THEN
 RAISE EXCEPTION 'Cannot discard Explore collection intent or ranking history';
 END IF; END; $$;""")
    for table in REPARENT:
        _retarget(table, "research_stage_owners", "research_discovery_intents")
    connection = op.get_bind()
    schema = connection.dialect.identifier_preparer.quote_schema(
        connection.scalar(text("SELECT current_schema()"))
    )
    op.execute(f"DROP TRIGGER surprise_stage_intent_scope ON {schema}.research_discovery_intents")
    op.execute(f"DROP FUNCTION {schema}.polymath_validate_surprise_stage_intent()")
    for table in reversed(NEW_TABLES):
        op.drop_table(table)
    op.drop_constraint("uq_stage_ranking_receipt", "research_stage_commits", type_="unique")
    op.drop_constraint("ck_surprise_owner_config", "research_discovery_intents", type_="check")
    op.drop_constraint("uq_surprise_owner_config", "research_discovery_intents", type_="unique")
