"""One exact Meta protocol request and observed accounting; no dispatch authority.

These helpers never send, activate, approve, mutate a ledger, or construct a
model. Native observations belong only in the private journal. API receipts use
the bounded assessment/action summary, never raw model/provider content.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator, model_validator

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

MAX_COUNTED_MICRODOLLARS = 9_000_000_000_000_000
PROBE_PROTOCOL = "polymath-meta-probe-v1"
OBSERVER_PROTOCOL = "polymath-meta-observation-v1"
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}")
_ACCOUNT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}")
ProbeId = Annotated[
    str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]
ModelId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$")]


def _canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _sha(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MetaProbePolicy:
    paid_probe_enabled: bool = False
    native_account_id: str | None = None
    model_id: str | None = None
    api_key: SecretStr | None = field(default=None, repr=False)
    consent_seconds: int = 300

    def __post_init__(self):
        if (
            type(self.paid_probe_enabled) is not bool
            or type(self.consent_seconds) is not int
            or not 1 <= self.consent_seconds <= 600
        ):
            raise ValueError("invalid native probe server policy")
        if self.native_account_id is not None and (
            not isinstance(self.native_account_id, str)
            or _ACCOUNT_ID.fullmatch(self.native_account_id) is None
        ):
            raise ValueError("invalid native ledger identifier")
        if self.model_id is not None and (
            not isinstance(self.model_id, str) or _MODEL_ID.fullmatch(self.model_id) is None
        ):
            raise ValueError("invalid explicit native model identifier")
        if self.api_key is not None and not isinstance(self.api_key, SecretStr):
            raise ValueError("native credentials must remain server secrets")

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
        if self.api_key is None or not self.api_key.get_secret_value().strip():
            return None
        return hashlib.sha256(
            b"polymath-meta-credential-binding-v1\0"
            + self.api_key.get_secret_value().encode("utf-8")
        ).hexdigest()


def probe_policy_sha256(policy: MetaProbePolicy) -> str:
    return _sha(
        {
            "paid_probe_enabled": policy.paid_probe_enabled,
            "native_account_id": policy.native_account_id,
            "model_id": policy.model_id,
            "credential_binding_sha256": policy.credential_binding_sha256,
            "consent_seconds": policy.consent_seconds,
        }
    )


class MetaProbeMessage(_ReadinessModel):
    role: Literal["system", "user"]
    content: Annotated[str, Field(min_length=1, max_length=1000)]


PROBE_MESSAGES_V1 = (
    MetaProbeMessage(
        role="system",
        content=(
            "Perform one isolated JSON protocol check. Do not search, use tools, cite sources, "
            "or make scientific claims. Return only one JSON action."
        ),
    ),
    MetaProbeMessage(
        role="user",
        content=(
            'Return exactly this JSON action: {"kind":"conclude","conclusion":'
            '{"outcome":"insufficient_evidence","reason":"Protocol check only."}}'
        ),
    ),
)


class MetaProbePayload(_ReadinessModel):
    model: ModelId
    messages: Annotated[tuple[MetaProbeMessage, ...], Field(min_length=2, max_length=2)]
    max_tokens: Annotated[int, Field(ge=16, le=4096)]
    stream: Literal[False] = False

    @field_validator("messages", mode="before")
    @classmethod
    def json_messages(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("stream", mode="before")
    @classmethod
    def never_stream(cls, value):
        if value is not False:
            raise ValueError("probe streaming is disabled")
        return value

    @model_validator(mode="after")
    def fixed_messages(self):
        if self.messages != PROBE_MESSAGES_V1:
            raise ValueError("native probes require the exact fixed server messages")
        return self


class MetaProbeRequest(_ReadinessModel):
    method: Literal["POST"] = "POST"
    endpoint: Literal["https://api.meta.ai/v1/chat/completions"] = META_COMPLETIONS_URL
    payload: MetaProbePayload


def build_probe_request(proposal: MetaReadinessProposal) -> MetaProbeRequest:
    proposal = MetaReadinessProposal.model_validate_json(proposal.model_dump_json())
    if (
        sum(len(item.content) for item in PROBE_MESSAGES_V1)
        > proposal.application_limits.max_context_characters
    ):
        raise ValueError("probe request exceeds its reviewed context limit")
    return MetaProbeRequest(
        payload=MetaProbePayload(
            model=proposal.model_id,
            messages=PROBE_MESSAGES_V1,
            max_tokens=proposal.application_limits.max_output_tokens,
        )
    )


def probe_request_sha256(request: MetaProbeRequest) -> str:
    request = MetaProbeRequest.model_validate_json(request.model_dump_json())
    return _sha(request.model_dump(mode="json"))


class ProviderAccountAttestation(_ReadinessModel):
    provider_billing_account_id: Annotated[
        str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")
    ]
    model_id: ModelId
    currency: Literal["USD"] = "USD"
    usage_contract: Literal["prompt_completion_inclusive_v1"] = "prompt_completion_inclusive_v1"
    observed_at: datetime
    valid_until: datetime
    available_credit_microdollars: Annotated[int, Field(ge=0, le=MAX_COUNTED_MICRODOLLARS)]
    remaining_provider_allowance_microdollars: Annotated[
        int, Field(ge=0, le=MAX_COUNTED_MICRODOLLARS)
    ]
    provider_hard_cap_microdollars: Annotated[int, Field(ge=1, le=MAX_COUNTED_MICRODOLLARS)]
    evidence: EvidenceReferences

    @field_validator("observed_at", "valid_until", mode="before")
    @classmethod
    def utc_times(cls, value):
        return _utc(value)

    @field_validator("evidence", mode="before")
    @classmethod
    def json_evidence(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def attested_bounds(self):
        if (
            self.observed_at >= self.valid_until
            or self.remaining_provider_allowance_microdollars > self.provider_hard_cap_microdollars
        ):
            raise ValueError("invalid provider account attestation interval or allowance")
        if len({item.url for item in self.evidence}) != len(self.evidence):
            raise ValueError("provider attestation evidence references must be distinct")
        return self


class MetaProbeManifest(_ReadinessModel):
    protocol: Literal["polymath-meta-probe-v1"] = PROBE_PROTOCOL
    readiness_id: ProbeId
    proposal_sha256: Digest
    account_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")]
    credential_binding_sha256: Digest
    native_control_epoch: Annotated[int, Field(ge=0, le=2**53 - 2)]
    clear_kill_switch: Literal[True] = True
    request: MetaProbeRequest
    request_sha256: Digest
    request_timeout_seconds: Annotated[float, Field(ge=0.001, le=60)]
    input_reservation_microdollars: Annotated[int, Field(ge=1, le=150000000)]
    output_reservation_microdollars: Annotated[int, Field(ge=1, le=150000000)]
    reservation_microdollars: Annotated[int, Field(ge=1, le=150000000)]
    provider_account_attestation: ProviderAccountAttestation
    valid_until: datetime

    @field_validator("valid_until", mode="before")
    @classmethod
    def utc_expiry(cls, value):
        return _utc(value)

    @field_validator("clear_kill_switch", mode="before")
    @classmethod
    def explicit_arm(cls, value):
        if value is not True:
            raise ValueError("one-call approval must explicitly review its scoped kill-clear")
        return value

    @model_validator(mode="after")
    def manifest_binding(self):
        if (
            self.request_sha256 != probe_request_sha256(self.request)
            or self.reservation_microdollars
            != self.input_reservation_microdollars + self.output_reservation_microdollars
        ):
            raise ValueError("probe manifest request/reservation binding failed")
        attestation = self.provider_account_attestation
        if (
            self.request.payload.model != attestation.model_id
            or self.valid_until > attestation.valid_until
            or self.valid_until <= attestation.observed_at
            or min(
                attestation.available_credit_microdollars,
                attestation.remaining_provider_allowance_microdollars,
            )
            < self.reservation_microdollars
        ):
            raise ValueError("probe manifest exceeds its bound provider attestation")
        return self


def validate_probe_manifest(manifest: MetaProbeManifest, proposal: MetaReadinessProposal) -> None:
    manifest = MetaProbeManifest.model_validate_json(manifest.model_dump_json())
    proposal = MetaReadinessProposal.model_validate_json(proposal.model_dump_json())
    quote = quote_proposal(proposal)
    if (
        manifest.proposal_sha256 != proposal_sha256(proposal)
        or manifest.account_id != proposal.account.account_id
        or manifest.request != build_probe_request(proposal)
        or manifest.request_timeout_seconds != proposal.application_limits.request_timeout_seconds
        or (
            manifest.input_reservation_microdollars,
            manifest.output_reservation_microdollars,
            manifest.reservation_microdollars,
        )
        != (
            quote.input_reservation_microdollars,
            quote.output_reservation_microdollars,
            quote.per_call_reservation_microdollars,
        )
        or manifest.valid_until > proposal.valid_until
        or manifest.provider_account_attestation.provider_hard_cap_microdollars
        < proposal.account.hard_cap_microdollars
    ):
        raise ValueError("probe manifest differs from its exact readiness proposal")


def build_probe_manifest(
    readiness_id: str,
    proposal: MetaReadinessProposal,
    *,
    policy: MetaProbePolicy,
    native_control_epoch: int,
    provider_account_attestation: ProviderAccountAttestation,
    valid_until: datetime,
) -> MetaProbeManifest:
    if (
        not policy.configured
        or policy.native_account_id != proposal.account.account_id
        or policy.model_id != proposal.model_id
    ):
        raise ValueError("native probe policy does not bind this readiness proposal")
    quote = quote_proposal(proposal)
    request = build_probe_request(proposal)
    manifest = MetaProbeManifest(
        readiness_id=readiness_id,
        proposal_sha256=proposal_sha256(proposal),
        account_id=proposal.account.account_id,
        credential_binding_sha256=policy.credential_binding_sha256,
        native_control_epoch=native_control_epoch,
        request=request,
        request_sha256=probe_request_sha256(request),
        request_timeout_seconds=proposal.application_limits.request_timeout_seconds,
        input_reservation_microdollars=quote.input_reservation_microdollars,
        output_reservation_microdollars=quote.output_reservation_microdollars,
        reservation_microdollars=quote.per_call_reservation_microdollars,
        provider_account_attestation=provider_account_attestation,
        valid_until=valid_until,
    )
    validate_probe_manifest(manifest, proposal)
    return manifest


def canonical_probe_manifest_json(manifest: MetaProbeManifest) -> str:
    manifest = MetaProbeManifest.model_validate_json(manifest.model_dump_json())
    return _canonical(manifest.model_dump(mode="json"))


def probe_manifest_sha256(manifest: MetaProbeManifest) -> str:
    return hashlib.sha256(canonical_probe_manifest_json(manifest).encode()).hexdigest()


class MetaObservation(_ReadinessModel):
    protocol: Literal["polymath-meta-observation-v1"] = OBSERVER_PROTOCOL
    request_sha256: Digest
    requested_model_id: ModelId
    observed_model_id: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    http_status: Annotated[int, Field(ge=100, le=599)] | None = None
    finish_reason: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    content: Annotated[str, Field(max_length=16000)] | None = None
    content_sha256: Digest | None = None
    body_sha256: Digest | None = None
    body_complete: bool
    input_tokens: Annotated[int, Field(ge=0, le=2**63 - 1)] | None = None
    output_tokens: Annotated[int, Field(ge=0, le=2**63 - 1)] | None = None
    error_code: Annotated[str, Field(pattern=r"^[a-z_]{1,80}$")] | None = None
    started_at: datetime
    finished_at: datetime

    @field_validator("started_at", "finished_at", mode="before")
    @classmethod
    def observed_times(cls, value):
        return _utc(value)

    @model_validator(mode="after")
    def observation_integrity(self):
        if (self.input_tokens is None) != (
            self.output_tokens is None
        ) or self.started_at > self.finished_at:
            raise ValueError("invalid paired usage or observation timing")
        if (
            self.content is not None
            and self.content_sha256 != hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        ):
            raise ValueError("observation content hash mismatch")
        if not self.body_complete and (
            self.input_tokens is not None
            or self.content is not None
            or self.observed_model_id is not None
        ):
            raise ValueError("incomplete response cannot invent complete observed facts")
        if self.body_complete and (self.body_sha256 is None or self.http_status is None):
            raise ValueError("complete response requires its body hash and HTTP status")
        return self


def observation_sha256(observation: MetaObservation) -> str:
    observation = MetaObservation.model_validate_json(observation.model_dump_json())
    return _sha(observation.model_dump(mode="json"))


class MetaProbeActionSummary(_ReadinessModel):
    kind: Literal["conclude"] = "conclude"
    outcome: Literal["insufficient_evidence"] = "insufficient_evidence"
    reason_sha256: Digest
    reason_characters: Annotated[int, Field(ge=1, le=3000)]


class MetaProbeAssessment(_ReadinessModel):
    status: Literal["passed", "failed", "unknown"]
    reason_code: Annotated[str, Field(pattern=r"^[a-z_]{1,80}$")]
    observation_sha256: Digest
    input_tokens: Annotated[int, Field(ge=0, le=2**63 - 1)] | None
    output_tokens: Annotated[int, Field(ge=0, le=2**63 - 1)] | None
    actual_input_microdollars: Annotated[int, Field(ge=0, le=MAX_COUNTED_MICRODOLLARS)] | None
    actual_output_microdollars: Annotated[int, Field(ge=0, le=MAX_COUNTED_MICRODOLLARS)] | None
    actual_microdollars: Annotated[int, Field(ge=0, le=MAX_COUNTED_MICRODOLLARS)] | None
    usage_within_bounds: bool | None
    freeze_required: bool
    action_sha256: Digest | None = None
    action: MetaProbeActionSummary | None = None
    research_execution_available: Literal[False] = False
    validation_scope: Literal["one_call_proposed_wire_action_and_reported_usage"] = (
        "one_call_proposed_wire_action_and_reported_usage"
    )


def assess_probe_usage(
    manifest: MetaProbeManifest, proposal: MetaReadinessProposal, observation: MetaObservation
) -> MetaProbeAssessment:
    """Account reported facts without parsing any generated action or importing its adapter."""
    validate_probe_manifest(manifest, proposal)
    observation = MetaObservation.model_validate_json(observation.model_dump_json())
    if (
        observation.request_sha256 != manifest.request_sha256
        or observation.requested_model_id != manifest.request.payload.model
    ):
        raise ValueError("probe observation request binding mismatch")
    values = {
        "observation_sha256": observation_sha256(observation),
        "input_tokens": observation.input_tokens,
        "output_tokens": observation.output_tokens,
        "actual_input_microdollars": None,
        "actual_output_microdollars": None,
        "actual_microdollars": None,
        "usage_within_bounds": None,
        "freeze_required": False,
    }
    if observation.observed_model_id != manifest.request.payload.model:
        return MetaProbeAssessment(
            status="unknown",
            reason_code="observed_model_missing"
            if observation.observed_model_id is None
            else "observed_model_mismatch",
            **(values | {"freeze_required": observation.observed_model_id is not None}),
        )
    if observation.input_tokens is None:
        return MetaProbeAssessment(
            status="unknown", reason_code="usage_missing_or_invalid", **values
        )
    i, o = observation.input_tokens, observation.output_tokens
    prices = proposal.pricing
    ci = (i * prices.input_microdollars_per_million + 999999) // 1000000
    co = (o * prices.output_microdollars_per_million + 999999) // 1000000
    if ci + co > MAX_COUNTED_MICRODOLLARS:
        return MetaProbeAssessment(
            status="unknown",
            reason_code="counted_cost_unrepresentable",
            **(values | {"freeze_required": True}),
        )
    within = (
        i <= proposal.billable_bounds.input_tokens_absolute
        and o <= proposal.billable_bounds.output_tokens_absolute
    )
    values |= {
        "actual_input_microdollars": ci,
        "actual_output_microdollars": co,
        "actual_microdollars": ci + co,
        "usage_within_bounds": within,
        "freeze_required": not within,
    }
    if not within:
        return MetaProbeAssessment(status="failed", reason_code="billable_bound_exceeded", **values)
    return MetaProbeAssessment(
        status="failed", reason_code="usage_counted_action_not_checked", **values
    )


def assess_probe_observation(
    manifest: MetaProbeManifest, proposal: MetaReadinessProposal, observation: MetaObservation
) -> MetaProbeAssessment:
    """Validate action semantics only after the caller durably journals the observation."""
    usage = assess_probe_usage(manifest, proposal, observation)
    if usage.reason_code != "usage_counted_action_not_checked":
        return usage
    values = usage.model_dump()
    if observation.http_status != 200:
        return MetaProbeAssessment(**(values | {"reason_code": "http_not_success"}))
    if observation.finish_reason != "stop":
        return MetaProbeAssessment(**(values | {"reason_code": "finish_not_stop"}))
    try:
        from polymath.research.models import ACTION_ADAPTER, ConcludeAction, InsufficientFinding

        action = ACTION_ADAPTER.validate_json(observation.content or "")
        if not isinstance(action, ConcludeAction) or not isinstance(
            action.conclusion, InsufficientFinding
        ):
            raise ValueError
        action_hash = _sha(action.model_dump(mode="json"))
        reason = action.conclusion.reason
        summary = MetaProbeActionSummary(
            reason_sha256=hashlib.sha256(reason.encode()).hexdigest(), reason_characters=len(reason)
        )
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return MetaProbeAssessment(**(values | {"reason_code": "invalid_insufficient_action"}))
    return MetaProbeAssessment(
        **(
            values
            | {
                "status": "passed",
                "reason_code": "expected_action_and_reported_usage",
                "action_sha256": action_hash,
                "action": summary,
            }
        )
    )
