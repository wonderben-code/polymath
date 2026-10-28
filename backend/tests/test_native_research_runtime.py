"""Native composition limits and default-off/free recovery without network calls."""

import sys
import uuid
from dataclasses import asdict
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from test_meta_readiness_contracts import proposal_data

import polymath.native_research_worker as runtime
from polymath.artifact_controller import ArtifactLimits
from polymath.assessment_controller import AssessmentLimits
from polymath.config import Settings
from polymath.meta_readiness_contracts import MetaReadinessProposal
from polymath.native_research_worker import (
    NativeArtifactLimits,
    NativeAssessmentLimits,
    NativeResearchLimits,
    original_runtime_profile,
)
from polymath.research.controller import ResearchLimits


@pytest.mark.asyncio
async def test_disabled_native_runtime_exits_before_database_policy_or_model(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("Disabled research worker touched an execution dependency")

    monkeypatch.setattr(runtime, "build_engine", forbidden)
    monkeypatch.setattr(runtime, "configured_native_research_policies", forbidden)
    monkeypatch.setattr(runtime, "MetaModel", forbidden)
    assert (
        await runtime.run(
            SimpleNamespace(paid_meta_research_enabled=False), once=True, worker_id="disabled"
        )
        == 2
    )
    assert "disabled" in capsys.readouterr().out


@pytest.mark.parametrize(
    "native,parent,calls,actions",
    [
        (NativeResearchLimits, ResearchLimits, 20, 16),
        (NativeArtifactLimits, ArtifactLimits, 12, 12),
        (NativeAssessmentLimits, AssessmentLimits, 12, 12),
    ],
)
def test_native_local_deadline_allows_full_provider_timeout_without_changing_legacy_limits(
    native, parent, calls, actions
):
    before = asdict(parent())
    value = native(
        max_model_calls=calls,
        max_actions=actions,
        max_output_tokens=4096,
        max_context_characters=90000,
        model_timeout_seconds=90,
    )
    assert value.model_timeout_seconds == 90
    assert value.max_model_calls == calls and value.max_actions == actions
    with pytest.raises(ValueError):
        parent(model_timeout_seconds=90)
    assert asdict(parent()) == before


@pytest.mark.parametrize("timeout", [True, float("nan"), float("inf"), 30, 90.001])
def test_native_extension_still_rejects_unbounded_or_wrong_deadlines(timeout):
    with pytest.raises(ValueError, match="invalid_native_orchestration_deadline"):
        NativeResearchLimits(model_timeout_seconds=timeout)


def test_native_extension_retains_existing_action_output_and_context_bounds():
    for changes in (
        {"max_model_calls": 21},
        {"max_actions": 17},
        {"max_output_tokens": 4097},
        {"max_context_characters": 90001},
        {"tool_timeout_seconds": 61},
    ):
        with pytest.raises(ValueError):
            NativeResearchLimits(model_timeout_seconds=90, **changes)


@pytest.mark.parametrize(
    "kind,calls,actions,tool",
    [("research", 20, 16, 20), ("formalise", 12, 12, 5), ("reassess_finding", 12, 12, 20)],
)
def test_original_profile_reconstructs_exact_saved_prices_caps_and_derived_task_limits(
    kind, calls, actions, tool
):
    data = proposal_data()
    data["application_limits"]["max_model_calls"] = 20
    proposal = MetaReadinessProposal.model_validate(data)
    value = SimpleNamespace(
        account_id=proposal.account.account_id,
        proposal=proposal,
        profile=SimpleNamespace(
            task_kind=kind,
            max_model_calls=calls,
            max_actions=actions,
            max_repairs=2,
            max_output_tokens=4096,
            max_context_characters=90000,
            model_timeout_seconds=75,
            request_timeout_seconds=45,
            tool_timeout_seconds=tool,
            max_sources=200,
        ),
        activation_sha256="a" * 64,
        profile_sha256="b" * 64,
        input_sha256="c" * 64,
    )
    profile = original_runtime_profile(value)
    assert profile.policy.account_id == proposal.account.account_id
    assert profile.policy.model_identity == proposal.model_id
    assert profile.policy.reservation_microdollars == 308192
    assert profile.policy.input_microdollars_per_million == 1000000
    assert profile.policy.output_microdollars_per_million == 2000000
    assert profile.policy.run_cap_microdollars == 5000000
    limits = (
        profile.limits
        if kind == "research"
        else profile.artifact_limits
        if kind == "formalise"
        else profile.assessment_limits
    )
    assert limits.max_model_calls == calls and limits.max_actions == actions
    assert limits.max_output_tokens == 4096 and limits.model_timeout_seconds == 75
    assert limits.tool_timeout_seconds == tool
    assert profile.protocol_binding == {
        "activation_sha256": "a" * 64,
        "profile_sha256": "b" * 64,
        "input_sha256": "c" * 64,
    }


@pytest.mark.parametrize("billing_status,code", [("known", 0), ("unknown", 2)])
@pytest.mark.asyncio
async def test_free_known_call_uses_database_only_and_never_constructs_research_worker(
    monkeypatch, capsys, billing_status, code
):
    run_id, actions = uuid.uuid4(), []
    engine = SimpleNamespace(dispose=lambda: actions.append("dispose"))

    class Store:
        def __init__(self, received):
            assert received is engine

        def finalize_known_call(self, received, request_id):
            assert received == run_id and request_id == "model-0003"
            actions.append("known-call")
            return SimpleNamespace(to_response=lambda: {"usage": {"status": billing_status}})

    def forbidden(*args, **kwargs):
        pytest.fail("Free billing recovery constructed native execution")

    monkeypatch.setattr(runtime, "build_engine", lambda _: engine)
    monkeypatch.setattr(runtime, "MetaResearchStore", Store)
    monkeypatch.setattr(runtime, "MetaModel", forbidden)
    monkeypatch.setattr(runtime, "configured_native_research_policies", forbidden)
    monkeypatch.setattr(runtime, "build_native_research_worker", forbidden)
    assert (
        await runtime.run(
            SimpleNamespace(),
            once=False,
            worker_id="free",
            finalize_only=run_id,
            request_id="model-0003",
        )
        == code
    )
    assert actions == ["known-call", "dispose"]
    output = capsys.readouterr().out
    assert ("no scientific completion" if billing_status == "known" else "hold retained") in output


@pytest.mark.parametrize(
    "args",
    [
        ["--model", "fake"],
        ["--api-key", "fake"],
        ["--account", "fake"],
        ["--endpoint", "https://example.org"],
        ["--request-id", "model-0001"],
        [
            "--finalize-only",
            "00000000-0000-0000-0000-000000000001",
            "--request-id",
            "model-0000",
        ],
        ["--once", "--finalize-only", "00000000-0000-0000-0000-000000000001"],
    ],
)
def test_cli_rejects_authority_overrides_and_invalid_mode_before_configuration(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["native-research-worker", *args])
    monkeypatch.setattr(runtime, "Settings", lambda: pytest.fail("Invalid CLI loaded settings"))
    with pytest.raises(SystemExit) as error:
        runtime.main()
    assert error.value.code == 2


def test_actual_free_cli_configuration_does_not_load_stale_authority_environment(
    monkeypatch, tmp_path, capsys
):
    run_id, captured = uuid.uuid4(), []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PAID_META_RESEARCH_ENABLED", "true")
    monkeypatch.setenv("PAID_META_PROBE_ENABLED", "true")
    monkeypatch.setenv("BUDGET_READINESS_ENABLED", "true")
    monkeypatch.setenv("PUBLICATION_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("MODEL_API_KEY", "")
    monkeypatch.setenv("OPERATOR_BOOTSTRAP_TOKEN", "invalid removed operator credential")
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+psycopg://fixture:fixture@127.0.0.1:5432/fixture"
    )
    monkeypatch.setattr(sys, "argv", ["native-research-worker", "--finalize-only", str(run_id)])

    async def known_only(configuration, **arguments):
        captured.append(configuration)
        assert arguments["finalize_only"] == run_id
        assert arguments["request_id"] == "model-0001"
        assert not hasattr(configuration, "model_api_key")
        return 0

    monkeypatch.setattr(runtime, "run", known_only)
    assert runtime.main() == 0
    assert len(captured) == 1 and isinstance(captured[0].database_url, SecretStr)
    assert capsys.readouterr().out == ""


def test_new_settings_remain_default_off():
    value = Settings(_env_file=None, environment="test")
    assert value.paid_meta_research_enabled is False


def test_optional_native_ladder_uses_its_pinned_twenty_action_profile_only():
    from polymath.discovery_contracts import DiscoveryIntent
    from polymath.meta_research_contracts import derive_discovery_ladder_profile
    from polymath.research.discovery import DiscoveryLimits

    proposal = MetaReadinessProposal.model_validate(proposal_data())
    binding = derive_discovery_ladder_profile(proposal, DiscoveryIntent())
    value = SimpleNamespace(
        account_id=proposal.account.account_id,
        proposal=proposal,
        profile=binding.profile,
        activation_sha256="a" * 64,
        profile_sha256=binding.profile_sha256,
        input_sha256="c" * 64,
    )
    runtime_profile = original_runtime_profile(value)
    assert isinstance(runtime_profile.limits, DiscoveryLimits)
    assert runtime_profile.limits.max_actions == 20
    assert runtime_profile.limits.max_model_calls == proposal.application_limits.max_model_calls
    assert runtime_profile.limits.model_timeout_seconds == 75
    assert ResearchLimits().max_actions == 12
    with pytest.raises(ValueError):
        ResearchLimits(max_actions=20)
