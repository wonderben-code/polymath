"""Immutable native Meta research authority facts and request/accounting helpers.

This module grants no authority, sends no HTTP, and constructs no model. The
store verifies saved evidence/receipt facts and current human ownership; native
dispatch rechecks them for every fresh paid call. Scientific controllers remain
the existing Ask/Explore/Surprise/artifact/reassessment implementations.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_serializer, model_validator

from polymath.meta_probe_contracts import (
    MetaObservation,
    MetaProbePolicy,
    ModelId,
    ProbeId,
    ProviderAccountAttestation,
    _assess_reported_meta_usage,
)
from polymath.meta_readiness_contracts import (
    META_COMPLETIONS_URL,
    Digest,
    EvidenceReferences,
    MetaReadinessProposal,
    _ReadinessModel,
    _utc,
    proposal_sha256,
    quote_proposal,
)
from polymath.scientific_context_contracts import (
    ArtifactScientificContextIntent,
    AskOverviewIntent,
    AskReassessmentOverviewIntent,
)

if TYPE_CHECKING:
    from polymath.research.meta import ModelReply

RESEARCH_PROTOCOL = "polymath-meta-research-activation-v1"
REQUEST_PROTOCOL = "polymath-meta-research-request-v1"
TaskKind = Literal["research", "formalise", "write_summary", "write_paper", "reassess_finding"]
TASK_KINDS = ("research", "formalise", "write_summary", "write_paper", "reassess_finding")
CONTROLLER_PROTOCOLS = {
    "research": "polymath-meta-research-controller-v1",
    "formalise": "polymath-meta-artifact-controller-v1",
    "write_summary": "polymath-meta-artifact-controller-v1",
    "write_paper": "polymath-meta-artifact-controller-v1",
    "reassess_finding": "polymath-meta-assessment-controller-v1",
}


def _canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NativeResearchPolicy:
    paid_research_enabled: bool = False
    native_account_id: str | None = None
    model_id: str | None = None
    api_key: SecretStr | None = field(default=None, repr=False)
    consent_seconds: int = 1200

    def __post_init__(self):
        if (
            type(self.paid_research_enabled) is not bool
            or type(self.consent_seconds) is not int
            or not 60 <= self.consent_seconds <= 3600
        ):
            raise ValueError("invalid native research server policy")
        MetaProbePolicy(
            native_account_id=self.native_account_id, model_id=self.model_id, api_key=self.api_key
        )

    @property
    def configured(self):
        return bool(
            self.native_account_id
            and self.model_id
            and self.api_key is not None
            and self.api_key.get_secret_value().strip()
        )

    @property
    def credential_binding_sha256(self):
        return MetaProbePolicy(
            native_account_id=self.native_account_id, model_id=self.model_id, api_key=self.api_key
        ).credential_binding_sha256


def research_policy_sha256(policy: NativeResearchPolicy) -> str:
    return _sha(
        {
            "paid_research_enabled": policy.paid_research_enabled,
            "native_account_id": policy.native_account_id,
            "model_id": policy.model_id,
            "credential_binding_sha256": policy.credential_binding_sha256,
            "consent_seconds": policy.consent_seconds,
        }
    )


class MetaResearchMessage(_ReadinessModel):
    role: Literal["system", "user", "assistant"]
    content: Annotated[str, Field(min_length=1, max_length=90000)]

    @field_validator("content")
    @classmethod
    def unicode_text(cls, value):
        value.encode("utf-8")
        return value


class MetaResearchPayload(_ReadinessModel):
    model: ModelId
    messages: Annotated[tuple[MetaResearchMessage, ...], Field(min_length=1, max_length=64)]
    max_tokens: Annotated[int, Field(ge=16, le=4096)]
    stream: Literal[False] = False

    @field_validator("messages", mode="before")
    @classmethod
    def json_messages(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("stream", mode="before")
    @classmethod
    def no_stream(cls, value):
        if value is not False:
            raise ValueError("native research streaming is disabled")
        return value

    @model_validator(mode="after")
    def context_bound(self):
        if sum(len(message.content) for message in self.messages) > 90000:
            raise ValueError("native research context limit exceeded")
        return self


class MetaResearchRequest(_ReadinessModel):
    method: Literal["POST"] = "POST"
    endpoint: Literal["https://api.meta.ai/v1/chat/completions"] = META_COMPLETIONS_URL
    payload: MetaResearchPayload


def build_meta_research_request(
    proposal: MetaReadinessProposal, messages, *, max_output_tokens: int
) -> MetaResearchRequest:
    proposal = MetaReadinessProposal.model_validate_json(proposal.model_dump_json())
    if max_output_tokens != proposal.application_limits.max_output_tokens:
        raise ValueError("native request output cap differs from its exact reviewed profile")
    bounded_messages = []
    for index, message in enumerate(messages):
        if index >= 64:
            raise ValueError("native research message limit exceeded")
        bounded_messages.append(message)
    request = MetaResearchRequest(
        payload=MetaResearchPayload(
            model=proposal.model_id, messages=tuple(bounded_messages), max_tokens=max_output_tokens
        )
    )
    if (
        sum(len(message.content) for message in request.payload.messages)
        > proposal.application_limits.max_context_characters
    ):
        raise ValueError("native request exceeds its reviewed context limit")
    return request


def meta_research_request_sha256(request: MetaResearchRequest) -> str:
    request = MetaResearchRequest.model_validate_json(request.model_dump_json())
    return _sha(request.model_dump(mode="json"))


def research_step_sha256(
    request: MetaResearchRequest, *, activation_sha256: str, profile_sha256: str, request_id: str
) -> str:
    for digest in (activation_sha256, profile_sha256):
        if not isinstance(digest, str) or re.fullmatch(r"[a-f0-9]{64}", digest) is None:
            raise ValueError("native step requires exact activation and profile hashes")
    if not isinstance(request_id, str) or re.fullmatch(r"model-[0-9]{4}", request_id) is None:
        raise ValueError("native step requires its deterministic model request ID")
    return _sha(
        {
            "protocol": REQUEST_PROTOCOL,
            "activation_sha256": activation_sha256,
            "profile_sha256": profile_sha256,
            "request_id": request_id,
            "request_sha256": meta_research_request_sha256(request),
        }
    )


class NativeTaskProfile(_ReadinessModel):
    task_kind: TaskKind
    controller_protocol: Annotated[str, Field(max_length=80)]
    controller_sha256: Digest
    request_protocol: Literal["polymath-meta-research-request-v1"] = REQUEST_PROTOCOL
    max_model_calls: Annotated[int, Field(ge=1, le=20)]
    max_actions: Annotated[int, Field(ge=1, le=16)]
    max_repairs: Literal[2] = 2
    max_output_tokens: Annotated[int, Field(ge=16, le=4096)]
    max_context_characters: Annotated[int, Field(ge=1000, le=90000)]
    request_timeout_seconds: Annotated[float, Field(ge=0.001, le=60)]
    model_timeout_seconds: Annotated[float, Field(ge=30.001, le=90)]
    tool_timeout_seconds: Literal[5.0, 20.0]
    max_sources: Literal[200] = 200

    @model_validator(mode="after")
    def supported_controller(self):
        research = self.task_kind == "research"
        artifact = self.task_kind in ("formalise", "write_summary", "write_paper")
        if (
            re.fullmatch(r"[a-z0-9-]{1,80}", self.controller_protocol) is None
            or self.max_model_calls > (20 if research else 12)
            or self.max_actions != (16 if research else 12)
            or self.tool_timeout_seconds != (5.0 if artifact else 20.0)
            or self.model_timeout_seconds != self.request_timeout_seconds + 30.0
        ):
            raise ValueError("native task profile differs from its supported controller protocol")
        return self


def controller_protocol_sha256(task_kind: TaskKind) -> str:
    """Fingerprint current static controller semantics, only for fresh authority."""
    base = {
        "orchestration_protocol": "polymath-native-controller-orchestration-v1",
        "task_kind": task_kind,
    }
    if task_kind == "research":
        from polymath.research.controller import ACTION_ADAPTER, MODE_INSTRUCTIONS, SYSTEM_PROMPT

        value = base | {
            "system_prompt": SYSTEM_PROMPT,
            "mode_instructions": MODE_INSTRUCTIONS,
            "action_schema": ACTION_ADAPTER.json_schema(),
        }
    elif task_kind in ("formalise", "write_summary", "write_paper"):
        from polymath.artifact_controller import (
            ARTIFACT_ACTION_ADAPTER,
            KIND_INSTRUCTIONS,
            SYSTEM_PROMPT,
        )

        value = base | {
            "system_prompt": SYSTEM_PROMPT,
            "kind_instructions": KIND_INSTRUCTIONS[task_kind],
            "action_schema": ARTIFACT_ACTION_ADAPTER.json_schema(),
        }
    elif task_kind == "reassess_finding":
        from polymath.assessment_controller import (
            ASSESSMENT_ACTION_ADAPTER,
            EXCERPT_LIMITATION,
            SYSTEM_PROMPT,
        )

        value = base | {
            "system_prompt": SYSTEM_PROMPT,
            "excerpt_limitation": EXCERPT_LIMITATION,
            "action_schema": ASSESSMENT_ACTION_ADAPTER.json_schema(),
        }
    else:
        raise ValueError("unsupported native task protocol")
    return _sha(value)


def minimum_controller_context_characters(task_kind: TaskKind) -> int:
    """Exact current fixed prompt/schema plus empty dynamic-wrapper overhead, never tokens."""
    if task_kind == "research":
        from polymath.research.controller import ACTION_ADAPTER, MODE_INSTRUCTIONS, SYSTEM_PROMPT

        schema = json.dumps(ACTION_ADAPTER.json_schema(), separators=(",", ":"))
        values = []
        for mode, instruction in MODE_INSTRUCTIONS.items():
            system = (
                SYSTEM_PROMPT
                + "\nCurrent mode: "
                + mode
                + ". "
                + instruction
                + "\nAction schema:\n"
                + schema
            )
            wrapper = json.dumps(
                {
                    "mode": mode,
                    "research_goal": "",
                    "memory_available": False,
                    "parent_result_ids": [],
                }
            )
            values.append(len(system) + len(wrapper))
        return max(values)
    if task_kind in ("formalise", "write_summary", "write_paper"):
        from polymath.artifact_controller import (
            ARTIFACT_ACTION_ADAPTER,
            KIND_INSTRUCTIONS,
            SYSTEM_PROMPT,
        )

        schema = json.dumps(ARTIFACT_ACTION_ADAPTER.json_schema(), separators=(",", ":"))
        system = (
            SYSTEM_PROMPT
            + "\nCurrent task: "
            + task_kind
            + ". "
            + KIND_INSTRUCTIONS[task_kind]
            + "\nAction schema:\n"
            + schema
        )
        return len(system) + len(json.dumps({"kind": task_kind, "research_basis": {}}))
    if task_kind == "reassess_finding":
        from polymath.assessment_controller import ASSESSMENT_ACTION_ADAPTER, SYSTEM_PROMPT

        schema = json.dumps(ASSESSMENT_ACTION_ADAPTER.json_schema(), separators=(",", ":"))
        return len(SYSTEM_PROMPT + "\nAction schema:\n" + schema) + len(
            json.dumps({"assessment_basis": {}})
        )
    raise ValueError("unsupported native task protocol")


def task_profile_sha256(profile: NativeTaskProfile) -> str:
    kind = (
        NativeDiscoveryProfile if isinstance(profile, NativeDiscoveryProfile) else NativeTaskProfile
    )
    profile = kind.model_validate_json(profile.model_dump_json())
    return _sha(profile.model_dump(mode="json"))


class NativeTaskProfileBinding(_ReadinessModel):
    profile: NativeTaskProfile
    profile_sha256: Digest

    @model_validator(mode="after")
    def exact_profile(self):
        if self.profile_sha256 != task_profile_sha256(self.profile):
            raise ValueError("native task profile hash mismatch")
        return self


SCIENTIFIC_CONTEXT_PROFILES_PROTOCOL = "polymath-scientific-context-profiles-v1"
ScientificCapability = Literal[
    "artifact_scientific_context_v1", "ask_claim_overview_v1", "ask_reassessment_overview_v1"
]
SCIENTIFIC_CAPABILITIES = (
    "artifact_scientific_context_v1",
    "ask_claim_overview_v1",
    "ask_reassessment_overview_v1",
)
SCIENTIFIC_PROFILE_ROWS = (
    (
        "artifact_scientific_context_v1",
        "formalise",
        None,
        "polymath-meta-artifact-scientific-context-v1",
    ),
    (
        "artifact_scientific_context_v1",
        "write_summary",
        None,
        "polymath-meta-artifact-scientific-context-v1",
    ),
    (
        "artifact_scientific_context_v1",
        "write_paper",
        None,
        "polymath-meta-artifact-scientific-context-v1",
    ),
    ("ask_claim_overview_v1", "research", "ask", "polymath-meta-ask-claim-overview-v1"),
    (
        "ask_reassessment_overview_v1",
        "reassess_finding",
        "ask",
        "polymath-meta-ask-reassessment-overview-v1",
    ),
)


class ScientificContextIntent(_ReadinessModel):
    """Operator requests server derivation; this is not supplied profile authority."""

    protocol: Literal["polymath-scientific-context-profiles-v1"] = (
        SCIENTIFIC_CONTEXT_PROFILES_PROTOCOL
    )


class NativeScientificContextProfileBinding(_ReadinessModel):
    capability: ScientificCapability
    mode: Literal["ask"] | None
    profile: NativeTaskProfile
    profile_sha256: Digest

    @model_validator(mode="after")
    def exact_profile(self):
        if self.profile_sha256 != task_profile_sha256(self.profile):
            raise ValueError("scientific_context_profile_hash_mismatch")
        row = (self.capability, self.profile.task_kind, self.mode, self.profile.controller_protocol)
        if row not in SCIENTIFIC_PROFILE_ROWS:
            raise ValueError("scientific_context_profile_scope_invalid")
        return self


class NativeScientificContextProfiles(_ReadinessModel):
    protocol: Literal["polymath-scientific-context-profiles-v1"] = (
        SCIENTIFIC_CONTEXT_PROFILES_PROTOCOL
    )
    bindings: Annotated[
        tuple[NativeScientificContextProfileBinding, ...], Field(min_length=5, max_length=5)
    ]

    @field_validator("bindings", mode="before")
    @classmethod
    def json_bindings(cls, value):
        if isinstance(value, list):
            return tuple(
                NativeScientificContextProfileBinding.model_validate_json(_canonical(item))
                if isinstance(item, dict)
                else item
                for item in value
            )
        return value

    @model_validator(mode="after")
    def exact_order(self):
        rows = tuple(
            (item.capability, item.profile.task_kind, item.mode, item.profile.controller_protocol)
            for item in self.bindings
        )
        if rows != SCIENTIFIC_PROFILE_ROWS:
            raise ValueError("scientific_context_profile_order_invalid")
        return self


def validate_historical_scientific_context_profiles(extension, proposal) -> None:
    """Check saved identity/ceilings without loading current controller code."""
    value = NativeScientificContextProfiles.model_validate_json(extension.model_dump_json())
    proposal = MetaReadinessProposal.model_validate_json(proposal.model_dump_json())
    limits = proposal.application_limits
    for item in value.bindings:
        profile = item.profile
        if (
            profile.max_model_calls > limits.max_model_calls
            or profile.max_output_tokens != limits.max_output_tokens
            or profile.max_context_characters != limits.max_context_characters
            or profile.request_timeout_seconds != limits.request_timeout_seconds
        ):
            raise ValueError("historical_scientific_profile_exceeds_proposal")


def _scientific_protocol_payload(capability, task_kind):
    """Fresh only. Missing actual variant helpers is an unavailable protocol."""
    try:
        if capability == "artifact_scientific_context_v1":
            from polymath.artifact_controller import artifact_scientific_context_protocol_payload

            return artifact_scientific_context_protocol_payload(task_kind)
        if capability == "ask_claim_overview_v1" and task_kind == "research":
            from polymath.research.ask_overview import ask_overview_protocol_payload

            return ask_overview_protocol_payload()
        if capability == "ask_reassessment_overview_v1" and task_kind == "reassess_finding":
            from polymath.assessment_overview import ask_reassessment_overview_protocol_payload

            return ask_reassessment_overview_protocol_payload()
    except ImportError:
        raise ValueError("scientific_context_protocol_unavailable") from None
    raise ValueError("scientific_context_profile_scope_invalid")


def scientific_controller_sha256(capability: ScientificCapability, task_kind: TaskKind) -> str:
    return _sha(_scientific_protocol_payload(capability, task_kind))


def minimum_scientific_context_characters(capability, task_kind) -> int:
    try:
        if capability == "artifact_scientific_context_v1":
            from polymath.artifact_controller import (
                minimum_artifact_scientific_context_characters,
            )

            return minimum_artifact_scientific_context_characters(task_kind)
        if capability == "ask_claim_overview_v1" and task_kind == "research":
            from polymath.research.ask_overview import ask_overview_initial_messages

            return sum(
                len(item["content"])
                for item in ask_overview_initial_messages("", memory_available=True)
            )
        if capability == "ask_reassessment_overview_v1" and task_kind == "reassess_finding":
            from polymath.assessment_overview import ask_reassessment_overview_initial_messages

            return sum(
                len(item["content"]) for item in ask_reassessment_overview_initial_messages(None)
            )
    except ImportError:
        raise ValueError("scientific_context_protocol_unavailable") from None
    raise ValueError("scientific_context_profile_scope_invalid")


def derive_scientific_context_profiles(proposal) -> NativeScientificContextProfiles:
    proposal = MetaReadinessProposal.model_validate_json(proposal.model_dump_json())
    base = {item.profile.task_kind: item.profile for item in derive_task_profiles(proposal)}
    bindings = []
    for capability, task_kind, mode, protocol in SCIENTIFIC_PROFILE_ROWS:
        if proposal.application_limits.max_context_characters < (
            minimum_scientific_context_characters(capability, task_kind)
        ):
            raise ValueError("scientific_context_fixed_prompt_exceeds_proposal")
        original = base[task_kind]
        profile = NativeTaskProfile.model_validate_json(
            _canonical(
                original.model_dump(mode="json")
                | {
                    "controller_protocol": protocol,
                    "controller_sha256": scientific_controller_sha256(capability, task_kind),
                }
            )
        )
        bindings.append(
            NativeScientificContextProfileBinding(
                capability=capability,
                mode=mode,
                profile=profile,
                profile_sha256=task_profile_sha256(profile),
            )
        )
    return NativeScientificContextProfiles(bindings=tuple(bindings))


def validate_current_scientific_context_profiles(extension, proposal) -> None:
    validate_historical_scientific_context_profiles(extension, proposal)
    value = NativeScientificContextProfiles.model_validate_json(extension.model_dump_json())
    if value != derive_scientific_context_profiles(proposal):
        raise ValueError("scientific_context_profile_changed")


class NativeDiscoveryProfile(_ReadinessModel):
    task_kind: Literal["research"] = "research"
    discovery_protocol: Literal["polymath-surprise-ladder-v1"] = "polymath-surprise-ladder-v1"
    controller_protocol: Annotated[str, Field(pattern=r"^[a-z0-9-]{1,80}$")]
    controller_sha256: Digest
    request_protocol: Literal["polymath-meta-research-request-v1"] = REQUEST_PROTOCOL
    max_model_calls: Annotated[int, Field(ge=1, le=20)]
    max_actions: Literal[20] = 20
    max_repairs: Literal[2] = 2
    max_output_tokens: Annotated[int, Field(ge=16, le=4096)]
    max_context_characters: Annotated[int, Field(ge=1000, le=90000)]
    request_timeout_seconds: Annotated[float, Field(ge=0.001, le=60)]
    model_timeout_seconds: Annotated[float, Field(ge=30.001, le=90)]
    tool_timeout_seconds: Literal[20.0] = 20.0
    max_sources: Literal[200] = 200
    max_stages: Annotated[int, Field(ge=1, le=4)]
    max_depth: Annotated[int, Field(ge=1, le=3)]

    @model_validator(mode="after")
    def reviewed_deadline(self):
        if self.model_timeout_seconds != self.request_timeout_seconds + 30.0:
            raise ValueError("discovery native deadline differs from its reviewed allowance")
        return self


class NativeDiscoveryProfileBinding(_ReadinessModel):
    profile: NativeDiscoveryProfile
    profile_sha256: Digest

    @model_validator(mode="after")
    def exact_profile(self):
        if self.profile_sha256 != task_profile_sha256(self.profile):
            raise ValueError("native discovery profile hash mismatch")
        return self


def discovery_controller_sha256() -> str:
    from polymath.discovery_contracts import DISCOVERY_ACTION_ADAPTER, DISCOVERY_PROTOCOL
    from polymath.research.discovery import DISCOVERY_SYSTEM_PROMPT, PRIOR_WORK_IDENTITY_PROTOCOL

    return _sha(
        {
            "orchestration_protocol": "polymath-discovery-controller-orchestration-v2",
            "discovery_protocol": DISCOVERY_PROTOCOL,
            "prior_work_identity_protocol": PRIOR_WORK_IDENTITY_PROTOCOL,
            "system_prompt": DISCOVERY_SYSTEM_PROMPT,
            "action_schema": DISCOVERY_ACTION_ADAPTER.json_schema(),
        }
    )


def derive_discovery_ladder_profile(
    proposal: MetaReadinessProposal, discovery
) -> NativeDiscoveryProfileBinding:
    from polymath.discovery_contracts import DiscoveryIntent
    from polymath.research.discovery import discovery_initial_messages

    proposal = MetaReadinessProposal.model_validate_json(proposal.model_dump_json())
    intent = DiscoveryIntent.model_validate_json(discovery.model_dump_json())
    limits = proposal.application_limits
    if (
        sum(len(message["content"]) for message in discovery_initial_messages("", intent, False))
        > limits.max_context_characters
    ):
        raise ValueError("reviewed context cannot fit the fixed discovery prompt/schema")
    profile = NativeDiscoveryProfile(
        controller_protocol="polymath-meta-discovery-controller-v1",
        controller_sha256=discovery_controller_sha256(),
        max_model_calls=min(limits.max_model_calls, 20),
        max_output_tokens=limits.max_output_tokens,
        max_context_characters=limits.max_context_characters,
        request_timeout_seconds=limits.request_timeout_seconds,
        model_timeout_seconds=limits.request_timeout_seconds + 30.0,
        max_stages=intent.max_stages,
        max_depth=intent.max_depth,
    )
    return NativeDiscoveryProfileBinding(
        profile=profile, profile_sha256=task_profile_sha256(profile)
    )


def derive_task_profiles(proposal: MetaReadinessProposal) -> tuple[NativeTaskProfileBinding, ...]:
    proposal = MetaReadinessProposal.model_validate_json(proposal.model_dump_json())
    limits = proposal.application_limits
    values = []
    for kind in TASK_KINDS:
        if limits.max_context_characters < minimum_controller_context_characters(kind):
            raise ValueError("reviewed context cannot fit a fixed native controller prompt/schema")
        research = kind == "research"
        profile = NativeTaskProfile(
            task_kind=kind,
            controller_protocol=CONTROLLER_PROTOCOLS[kind],
            controller_sha256=controller_protocol_sha256(kind),
            max_model_calls=min(limits.max_model_calls, 20 if research else 12),
            max_actions=16 if research else 12,
            max_output_tokens=limits.max_output_tokens,
            max_context_characters=limits.max_context_characters,
            request_timeout_seconds=limits.request_timeout_seconds,
            model_timeout_seconds=limits.request_timeout_seconds + 30,
            tool_timeout_seconds=5.0
            if kind in ("formalise", "write_summary", "write_paper")
            else 20.0,
        )
        values.append(
            NativeTaskProfileBinding(profile=profile, profile_sha256=task_profile_sha256(profile))
        )
    return tuple(values)


class PassedCanaryBinding(_ReadinessModel):
    receipt_id: ProbeId
    receipt_sha256: Digest
    account_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")]
    model_id: ModelId
    credential_binding_sha256: Digest
    endpoint: Literal["https://api.meta.ai/v1/chat/completions"] = META_COMPLETIONS_URL
    probe_protocol: Literal["polymath-meta-probe-v1"] = "polymath-meta-probe-v1"
    status: Literal["passed"] = "passed"


class NativeResearchReview(_ReadinessModel):
    model_wire_usage_confirmed: Literal[True]
    pricing_bounds_confirmed: Literal[True]
    funding_cap_confirmed: Literal[True]
    observed_at: datetime
    valid_until: datetime
    evidence: EvidenceReferences

    @field_validator(
        "model_wire_usage_confirmed",
        "pricing_bounds_confirmed",
        "funding_cap_confirmed",
        mode="before",
    )
    @classmethod
    def explicit_confirmation(cls, value):
        if value is not True:
            raise ValueError("native research needs each explicit current human confirmation")
        return value

    @field_validator("observed_at", "valid_until", mode="before")
    @classmethod
    def utc_review(cls, value):
        return _utc(value)

    @field_validator("evidence", mode="before")
    @classmethod
    def json_evidence(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def current_review(self):
        if self.observed_at >= self.valid_until or len({item.url for item in self.evidence}) != len(
            self.evidence
        ):
            raise ValueError("invalid current human research review")
        return self


class MetaResearchManifest(_ReadinessModel):
    protocol: Literal["polymath-meta-research-activation-v1"] = RESEARCH_PROTOCOL
    audience: Literal["operator_browser"] = "operator_browser"
    readiness_id: ProbeId
    proposal_sha256: Digest
    proposal: MetaReadinessProposal
    account_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")]
    model_id: ModelId
    endpoint: Literal["https://api.meta.ai/v1/chat/completions"] = META_COMPLETIONS_URL
    credential_binding_sha256: Digest
    canary: PassedCanaryBinding
    review: NativeResearchReview
    provider_account_attestation: ProviderAccountAttestation
    native_control_epoch: Annotated[int, Field(ge=0, le=2**53 - 2)]
    clear_kill_switch: Literal[True] = True
    task_profiles: Annotated[
        tuple[NativeTaskProfileBinding, ...], Field(min_length=5, max_length=5)
    ]
    discovery_ladder_profile: NativeDiscoveryProfileBinding | None = None
    scientific_context_profiles: NativeScientificContextProfiles | None = None
    valid_until: datetime

    @model_serializer(mode="wrap")
    def preserve_absent_discovery(self, handler):
        value = handler(self)
        if self.discovery_ladder_profile is None:
            value.pop("discovery_ladder_profile", None)
        if self.scientific_context_profiles is None:
            value.pop("scientific_context_profiles", None)
        return value

    @field_validator("valid_until", mode="before")
    @classmethod
    def utc_deadline(cls, value):
        return _utc(value)

    @field_validator("task_profiles", mode="before")
    @classmethod
    def json_profiles(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("clear_kill_switch", mode="before")
    @classmethod
    def clear_scope_kill(cls, value):
        if value is not True:
            raise ValueError("native research approval must review its scoped kill-clear")
        return value

    @model_validator(mode="after")
    def exact_activation(self):
        proposal = self.proposal
        canary = self.canary
        attestation = self.provider_account_attestation
        quote = quote_proposal(proposal)
        if (
            self.proposal_sha256 != proposal_sha256(proposal)
            or (self.account_id, self.model_id) != (proposal.account.account_id, proposal.model_id)
            or (canary.account_id, canary.model_id, canary.credential_binding_sha256)
            != (self.account_id, self.model_id, self.credential_binding_sha256)
            or attestation.model_id != self.model_id
        ):
            raise ValueError("native research activation identity/profile binding mismatch")
        if tuple(binding.profile.task_kind for binding in self.task_profiles) != TASK_KINDS:
            raise ValueError("native research requires each scientific task profile in order")
        all_profiles = self.task_profiles + (
            (self.discovery_ladder_profile,) if self.discovery_ladder_profile is not None else ()
        )
        for binding in all_profiles:
            profile = binding.profile
            if (
                profile.max_model_calls > proposal.application_limits.max_model_calls
                or profile.max_output_tokens != proposal.application_limits.max_output_tokens
                or profile.max_context_characters
                != proposal.application_limits.max_context_characters
                or profile.request_timeout_seconds
                != proposal.application_limits.request_timeout_seconds
            ):
                raise ValueError("historical native profile exceeds its original reviewed proposal")
        if self.scientific_context_profiles is not None:
            validate_historical_scientific_context_profiles(
                self.scientific_context_profiles, proposal
            )
        if self.valid_until > min(
            proposal.valid_until, self.review.valid_until, attestation.valid_until
        ) or self.valid_until <= max(
            proposal.observed_at, self.review.observed_at, attestation.observed_at
        ):
            raise ValueError("native research activation exceeds its reviewed validity")
        if (
            attestation.provider_hard_cap_microdollars < proposal.account.hard_cap_microdollars
            or min(
                attestation.available_credit_microdollars,
                attestation.remaining_provider_allowance_microdollars,
            )
            < quote.per_call_reservation_microdollars
        ):
            raise ValueError("native research exceeds its reviewed provider funds/cap")
        return self


def build_research_manifest(
    readiness_id: str,
    proposal: MetaReadinessProposal,
    *,
    policy: NativeResearchPolicy,
    native_control_epoch: int,
    canary: PassedCanaryBinding,
    review: NativeResearchReview,
    provider_account_attestation: ProviderAccountAttestation,
    valid_until: datetime,
    discovery=None,
    scientific_context: ScientificContextIntent | None = None,
) -> MetaResearchManifest:
    if (
        not policy.configured
        or policy.native_account_id != proposal.account.account_id
        or policy.model_id != proposal.model_id
    ):
        raise ValueError("native research policy does not bind the saved readiness proposal")
    if scientific_context is not None:
        ScientificContextIntent.model_validate_json(scientific_context.model_dump_json())
    return MetaResearchManifest(
        readiness_id=readiness_id,
        proposal_sha256=proposal_sha256(proposal),
        proposal=proposal,
        account_id=proposal.account.account_id,
        model_id=proposal.model_id,
        credential_binding_sha256=policy.credential_binding_sha256,
        canary=canary,
        review=review,
        provider_account_attestation=provider_account_attestation,
        native_control_epoch=native_control_epoch,
        task_profiles=derive_task_profiles(proposal),
        discovery_ladder_profile=derive_discovery_ladder_profile(proposal, discovery)
        if discovery is not None
        else None,
        scientific_context_profiles=derive_scientific_context_profiles(proposal)
        if scientific_context is not None
        else None,
        valid_until=valid_until,
    )


def validate_research_manifest(
    manifest: MetaResearchManifest, proposal: MetaReadinessProposal | None = None
) -> None:
    value = MetaResearchManifest.model_validate_json(manifest.model_dump_json())
    if proposal is not None and (
        value.proposal_sha256 != proposal_sha256(proposal) or value.proposal != proposal
    ):
        raise ValueError("native research activation saved-proposal mismatch")


def canonical_research_manifest_json(manifest: MetaResearchManifest) -> str:
    validate_research_manifest(manifest)
    return _canonical(manifest.model_dump(mode="json"))


def research_manifest_sha256(manifest: MetaResearchManifest) -> str:
    return hashlib.sha256(canonical_research_manifest_json(manifest).encode()).hexdigest()


def profile_for_task(
    manifest: MetaResearchManifest, task_kind: TaskKind, *, discovery=None
) -> NativeTaskProfile | NativeDiscoveryProfile:
    validate_research_manifest(manifest)
    if discovery is not None:
        from polymath.discovery_contracts import DiscoveryIntent

        intent = DiscoveryIntent.model_validate_json(discovery.model_dump_json())
        binding = manifest.discovery_ladder_profile
        if (
            task_kind != "research"
            or binding is None
            or (
                intent.max_stages > binding.profile.max_stages
                or intent.max_depth > binding.profile.max_depth
            )
        ):
            raise ValueError("native discovery intent exceeds its explicit approved profile")
        return binding.profile
    for binding in manifest.task_profiles:
        if binding.profile.task_kind == task_kind:
            return binding.profile
    raise ValueError("unsupported native scientific task")


def select_scientific_profile(
    manifest, task_kind, mode, *, discovery=None, scientific_intent=None
) -> NativeTaskProfile | NativeDiscoveryProfile:
    """Select only original approved profiles, never current prompt defaults."""
    from polymath.public_research_contracts import (
        PublicResearchManifest,
        public_profile_for_task,
        validate_public_research_manifest,
    )

    public = isinstance(manifest, PublicResearchManifest)
    if scientific_intent is None:
        selector = public_profile_for_task if public else profile_for_task
        return selector(manifest, task_kind, discovery=discovery)
    if public:
        validate_public_research_manifest(manifest)
    else:
        validate_research_manifest(manifest)
    if discovery is not None:
        raise ValueError("scientific_context_intents_conflict")
    if isinstance(scientific_intent, ArtifactScientificContextIntent):
        ArtifactScientificContextIntent.model_validate_json(scientific_intent.model_dump_json())
        capability = "artifact_scientific_context_v1"
        if task_kind not in ("formalise", "write_summary", "write_paper"):
            raise ValueError("scientific_context_intent_scope_invalid")
    elif isinstance(scientific_intent, AskOverviewIntent):
        AskOverviewIntent.model_validate_json(scientific_intent.model_dump_json())
        capability = "ask_claim_overview_v1"
        if task_kind != "research" or mode != "ask":
            raise ValueError("scientific_context_intent_scope_invalid")
    elif isinstance(scientific_intent, AskReassessmentOverviewIntent):
        AskReassessmentOverviewIntent.model_validate_json(scientific_intent.model_dump_json())
        capability = "ask_reassessment_overview_v1"
        if task_kind != "reassess_finding" or mode != "ask":
            raise ValueError("scientific_context_intent_scope_invalid")
    else:
        raise ValueError("scientific_context_intent_invalid")
    if mode not in ("explore", "ask", "surprise"):
        raise ValueError("scientific_context_intent_scope_invalid")
    extension = manifest.scientific_context_profiles
    if extension is None:
        raise ValueError("scientific_context_profile_not_approved")
    for binding in extension.bindings:
        if binding.capability == capability and binding.profile.task_kind == task_kind:
            if binding.mode is not None and binding.mode != mode:
                raise ValueError("scientific_context_intent_scope_invalid")
            return binding.profile
    raise ValueError("scientific_context_profile_not_approved")


class PublicResearchProtocols(_ReadinessModel):
    protocol: Literal["polymath-research-protocols-v1"] = "polymath-research-protocols-v1"
    activation_sha256: Digest | None
    capabilities: Annotated[tuple[ScientificCapability, ...], Field(max_length=3)]

    @field_validator("capabilities", mode="before")
    @classmethod
    def json_capabilities(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def exact_scope(self):
        actual = tuple(item for item in SCIENTIFIC_CAPABILITIES if item in self.capabilities)
        if self.capabilities != actual:
            raise ValueError("scientific_capabilities_order_invalid")
        if self.activation_sha256 is None and self.capabilities:
            raise ValueError("scientific_capabilities_without_public_activation")
        return self


class MetaUsageAssessment(_ReadinessModel):
    status: Literal["known", "unknown"]
    reason_code: Annotated[str, Field(pattern=r"^[a-z_]{1,80}$")]
    observation_sha256: Digest
    input_tokens: Annotated[int, Field(ge=0, le=2**63 - 1)] | None
    output_tokens: Annotated[int, Field(ge=0, le=2**63 - 1)] | None
    actual_input_microdollars: Annotated[int, Field(ge=0, le=9000000000000000)] | None
    actual_output_microdollars: Annotated[int, Field(ge=0, le=9000000000000000)] | None
    actual_microdollars: Annotated[int, Field(ge=0, le=9000000000000000)] | None
    usage_within_bounds: bool | None
    freeze_required: bool


def assess_meta_usage(
    proposal: MetaReadinessProposal, request: MetaResearchRequest, observation: MetaObservation
) -> MetaUsageAssessment:
    request = MetaResearchRequest.model_validate_json(request.model_dump_json())
    if (
        request.payload.model != proposal.model_id
        or request.payload.max_tokens != proposal.application_limits.max_output_tokens
        or sum(len(item.content) for item in request.payload.messages)
        > proposal.application_limits.max_context_characters
    ):
        raise ValueError("native research request differs from its reviewed proposal")
    value = _assess_reported_meta_usage(
        proposal, meta_research_request_sha256(request), proposal.model_id, observation
    )
    return MetaUsageAssessment(
        status="known" if value.actual_microdollars is not None else "unknown",
        reason_code=value.reason_code,
        observation_sha256=value.observation_sha256,
        input_tokens=value.input_tokens,
        output_tokens=value.output_tokens,
        actual_input_microdollars=value.actual_input_microdollars,
        actual_output_microdollars=value.actual_output_microdollars,
        actual_microdollars=value.actual_microdollars,
        usage_within_bounds=value.usage_within_bounds,
        freeze_required=value.freeze_required,
    )


def model_reply_from_observation(
    proposal: MetaReadinessProposal, request: MetaResearchRequest, observation: MetaObservation
) -> ModelReply:
    from polymath.research.meta import ModelError, ModelReply

    usage = assess_meta_usage(proposal, request, observation)
    if usage.status != "known" or usage.usage_within_bounds is not True:
        raise ModelError("native_usage_not_usable")
    if (
        observation.http_status != 200
        or observation.finish_reason != "stop"
        or observation.content is None
        or not observation.content
    ):
        raise ModelError("model_invalid_response")
    return ModelReply(observation.content, observation.input_tokens, observation.output_tokens)
