"""Pure one-call contracts plus real Meta adapter with synthetic MockTransport."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr, ValidationError
from test_meta_readiness_contracts import proposal_data

from polymath.meta_probe_contracts import (
    PROBE_MESSAGES_V1,
    MetaObservation,
    MetaProbeManifest,
    MetaProbePolicy,
    ProviderAccountAttestation,
    assess_probe_observation,
    assess_probe_usage,
    build_probe_manifest,
    build_probe_request,
    canonical_probe_manifest_json,
    observation_sha256,
    probe_manifest_sha256,
    probe_policy_sha256,
    probe_request_sha256,
    validate_probe_manifest,
)
from polymath.meta_readiness_contracts import MetaReadinessProposal
from polymath.research.meta import MetaModel, ModelError

NOW = datetime(2026, 10, 28, 12, tzinfo=UTC)
READINESS_ID = "12345678-1234-1234-1234-123456789abc"
SECRET = "synthetic-meta-probe-key-not-a-real-credential"
ACTION = {
    "kind": "conclude",
    "conclusion": {"outcome": "insufficient_evidence", "reason": "Protocol check only."},
}


def attestation_data(*, now=None):
    now = now or NOW
    return {
        "provider_billing_account_id": "fixture-provider-billing-account",
        "model_id": "fixture-attested-model",
        "currency": "USD",
        "usage_contract": "prompt_completion_inclusive_v1",
        "observed_at": (now - timedelta(minutes=1)).isoformat(),
        "valid_until": (now + timedelta(hours=1)).isoformat(),
        "available_credit_microdollars": 150000000,
        "remaining_provider_allowance_microdollars": 150000000,
        "provider_hard_cap_microdollars": 150000000,
        "evidence": [
            {"url": "https://example.org/provider-billing-dashboard", "content_sha256": "d" * 64}
        ],
    }


def probe_fixture(*, now=None):
    now = now or NOW
    proposal = MetaReadinessProposal.model_validate(proposal_data(now=now))
    policy = MetaProbePolicy(
        paid_probe_enabled=True,
        native_account_id=proposal.account.account_id,
        model_id=proposal.model_id,
        api_key=SecretStr(SECRET),
    )
    attestation = ProviderAccountAttestation.model_validate(attestation_data(now=now))
    manifest = build_probe_manifest(
        READINESS_ID,
        proposal,
        policy=policy,
        native_control_epoch=0,
        provider_account_attestation=attestation,
        valid_until=now + timedelta(minutes=5),
    )
    return proposal, policy, attestation, manifest


def observation_for(manifest, **changes):
    content = json.dumps(ACTION)
    if "content" in changes and "content_sha256" not in changes:
        value = changes["content"]
        changes["content_sha256"] = (
            hashlib.sha256(value.encode()).hexdigest() if value is not None else None
        )
    return MetaObservation(
        **(
            {
                "request_sha256": manifest.request_sha256,
                "requested_model_id": manifest.request.payload.model,
                "observed_model_id": manifest.request.payload.model,
                "http_status": 200,
                "finish_reason": "stop",
                "content": content,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "body_sha256": hashlib.sha256(b"synthetic observed body").hexdigest(),
                "body_complete": True,
                "input_tokens": 12,
                "output_tokens": 5,
                "started_at": NOW,
                "finished_at": NOW + timedelta(seconds=1),
            }
            | changes
        )
    )


def response_body(manifest, **changes):
    return {
        "model": manifest.request.payload.model,
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(ACTION)}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 5},
    } | changes


def adapter(policy, handler, *, enabled=True, timeout=45):
    return MetaModel(
        api_key=policy.api_key,
        model=policy.model_id,
        live_enabled=enabled,
        transport=httpx.MockTransport(handler),
        request_timeout_seconds=timeout,
    )


def test_fixed_request_uses_complete_reviewed_output_cap_and_no_scientific_context():
    proposal, _, _, manifest = probe_fixture()
    request = build_probe_request(proposal)
    assert request.payload.messages == PROBE_MESSAGES_V1
    assert request.payload.max_tokens == proposal.application_limits.max_output_tokens == 4096
    assert request.payload.stream is False
    assert request.payload.model == proposal.model_id
    assert manifest.request_sha256 == probe_request_sha256(request)
    assert manifest.request == request and manifest.clear_kill_switch is True
    assert manifest.reservation_microdollars == 308192


def test_manifest_hash_is_acyclic_exact_and_independent_of_approval_or_job_ids():
    _, policy, _, manifest = probe_fixture()
    encoded = canonical_probe_manifest_json(manifest)
    assert probe_manifest_sha256(manifest) == hashlib.sha256(encoded.encode()).hexdigest()
    assert "approval_id" not in encoded and "job_id" not in encoded
    assert SECRET not in encoded and SECRET not in repr(policy)
    assert manifest.credential_binding_sha256 == policy.credential_binding_sha256
    assert MetaProbeManifest.model_validate_json(manifest.model_dump_json()) == manifest


def test_credential_binding_is_domain_separated_and_stable_but_changes_on_rotation():
    _, policy, _, _ = probe_fixture()
    other = MetaProbePolicy(
        native_account_id="another-ledger", model_id="another-model", api_key=policy.api_key
    )
    rotated = MetaProbePolicy(
        native_account_id=policy.native_account_id,
        model_id=policy.model_id,
        api_key=SecretStr("rotated-fixture-key"),
    )
    assert policy.credential_binding_sha256 == other.credential_binding_sha256
    assert policy.credential_binding_sha256 != rotated.credential_binding_sha256
    assert probe_policy_sha256(policy) != probe_policy_sha256(rotated)
    assert not MetaProbePolicy().configured and MetaProbePolicy().paid_probe_enabled is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("request_sha256", "0" * 64),
        ("reservation_microdollars", 1),
        ("clear_kill_switch", False),
        ("clear_kill_switch", 1),
        ("native_control_epoch", -1),
        ("request_timeout_seconds", 61),
    ],
)
def test_manifest_cannot_forge_arm_request_reservation_or_limits(field, value):
    _, _, _, manifest = probe_fixture()
    with pytest.raises(ValidationError):
        MetaProbeManifest.model_validate(manifest.model_dump(mode="json") | {field: value})


@pytest.mark.parametrize(
    "field,value",
    [("proposal_sha256", "0" * 64), ("account_id", "other-ledger"), ("request_timeout_seconds", 1)],
)
def test_saved_manifest_derived_fields_must_match_the_exact_saved_readiness_proposal(field, value):
    proposal, _, _, manifest = probe_fixture()
    bad = manifest.model_copy(update={field: value})
    with pytest.raises(ValueError, match="exact readiness"):
        validate_probe_manifest(bad, proposal)


def test_human_attestation_has_no_credential_upload_or_fingerprint_input():
    with pytest.raises(ValidationError, match="Extra inputs"):
        ProviderAccountAttestation.model_validate(
            attestation_data() | {"credential_binding_sha256": "a" * 64}
        )


def test_credit_provider_cap_and_reviewed_deadline_are_bound_without_native_confirmation():
    proposal, policy, attestation, manifest = probe_fixture()
    for changes in (
        {"available_credit_microdollars": 1},
        {"remaining_provider_allowance_microdollars": 1},
    ):
        insufficient = attestation.model_copy(update=changes)
        with pytest.raises(ValueError):
            build_probe_manifest(
                READINESS_ID,
                proposal,
                policy=policy,
                native_control_epoch=0,
                provider_account_attestation=insufficient,
                valid_until=NOW + timedelta(minutes=5),
            )
    too_small = attestation.model_copy(
        update={
            "provider_hard_cap_microdollars": 25000000,
            "remaining_provider_allowance_microdollars": 25000000,
        }
    )
    with pytest.raises(ValueError, match="exact readiness"):
        build_probe_manifest(
            READINESS_ID,
            proposal,
            policy=policy,
            native_control_epoch=0,
            provider_account_attestation=too_small,
            valid_until=NOW + timedelta(minutes=5),
        )
    assert "native_status" not in manifest.model_dump()


def test_passed_assessment_exposes_only_validated_action_hash_summary_and_reported_cost():
    proposal, _, _, manifest = probe_fixture()
    observation = observation_for(manifest)
    result = assess_probe_observation(manifest, proposal, observation)
    assert result.status == "passed" and result.actual_microdollars == 22
    assert (result.actual_input_microdollars, result.actual_output_microdollars) == (12, 10)
    assert (
        result.action.outcome == "insufficient_evidence" and result.action.reason_characters == 20
    )
    assert result.action.reason_sha256 == hashlib.sha256(b"Protocol check only.").hexdigest()
    assert "Protocol check only." not in result.model_dump_json()
    assert result.research_execution_available is False and result.freeze_required is False
    assert result.observation_sha256 == observation_sha256(observation)


def test_prejournal_usage_assessment_never_imports_or_parses_generated_action(monkeypatch):
    import builtins

    proposal, _, _, manifest = probe_fixture()
    observation = observation_for(manifest, content="invalid generated JSON")
    original = builtins.__import__

    def no_action_import(name, *args, **kwargs):
        if name == "polymath.research.models":
            pytest.fail("Accounting tried to import generated-action semantics before journaling")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_action_import)
    result = assess_probe_usage(manifest, proposal, observation)
    assert result.status == "failed" and result.reason_code == "usage_counted_action_not_checked"
    assert result.actual_microdollars == 22 and result.action is None


def test_manifest_epoch_bounds_keep_armed_generation_exact_in_browser_json():
    _, _, _, manifest = probe_fixture()
    payload = manifest.model_dump(mode="json") | {"native_control_epoch": 2**53 - 2}
    assert MetaProbeManifest.model_validate(payload).native_control_epoch + 1 == 2**53 - 1
    with pytest.raises(ValidationError):
        MetaProbeManifest.model_validate(payload | {"native_control_epoch": 2**53 - 1})


@pytest.mark.parametrize(
    "content",
    [
        "not JSON",
        "```json\n{}\n```",
        json.dumps({"kind": "search_literature", "query": "papers", "purpose": "discovery"}),
        json.dumps(
            {"kind": "conclude", "conclusion": {"outcome": "insufficient_evidence", "reason": ""}}
        ),
        json.dumps(
            {
                "kind": "conclude",
                "conclusion": {"outcome": "insufficient_evidence", "reason": "unsafe\u0000"},
            }
        ),
    ],
)
def test_invalid_action_never_discards_known_usage_or_triggers_repairs(content):
    proposal, _, _, manifest = probe_fixture()
    result = assess_probe_observation(
        manifest, proposal, observation_for(manifest, content=content)
    )
    assert result.status == "failed" and result.actual_microdollars == 22
    assert result.action is result.action_sha256 is None


@pytest.mark.parametrize("finish", [None, "length", "content_filter"])
def test_invalid_finish_is_counted_but_never_success(finish):
    proposal, _, _, manifest = probe_fixture()
    result = assess_probe_observation(
        manifest, proposal, observation_for(manifest, finish_reason=finish)
    )
    assert result.status == "failed" and result.actual_microdollars == 22


def test_zero_usage_is_known_only_when_both_strict_reported_counters_are_present():
    proposal, _, _, manifest = probe_fixture()
    known = assess_probe_observation(
        manifest, proposal, observation_for(manifest, input_tokens=0, output_tokens=0)
    )
    missing = assess_probe_observation(
        manifest, proposal, observation_for(manifest, input_tokens=None, output_tokens=None)
    )
    assert known.status == "passed" and known.actual_microdollars == 0
    assert missing.status == "unknown" and missing.actual_microdollars is None


@pytest.mark.parametrize("model", [None, "fixture-attested-model-alias"])
def test_missing_or_mismatched_observed_model_keeps_unknown_cost_even_with_counts(model):
    proposal, _, _, manifest = probe_fixture()
    result = assess_probe_observation(
        manifest, proposal, observation_for(manifest, observed_model_id=model)
    )
    assert result.status == "unknown" and result.actual_microdollars is None
    assert result.input_tokens == 12


def test_representable_overbound_counters_are_fully_counted_and_freeze_instead_of_clamped():
    proposal, _, _, manifest = probe_fixture()
    result = assess_probe_observation(
        manifest, proposal, observation_for(manifest, input_tokens=400000, output_tokens=5000)
    )
    assert result.status == "failed" and result.actual_microdollars == 410000
    assert result.actual_microdollars > manifest.reservation_microdollars
    assert result.usage_within_bounds is False and result.freeze_required


def test_unrepresentable_known_cost_keeps_full_unknown_hold_and_retains_reported_counters():
    proposal, _, _, manifest = probe_fixture()
    result = assess_probe_observation(
        manifest, proposal, observation_for(manifest, input_tokens=2**63 - 1)
    )
    assert result.status == "unknown" and result.actual_microdollars is None
    assert result.input_tokens == 2**63 - 1 and result.freeze_required


def test_billable_completion_is_compared_to_attested_bound_not_requested_visible_cap():
    data = proposal_data()
    data["application_limits"]["max_output_tokens"] = 16
    proposal = MetaReadinessProposal.model_validate(data)
    _, policy, attestation, _ = probe_fixture()
    manifest = build_probe_manifest(
        READINESS_ID,
        proposal,
        policy=policy,
        native_control_epoch=0,
        provider_account_attestation=attestation,
        valid_until=NOW + timedelta(minutes=5),
    )
    result = assess_probe_observation(
        manifest, proposal, observation_for(manifest, output_tokens=1000)
    )
    assert manifest.request.payload.max_tokens == 16
    assert result.status == "passed" and result.actual_output_microdollars == 2000


@pytest.mark.asyncio
async def test_observe_sends_exact_reviewed_payload_and_parses_usage_before_action_semantics():
    proposal, policy, _, manifest = probe_fixture()
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == manifest.request.endpoint and request.method == "POST"
        assert json.loads(request.content) == manifest.request.payload.model_dump(mode="json")
        assert request.headers["authorization"] == "Bearer " + SECRET
        return httpx.Response(
            200,
            json=response_body(
                manifest, choices=[{"finish_reason": "length", "message": {"content": "not JSON"}}]
            ),
        )

    observed = await adapter(policy, handler).observe(manifest.request)
    assert observed.input_tokens == 12 and observed.output_tokens == 5
    assert observed.finish_reason == "length" and observed.content == "not JSON"
    assert observed.observed_model_id == policy.model_id
    assert assess_probe_observation(manifest, proposal, observed).actual_microdollars == 22
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": 12},
        {"prompt_tokens": True, "completion_tokens": 5},
        {"prompt_tokens": 12, "completion_tokens": 5.0},
        {"prompt_tokens": -1, "completion_tokens": 5},
        {"prompt_tokens": 2**63, "completion_tokens": 5},
    ],
)
async def test_observe_never_invents_or_partially_accepts_malformed_usage(usage):
    proposal, policy, _, manifest = probe_fixture()
    observed = await adapter(
        policy, lambda _: httpx.Response(200, json=response_body(manifest, usage=usage))
    ).observe(manifest.request)
    assert observed.input_tokens is observed.output_tokens is None
    assert assess_probe_observation(manifest, proposal, observed).status == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
async def test_http_failures_never_follow_redirects_or_persist_raw_error_bodies(status):
    _, policy, _, manifest = probe_fixture()
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, text=SECRET, headers={"location": "https://evil.test/steal"})

    observed = await adapter(policy, handler).observe(manifest.request)
    assert observed.http_status == status and observed.content is None
    assert SECRET not in observed.model_dump_json()
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_known_model_and_usage_in_http_failure_are_counted_without_protocol_success():
    proposal, policy, _, manifest = probe_fixture()
    observed = await adapter(
        policy, lambda _: httpx.Response(500, json=response_body(manifest))
    ).observe(manifest.request)
    result = assess_probe_observation(manifest, proposal, observed)
    assert result.status == "failed" and result.actual_microdollars == 22
    assert observed.content is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body", [b"\xff\xfe", b"{", b'{"model":"a","model":"b"}', b'{"usage":{"prompt_tokens":NaN}}']
)
async def test_invalid_utf8_duplicate_keys_and_nonfinite_json_leave_unknown_observation(body):
    _, policy, _, manifest = probe_fixture()
    observed = await adapter(policy, lambda _: httpx.Response(200, content=body)).observe(
        manifest.request
    )
    assert observed.body_complete and observed.input_tokens is None
    assert observed.error_code == "model_response_invalid_json"
    assert observed.body_sha256 == hashlib.sha256(body).hexdigest()


@pytest.mark.asyncio
async def test_deep_bounded_json_is_a_sanitized_observation_instead_of_a_lost_response():
    _, policy, _, manifest = probe_fixture()
    body = b'{"nested":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}"
    observed = await adapter(policy, lambda _: httpx.Response(200, content=body)).observe(
        manifest.request
    )
    assert observed.body_complete and observed.body_sha256 == hashlib.sha256(body).hexdigest()
    assert observed.error_code == "model_response_invalid_json"
    assert observed.input_tokens is observed.output_tokens is None


@pytest.mark.asyncio
async def test_observe_rejects_wrong_model_or_tampered_fixed_prompt_before_any_send():
    _, policy, _, manifest = probe_fixture()
    requests = []
    wrong_model = manifest.request.model_copy(
        update={"payload": manifest.request.payload.model_copy(update={"model": "different-model"})}
    )
    with pytest.raises(ModelError, match="binding_mismatch"):
        await adapter(policy, lambda request: requests.append(request)).observe(wrong_model)
    tampered = manifest.request.model_copy(
        update={
            "payload": manifest.request.payload.model_copy(
                update={
                    "messages": (
                        PROBE_MESSAGES_V1[0],
                        PROBE_MESSAGES_V1[1].model_copy(
                            update={"content": "Read private science instead"}
                        ),
                    )
                }
            )
        }
    )
    with pytest.raises(ModelError, match="request_invalid"):
        await adapter(policy, lambda request: requests.append(request)).observe(tampered)
    assert requests == []


@pytest.mark.asyncio
async def test_oversized_content_keeps_strict_usage_and_hash_but_not_provider_text():
    proposal, policy, _, manifest = probe_fixture()
    body = response_body(
        manifest, choices=[{"finish_reason": "stop", "message": {"content": "x" * 16001}}]
    )
    observed = await adapter(policy, lambda _: httpx.Response(200, json=body)).observe(
        manifest.request
    )
    assert (
        observed.content is None
        and observed.content_sha256 == hashlib.sha256(("x" * 16001).encode()).hexdigest()
    )
    result = assess_probe_observation(manifest, proposal, observed)
    assert result.status == "failed" and result.actual_microdollars == 22


@pytest.mark.asyncio
async def test_credential_echo_is_never_written_to_private_observation_or_public_assessment():
    _, policy, _, manifest = probe_fixture()
    body = response_body(
        manifest, model=SECRET, choices=[{"finish_reason": SECRET, "message": {"content": SECRET}}]
    )
    observed = await adapter(policy, lambda _: httpx.Response(200, json=body)).observe(
        manifest.request
    )
    assert SECRET not in observed.model_dump_json()
    assert observed.observed_model_id is observed.finish_reason is observed.content is None


@pytest.mark.asyncio
async def test_timeout_and_transport_errors_are_observations_not_zero_cost_or_retries():
    _, policy, _, manifest = probe_fixture()
    requests = []

    def failure(request):
        requests.append(request)
        raise httpx.ReadTimeout(SECRET)

    observed = await adapter(policy, failure).observe(manifest.request)
    assert observed.error_code == "model_timeout_outcome_unknown" and not observed.body_complete
    assert observed.input_tokens is observed.observed_model_id is None and len(requests) == 1
    assert SECRET not in observed.model_dump_json()


class OversizedProbeStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"x" * 64000
        yield b"x" * 64001


@pytest.mark.asyncio
@pytest.mark.parametrize("declared", [True, False])
async def test_observer_bounds_declared_and_streamed_bytes_without_fabricating_usage(declared):
    _, policy, _, manifest = probe_fixture()
    headers = {"content-length": "128001"} if declared else {}
    observed = await adapter(
        policy, lambda _: httpx.Response(200, stream=OversizedProbeStream(), headers=headers)
    ).observe(manifest.request)
    assert observed.error_code == "model_response_too_large"
    assert not observed.body_complete and observed.content is None
    assert observed.input_tokens is observed.output_tokens is None


@pytest.mark.asyncio
async def test_wall_deadline_cancellation_and_default_disabled_state_are_honest():
    _, policy, _, manifest = probe_fixture()

    async def slow(_request):
        await asyncio.sleep(1)

    observed = await adapter(policy, slow, timeout=0.001).observe(manifest.request)
    assert observed.error_code == "model_timeout_outcome_unknown"
    requests = []
    with pytest.raises(ModelError, match="live_model_disabled"):
        await adapter(policy, lambda request: requests.append(request), enabled=False).observe(
            manifest.request
        )
    assert requests == []

    async def cancelled(_request):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await adapter(policy, cancelled).observe(manifest.request)
