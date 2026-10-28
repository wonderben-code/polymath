"""Pure operator-attested preparation proposals; never native proof or authority.

No model/key/factory, HTTP request, account mutation, or execution is performed.
Absolute billable bounds require external evidence. Character limits are never
converted to tokens. usage_evidence covers the proposed wire/model/endpoint and
both inclusive USD billing counters, including any reasoning/cached usage.
Provider credits and the provider-side hard cap remain unchecked.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

META_COMPLETIONS_URL = "https://api.meta.ai/v1/chat/completions"
READINESS_PROTOCOL = "polymath-meta-readiness-v1"
MAX_HARD_CAP_MICRODOLLARS = 150_000_000
MAX_READINESS_BYTES = 65_536
MAX_PRICE_RATE = 9_000_000_000_000_000
MAX_BILLABLE_TOKENS = 2**63 - 1
BUDGET_LIMITED_STOP_NOTICE = (
    "Budget limits may stop this run before the configured maximum number of model calls."
)
_ACTOR_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}")
_UTC_ISO = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,6})?(?:Z|\+00:00)"
)
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ReadinessError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class _ReadinessModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


@dataclass(frozen=True)
class ReadinessPolicy:
    preparation_enabled: bool = False
    allowed_actor_ids: tuple[str, ...] = ()
    max_hard_cap_microdollars: int = MAX_HARD_CAP_MICRODOLLARS

    def __post_init__(self):
        if type(self.preparation_enabled) is not bool:
            raise ValueError("preparation_enabled must be a server boolean")
        if (
            type(self.allowed_actor_ids) is not tuple
            or len(self.allowed_actor_ids) > 10
            or any(
                not isinstance(actor, str) or _ACTOR_ID.fullmatch(actor) is None
                for actor in self.allowed_actor_ids
            )
            or len(set(self.allowed_actor_ids)) != len(self.allowed_actor_ids)
        ):
            raise ValueError("allowed_actor_ids must be a bounded distinct immutable actor list")
        if (
            type(self.max_hard_cap_microdollars) is not int
            or not 1 <= self.max_hard_cap_microdollars <= MAX_HARD_CAP_MICRODOLLARS
        ):
            raise ValueError("readiness hard cap cannot exceed150000000 microdollars")


class ReadinessEvidence(_ReadinessModel):
    url: Annotated[str, Field(min_length=1, max_length=2000)]
    content_sha256: Digest

    @field_validator("url")
    @classmethod
    def public_https_reference(cls, value):
        if any(ord(character) < 33 or ord(character) == 127 for character in value):
            raise ValueError("readiness references require a bounded HTTPS document URL")
        try:
            parsed = urlsplit(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.port not in (None, 443)
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError
            host = parsed.hostname.encode("idna").decode("ascii").lower()
            if ":" in host:
                host = "[" + host + "]"
            path = quote(parsed.path or "/", safe="/:@!$&'()*+,;=-._~%")
            if any(segment in (".", "..") for segment in path.split("/")):
                raise ValueError
            canonical = urlunsplit(("https", host, path, "", ""))
            if len(canonical) > 2000:
                raise ValueError
            return canonical
        except (ValueError, UnicodeError):
            raise ValueError(
                "readiness references require uncredentialed HTTPS without queries or fragments"
            ) from None


EvidenceReferences = Annotated[tuple[ReadinessEvidence, ...], Field(min_length=1, max_length=8)]


class _EvidenceGroup(_ReadinessModel):
    evidence: EvidenceReferences

    @field_validator("evidence", mode="before")
    @classmethod
    def json_evidence_array(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def distinct_evidence(self):
        if len({item.url for item in self.evidence}) != len(self.evidence):
            raise ValueError("evidence document URLs must be distinct within each purpose")
        return self


class BillableBounds(_EvidenceGroup):
    input_tokens_absolute: Annotated[int, Field(ge=1, le=MAX_BILLABLE_TOKENS)]
    output_tokens_absolute: Annotated[int, Field(ge=1, le=MAX_BILLABLE_TOKENS)]


class PricingCeilings(_EvidenceGroup):
    input_microdollars_per_million: Annotated[int, Field(ge=1, le=MAX_PRICE_RATE)]
    output_microdollars_per_million: Annotated[int, Field(ge=1, le=MAX_PRICE_RATE)]


class ApplicationLimits(_ReadinessModel):
    max_output_tokens: Annotated[int, Field(ge=16, le=4096)]
    max_context_characters: Annotated[int, Field(ge=1000, le=90000)]
    max_model_calls: Annotated[int, Field(ge=1, le=20)]
    request_timeout_seconds: Annotated[float, Field(ge=0.001, le=60)]


class AccountCaps(_ReadinessModel):
    account_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")]
    hard_cap_microdollars: Annotated[int, Field(ge=1, le=MAX_HARD_CAP_MICRODOLLARS)]
    daily_cap_microdollars: Annotated[int, Field(ge=1, le=MAX_HARD_CAP_MICRODOLLARS)]
    run_cap_microdollars: Annotated[int, Field(ge=1, le=MAX_HARD_CAP_MICRODOLLARS)]
    max_in_flight: Annotated[int, Field(ge=1, le=100)]

    @model_validator(mode="after")
    def ordered_caps(self):
        if (
            not self.run_cap_microdollars
            <= self.daily_cap_microdollars
            <= self.hard_cap_microdollars
        ):
            raise ValueError("readiness requires run cap<=daily cap<=hard cap")
        return self


def _utc(value):
    if isinstance(value, str):
        if _UTC_ISO.fullmatch(value) is None:
            raise ValueError("readiness times require explicit UTC ISO8601 timestamps")
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("readiness times require aware UTC timestamps")
    return value


def _costs(proposal):
    bounds, prices = proposal.billable_bounds, proposal.pricing
    input_cost = (
        bounds.input_tokens_absolute * prices.input_microdollars_per_million + 999999
    ) // 1000000
    output_cost = (
        bounds.output_tokens_absolute * prices.output_microdollars_per_million + 999999
    ) // 1000000
    return input_cost, output_cost


class MetaReadinessProposal(_ReadinessModel):
    protocol: Literal["polymath-meta-readiness-v1"] = READINESS_PROTOCOL
    model_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}$")]
    endpoint: Literal["https://api.meta.ai/v1/chat/completions"] = META_COMPLETIONS_URL
    currency: Literal["USD"] = "USD"
    usage_contract: Literal["prompt_completion_inclusive_v1"] = "prompt_completion_inclusive_v1"
    observed_at: datetime
    valid_until: datetime
    billable_bounds: BillableBounds
    pricing: PricingCeilings
    usage_evidence: EvidenceReferences
    application_limits: ApplicationLimits
    account: AccountCaps
    native_status: Literal["not_run"] = "not_run"
    execution_available: Literal[False] = False

    @field_validator("observed_at", "valid_until", mode="before")
    @classmethod
    def utc_time(cls, value):
        return _utc(value)

    @field_validator("usage_evidence", mode="before")
    @classmethod
    def json_usage_evidence(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @field_validator("execution_available", mode="before")
    @classmethod
    def never_execution_authority(cls, value):
        if value is not False:
            raise ValueError("readiness proposals never enable execution")
        return value

    @model_validator(mode="after")
    def conservative_proposal(self):
        if self.observed_at >= self.valid_until:
            raise ValueError("readiness validity must end after its observation")
        if self.billable_bounds.output_tokens_absolute < self.application_limits.max_output_tokens:
            raise ValueError(
                "absolute billable output bound must cover the application's requested output"
            )
        if sum(_costs(self)) > self.account.run_cap_microdollars:
            raise ValueError("one conservative call reservation must fit the configured run cap")
        references = (*self.billable_bounds.evidence, *self.pricing.evidence, *self.usage_evidence)
        hashes = {}
        for reference in references:
            if reference.url in hashes and hashes[reference.url] != reference.content_sha256:
                raise ValueError("one evidence document cannot have conflicting content hashes")
            hashes[reference.url] = reference.content_sha256
        if len({item.url for item in self.usage_evidence}) != len(self.usage_evidence):
            raise ValueError("usage evidence document URLs must be distinct")
        if len(_canonical(self).encode("utf-8")) > MAX_READINESS_BYTES:
            raise ValueError("readiness_proposal_too_large")
        return self


def _canonical(proposal):
    return json.dumps(
        proposal.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_proposal_json(proposal: MetaReadinessProposal) -> str:
    proposal = MetaReadinessProposal.model_validate_json(proposal.model_dump_json())
    return _canonical(proposal)


def proposal_sha256(proposal: MetaReadinessProposal) -> str:
    return hashlib.sha256(canonical_proposal_json(proposal).encode("utf-8")).hexdigest()


def validate_proposal_time(proposal: MetaReadinessProposal, now: datetime) -> None:
    proposal = MetaReadinessProposal.model_validate_json(proposal.model_dump_json())
    now = _utc(now)
    if proposal.observed_at > now:
        raise ReadinessError("readiness_observation_in_future")
    if proposal.valid_until <= now:
        raise ReadinessError("readiness_proposal_expired")


class MetaReadinessQuote(_ReadinessModel):
    proposal_sha256: Digest
    proposal: MetaReadinessProposal
    input_reservation_microdollars: Annotated[int, Field(ge=1, le=MAX_HARD_CAP_MICRODOLLARS)]
    output_reservation_microdollars: Annotated[int, Field(ge=1, le=MAX_HARD_CAP_MICRODOLLARS)]
    per_call_reservation_microdollars: Annotated[int, Field(ge=1, le=MAX_HARD_CAP_MICRODOLLARS)]
    max_run_reservation_microdollars: Annotated[int, Field(ge=1, le=20 * MAX_HARD_CAP_MICRODOLLARS)]
    full_run_reservation_fits: bool
    budget_limited_stop_notice: (
        Literal[
            "Budget limits may stop this run before the configured maximum number of model calls."
        ]
        | None
    )
    native_status: Literal["not_run"] = "not_run"
    execution_available: Literal[False] = False

    @field_validator("execution_available", mode="before")
    @classmethod
    def not_an_execution_toggle(cls, value):
        if value is not False:
            raise ValueError("readiness quotes never enable execution")
        return value

    @model_validator(mode="after")
    def authoritative_quote(self):
        input_cost, output_cost = _costs(self.proposal)
        per_call = input_cost + output_cost
        maximum = per_call * self.proposal.application_limits.max_model_calls
        fits = maximum <= self.proposal.account.run_cap_microdollars
        notice = None if fits else BUDGET_LIMITED_STOP_NOTICE
        if (
            self.proposal_sha256,
            self.input_reservation_microdollars,
            self.output_reservation_microdollars,
            self.per_call_reservation_microdollars,
            self.max_run_reservation_microdollars,
            self.full_run_reservation_fits,
            self.budget_limited_stop_notice,
        ) != (
            proposal_sha256(self.proposal),
            input_cost,
            output_cost,
            per_call,
            maximum,
            fits,
            notice,
        ):
            raise ValueError("readiness quote must match its exact canonical proposal")
        return self


def quote_proposal(
    proposal: MetaReadinessProposal,
    *,
    policy: ReadinessPolicy | None = None,
) -> MetaReadinessQuote:
    proposal = MetaReadinessProposal.model_validate_json(proposal.model_dump_json())
    policy = policy or ReadinessPolicy()
    if proposal.account.hard_cap_microdollars > policy.max_hard_cap_microdollars:
        raise ReadinessError("readiness_hard_cap_exceeds_policy")
    input_cost, output_cost = _costs(proposal)
    per_call = input_cost + output_cost
    maximum = per_call * proposal.application_limits.max_model_calls
    fits = maximum <= proposal.account.run_cap_microdollars
    return MetaReadinessQuote(
        proposal_sha256=proposal_sha256(proposal),
        proposal=proposal,
        input_reservation_microdollars=input_cost,
        output_reservation_microdollars=output_cost,
        per_call_reservation_microdollars=per_call,
        max_run_reservation_microdollars=maximum,
        full_run_reservation_fits=fits,
        budget_limited_stop_notice=None if fits else BUDGET_LIMITED_STOP_NOTICE,
    )
