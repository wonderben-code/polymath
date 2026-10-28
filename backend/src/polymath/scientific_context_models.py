"""Immutable scientific-context and claim-overview admission sidecars."""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polymath.database import Base


class ArtifactContextBinding(Base):
    __tablename__ = "artifact_scientific_context_bindings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "source_result_id", "source_run_id", "source_sha256"],
            [
                "artifact_requests.run_id",
                "artifact_requests.source_result_id",
                "artifact_requests.source_run_id",
                "artifact_requests.source_sha256",
            ],
        ),
        ForeignKeyConstraint(
            ["source_result_id", "source_run_id", "source_sha256"],
            ["evidence_results.id", "evidence_results.run_id", "evidence_results.content_sha256"],
        ),
        CheckConstraint(
            "protocol='polymath-artifact-scientific-context-v1' "
            "AND (context->>'protocol') IS NOT DISTINCT FROM protocol",
            name="ck_scientific_context_protocol",
        ),
        CheckConstraint(
            "jsonb_typeof(context)='object' AND octet_length(canonical_context) "
            "BETWEEN 1 AND 262144 AND context=canonical_context::jsonb",
            name="ck_scientific_context_size",
        ),
        CheckConstraint(
            "context_sha256=encode(sha256(convert_to(canonical_context,'UTF8')),'hex')",
            name="ck_scientific_context_hash",
        ),
        CheckConstraint(
            "(context#>>'{source_reference,result_id}') "
            "IS NOT DISTINCT FROM source_result_id::text AND "
            "(context#>>'{source_reference,run_id}') IS NOT DISTINCT FROM source_run_id::text AND "
            "(context#>>'{source_reference,content_sha256}') IS NOT DISTINCT FROM source_sha256",
            name="ck_scientific_context_source",
        ),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    protocol: Mapped[str] = mapped_column(String(64))
    source_result_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source_run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source_sha256: Mapped[str] = mapped_column(String(64))
    context: Mapped[dict] = mapped_column(JSONB)
    canonical_context: Mapped[str] = mapped_column(Text)
    context_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AskOverviewIntentRow(Base):
    __tablename__ = "research_ask_overview_intents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "mode", "task_kind"],
            ["research_runs.id", "research_runs.mode", "research_runs.task_kind"],
        ),
        CheckConstraint("mode='ask' AND task_kind='research'", name="ck_scientific_ask_kind"),
        CheckConstraint(
            "protocol='polymath-ask-claim-overview-v1' "
            "AND intent=jsonb_build_object('protocol',protocol)",
            name="ck_scientific_ask_protocol",
        ),
        CheckConstraint(
            "octet_length(canonical_intent) BETWEEN 1 AND 256 "
            "AND canonical_intent::jsonb=intent AND "
            "intent_sha256=encode(sha256(convert_to(canonical_intent,'UTF8')),'hex')",
            name="ck_scientific_ask_hash",
        ),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    protocol: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32))
    task_kind: Mapped[str] = mapped_column(String(32))
    intent: Mapped[dict] = mapped_column(JSONB)
    canonical_intent: Mapped[str] = mapped_column(Text)
    intent_sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AskReassessmentOverviewIntentRow(Base):
    __tablename__ = "assessment_ask_overview_intents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["run_id", "mode", "task_kind"],
            ["research_runs.id", "research_runs.mode", "research_runs.task_kind"],
        ),
        ForeignKeyConstraint(
            ["run_id", "source_result_id", "source_sha256", "basis_sha256"],
            [
                "assessment_requests.run_id",
                "assessment_requests.source_result_id",
                "assessment_requests.source_sha256",
                "assessment_requests.basis_sha256",
            ],
        ),
        ForeignKeyConstraint(
            ["source_result_id", "source_run_id", "source_sha256"],
            ["evidence_results.id", "evidence_results.run_id", "evidence_results.content_sha256"],
        ),
        CheckConstraint(
            "mode='ask' AND task_kind='reassess_finding'", name="ck_scientific_revision_kind"
        ),
        CheckConstraint(
            "protocol='polymath-ask-reassessment-overview-v1' "
            "AND intent=jsonb_build_object('protocol',protocol)",
            name="ck_scientific_revision_protocol",
        ),
        CheckConstraint(
            "octet_length(canonical_intent) BETWEEN 1 AND 256 "
            "AND canonical_intent::jsonb=intent AND "
            "intent_sha256=encode(sha256(convert_to(canonical_intent,'UTF8')),'hex')",
            name="ck_scientific_revision_hash",
        ),
        CheckConstraint(
            "(prior_overview_binding IS NULL AND prior_overview_canonical IS NULL "
            "AND prior_overview_sha256 IS NULL) OR (prior_overview_binding IS NOT NULL "
            "AND prior_overview_canonical IS NOT NULL AND prior_overview_sha256 IS NOT NULL "
            "AND jsonb_typeof(prior_overview_binding)='object' "
            "AND octet_length(prior_overview_canonical) BETWEEN 1 AND 4096 "
            "AND prior_overview_canonical::jsonb=prior_overview_binding AND "
            "prior_overview_sha256=encode(sha256(convert_to(prior_overview_canonical,'UTF8')),'hex'))",
            name="ck_scientific_revision_prior",
        ),
    )
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    protocol: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32))
    task_kind: Mapped[str] = mapped_column(String(32))
    intent: Mapped[dict] = mapped_column(JSONB)
    canonical_intent: Mapped[str] = mapped_column(Text)
    intent_sha256: Mapped[str] = mapped_column(String(64))
    source_result_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source_run_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    source_sha256: Mapped[str] = mapped_column(String(64))
    basis_sha256: Mapped[str] = mapped_column(String(64))
    prior_overview_binding: Mapped[dict | None] = mapped_column(JSONB(none_as_null=True))
    prior_overview_canonical: Mapped[str | None] = mapped_column(Text)
    prior_overview_sha256: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


SIDECAR_MODELS = (ArtifactContextBinding, AskOverviewIntentRow, AskReassessmentOverviewIntentRow)
