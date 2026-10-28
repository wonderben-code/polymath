"""Mode hooks for ONE atomic scientific stage transaction.

The registry owner validates concrete intent and structural ownership. These
hooks choose actual server-owned schemas and mode rules; they never grant a
lease, funding, source access, ranking, browser or operator authority.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from polymath.discovery_contracts import (
    DISCOVERY_ACTION_ADAPTER,
    DiscoveryIntent,
    StageCommitPacket,
    StageContext,
    discovery_packet_sha256,
)
from polymath.evidence import EvidenceError, EvidenceStore, _canonical, _hash
from polymath.research.models import BridgeFinding

SURPRISE_PROTOCOL = "polymath-surprise-ladder-v1"
EXPLORE_PROTOCOL = "polymath-explore-collection-v1"


class StageProtocolError(ValueError):
    """Fixed private diagnostics; callers retain storage/control error boundaries."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _typed(cls, value):
    encoded = value.model_dump_json() if hasattr(value, "model_dump_json") else json.dumps(value)
    return cls.model_validate_json(encoded)


@dataclass(frozen=True)
class StageProtocolBinding:
    mode: Literal["surprise", "explore"]
    intent: object
    protocol: str
    config_sha256: str

    @property
    def max_stages(self):
        return self.intent.max_stages if self.mode == "surprise" else self.intent.max_bridges

    @property
    def max_depth(self):
        return self.intent.max_depth if self.mode == "surprise" else 1

    @property
    def context_action_kinds(self):
        return (
            ("select_fields", "select_parents") if self.mode == "surprise" else ("select_fields",)
        )

    def parse_action(self, content):
        if self.mode == "surprise":
            return DISCOVERY_ACTION_ADAPTER.validate_json(content)
        from polymath.explore_collection_contracts import EXPLORE_ACTION_ADAPTER

        return EXPLORE_ACTION_ADAPTER.validate_json(content)

    def parse_packet(self, value):
        if self.mode == "surprise":
            return _typed(StageCommitPacket, value)
        from polymath.explore_collection_contracts import ExploreCommitPacket

        return _typed(ExploreCommitPacket, value)

    def packet_sha256(self, packet):
        packet = self.parse_packet(packet)
        # Reuse the actual old canonicalizer, including explicit null fields.
        if self.mode == "surprise":
            return discovery_packet_sha256(packet)
        from polymath.explore_collection_contracts import explore_commit_sha256

        return explore_commit_sha256(packet)

    def revision_reference(self, packet):
        return packet.revision_of if self.mode == "surprise" else None

    def scientific_body(self, context, result):
        body = {
            "finding": result.finding.model_dump(mode="json"),
            "quotes": [quote.model_dump(mode="json") for quote in result.quotes],
        }
        if self.mode == "surprise":
            # This is precisely the original F6 scientific body, no new domain.
            return body | {"fields": list(context.selection.fields)}
        # Actual left/right fields are already inside the exact finding. Extra
        # unused selected fields or conceptual leads cannot manufacture another
        # byte-identical bridge; this is not semantic novelty or ranking.
        return {"protocol": EXPLORE_PROTOCOL, **body}

    def scientific_sha256(self, context, result):
        return _hash(_canonical(self.scientific_body(context, result)))

    def validate_context(self, context):
        context = _typed(StageContext, context)
        if self.mode == "explore":
            from polymath.explore_collection_contracts import validate_explore_context

            try:
                validate_explore_context(context)
            except ValueError:
                raise StageProtocolError("explore_collection_context_invalid") from None
        return context

    def validate_context_action(self, context, action):
        context = self.validate_context(context)
        if action.kind not in self.context_action_kinds or (
            tuple(action.fields) != context.selection.fields
            or action.rationale != context.selection.rationale
        ):
            raise StageProtocolError("discovery_context_action_mismatch")
        refs = tuple(action.parents) if action.kind == "select_parents" else ()
        if refs != context.parent_refs:
            raise StageProtocolError("discovery_context_action_mismatch")

    def validate_commit_action(self, packet, action):
        finding = getattr(action, "finding", getattr(action, "conclusion", None))
        allowed = ("record_bridge", "conclude") if self.mode == "surprise" else ("record_bridge",)
        if (
            action.kind not in allowed
            or finding != packet.result.finding
            or getattr(action, "assessment", None) != packet.assessment
            or getattr(action, "revision_of", None) != self.revision_reference(packet)
        ):
            raise StageProtocolError("discovery_commit_action_mismatch")

    def validate_result_branch_fields(self, packet):
        packet = self.parse_packet(packet)
        self.validate_result_scope(packet.context, packet.result)
        return packet

    def validate_result_scope(self, context, result):
        context = self.validate_context(context)
        if self.mode == "explore":
            selected = {" ".join(field.split()).casefold() for field in context.selection.fields}
            if (
                result.mode != "explore"
                or result.status != "bridge"
                or not isinstance(result.finding, BridgeFinding)
                or result.field_selections
                or not {
                    " ".join(result.finding.field_left.split()).casefold(),
                    " ".join(result.finding.field_right.split()).casefold(),
                }.issubset(selected)
            ):
                raise StageProtocolError("explore_collection_branch_fields_invalid")
        elif (
            result.mode != "surprise"
            or result.status != "bridge"
            or not isinstance(result.finding, BridgeFinding)
            or not result.field_selections
            or result.field_selections[-1] != context.selection
        ):
            raise StageProtocolError("discovery_stage_packet_invalid")

    def validate_saved_stage(self, receipt, context, result, *, revision_of=None):
        """Historical scientific-context scope, never current-head authority."""
        self.validate_result_scope(context, result)
        if (
            receipt.ordinal >= self.max_stages
            or receipt.depth > self.max_depth
            or context.parent_refs != receipt.parent_refs
        ):
            raise StageProtocolError("stage_protocol_saved_scope_invalid")
        if self.mode == "surprise":
            if tuple(result.memory_reads) != receipt.parent_refs:
                raise StageProtocolError("stage_protocol_saved_scope_invalid")
        elif receipt.depth != 1 or receipt.parent_refs or revision_of is not None:
            raise StageProtocolError("stage_protocol_saved_scope_invalid")

    def commit_depth(self, selected_reads):
        if self.mode == "explore":
            if selected_reads:
                raise StageProtocolError("explore_collection_parents_not_supported")
            return 1
        return 1 + max(
            (read.depth for _, read in selected_reads if read.stage_id is not None), default=0
        )

    def memory_keys(self, session, run, result, scoped_stage_reads):
        """Ordinary Explore leads are cumulative actual reads, not stage parents."""
        if self.mode == "surprise":
            return tuple(
                (read.reference.result_id, read.reference.run_id, read.reference.content_sha256)
                for read in scoped_stage_reads.values()
            )
        try:
            # Existing validator verifies the complete actual ledger, ownership,
            # exact immutable parent hashes and current support for fresh use.
            EvidenceStore._validate_memory_reads(session, run, result)
        except EvidenceError as error:
            raise StageProtocolError(str(error)) from None
        return tuple(
            (reference.result_id, reference.run_id, reference.content_sha256)
            for reference in result.memory_reads
        )

    def additional_identity_references(self, session, run, *, identity_limit=256):
        if self.mode == "surprise":
            return ()
        from polymath.stage_memory import owned_memory_identity_references

        return owned_memory_identity_references(session, run, identity_limit=identity_limit)

    def require_surprise_terminal_protocol(self):
        if self.mode != "surprise":
            raise StageProtocolError("explore_collection_ranking_required")


def resolve_stage_protocol(session, run, *, owner_loader: Callable | None = None):
    """Wrap one authoritative owner-registry reader; never infer from Run.mode alone."""
    if owner_loader is None:
        from polymath.stage_owners import load_stage_owner

        owner_loader = load_stage_owner
    owner = owner_loader(session, run)
    if owner is None:
        return None
    if owner.mode == "surprise":
        intent = _typed(DiscoveryIntent, owner.intent)
        expected = SURPRISE_PROTOCOL
    elif owner.mode == "explore":
        from polymath.explore_collection_contracts import ExploreCollectionIntent

        intent = _typed(ExploreCollectionIntent, owner.intent)
        expected = EXPLORE_PROTOCOL
    else:
        raise StageProtocolError("stage_protocol_storage_unavailable")
    if (
        owner.run_id != run.id
        or owner.mode != run.mode
        or owner.task_kind != run.task_kind
        or run.task_kind != "research"
        or owner.protocol != expected
        or intent.protocol != expected
        or owner.config_sha256 != _hash(_canonical(intent.model_dump(mode="json")))
    ):
        raise StageProtocolError("stage_protocol_storage_unavailable")
    return StageProtocolBinding(owner.mode, intent, owner.protocol, owner.config_sha256)
