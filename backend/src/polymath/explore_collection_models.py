"""Immutable shared stage ownership and model-ranked Explore collections."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


def snapshot_checks(field, *, label, limit=262144):
    return (
        CheckConstraint(
            f"jsonb_typeof({field})='object' AND octet_length(canonical_{field}) "
            f"BETWEEN 1 AND {limit} AND {field}=canonical_{field}::jsonb",
            name=f"ck_{label}_bytes",
        ),
        CheckConstraint(
            f"{field}_sha256=encode(sha256(convert_to(canonical_{field},'UTF8')),'hex')",
            name=f"ck_{label}_hash",
        ),
    )


class ExploreCollectionIntentRecord(Base):
    __tablename__ = "research_explore_collection_intents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "mode", "task_kind"],
            ["research_runs.id", "research_runs.mode", "research_runs.task_kind"],
        ),
        UniqueConstraint("run_id", "protocol", "config_sha256", name="uq_explore_intent_config"),
        CheckConstraint("mode='explore' AND task_kind='research'", name="ck_explore_intent_mode"),
        CheckConstraint(
            "protocol='polymath-explore-collection-v1' AND max_bridges BETWEEN 1 AND 4",
            name="ck_explore_intent_protocol",
        ),
        CheckConstraint(
            "config_sha256=encode(sha256(convert_to('{\"max_bridges\":' || max_bridges::text || "
            "',\"protocol\":\"polymath-explore-collection-v1\"}','UTF8')),'hex')",
            name="ck_explore_intent_hash",
        ),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    mode: Mapped[str] = mapped_column(String(32))
    task_kind: Mapped[str] = mapped_column(String(32))
    protocol: Mapped[str] = mapped_column(String(64))
    max_bridges: Mapped[int] = mapped_column(Integer)
    config_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ResearchStageOwner(Base):
    __tablename__ = "research_stage_owners"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "mode", "task_kind"],
            ["research_runs.id", "research_runs.mode", "research_runs.task_kind"],
        ),
        ForeignKeyConstraint(
            ["surprise_intent_run_id", "protocol", "config_sha256"],
            [
                "research_discovery_intents.run_id",
                "research_discovery_intents.protocol",
                "research_discovery_intents.config_sha256",
            ],
        ),
        ForeignKeyConstraint(
            ["explore_intent_run_id", "protocol", "config_sha256"],
            [
                "research_explore_collection_intents.run_id",
                "research_explore_collection_intents.protocol",
                "research_explore_collection_intents.config_sha256",
            ],
        ),
        CheckConstraint(
            "task_kind='research' AND ((mode='surprise' AND protocol='polymath-surprise-ladder-v1' "
            "AND surprise_intent_run_id IS NOT NULL AND surprise_intent_run_id=run_id "
            "AND explore_intent_run_id IS NULL) OR (mode='explore' AND "
            "protocol='polymath-explore-collection-v1' AND explore_intent_run_id IS NOT NULL "
            "AND explore_intent_run_id=run_id AND surprise_intent_run_id IS NULL))",
            name="ck_stage_owner_exact_intent",
        ),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    mode: Mapped[str] = mapped_column(String(32))
    task_kind: Mapped[str] = mapped_column(String(32))
    protocol: Mapped[str] = mapped_column(String(64))
    config_sha256: Mapped[str] = mapped_column(String(64))
    surprise_intent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    explore_intent_run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExploreRankingRecord(Base):
    __tablename__ = "research_explore_rankings"
    __table_args__ = (
        ForeignKeyConstraint(["run_id"], ["research_explore_collection_intents.run_id"]),
        ForeignKeyConstraint(
            ["run_id", "model_request_id"],
            ["research_external_requests.run_id", "research_external_requests.request_id"],
        ),
        UniqueConstraint("run_id", "model_request_id", name="uq_explore_rank_request"),
        UniqueConstraint("run_id", "action_ordinal", name="uq_explore_rank_action"),
        UniqueConstraint("id", "run_id", name="uq_explore_rank_run"),
        UniqueConstraint(
            "id",
            "run_id",
            "model_request_id",
            "action_ordinal",
            "ranked_count",
            name="uq_explore_rank_finish",
        ),
        CheckConstraint(
            "action_ordinal BETWEEN 1 AND 16 AND model_request_id ~ '^model-[0-9]{4}$'",
            name="ck_explore_rank_action",
        ),
        CheckConstraint(
            "ranked_count BETWEEN 0 AND 4 AND excluded_count BETWEEN 0 AND 4 "
            "AND ranked_count+excluded_count<=4 AND ((covered_through_ordinal IS NULL "
            "AND ranked_count+excluded_count=0) OR (covered_through_ordinal IS NOT NULL "
            "AND covered_through_ordinal BETWEEN 0 AND 3 "
            "AND ranked_count+excluded_count=covered_through_ordinal+1))",
            name="ck_explore_rank_coverage",
        ),
        *snapshot_checks("packet", label="explore_rank_packet"),
        *snapshot_checks("receipt", label="explore_rank_receipt"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    model_request_id: Mapped[str] = mapped_column(String(128))
    action_ordinal: Mapped[int] = mapped_column(Integer)
    covered_through_ordinal: Mapped[int | None] = mapped_column(Integer)
    ranked_count: Mapped[int] = mapped_column(Integer)
    excluded_count: Mapped[int] = mapped_column(Integer)
    packet: Mapped[dict] = mapped_column(JSONB)
    canonical_packet: Mapped[str] = mapped_column(Text)
    packet_sha256: Mapped[str] = mapped_column(String(64))
    receipt: Mapped[dict] = mapped_column(JSONB)
    canonical_receipt: Mapped[str] = mapped_column(Text)
    receipt_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExploreRankingMember(Base):
    __tablename__ = "research_explore_ranking_members"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ranking_id", "run_id"],
            ["research_explore_rankings.id", "research_explore_rankings.run_id"],
        ),
        ForeignKeyConstraint(
            ["stage_id", "result_id", "run_id", "result_sha256", "stage_receipt_sha256"],
            [
                "research_stage_commits.id",
                "research_stage_commits.result_id",
                "research_stage_commits.run_id",
                "research_stage_commits.content_sha256",
                "research_stage_commits.receipt_sha256",
            ],
        ),
        UniqueConstraint("ranking_id", "stage_id", name="uq_explore_rank_stage"),
        UniqueConstraint(
            "ranking_id",
            "bucket",
            "position",
            "stage_id",
            "result_id",
            "run_id",
            "result_sha256",
            name="uq_explore_rank_member_finish",
        ),
        CheckConstraint(
            "bucket IN ('ranked','excluded') AND position BETWEEN 0 AND 3",
            name="ck_explore_rank_member_position",
        ),
        CheckConstraint(
            "length(explanation) BETWEEN 1 AND 2000", name="ck_explore_rank_explanation"
        ),
    )
    ranking_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    bucket: Mapped[str] = mapped_column(String(16), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    stage_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    result_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    result_sha256: Mapped[str] = mapped_column(String(64))
    stage_receipt_sha256: Mapped[str] = mapped_column(String(64))
    explanation: Mapped[str] = mapped_column(Text)


class ExploreCollectionFinish(Base):
    __tablename__ = "research_explore_collection_finishes"
    __table_args__ = (
        ForeignKeyConstraint(["run_id"], ["research_explore_collection_intents.run_id"]),
        ForeignKeyConstraint(
            ["ranking_id", "run_id", "model_request_id", "action_ordinal", "ranked_count"],
            [
                "research_explore_rankings.id",
                "research_explore_rankings.run_id",
                "research_explore_rankings.model_request_id",
                "research_explore_rankings.action_ordinal",
                "research_explore_rankings.ranked_count",
            ],
        ),
        ForeignKeyConstraint(
            [
                "ranking_id",
                "selected_bucket",
                "selected_position",
                "selected_stage_id",
                "selected_result_id",
                "run_id",
                "selected_result_sha256",
            ],
            [
                "research_explore_ranking_members.ranking_id",
                "research_explore_ranking_members.bucket",
                "research_explore_ranking_members.position",
                "research_explore_ranking_members.stage_id",
                "research_explore_ranking_members.result_id",
                "research_explore_ranking_members.run_id",
                "research_explore_ranking_members.result_sha256",
            ],
        ),
        UniqueConstraint("run_id", "ranking_id", name="uq_explore_finish_stop"),
        CheckConstraint(
            "(ranked_count=0 AND selected_bucket IS NULL AND selected_position IS NULL "
            "AND selected_stage_id IS NULL AND selected_result_id IS NULL "
            "AND selected_result_sha256 IS NULL) OR "
            "(ranked_count BETWEEN 1 AND 4 AND selected_bucket IS NOT NULL "
            "AND selected_bucket='ranked' AND selected_position IS NOT NULL "
            "AND selected_position=0 AND selected_stage_id IS NOT NULL "
            "AND selected_result_id IS NOT NULL AND selected_result_sha256 IS NOT NULL)",
            name="ck_explore_finish_explicit_selection",
        ),
        *snapshot_checks("packet", label="explore_finish_packet"),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    ranking_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    model_request_id: Mapped[str] = mapped_column(String(128))
    action_ordinal: Mapped[int] = mapped_column(Integer)
    ranked_count: Mapped[int] = mapped_column(Integer)
    selected_bucket: Mapped[str | None] = mapped_column(String(16))
    selected_position: Mapped[int | None] = mapped_column(Integer)
    selected_stage_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    selected_result_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    selected_result_sha256: Mapped[str | None] = mapped_column(String(64))
    packet: Mapped[dict] = mapped_column(JSONB)
    canonical_packet: Mapped[str] = mapped_column(Text)
    packet_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ExploreCollectionStop(Base):
    __tablename__ = "research_explore_collection_stops"
    __table_args__ = (
        ForeignKeyConstraint(["run_id"], ["research_explore_collection_intents.run_id"]),
        ForeignKeyConstraint(
            ["run_id", "finish_ranking_id"],
            [
                "research_explore_collection_finishes.run_id",
                "research_explore_collection_finishes.ranking_id",
            ],
        ),
        CheckConstraint(
            "status IN ('completed','insufficient_evidence','limit_reached','failed') "
            "AND (status<>'completed' OR finish_ranking_id IS NOT NULL)",
            name="ck_explore_stop_status",
        ),
        *snapshot_checks("payload", label="explore_stop_payload"),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    finish_ranking_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    payload: Mapped[dict] = mapped_column(JSONB)
    canonical_payload: Mapped[str] = mapped_column(Text)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


MODELS = (
    ExploreCollectionIntentRecord,
    ResearchStageOwner,
    ExploreRankingRecord,
    ExploreRankingMember,
    ExploreCollectionFinish,
    ExploreCollectionStop,
)
