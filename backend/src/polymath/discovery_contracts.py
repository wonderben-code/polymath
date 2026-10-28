"""Immutable contracts for an explicitly opted-in, bounded Surprise ladder.

These contracts grant no model, source or spending authority. Stage persistence
independently validates the actual read/search journal and original quotations.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Literal, Protocol

from pydantic import Field, StringConstraints, TypeAdapter, model_validator

from polymath.research.models import (
    ArtifactId,
    BridgeFinding,
    ContentHash,
    FieldSelection,
    InsufficientFinding,
    MemoryReadReference,
    MemorySourceReference,
    OpenFullTextAction,
    ReadAction,
    ReadFullTextWindowAction,
    ResearchEvent,
    ResearchResult,
    SearchAction,
    SearchMemoryAction,
    SelectFieldsAction,
    SourceDocument,
    StrictModel,
    Text,
    Usage,
    VerifiedQuote,
    _validate_public_text,
)

DISCOVERY_PROTOCOL = "polymath-surprise-ladder-v1"
ModelRequestId = Annotated[str, StringConstraints(pattern=r"^model-[0-9]{4}$")]
ActionOrdinal = Annotated[int, Field(ge=1, le=20)]
ParentReferences = Annotated[tuple[MemoryReadReference, ...], Field(max_length=4)]


class DiscoveryRejection(ValueError):
    """An explicit, safe model-validation rejection, never an opaque DB failure."""

    def __init__(self, code: str):
        self.code = (
            code
            if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,80}", code)
            else "discovery_stage_rejected"
        )
        super().__init__(self.code)


class DiscoveryModel(StrictModel):
    @model_validator(mode="after")
    def public_text(self):
        _validate_public_text(self.model_dump(mode="json"))
        return self


class DiscoveryIntent(DiscoveryModel):
    protocol: Literal["polymath-surprise-ladder-v1"] = DISCOVERY_PROTOCOL
    max_stages: Annotated[int, Field(ge=1, le=4)] = 3
    max_depth: Annotated[int, Field(ge=1, le=3)] = 2


class StageAssessment(DiscoveryModel):
    novelty: Literal["known", "partly_known", "not_found_in_bounded_search", "uncertain"]
    novelty_rationale: Annotated[Text, Field(max_length=2000)]
    confidence: Literal["tentative", "moderate", "strong"]
    confidence_score: Annotated[int, Field(ge=0, le=100)]
    confidence_scope: Literal["uncalibrated_model_judgement"]
    confidence_rationale: Annotated[Text, Field(max_length=2000)]
    limitations: Annotated[
        tuple[Annotated[Text, Field(max_length=1000)], ...], Field(min_length=1, max_length=8)
    ]


def _distinct_parents(parents: tuple[MemoryReadReference, ...]) -> None:
    if len(parents) not in (0, 2, 3, 4) or len({p.result_id for p in parents}) != len(parents):
        raise ValueError("discovery_parent_selection_invalid")


class StageContext(DiscoveryModel):
    ordinal: Annotated[int, Field(ge=0, le=19)]
    model_request_id: ModelRequestId
    action_ordinal: ActionOrdinal
    selection: FieldSelection
    parent_refs: ParentReferences = ()

    @model_validator(mode="after")
    def distinct_parents(self):
        _distinct_parents(self.parent_refs)
        return self


class StageParentQuote(DiscoveryModel):
    retrieval_id: ArtifactId
    version_id: ArtifactId
    quote: VerifiedQuote


class StageParentFinding(DiscoveryModel):
    reference: MemoryReadReference
    stage_id: ArtifactId | None
    ordinal: Annotated[int, Field(ge=0, le=3)] | None
    depth: Annotated[int, Field(ge=0, le=3)]
    finding: BridgeFinding
    sources: Annotated[tuple[MemorySourceReference, ...], Field(max_length=64)]
    quotes: Annotated[tuple[StageParentQuote, ...], Field(min_length=2, max_length=12)]

    @model_validator(mode="after")
    def bound_quotes(self):
        if (self.stage_id is None) != (self.ordinal is None):
            raise ValueError("discovery_parent_stage_mismatch")
        if self.stage_id is not None and self.depth < 1:
            raise ValueError("discovery_parent_depth_invalid")
        source_map = {item.retrieval_id: item for item in self.sources}
        if len(source_map) != len(self.sources):
            raise ValueError("discovery_parent_source_duplicate")
        if len(self.quotes) != len(self.finding.evidence):
            raise ValueError("discovery_parent_quote_mismatch")
        for item, evidence in zip(self.quotes, self.finding.evidence, strict=True):
            source = source_map.get(item.retrieval_id)
            quote = item.quote
            if source is None or (
                (quote.source_id, quote.side, quote.quote, quote.supports)
                != (evidence.source_id, evidence.side, evidence.quote, evidence.supports)
                or quote.source_id != source.source.source_id
                or quote.source_sha256 != source.content_sha256
                or quote.content_kind != source.content_kind
                or quote.start < 0
                or quote.end - quote.start != len(quote.quote)
            ):
                raise ValueError("discovery_parent_quote_mismatch")
        return self


class StageCommitPacket(DiscoveryModel):
    context: StageContext
    model_request_id: ModelRequestId
    action_ordinal: ActionOrdinal
    result: ResearchResult
    assessment: StageAssessment
    revision_of: MemoryReadReference | None = None

    @model_validator(mode="after")
    def positive_stage(self):
        if (
            self.result.mode != "surprise"
            or self.result.status != "bridge"
            or not isinstance(self.result.finding, BridgeFinding)
            or self.action_ordinal <= self.context.action_ordinal
            or self.model_request_id <= self.context.model_request_id
            or not self.result.field_selections
            or self.result.field_selections[-1] != self.context.selection
        ):
            raise ValueError("discovery_stage_packet_invalid")
        if not set(self.context.parent_refs).issubset(set(self.result.memory_reads)):
            raise ValueError("discovery_parent_not_read")
        return self


class StageCommitReceipt(DiscoveryModel):
    stage_id: ArtifactId
    run_id: ArtifactId
    ordinal: Annotated[int, Field(ge=0, le=3)]
    result_id: ArtifactId
    result_sha256: ContentHash
    depth: Annotated[int, Field(ge=1, le=3)]
    assessment: StageAssessment
    parent_refs: ParentReferences
    action_ordinal: ActionOrdinal
    model_request_id: ModelRequestId

    @model_validator(mode="after")
    def distinct_parents(self):
        _distinct_parents(self.parent_refs)
        return self

    @property
    def reference(self) -> MemoryReadReference:
        return MemoryReadReference(
            result_id=self.result_id, run_id=self.run_id, content_sha256=self.result_sha256
        )


class DiscoveryFinishPacket(DiscoveryModel):
    model_request_id: ModelRequestId
    action_ordinal: ActionOrdinal
    stage: MemoryReadReference
    reason: Annotated[Text, Field(max_length=1000)]


class DiscoveryOutcome(DiscoveryModel):
    status: Literal["completed", "insufficient_evidence", "limit_reached", "failed"]
    selected_stage: StageCommitReceipt | None = None
    stages: Annotated[tuple[StageCommitReceipt, ...], Field(max_length=4)] = ()
    result: ResearchResult | None = None
    events: tuple[ResearchEvent, ...]
    usage: Usage
    error_code: Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,80}$")] | None = None
    reason: Annotated[Text, Field(max_length=2000)] | None = None

    @model_validator(mode="after")
    def selected_only_on_finish(self):
        if (self.status == "completed") != (self.selected_stage is not None):
            raise ValueError("discovery_finish_selection_invalid")
        if self.selected_stage is not None and self.selected_stage not in self.stages:
            raise ValueError("discovery_finish_stage_not_observed")
        if self.result is not None and self.result.status in ("bridge", "answer"):
            raise ValueError("discovery_positive_requires_stage_commit")
        if tuple(stage.ordinal for stage in self.stages) != tuple(range(len(self.stages))):
            raise ValueError("discovery_stage_order_invalid")
        if (
            len({stage.run_id for stage in self.stages}) > 1
            or len({stage.stage_id for stage in self.stages}) != len(self.stages)
            or len({stage.result_id for stage in self.stages}) != len(self.stages)
        ):
            raise ValueError("discovery_stage_identity_mismatch")
        return self


class RecordNoteAction(DiscoveryModel):
    kind: Literal["record_note"]
    hypothesis_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")]
    state: Literal["proposed", "revised", "discarded"]
    summary: Annotated[Text, Field(max_length=1000)]
    reason: Annotated[Text, Field(max_length=1000)]


class SelectParentsAction(FieldSelection):
    kind: Literal["select_parents"]
    parents: Annotated[tuple[MemoryReadReference, ...], Field(min_length=2, max_length=4)]

    @model_validator(mode="after")
    def distinct_parents(self):
        _distinct_parents(self.parents)
        return self


class ReadStageAction(DiscoveryModel):
    kind: Literal["read_stage"]
    result_id: ArtifactId


class ReadStageSourceAction(DiscoveryModel):
    kind: Literal["read_stage_source"]
    result_id: ArtifactId
    retrieval_id: ArtifactId


class RecordBridgeAction(DiscoveryModel):
    kind: Literal["record_bridge"]
    finding: BridgeFinding
    assessment: StageAssessment
    revision_of: MemoryReadReference | None = None


class FinishSavedAction(DiscoveryModel):
    kind: Literal["finish_saved"]
    stage: MemoryReadReference
    reason: Annotated[Text, Field(max_length=1000)]


class DiscoveryConcludeAction(DiscoveryModel):
    kind: Literal["conclude"]
    conclusion: BridgeFinding | InsufficientFinding
    assessment: StageAssessment | None = None

    @model_validator(mode="after")
    def assessed_positive(self):
        if isinstance(self.conclusion, BridgeFinding) and self.assessment is None:
            raise ValueError("discovery_stage_assessment_required")
        return self


DiscoveryAction = Annotated[
    SearchAction
    | ReadAction
    | OpenFullTextAction
    | ReadFullTextWindowAction
    | SearchMemoryAction
    | SelectFieldsAction
    | RecordNoteAction
    | SelectParentsAction
    | ReadStageAction
    | ReadStageSourceAction
    | RecordBridgeAction
    | FinishSavedAction
    | DiscoveryConcludeAction,
    Field(discriminator="kind"),
]
DISCOVERY_ACTION_ADAPTER = TypeAdapter(DiscoveryAction)


class DiscoverySink(Protocol):
    async def begin_context(self, context: StageContext) -> None: ...
    async def read_parent(self, result_id: str, context_ordinal: int) -> StageParentFinding: ...
    async def read_parent_source(
        self, result_id: str, retrieval_id: str, context_ordinal: int
    ) -> SourceDocument: ...
    async def commit(self, packet: StageCommitPacket) -> StageCommitReceipt: ...
    async def finish(self, packet: DiscoveryFinishPacket) -> None: ...


def discovery_packet_sha256(packet: StageCommitPacket) -> str:
    value = StageCommitPacket.model_validate_json(packet.model_dump_json())
    return hashlib.sha256(
        json.dumps(
            value.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
