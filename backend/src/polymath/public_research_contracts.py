"""Pure durable public delegation facts, limits and visitor-safe projections.

Nothing here activates, authenticates, sends, constructs a model, mutates a
ledger or duplicates a credential. Fresh policy checks are separate from exact
historical parsing so old accounting remains inspectable after deployment changes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_serializer, model_validator

from polymath.discovery_contracts import DiscoveryIntent
from polymath.meta_probe_contracts import ModelId, ProbeId, ProviderAccountAttestation
from polymath.meta_readiness_contracts import (
    MAX_HARD_CAP_MICRODOLLARS,
    META_COMPLETIONS_URL,
    Digest,
    MetaReadinessProposal,
    ReadinessPolicy,
    _ReadinessModel,
    _utc,
    proposal_sha256,
    quote_proposal,
    validate_proposal_time,
)
from polymath.meta_research_contracts import (
    RESEARCH_PROTOCOL,
    TASK_KINDS,
    MetaResearchManifest,
    NativeDiscoveryProfile,
    NativeDiscoveryProfileBinding,
    NativeResearchPolicy,
    NativeResearchReview,
    NativeTaskProfile,
    NativeTaskProfileBinding,
    PassedCanaryBinding,
    TaskKind,
    build_research_manifest,
    derive_discovery_ladder_profile,
    derive_task_profiles,
)

PUBLIC_RESEARCH_PROTOCOL = "polymath-meta-public-research-v1"
PUBLIC_ACCESS_PROTOCOL = "polymath-research-access-v1"
MAX_PUBLIC_DELEGATION_SECONDS = 86400
MAX_PUBLIC_MANIFEST_BYTES = 262144


def _canonical(value) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class PublicResourceLimits(_ReadinessModel):
    submit_session_per_minute: Annotated[int, Field(ge=1, le=6)] = 6
    submit_network_per_minute: Annotated[int, Field(ge=1, le=10)] = 10
    submit_service_per_minute: Annotated[int, Field(ge=1, le=30)] = 30
    mint_network_per_hour: Annotated[int, Field(ge=1, le=10)] = 10
    mint_network_per_day: Annotated[int, Field(ge=1, le=50)] = 50
    mint_service_per_hour: Annotated[int, Field(ge=1, le=200)] = 200
    mint_service_per_day: Annotated[int, Field(ge=1, le=1000)] = 1000
    read_session_per_minute: Annotated[int, Field(ge=1, le=60)] = 60
    read_network_per_minute: Annotated[int, Field(ge=1, le=240)] = 240
    read_service_per_minute: Annotated[int, Field(ge=1, le=600)] = 600
    sse_session_connections: Annotated[int, Field(ge=1, le=2)] = 2
    sse_network_connections: Annotated[int, Field(ge=1, le=4)] = 4
    sse_service_connections: Annotated[int, Field(ge=1, le=20)] = 20
    sse_max_seconds: Annotated[int, Field(ge=1, le=300)] = 300
    free_write_session_per_minute: Annotated[int, Field(ge=1, le=3)] = 3
    free_write_network_per_minute: Annotated[int, Field(ge=1, le=6)] = 6
    free_write_service_per_minute: Annotated[int, Field(ge=1, le=20)] = 20
    control_session_per_minute: Annotated[int, Field(ge=1, le=30)] = 30
    control_network_per_minute: Annotated[int, Field(ge=1, le=60)] = 60
    control_service_per_minute: Annotated[int, Field(ge=1, le=120)] = 120
    body_max_bytes: Annotated[int, Field(ge=1, le=65536)] = 65536


MoneyCap = Annotated[int, Field(ge=1, le=MAX_HARD_CAP_MICRODOLLARS)]


class PublicLimits(_ReadinessModel):
    public_total_cap_microdollars: MoneyCap
    public_daily_cap_microdollars: MoneyCap
    session_total_cap_microdollars: MoneyCap
    session_daily_cap_microdollars: MoneyCap
    max_jobs_per_session_day: Annotated[int, Field(ge=1, le=12)] = 12
    max_jobs_per_day: Annotated[int, Field(ge=1, le=50)] = 50
    max_active_jobs_per_session: Literal[1] = 1
    max_active_jobs: Annotated[int, Field(ge=1, le=5)] = 5
    max_queued_jobs: Annotated[int, Field(ge=1, le=4)] = 4
    max_active_jobs_per_network: Annotated[int, Field(ge=1, le=2)] = 2
    resources: PublicResourceLimits = Field(default_factory=PublicResourceLimits)

    @field_validator("max_active_jobs_per_session", mode="before")
    @classmethod
    def one_owner_job(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("public_active_owner_limit_must_remain_one")
        return value

    @model_validator(mode="after")
    def nested_ceilings(self):
        if (
            self.public_daily_cap_microdollars > self.public_total_cap_microdollars
            or self.session_total_cap_microdollars > self.public_total_cap_microdollars
            or self.session_daily_cap_microdollars
            > min(self.session_total_cap_microdollars, self.public_daily_cap_microdollars)
            or self.max_jobs_per_session_day > self.max_jobs_per_day
            or self.max_queued_jobs > self.max_active_jobs
            or self.max_active_jobs_per_network > self.max_active_jobs
        ):
            raise ValueError("public_limits_exceed_parent_ceiling")
        return self


@dataclass(frozen=True)
class PublicResearchPolicy:
    enabled: bool = False
    max_delegation_seconds: int = MAX_PUBLIC_DELEGATION_SECONDS
    max_jobs_per_session_day: int = 12
    max_jobs_per_day: int = 50
    max_active_jobs: int = 5
    max_queued_jobs: int = 4
    max_active_jobs_per_network: int = 2
    resources: PublicResourceLimits = field(default_factory=PublicResourceLimits)

    def __post_init__(self):
        if type(self.enabled) is not bool or not isinstance(self.resources, PublicResourceLimits):
            raise ValueError("invalid_public_research_policy")
        for name, ceiling in (
            ("max_delegation_seconds", MAX_PUBLIC_DELEGATION_SECONDS),
            ("max_jobs_per_session_day", 12),
            ("max_jobs_per_day", 50),
            ("max_active_jobs", 5),
            ("max_queued_jobs", 4),
            ("max_active_jobs_per_network", 2),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= ceiling:
                raise ValueError("invalid_public_research_policy")
        PublicResourceLimits.model_validate_json(self.resources.model_dump_json())


def public_policy_sha256(policy: PublicResearchPolicy) -> str:
    if not isinstance(policy, PublicResearchPolicy):
        raise ValueError("invalid_public_research_policy")
    return _sha(
        {
            "protocol": "polymath-public-research-policy-v1",
            "enabled": policy.enabled,
            "max_delegation_seconds": policy.max_delegation_seconds,
            **{
                name: getattr(policy, name)
                for name in (
                    "max_jobs_per_session_day",
                    "max_jobs_per_day",
                    "max_active_jobs",
                    "max_queued_jobs",
                    "max_active_jobs_per_network",
                )
            },
            "resources": policy.resources.model_dump(mode="json"),
        }
    )


def public_sponsor_policy_sha256(
    *, operator_fingerprint: str, actor_id: str, readiness_policy: ReadinessPolicy
) -> str:
    if (
        not isinstance(operator_fingerprint, str)
        or re.fullmatch(r"[a-f0-9]{64}", operator_fingerprint) is None
        or not isinstance(actor_id, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", actor_id) is None
        or not isinstance(readiness_policy, ReadinessPolicy)
        or actor_id not in readiness_policy.allowed_actor_ids
    ):
        raise ValueError("invalid_public_sponsor_policy")
    return _sha(
        {
            "protocol": "polymath-public-sponsor-policy-v1",
            "operator_fingerprint": operator_fingerprint,
            "actor_id": actor_id,
            "preparation_enabled": readiness_policy.preparation_enabled,
            "allowed_actor_ids": list(readiness_policy.allowed_actor_ids),
            "max_hard_cap_microdollars": readiness_policy.max_hard_cap_microdollars,
        }
    )


def public_limits_sha256(limits: PublicLimits) -> str:
    value = PublicLimits.model_validate_json(limits.model_dump_json())
    return _sha(value.model_dump(mode="json"))


_PUBLIC_EXTRA = frozenset(
    {
        "authorization_mode",
        "continues_after_operator_logout",
        "public_limits",
        "public_policy_sha256",
        "sponsor_policy_sha256",
    }
)


class PublicResearchManifest(_ReadinessModel):
    protocol: Literal["polymath-meta-public-research-v1"] = PUBLIC_RESEARCH_PROTOCOL
    audience: Literal["public_sessions"] = "public_sessions"
    authorization_mode: Literal["durable_owner_delegation"] = "durable_owner_delegation"
    continues_after_operator_logout: Literal[True]
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
    valid_until: datetime
    public_limits: PublicLimits
    public_policy_sha256: Digest
    sponsor_policy_sha256: Digest

    @field_validator("continues_after_operator_logout", "clear_kill_switch", mode="before")
    @classmethod
    def explicit_true(cls, value):
        if value is not True:
            raise ValueError("public_delegation_requires_explicit_true_confirmation")
        return value

    @field_validator("valid_until", mode="before")
    @classmethod
    def utc_deadline(cls, value):
        return _utc(value)

    @field_validator("task_profiles", mode="before")
    @classmethod
    def json_profiles(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_serializer(mode="wrap")
    def omit_absent_discovery(self, handler):
        value = handler(self)
        if self.discovery_ladder_profile is None:
            value.pop("discovery_ladder_profile", None)
        return value

    @model_validator(mode="after")
    def original_common_bindings(self):
        # A separate DTO preserves private isinstance/protocol boundaries while
        # reusing its exact historical quantitative/profile validators.
        raw = self.model_dump(mode="json", exclude=_PUBLIC_EXTRA)
        raw["protocol"], raw["audience"] = RESEARCH_PROTOCOL, "operator_browser"
        MetaResearchManifest.model_validate_json(_canonical(raw))
        quote = quote_proposal(self.proposal)
        limits = self.public_limits
        if (
            limits.public_total_cap_microdollars > self.proposal.account.hard_cap_microdollars
            or limits.public_daily_cap_microdollars > self.proposal.account.daily_cap_microdollars
            or min(
                limits.public_total_cap_microdollars,
                limits.public_daily_cap_microdollars,
                limits.session_total_cap_microdollars,
                limits.session_daily_cap_microdollars,
            )
            < quote.per_call_reservation_microdollars
        ):
            raise ValueError("public_caps_cannot_cover_reviewed_native_reservation")
        if (
            len(_canonical(self.model_dump(mode="json")).encode("utf-8"))
            > MAX_PUBLIC_MANIFEST_BYTES
        ):
            raise ValueError("public_manifest_size_limit")
        return self


def validate_public_research_manifest(
    manifest: PublicResearchManifest, proposal: MetaReadinessProposal | None = None
) -> None:
    value = PublicResearchManifest.model_validate_json(manifest.model_dump_json())
    if proposal is not None and (
        value.proposal != proposal or value.proposal_sha256 != proposal_sha256(proposal)
    ):
        raise ValueError("public_saved_proposal_mismatch")


def _current_public_limits(limits: PublicLimits, policy: PublicResearchPolicy) -> None:
    for name in (
        "max_jobs_per_session_day",
        "max_jobs_per_day",
        "max_active_jobs",
        "max_queued_jobs",
        "max_active_jobs_per_network",
    ):
        if getattr(limits, name) > getattr(policy, name):
            raise ValueError("public_limits_exceed_current_policy")
    actual, ceiling = limits.resources.model_dump(), policy.resources.model_dump()
    if any(actual[name] > ceiling[name] for name in actual):
        raise ValueError("public_resources_exceed_current_policy")


def validate_current_public_research_manifest(
    manifest: PublicResearchManifest,
    *,
    research_policy: NativeResearchPolicy,
    public_policy: PublicResearchPolicy,
    sponsor_policy_sha256: str,
    now: datetime,
) -> None:
    validate_public_research_manifest(manifest)
    now = _utc(now)
    if (
        not public_policy.enabled
        or not research_policy.paid_research_enabled
        or not research_policy.configured
    ):
        raise ValueError("public_research_disabled")
    if (
        manifest.public_policy_sha256 != public_policy_sha256(public_policy)
        or manifest.sponsor_policy_sha256 != sponsor_policy_sha256
        or manifest.account_id != research_policy.native_account_id
        or manifest.model_id != research_policy.model_id
        or manifest.credential_binding_sha256 != research_policy.credential_binding_sha256
    ):
        raise ValueError("public_research_authority_changed")
    validate_proposal_time(manifest.proposal, now)
    if not (
        max(manifest.review.observed_at, manifest.provider_account_attestation.observed_at)
        <= now
        < manifest.valid_until
        <= now + timedelta(seconds=public_policy.max_delegation_seconds)
    ):
        raise ValueError("public_research_authority_expired")
    _current_public_limits(manifest.public_limits, public_policy)
    if manifest.task_profiles != derive_task_profiles(manifest.proposal):
        raise ValueError("public_research_profile_changed")
    if manifest.discovery_ladder_profile is not None:
        original = manifest.discovery_ladder_profile.profile
        if manifest.discovery_ladder_profile != derive_discovery_ladder_profile(
            manifest.proposal,
            DiscoveryIntent(max_stages=original.max_stages, max_depth=original.max_depth),
        ):
            raise ValueError("public_research_profile_changed")


def build_public_research_manifest(
    readiness_id: str,
    proposal: MetaReadinessProposal,
    *,
    research_policy: NativeResearchPolicy,
    public_policy: PublicResearchPolicy,
    sponsor_policy_sha256: str,
    native_control_epoch: int,
    canary: PassedCanaryBinding,
    review: NativeResearchReview,
    provider_account_attestation: ProviderAccountAttestation,
    public_limits: PublicLimits,
    valid_until: datetime,
    now: datetime,
    discovery: DiscoveryIntent | None = None,
) -> PublicResearchManifest:
    common = build_research_manifest(
        readiness_id,
        proposal,
        policy=research_policy,
        native_control_epoch=native_control_epoch,
        canary=canary,
        review=review,
        provider_account_attestation=provider_account_attestation,
        valid_until=valid_until,
        discovery=discovery,
    ).model_dump(mode="json")
    common.update(
        protocol=PUBLIC_RESEARCH_PROTOCOL,
        audience="public_sessions",
        authorization_mode="durable_owner_delegation",
        continues_after_operator_logout=True,
        public_limits=public_limits.model_dump(mode="json"),
        public_policy_sha256=public_policy_sha256(public_policy),
        sponsor_policy_sha256=sponsor_policy_sha256,
    )
    value = PublicResearchManifest.model_validate_json(_canonical(common))
    validate_current_public_research_manifest(
        value,
        research_policy=research_policy,
        public_policy=public_policy,
        sponsor_policy_sha256=sponsor_policy_sha256,
        now=now,
    )
    return value


def canonical_public_research_manifest_json(manifest: PublicResearchManifest) -> str:
    validate_public_research_manifest(manifest)
    return _canonical(manifest.model_dump(mode="json"))


def public_research_manifest_sha256(manifest: PublicResearchManifest) -> str:
    return hashlib.sha256(
        canonical_public_research_manifest_json(manifest).encode("utf-8")
    ).hexdigest()


def public_profile_for_task(
    manifest: PublicResearchManifest,
    task_kind: TaskKind,
    *,
    discovery: DiscoveryIntent | None = None,
) -> NativeTaskProfile | NativeDiscoveryProfile:
    validate_public_research_manifest(manifest)
    if discovery is not None:
        intent = DiscoveryIntent.model_validate_json(discovery.model_dump_json())
        binding = manifest.discovery_ladder_profile
        if (
            task_kind != "research"
            or binding is None
            or intent.max_stages > binding.profile.max_stages
            or intent.max_depth > binding.profile.max_depth
        ):
            raise ValueError("public_discovery_intent_exceeds_approved_profile")
        return binding.profile
    for binding in manifest.task_profiles:
        if binding.profile.task_kind == task_kind:
            return binding.profile
    raise ValueError("unsupported_public_scientific_task")


AccessReason = Literal[
    "public_research_disabled",
    "public_research_activation_required",
    "public_research_authority_expired",
    "public_research_stopped",
    "public_research_frozen",
    "public_research_budget_exhausted",
    "public_research_daily_budget_exhausted",
    "public_research_session_budget_exhausted",
    "public_research_session_daily_budget_exhausted",
    "public_research_prior_unresolved",
    "public_research_resource_unavailable",
    "operator_required",
    "research_unavailable",
]
PUBLIC_ACCESS_REASONS = frozenset(AccessReason.__args__)
MoneyString = Annotated[str, Field(pattern=r"^(?:0|[1-9][0-9]{0,8})$")]


class PublicAccessLimits(_ReadinessModel):
    session_total_cap_microdollars: MoneyString
    session_daily_cap_microdollars: MoneyString
    max_jobs_per_session_day: Annotated[int, Field(ge=1, le=12)]
    max_active_jobs_per_session: Literal[1]
    task_kinds: Annotated[tuple[TaskKind, ...], Field(min_length=5, max_length=5)]
    discovery_intent: DiscoveryIntent | None

    @field_validator("max_active_jobs_per_session", mode="before")
    @classmethod
    def one_owner(cls, value):
        return PublicLimits.one_owner_job(value)

    @field_validator("task_kinds", mode="before")
    @classmethod
    def json_kinds(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def safe_visitor_limits(self):
        if (
            set(self.task_kinds) != set(TASK_KINDS)
            or not 1
            <= int(self.session_daily_cap_microdollars)
            <= int(self.session_total_cap_microdollars)
            <= MAX_HARD_CAP_MICRODOLLARS
        ):
            raise ValueError("invalid_public_visitor_limits")
        return self


class PublicResearchAccess(_ReadinessModel):
    protocol: Literal["polymath-research-access-v1"] = PUBLIC_ACCESS_PROTOCOL
    audience: Literal["public_sessions", "operator_browser"] | None
    available: bool
    reason: AccessReason | None
    expected_activation_sha256: Digest | None
    expires_at: datetime | None
    limits: PublicAccessLimits | None
    remaining_session_allowance_microdollars: MoneyString | None
    remaining_session_jobs_today: Annotated[int, Field(ge=0, le=12)] | None
    retry_after_seconds: Annotated[int, Field(ge=0, le=86400)] | None

    @field_validator("expires_at", mode="before")
    @classmethod
    def utc_expiry(cls, value):
        return None if value is None else _utc(value)

    @model_validator(mode="after")
    def projection_scope(self):
        scope = (
            self.expected_activation_sha256,
            self.expires_at,
            self.limits,
        )
        if (
            self.audience == "public_sessions"
            and any(item is not None for item in scope)
            and any(item is None for item in scope)
        ):
            raise ValueError("public_access_scope_must_be_complete_or_absent")
        if self.limits is None and (
            self.remaining_session_allowance_microdollars is not None
            or self.remaining_session_jobs_today is not None
        ):
            raise ValueError("public_access_counters_require_scoped_limits")
        if self.available and (
            self.audience != "public_sessions"
            or self.reason is not None
            or self.expected_activation_sha256 is None
            or self.expires_at is None
            or self.limits is None
            or self.remaining_session_allowance_microdollars is None
            or self.remaining_session_jobs_today is None
            or int(self.remaining_session_allowance_microdollars) < 1
        ):
            raise ValueError("public_access_requires_current_scoped_facts")
        if not self.available and self.reason is None:
            raise ValueError("unavailable_public_access_requires_reason")
        if self.audience != "public_sessions" and any(
            value is not None
            for value in (
                self.expected_activation_sha256,
                self.expires_at,
                self.limits,
                self.remaining_session_allowance_microdollars,
                self.remaining_session_jobs_today,
            )
        ):
            raise ValueError("private_authority_cannot_escape_access_projection")
        if self.audience == "operator_browser" and self.reason != "operator_required":
            raise ValueError("operator_access_uses_existing_private_route")
        if self.limits is not None and (
            self.remaining_session_allowance_microdollars is not None
            and int(self.remaining_session_allowance_microdollars)
            > int(self.limits.session_total_cap_microdollars)
            or self.remaining_session_jobs_today is not None
            and self.remaining_session_jobs_today > self.limits.max_jobs_per_session_day
        ):
            raise ValueError("public_access_remaining_exceeds_visitor_limits")
        return self
