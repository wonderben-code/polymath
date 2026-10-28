"""Native research contracts and shared Meta wire observations, entirely synthetic."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from test_meta_probe_contracts import SECRET, attestation_data
from test_meta_readiness_contracts import proposal_data

from polymath.meta_probe_contracts import (
    MetaObservation,
    MetaProbePolicy,
    ProviderAccountAttestation,
)
from polymath.meta_readiness_contracts import MetaReadinessProposal, proposal_sha256
from polymath.meta_research_contracts import (
    TASK_KINDS,
    MetaResearchManifest,
    MetaResearchPayload,
    MetaResearchRequest,
    NativeResearchPolicy,
    NativeResearchReview,
    NativeTaskProfileBinding,
    PassedCanaryBinding,
    assess_meta_usage,
    build_meta_research_request,
    build_research_manifest,
    canonical_research_manifest_json,
    controller_protocol_sha256,
    derive_task_profiles,
    meta_research_request_sha256,
    minimum_controller_context_characters,
    model_reply_from_observation,
    profile_for_task,
    research_manifest_sha256,
    research_policy_sha256,
    research_step_sha256,
    task_profile_sha256,
    validate_research_manifest,
)
from polymath.research.meta import MetaModel, ModelError

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)
READINESS_ID = "12345678-1234-1234-1234-123456789abc"
RECEIPT_ID = "12345678-1234-1234-1234-123456789def"
MESSAGES = [
    {"role": "system", "content": "Existing scientific controller instructions."},
    {"role": "user", "content": "Scientific goal and exact saved source observations."},
]


def research_fixture(*, now=None):
    now = now or NOW
    proposal = MetaReadinessProposal.model_validate(proposal_data(now=now))
    policy = NativeResearchPolicy(
        paid_research_enabled=True,
        native_account_id=proposal.account.account_id,
        model_id=proposal.model_id,
        api_key=SecretStr(SECRET),
    )
    canary = PassedCanaryBinding(
        receipt_id=RECEIPT_ID,
        receipt_sha256="e" * 64,
        account_id=proposal.account.account_id,
        model_id=proposal.model_id,
        credential_binding_sha256=policy.credential_binding_sha256,
    )
    review = NativeResearchReview.model_validate(
        {
            "model_wire_usage_confirmed": True,
            "pricing_bounds_confirmed": True,
            "funding_cap_confirmed": True,
            "observed_at": (now - timedelta(minutes=1)).isoformat(),
            "valid_until": (now + timedelta(hours=1)).isoformat(),
            "evidence": [
                {"url": "https://example.org/current-human-review", "content_sha256": "f" * 64}
            ],
        }
    )
    attestation = ProviderAccountAttestation.model_validate(attestation_data(now=now))
    manifest = build_research_manifest(
        READINESS_ID,
        proposal,
        policy=policy,
        native_control_epoch=4,
        canary=canary,
        review=review,
        provider_account_attestation=attestation,
        valid_until=now + timedelta(minutes=20),
    )
    return proposal, policy, canary, review, attestation, manifest


def research_observation(request, **changes):
    content = "Known generated controller action content."
    if "content" in changes and "content_sha256" not in changes:
        value = changes["content"]
        changes["content_sha256"] = (
            hashlib.sha256(value.encode()).hexdigest() if value is not None else None
        )
    return MetaObservation(
        **(
            {
                "request_sha256": meta_research_request_sha256(request),
                "requested_model_id": request.payload.model,
                "observed_model_id": request.payload.model,
                "http_status": 200,
                "finish_reason": "stop",
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "body_sha256": "a" * 64,
                "body_complete": True,
                "input_tokens": 12,
                "output_tokens": 5,
                "started_at": NOW,
                "finished_at": NOW + timedelta(seconds=1),
            }
            | changes
        )
    )


def test_all_five_profiles_bind_actual_full_reviewed_limits_and_orchestration_deadline():
    data = proposal_data()
    data["application_limits"] |= {"max_model_calls": 20, "request_timeout_seconds": 60}
    proposal = MetaReadinessProposal.model_validate(data)
    profiles = derive_task_profiles(proposal)
    assert tuple(item.profile.task_kind for item in profiles) == TASK_KINDS
    for binding in profiles:
        value = binding.profile
        assert value.max_model_calls == (20 if value.task_kind == "research" else 12)
        assert value.max_actions == (16 if value.task_kind == "research" else 12)
        assert value.max_repairs == 2 and value.max_sources == 200
        assert value.max_output_tokens == 4096 and value.max_context_characters == 90000
        assert value.request_timeout_seconds == 60 and value.model_timeout_seconds == 90
        assert value.tool_timeout_seconds == (
            5 if value.task_kind in ("formalise", "write_summary", "write_paper") else 20
        )
        assert value.controller_sha256 == controller_protocol_sha256(value.task_kind)
        assert binding.profile_sha256 == task_profile_sha256(value)


def test_activation_preflights_fixed_controller_characters_without_guessing_tokens():
    values = [minimum_controller_context_characters(kind) for kind in TASK_KINDS]
    assert max(values) > 1000 and max(values) < 90000
    data = proposal_data()
    data["application_limits"]["max_context_characters"] = 1000
    with pytest.raises(ValueError, match="fixed native controller prompt"):
        derive_task_profiles(MetaReadinessProposal.model_validate(data))


def test_manifest_binds_quantitative_proposal_canary_and_review_without_credentials():
    proposal, policy, canary, review, _, manifest = research_fixture()
    assert manifest.proposal == proposal and manifest.proposal_sha256 == proposal_sha256(proposal)
    assert manifest.canary == canary and manifest.review == review
    assert manifest.audience == "operator_browser" and manifest.clear_kill_switch is True
    assert SECRET not in canonical_research_manifest_json(manifest) and SECRET not in repr(policy)
    assert (
        research_manifest_sha256(manifest)
        == hashlib.sha256(canonical_research_manifest_json(manifest).encode()).hexdigest()
    )
    assert MetaResearchManifest.model_validate_json(manifest.model_dump_json()) == manifest


def test_policy_defaults_off_and_same_key_retains_one_binding_across_probe_and_research():
    _, policy, _, _, _, _ = research_fixture()
    probe = MetaProbePolicy(
        native_account_id=policy.native_account_id, model_id=policy.model_id, api_key=policy.api_key
    )
    assert (
        not NativeResearchPolicy().paid_research_enabled and not NativeResearchPolicy().configured
    )
    assert policy.credential_binding_sha256 == probe.credential_binding_sha256
    rotated = NativeResearchPolicy(
        native_account_id=policy.native_account_id,
        model_id=policy.model_id,
        api_key=SecretStr("rotated-key"),
    )
    assert research_policy_sha256(policy) != research_policy_sha256(rotated)


@pytest.mark.parametrize("consent", [0, 59, 3601, True])
def test_consent_is_bounded_server_policy_not_client_authority(consent):
    with pytest.raises(ValueError):
        NativeResearchPolicy(consent_seconds=consent)


def test_controller_fingerprint_changes_with_actual_prompt_but_historical_manifest_remains_valid(
    monkeypatch,
):
    from polymath.research import controller

    proposal, _, _, _, _, manifest = research_fixture()
    old = manifest.task_profiles[0]
    monkeypatch.setattr(
        controller, "SYSTEM_PROMPT", controller.SYSTEM_PROMPT + "\nNew static instructions."
    )
    assert controller_protocol_sha256("research") != old.profile.controller_sha256
    assert derive_task_profiles(proposal) != manifest.task_profiles
    validate_research_manifest(manifest, proposal)
    assert profile_for_task(manifest, "research") == old.profile
    assert MetaResearchManifest.model_validate_json(manifest.model_dump_json()) == manifest


def test_each_artifact_profile_hash_binds_its_actual_kind_instructions(monkeypatch):
    from polymath import artifact_controller

    before = {
        kind: controller_protocol_sha256(kind)
        for kind in ("formalise", "write_summary", "write_paper")
    }
    instructions = dict(artifact_controller.KIND_INSTRUCTIONS)
    instructions["write_paper"] += "\nNew paper-only instruction."
    monkeypatch.setattr(artifact_controller, "KIND_INSTRUCTIONS", instructions)
    assert controller_protocol_sha256("write_paper") != before["write_paper"]
    assert controller_protocol_sha256("write_summary") == before["write_summary"]


def test_historical_manifest_does_not_consult_current_prompt_fingerprints(monkeypatch):
    import polymath.meta_research_contracts as contracts

    proposal, _, _, _, _, manifest = research_fixture()

    def deny(*args, **kwargs):
        pytest.fail("Historical accounting tried to use current controller code")

    monkeypatch.setattr(contracts, "controller_protocol_sha256", deny)
    validate_research_manifest(manifest)
    request = build_meta_research_request(proposal, MESSAGES, max_output_tokens=4096)
    assert (
        assess_meta_usage(proposal, request, research_observation(request)).actual_microdollars
        == 22
    )
    assert profile_for_task(manifest, "write_summary").task_kind == "write_summary"


@pytest.mark.parametrize(
    "field,value",
    [
        ("audience", "public_sessions"),
        ("clear_kill_switch", False),
        ("clear_kill_switch", 1),
        ("proposal_sha256", "0" * 64),
        ("account_id", "another-ledger"),
        ("model_id", "other-model"),
        ("credential_binding_sha256", "0" * 64),
        ("native_control_epoch", 2**53 - 1),
    ],
)
def test_manifest_cannot_escape_audience_scope_key_model_or_proposal(field, value):
    _, _, _, _, _, manifest = research_fixture()
    with pytest.raises(ValidationError):
        MetaResearchManifest.model_validate(manifest.model_dump(mode="json") | {field: value})


@pytest.mark.parametrize(
    "field", ["model_wire_usage_confirmed", "pricing_bounds_confirmed", "funding_cap_confirmed"]
)
def test_fresh_review_requires_all_explicit_boolean_confirmations(field):
    _, _, _, review, _, _ = research_fixture()
    for value in (False, 1, "true"):
        with pytest.raises(ValidationError):
            NativeResearchReview.model_validate(review.model_dump(mode="json") | {field: value})


def test_task_profile_hash_prevents_unreviewed_limit_edits():
    _, _, _, _, _, manifest = research_fixture()
    value = manifest.task_profiles[0].model_dump(mode="json")
    value["profile"]["max_output_tokens"] = 1200
    with pytest.raises(ValidationError, match="hash mismatch"):
        NativeTaskProfileBinding.model_validate(value)


def test_request_is_immutable_exact_roles_and_full_output_cap_with_context_bound():
    proposal, _, _, _, _, _ = research_fixture()
    messages = [*MESSAGES, {"role": "assistant", "content": "Prior generated action."}]
    request = build_meta_research_request(proposal, messages, max_output_tokens=4096)
    messages[-1]["content"] = "Changed after fingerprint"
    assert request.payload.messages[-1].content == "Prior generated action."
    assert request.payload.max_tokens == 4096 and request.payload.stream is False
    with pytest.raises(ValueError, match="output cap"):
        build_meta_research_request(proposal, MESSAGES, max_output_tokens=1200)
    assert (
        meta_research_request_sha256(request)
        == hashlib.sha256(
            json.dumps(
                request.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest()
    )


@pytest.mark.parametrize(
    "messages",
    [
        [{"role": "tool", "content": "Unapproved role"}],
        [{"role": "user", "content": "x"}] * 65,
        [{"role": "user", "content": "x" * 90001}],
        [{"role": "user", "content": "x" * 45001}, {"role": "assistant", "content": "y" * 45000}],
    ],
)
def test_request_limits_are_explicit_and_bounded(messages):
    proposal, _, _, _, _, _ = research_fixture()
    with pytest.raises(ValueError):
        build_meta_research_request(proposal, messages, max_output_tokens=4096)


def test_request_context_uses_reviewed_char_limit_without_any_billing_token_estimate():
    data = proposal_data()
    data["application_limits"]["max_context_characters"] = 1000
    proposal = MetaReadinessProposal.model_validate(data)
    with pytest.raises(ValueError, match="reviewed context"):
        build_meta_research_request(
            proposal, [{"role": "user", "content": "x" * 1001}], max_output_tokens=4096
        )
    request = build_meta_research_request(
        proposal, [{"role": "user", "content": "😀" * 1000}], max_output_tokens=4096
    )
    assert (
        assess_meta_usage(proposal, request, research_observation(request)).actual_microdollars
        == 22
    )


def test_step_hash_binds_original_activation_profile_request_and_step_identity():
    proposal, _, _, _, _, manifest = research_fixture()
    request = build_meta_research_request(proposal, MESSAGES, max_output_tokens=4096)
    activation = research_manifest_sha256(manifest)
    profile = manifest.task_profiles[0].profile_sha256
    first = research_step_sha256(
        request, activation_sha256=activation, profile_sha256=profile, request_id="model-0001"
    )
    assert first != research_step_sha256(
        request, activation_sha256=activation, profile_sha256=profile, request_id="model-0002"
    )
    assert first != research_step_sha256(
        request, activation_sha256="0" * 64, profile_sha256=profile, request_id="model-0001"
    )
    assert first != research_step_sha256(
        request, activation_sha256=activation, profile_sha256="0" * 64, request_id="model-0001"
    )


@pytest.mark.parametrize(
    "changes,status,cost,freeze",
    [
        ({}, "known", 22, False),
        ({"content": "Invalid generated JSON"}, "known", 22, False),
        ({"finish_reason": "length"}, "known", 22, False),
        ({"observed_model_id": "alias"}, "unknown", None, True),
        ({"input_tokens": None, "output_tokens": None}, "unknown", None, False),
        ({"input_tokens": 400000, "output_tokens": 5000}, "known", 410000, True),
        ({"input_tokens": 2**63 - 1}, "unknown", None, True),
    ],
)
def test_usage_reuses_probe_accounting_without_parsing_scientific_actions(
    changes, status, cost, freeze
):
    proposal, _, _, _, _, _ = research_fixture()
    request = build_meta_research_request(proposal, MESSAGES, max_output_tokens=4096)
    result = assess_meta_usage(proposal, request, research_observation(request, **changes))
    assert (result.status, result.actual_microdollars, result.freeze_required) == (
        status,
        cost,
        freeze,
    )


def test_modelreply_adapter_preserves_action_for_existing_controller_repairs_only_after_counting():
    proposal, _, _, _, _, _ = research_fixture()
    request = build_meta_research_request(proposal, MESSAGES, max_output_tokens=4096)
    observation = research_observation(request, content="Invalid generated JSON")
    reply = model_reply_from_observation(proposal, request, observation)
    assert (
        reply.content == "Invalid generated JSON"
        and reply.input_tokens == 12
        and reply.output_tokens == 5
    )
    for changes in (
        {"finish_reason": "length"},
        {"input_tokens": None, "output_tokens": None},
        {"input_tokens": 400000},
    ):
        with pytest.raises(ModelError):
            model_reply_from_observation(
                proposal, request, research_observation(request, **changes)
            )


@pytest.mark.asyncio
async def test_research_observer_uses_same_native_wire_without_alternate_provider():
    proposal, policy, _, _, _, _ = research_fixture()
    request = build_meta_research_request(proposal, MESSAGES, max_output_tokens=4096)
    requests = []

    def handler(http_request):
        requests.append(http_request)
        assert str(http_request.url) == request.endpoint
        assert json.loads(http_request.content) == request.payload.model_dump(mode="json")
        return httpx.Response(
            200,
            json={
                "model": policy.model_id,
                "usage": {"prompt_tokens": 12, "completion_tokens": 5},
                "choices": [
                    {"finish_reason": "length", "message": {"content": "Invalid JSON action"}}
                ],
            },
        )

    adapter = MetaModel(
        api_key=policy.api_key,
        model=policy.model_id,
        live_enabled=True,
        transport=httpx.MockTransport(handler),
    )
    observed = await adapter.observe_research(request)
    assert len(requests) == 1 and observed.request_sha256 == meta_research_request_sha256(request)
    assert assess_meta_usage(proposal, request, observed).actual_microdollars == 22
    with pytest.raises(ModelError, match="invalid_response"):
        model_reply_from_observation(proposal, request, observed)


@pytest.mark.asyncio
async def test_research_observer_defaults_disabled_and_rejects_wrong_model_before_send():
    proposal, policy, _, _, _, _ = research_fixture()
    request = build_meta_research_request(proposal, MESSAGES, max_output_tokens=4096)
    requests = []
    transport = httpx.MockTransport(lambda value: requests.append(value))
    disabled = MetaModel(api_key=policy.api_key, model=policy.model_id, transport=transport)
    with pytest.raises(ModelError, match="live_model_disabled"):
        await disabled.observe_research(request)
    wrong = MetaResearchRequest(
        payload=MetaResearchPayload(
            model="different-model", messages=request.payload.messages, max_tokens=4096
        )
    )
    enabled = MetaModel(
        api_key=policy.api_key, model=policy.model_id, live_enabled=True, transport=transport
    )
    with pytest.raises(ModelError, match="binding_mismatch"):
        await enabled.observe_research(wrong)
    assert requests == []
