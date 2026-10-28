"""Public worker composition remains lazy, scoped and default off."""

from types import SimpleNamespace

import pytest
from pydantic import SecretStr

import polymath.native_research_worker as runtime
from polymath.meta_readiness_contracts import ReadinessPolicy
from polymath.meta_research_contracts import NativeResearchPolicy
from polymath.meta_research_errors import MetaResearchError
from polymath.operator_auth import OperatorPolicy
from polymath.public_research_contracts import PublicResearchPolicy
from polymath.public_research_quota import PublicQuotaError
from polymath.worker import ExecutionStopped


def policies():
    return (
        NativeResearchPolicy(
            paid_research_enabled=True,
            native_account_id="reviewed-ledger",
            model_id="reviewed-model",
            api_key=SecretStr("synthetic-key-never-sent"),
        ),
        ReadinessPolicy(preparation_enabled=True, allowed_actor_ids=("publisher",)),
        OperatorPolicy(
            bootstrap_token=SecretStr("synthetic-operator-credential-0123456789"),
        ),
    )


def compose(monkeypatch, **kwargs):
    calls = []

    def dependency(*args, **keywords):
        calls.append((args, keywords))
        return SimpleNamespace()

    monkeypatch.setattr(runtime, "MetaResearchStore", dependency)
    monkeypatch.setattr(runtime, "MetaResearchDispatcher", dependency)
    monkeypatch.setattr(runtime, "ResearchWorker", lambda *args, **keywords: keywords)
    research, readiness, operator = policies()
    engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    result = runtime.build_native_research_worker(
        engine,
        research_policy=research,
        readiness_policy=readiness,
        operator_policy=operator,
        **kwargs,
    )
    return result, calls, engine


def test_public_policy_and_account_reach_both_fresh_authority_paths(monkeypatch):
    policy = PublicResearchPolicy(enabled=True)
    worker, calls, _ = compose(monkeypatch, public_policy=policy)
    assert len(calls) == 2 and all(call[1]["public_policy"] is policy for call in calls)
    assert worker["native_only"] is True
    assert worker["native_claim_account_id"] == "reviewed-ledger"
    assert worker["policy"] is None
    assert worker["execution_enabled"] is True


def test_default_source_factory_pins_context_lease_and_real_pacing_bounds(monkeypatch):
    worker, _, engine = compose(monkeypatch)
    captured = {}
    lease, snapshot = object(), object()

    def pacing(received, **keywords):
        assert received is engine
        captured.update(keywords)
        return "provider-slot"

    monkeypatch.setattr(runtime, "DatabaseProviderPacer", pacing)
    monkeypatch.setattr(runtime, "OpenLiteratureTools", lambda **keywords: keywords)
    authorized = []
    monkeypatch.setattr(runtime, "_authorize_provider", lambda *args: authorized.append(args))
    tools = worker["tools_context_factory"](SimpleNamespace(lease=lease), snapshot)
    assert tools == {"provider_slot": "provider-slot"}
    assert captured["max_wait_seconds"] == 10
    session = object()
    captured["authorize"](session)
    assert authorized == [(session, lease)]


def test_injected_source_factory_is_not_wrapped_or_paced_twice(monkeypatch):
    def tools(snapshot):
        return snapshot

    worker, _, _ = compose(monkeypatch, tools_factory=tools)
    assert worker["tools_factory"] is tools
    assert worker["tools_context_factory"] is None


@pytest.mark.parametrize(
    "code,public,expected",
    [
        ("meta_research_authority_expired", True, "public_research_authority_expired"),
        ("meta_research_prior_unresolved", True, "public_research_prior_unresolved"),
        ("meta_research_unavailable", True, "public_research_disabled"),
        ("meta_research_authority_expired", False, "meta_research_authority_expired"),
        ("public_session_total_budget_exceeded", True, "public_session_total_budget_exceeded"),
        ("raw credential from an unexpected exception", True, "native_dispatch_not_safe"),
    ],
)
def test_only_static_control_codes_survive_and_private_authority_is_not_upgraded(
    code, public, expected
):
    error = runtime._dispatch_stop(PublicQuotaError(code), public=public)
    assert isinstance(error, ExecutionStopped) and str(error) == expected


def test_provider_ownership_database_failure_is_a_control_stop(monkeypatch):
    def lost(*args):
        raise ValueError("private database diagnostics")

    monkeypatch.setattr(runtime, "_owned", lost)
    with pytest.raises(ExecutionStopped, match="^native_source_authority_unavailable$"):
        runtime._authorize_provider(object(), object())


@pytest.mark.asyncio
@pytest.mark.parametrize("public_enabled", [False, True])
async def test_enabled_cli_composes_public_flag_without_changing_private_configuration(
    monkeypatch, public_enabled
):
    captured = []
    engine = SimpleNamespace(dispose=lambda: captured.append("disposed"))
    monkeypatch.setattr(runtime, "build_engine", lambda _: engine)
    monkeypatch.setattr(runtime, "configured_native_research_policies", lambda _: policies())

    async def once(worker_id):
        assert worker_id == "synthetic-runtime"
        return SimpleNamespace(status="idle")

    def worker(received, **kwargs):
        assert received is engine
        captured.append(kwargs)
        return SimpleNamespace(run_once=once)

    monkeypatch.setattr(runtime, "build_native_research_worker", worker)
    assert (
        await runtime.run(
            SimpleNamespace(
                paid_meta_research_enabled=True, public_meta_research_enabled=public_enabled
            ),
            once=True,
            worker_id="synthetic-runtime",
        )
        == 0
    )
    assert captured[0]["public_policy"].enabled is public_enabled
    assert captured[0]["research_policy"].paid_research_enabled is True
    assert captured[1] == "disposed"


def test_known_error_class_cannot_echo_an_unrecognized_code():
    assert str(runtime._dispatch_stop(MetaResearchError("secret-body"))) == (
        "native_dispatch_not_safe"
    )
