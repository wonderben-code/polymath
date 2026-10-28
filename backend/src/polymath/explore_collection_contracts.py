"""Pure, opt-in Explore collections; no record grants scientific/spending authority.

Visible receipts are supplied by the application from acknowledgements already
replayed to the model. A real store must independently compare them with exact
durable rows and journals under its lease; a self-consistent packet is no proof
that its claimed receipts, quotes or classification are genuine.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Literal, Protocol

from pydantic import Field, StringConstraints, TypeAdapter, model_validator

from polymath.discovery_contracts import (
    RecordNoteAction,
    StageAssessment,
    StageCommitReceipt,
    StageContext,
)
from polymath.research.models import (
    ArtifactId,
    BridgeFinding,
    ContentHash,
    InsufficientFinding,
    MemoryReadReference,
    OpenFullTextAction,
    ReadAction,
    ReadFullTextWindowAction,
    ReadMemoryAction,
    ReadMemorySourceAction,
    ResearchEvent,
    ResearchResult,
    SearchAction,
    SearchMemoryAction,
    SelectFieldsAction,
    StrictModel,
    Text,
    Usage,
    _validate_public_text,
)

EXPLORE_COLLECTION_PROTOCOL = "polymath-explore-collection-v1"
MAX_EXPLORE_BRIDGES = 4
MAX_EXPLORE_ACTIONS = 16
MAX_EXPLORE_MODEL_CALLS = 20
MAX_EXPLORE_PACKET_BYTES = 262_144
ExploreModelRequestId = Annotated[str, StringConstraints(pattern=r"^model-[0-9]{4}$")]
ExploreActionOrdinal = Annotated[int, Field(ge=1, le=MAX_EXPLORE_ACTIONS)]


def _canonical(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _step(value: str) -> int:
    if not isinstance(value, str) or re.fullmatch(r"model-[0-9]{4}", value) is None:
        raise ValueError("explore_model_step_invalid")
    number = int(value[-4:])
    if not 1 <= number <= MAX_EXPLORE_MODEL_CALLS:
        raise ValueError("explore_model_step_invalid")
    return number


def _position(request_id: str, action_ordinal: int) -> int:
    step = _step(request_id)
    if type(action_ordinal) is not int or not 1 <= action_ordinal <= min(step, MAX_EXPLORE_ACTIONS):
        raise ValueError("explore_action_position_invalid")
    return step


class ExploreModel(StrictModel):
    @model_validator(mode="after")
    def bounded_public_text(self):
        data = self.model_dump(mode="json")
        _validate_public_text(data)
        if len(_canonical(data).encode("utf-8")) > MAX_EXPLORE_PACKET_BYTES:
            raise ValueError("explore_packet_too_large")
        return self


class ExploreCollectionIntent(ExploreModel):
    protocol: Literal["polymath-explore-collection-v1"] = EXPLORE_COLLECTION_PROTOCOL
    max_bridges: Annotated[int, Field(ge=1, le=MAX_EXPLORE_BRIDGES)] = 3


def validate_explore_admission(
    intent: ExploreCollectionIntent, *, mode: str, task_kind: str, parent_result_ids=()
) -> None:
    ExploreCollectionIntent.model_validate_json(intent.model_dump_json())
    if (
        mode != "explore"
        or task_kind != "research"
        or type(parent_result_ids) not in (tuple, list)
        or parent_result_ids
    ):
        raise ValueError("explore_collection_wrong_scope")


def validate_explore_context(context: StageContext) -> None:
    value = StageContext.model_validate_json(context.model_dump_json())
    _position(value.model_request_id, value.action_ordinal)
    if value.parent_refs or value.ordinal >= MAX_EXPLORE_ACTIONS:
        raise ValueError("explore_context_parents_or_bounds_invalid")


def _usage(value: Usage) -> None:
    for item in value.model_dump().values():
        if type(item) is not int or not 0 <= item <= 2**53 - 1:
            raise ValueError("explore_usage_invalid")
    if value.model_calls > MAX_EXPLORE_MODEL_CALLS or value.calls_without_usage > value.model_calls:
        raise ValueError("explore_usage_invalid")


def _events(events: tuple[ResearchEvent, ...]) -> None:
    if len(events) > 1000 or any(
        type(event.sequence) is not int
        or event.sequence != index + 1
        or len(event.kind) > 64
        or len(event.summary) > 2000
        for index, event in enumerate(events)
    ):
        raise ValueError("explore_events_not_global_or_bounded")


class ExploreCommitPacket(ExploreModel):
    context: StageContext
    model_request_id: ExploreModelRequestId
    action_ordinal: ExploreActionOrdinal
    result: ResearchResult
    assessment: StageAssessment

    @model_validator(mode="after")
    def positive_base_bridge(self):
        validate_explore_context(self.context)
        step = _position(self.model_request_id, self.action_ordinal)
        finding = self.result.finding
        if (
            self.result.mode != "explore"
            or self.result.status != "bridge"
            or not isinstance(finding, BridgeFinding)
            or self.result.field_selections
            or self.result.overview_binding is not None
            or self.action_ordinal <= self.context.action_ordinal
            or step <= _step(self.context.model_request_id)
        ):
            raise ValueError("explore_commit_packet_invalid")
        selected = {" ".join(name.split()).casefold() for name in self.context.selection.fields}
        fields = {
            " ".join(finding.field_left.split()).casefold(),
            " ".join(finding.field_right.split()).casefold(),
        }
        if len(fields) != 2 or not fields.issubset(selected):
            raise ValueError("explore_bridge_outside_branch")
        _usage(self.result.usage)
        _events(self.result.events)
        if self.result.usage.model_calls != step:
            raise ValueError("explore_commit_usage_not_global")
        # Actual ordinary memory leads may be present. No automatic StageParent
        # edge or forced citation is invented for those reads.
        return self


def validate_explore_receipts(
    receipts: tuple[StageCommitReceipt, ...],
    *,
    intent: ExploreCollectionIntent | None = None,
    before_request_id: str | None = None,
    before_action_ordinal: int | None = None,
) -> None:
    if before_request_id is not None:
        _step(before_request_id)
    if before_action_ordinal is not None and (
        type(before_action_ordinal) is not int
        or not 1 <= before_action_ordinal <= MAX_EXPLORE_ACTIONS
    ):
        raise ValueError("explore_action_position_invalid")
    if type(receipts) is not tuple or len(receipts) > MAX_EXPLORE_BRIDGES:
        raise ValueError("explore_receipt_limit")
    if intent is not None:
        intent = ExploreCollectionIntent.model_validate_json(intent.model_dump_json())
        if len(receipts) > intent.max_bridges:
            raise ValueError("explore_receipt_limit")
    if tuple(item.ordinal for item in receipts) != tuple(range(len(receipts))):
        raise ValueError("explore_receipt_order_invalid")
    if len({item.run_id for item in receipts}) > 1 or (
        len({item.stage_id for item in receipts}) != len(receipts)
        or len({item.result_id for item in receipts}) != len(receipts)
    ):
        raise ValueError("explore_receipt_identity_invalid")
    previous_step = previous_action = 0
    for item in receipts:
        item = StageCommitReceipt.model_validate_json(item.model_dump_json())
        step = _position(item.model_request_id, item.action_ordinal)
        if item.depth != 1 or item.parent_refs:
            raise ValueError("explore_receipt_not_base")
        if step <= previous_step or item.action_ordinal <= previous_action:
            raise ValueError("explore_receipt_chronology_invalid")
        if before_request_id is not None and step >= _step(before_request_id):
            raise ValueError("explore_receipt_not_acknowledged")
        if before_action_ordinal is not None and item.action_ordinal >= before_action_ordinal:
            raise ValueError("explore_receipt_not_acknowledged")
        previous_step, previous_action = step, item.action_ordinal


class ExploreRankedBridge(ExploreModel):
    reference: MemoryReadReference
    rationale: Annotated[Text, Field(max_length=1500)]


class ExploreExcludedBridge(ExploreModel):
    reference: MemoryReadReference
    reason: Annotated[Text, Field(max_length=1500)]


class ExploreRankingDraft(ExploreModel):
    ranked: Annotated[tuple[ExploreRankedBridge, ...], Field(max_length=MAX_EXPLORE_BRIDGES)] = ()
    excluded: Annotated[
        tuple[ExploreExcludedBridge, ...], Field(max_length=MAX_EXPLORE_BRIDGES)
    ] = ()

    @model_validator(mode="after")
    def disjoint_selection(self):
        refs = [item.reference for item in self.ranked + self.excluded]
        if len(refs) > MAX_EXPLORE_BRIDGES or len({ref.result_id for ref in refs}) != len(refs):
            raise ValueError("explore_ranking_duplicate_or_limit")
        return self


def _partition(ranked, excluded, references) -> None:
    draft = ExploreRankingDraft(ranked=ranked, excluded=excluded)
    declared = {item.reference for item in draft.ranked + draft.excluded}
    if declared != set(references):
        raise ValueError("explore_ranking_partition_invalid")


class ExploreRankingPacket(ExploreRankingDraft):
    model_request_id: ExploreModelRequestId
    action_ordinal: ExploreActionOrdinal
    visible_receipts: Annotated[
        tuple[StageCommitReceipt, ...], Field(max_length=MAX_EXPLORE_BRIDGES)
    ] = ()

    @model_validator(mode="after")
    def exactly_earlier_visible_receipts(self):
        _position(self.model_request_id, self.action_ordinal)
        validate_explore_receipts(
            self.visible_receipts,
            before_request_id=self.model_request_id,
            before_action_ordinal=self.action_ordinal,
        )
        _partition(
            self.ranked, self.excluded, tuple(item.reference for item in self.visible_receipts)
        )
        return self


class ExploreRankingReceipt(ExploreRankingDraft):
    ranking_id: ArtifactId
    run_id: ArtifactId
    packet_sha256: ContentHash
    model_request_id: ExploreModelRequestId
    action_ordinal: ExploreActionOrdinal
    covered_through_ordinal: Annotated[int, Field(ge=0, le=3)] | None

    @model_validator(mode="after")
    def coherent_coverage(self):
        _position(self.model_request_id, self.action_ordinal)
        members = self.ranked + self.excluded
        count = 0 if self.covered_through_ordinal is None else self.covered_through_ordinal + 1
        if len(members) != count or any(item.reference.run_id != self.run_id for item in members):
            raise ValueError("explore_ranking_receipt_coverage_invalid")
        return self

    @property
    def selected_reference(self) -> MemoryReadReference | None:
        return self.ranked[0].reference if self.ranked else None


class ExploreFinishPacket(ExploreModel):
    ranking: ExploreRankingPacket
    reason: Annotated[Text, Field(max_length=1000)]


class ExploreCollectionOutcome(ExploreModel):
    status: Literal["completed", "insufficient_evidence", "limit_reached", "failed"]
    stages: Annotated[tuple[StageCommitReceipt, ...], Field(max_length=MAX_EXPLORE_BRIDGES)] = ()
    rankings: Annotated[
        tuple[ExploreRankingReceipt, ...], Field(max_length=MAX_EXPLORE_ACTIONS)
    ] = ()
    final_ranking: ExploreRankingReceipt | None = None
    result: ResearchResult | None = None
    events: Annotated[tuple[ResearchEvent, ...], Field(max_length=1000)] = ()
    usage: Usage
    error_code: Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,80}$")] | None = None
    reason: Annotated[Text, Field(max_length=2000)] | None = None

    @model_validator(mode="after")
    def exact_history_and_whole_usage(self):
        validate_explore_receipts(self.stages)
        _usage(self.usage)
        if (self.status == "completed") != (self.final_ranking is not None):
            raise ValueError("explore_finish_ranking_required")
        if self.final_ranking is not None and self.final_ranking not in self.rankings:
            raise ValueError("explore_final_ranking_not_observed")
        if self.final_ranking is not None and (
            self.final_ranking != self.rankings[-1]
            or self.final_ranking.covered_through_ordinal
            != (len(self.stages) - 1 if self.stages else None)
        ):
            raise ValueError("explore_final_ranking_not_terminal")
        if self.result is not None and (
            self.result.mode != "explore"
            or self.result.status in ("bridge", "answer")
            or self.result.field_selections
            or self.result.overview_binding is not None
            or self.result.status != self.status
            or self.result.usage != self.usage
            or self.result.events != self.events
        ):
            raise ValueError("explore_positive_requires_stage_commit")
        _events(self.events)
        ids, previous_step, previous_action = set(), 0, 0
        run_id = self.stages[0].run_id if self.stages else None
        maximum_step = max((_step(item.model_request_id) for item in self.stages), default=0)
        for ranking in self.rankings:
            ranking = ExploreRankingReceipt.model_validate_json(ranking.model_dump_json())
            step = _position(ranking.model_request_id, ranking.action_ordinal)
            if (
                ranking.ranking_id in ids
                or step <= previous_step
                or ranking.action_ordinal <= previous_action
                or (run_id is not None and ranking.run_id != run_id)
            ):
                raise ValueError("explore_ranking_history_invalid")
            ids.add(ranking.ranking_id)
            visible = (
                ()
                if ranking.covered_through_ordinal is None
                else self.stages[: ranking.covered_through_ordinal + 1]
            )
            if len(visible) != len(ranking.ranked + ranking.excluded):
                raise ValueError("explore_ranking_history_coverage_invalid")
            validate_explore_receipts(
                visible,
                before_request_id=ranking.model_request_id,
                before_action_ordinal=ranking.action_ordinal,
            )
            _partition(ranking.ranked, ranking.excluded, tuple(item.reference for item in visible))
            previous_step, previous_action = step, ranking.action_ordinal
            maximum_step = max(maximum_step, step)
            run_id = ranking.run_id
        if self.usage.model_calls < maximum_step:
            raise ValueError("explore_usage_not_global")
        return self

    @property
    def selected_reference(self) -> MemoryReadReference | None:
        return self.final_ranking.selected_reference if self.final_ranking is not None else None


ExploreStopPacket = ExploreCollectionOutcome


class ExploreRecordBridgeAction(ExploreModel):
    kind: Literal["record_bridge"]
    finding: BridgeFinding
    assessment: StageAssessment


class ExploreRankSavedAction(ExploreModel):
    kind: Literal["rank_saved"]
    ranking: ExploreRankingDraft


class ExploreFinishRankedAction(ExploreModel):
    kind: Literal["finish_ranked"]
    ranking: ExploreRankingDraft
    reason: Annotated[Text, Field(max_length=1000)]


class ExploreConcludeAction(ExploreModel):
    kind: Literal["conclude"]
    conclusion: InsufficientFinding


ExploreAction = Annotated[
    SearchAction
    | ReadAction
    | OpenFullTextAction
    | ReadFullTextWindowAction
    | SearchMemoryAction
    | ReadMemoryAction
    | ReadMemorySourceAction
    | SelectFieldsAction
    | RecordNoteAction
    | ExploreRecordBridgeAction
    | ExploreRankSavedAction
    | ExploreFinishRankedAction
    | ExploreConcludeAction,
    Field(discriminator="kind"),
]
EXPLORE_ACTION_ADAPTER = TypeAdapter(ExploreAction)


def explore_commit_sha256(packet: ExploreCommitPacket) -> str:
    return hashlib.sha256(canonical_explore_commit_json(packet).encode("utf-8")).hexdigest()


def explore_ranking_sha256(packet: ExploreRankingPacket) -> str:
    return hashlib.sha256(canonical_explore_ranking_json(packet).encode("utf-8")).hexdigest()


def explore_finish_sha256(packet: ExploreFinishPacket) -> str:
    return hashlib.sha256(canonical_explore_finish_json(packet).encode("utf-8")).hexdigest()


def canonical_explore_commit_json(packet: ExploreCommitPacket) -> str:
    value = ExploreCommitPacket.model_validate_json(packet.model_dump_json())
    return _canonical(value.model_dump(mode="json"))


def canonical_explore_ranking_json(packet: ExploreRankingPacket) -> str:
    value = ExploreRankingPacket.model_validate_json(packet.model_dump_json())
    return _canonical(value.model_dump(mode="json"))


def canonical_explore_finish_json(packet: ExploreFinishPacket) -> str:
    value = ExploreFinishPacket.model_validate_json(packet.model_dump_json())
    return _canonical(value.model_dump(mode="json"))


def validate_explore_commit_receipt(
    packet: ExploreCommitPacket,
    receipt: StageCommitReceipt,
    *,
    previous_receipts: tuple[StageCommitReceipt, ...] = (),
) -> None:
    value = ExploreCommitPacket.model_validate_json(packet.model_dump_json())
    receipt = StageCommitReceipt.model_validate_json(receipt.model_dump_json())
    validate_explore_receipts(previous_receipts + (receipt,))
    if (
        receipt.model_request_id != value.model_request_id
        or receipt.action_ordinal != value.action_ordinal
        or receipt.assessment != value.assessment
    ):
        raise ValueError("explore_commit_receipt_mismatch")


def validate_explore_ranking_receipt(
    packet: ExploreRankingPacket,
    receipt: ExploreRankingReceipt,
    *,
    run_id: str | None = None,
) -> None:
    value = ExploreRankingPacket.model_validate_json(packet.model_dump_json())
    receipt = ExploreRankingReceipt.model_validate_json(receipt.model_dump_json())
    expected_run = value.visible_receipts[0].run_id if value.visible_receipts else run_id
    if (
        receipt.packet_sha256 != explore_ranking_sha256(value)
        or receipt.model_request_id != value.model_request_id
        or receipt.action_ordinal != value.action_ordinal
        or receipt.covered_through_ordinal
        != (len(value.visible_receipts) - 1 if value.visible_receipts else None)
        or receipt.ranked != value.ranked
        or receipt.excluded != value.excluded
        or (expected_run is not None and receipt.run_id != expected_run)
        or (run_id is not None and receipt.run_id != run_id)
    ):
        raise ValueError("explore_ranking_receipt_mismatch")


def validate_explore_ranking(
    packet: ExploreRankingPacket,
    *,
    visible_receipts: tuple[StageCommitReceipt, ...],
    intent: ExploreCollectionIntent,
) -> None:
    value = ExploreRankingPacket.model_validate_json(packet.model_dump_json())
    validate_explore_receipts(visible_receipts, intent=intent)
    if value.visible_receipts != visible_receipts:
        raise ValueError("explore_ranking_visibility_mismatch")


def validate_explore_commit(
    packet: ExploreCommitPacket,
    *,
    intent: ExploreCollectionIntent,
    previous_receipts: tuple[StageCommitReceipt, ...] = (),
) -> None:
    value = ExploreCommitPacket.model_validate_json(packet.model_dump_json())
    validate_explore_receipts(
        previous_receipts,
        intent=intent,
        before_request_id=value.model_request_id,
        before_action_ordinal=value.action_ordinal,
    )
    if len(previous_receipts) >= intent.max_bridges:
        raise ValueError("explore_receipt_limit")


class ExploreCollectionSink(Protocol):
    async def begin_context(self, context: StageContext) -> None: ...
    async def commit(self, packet: ExploreCommitPacket) -> StageCommitReceipt: ...
    async def rank(self, packet: ExploreRankingPacket) -> ExploreRankingReceipt: ...
    async def finish(self, packet: ExploreFinishPacket) -> ExploreRankingReceipt: ...
