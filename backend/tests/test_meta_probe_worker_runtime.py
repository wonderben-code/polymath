"""Default-off command composition without database, native clients, or HTTP."""

import sys
import uuid
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

import polymath.meta_probe_worker as runtime
from polymath.config import Settings
from polymath.meta_probe_worker import MetaProbeWorker, configured_meta_probe_policies

BOOTSTRAP = "Synthetic_fixture_only_0123456789_ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def settings(**changes):
    return Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+psycopg://fixture:fixture@127.0.0.1:5432/fixture",
        **changes,
    )


def enabled_settings():
    return settings(
        paid_meta_probe_enabled=True,
        budget_readiness_enabled=True,
        budget_admin_actor_ids=("budget-operator",),
        operator_actor_id="budget-operator",
        operator_bootstrap_token=SecretStr(BOOTSTRAP),
        native_budget_account_id="configured-global-ledger",
        meta_model="synthetic-explicit-model",
        model_api_key=SecretStr("synthetic-no-live-key"),
    )


@pytest.mark.parametrize("key_present", [False, True])
@pytest.mark.asyncio
async def test_disabled_runtime_exits_before_database_policies_or_native_factory(
    monkeypatch, capsys, key_present
):
    def forbidden(*args, **kwargs):
        pytest.fail("Disabled native worker touched an execution dependency")

    monkeypatch.setattr(runtime, "build_engine", forbidden)
    monkeypatch.setattr(runtime, "configured_meta_probe_policies", forbidden)
    monkeypatch.setattr(runtime, "MetaModel", forbidden)
    value = settings(
        model_api_key=SecretStr("synthetic-key-alone") if key_present else None,
        meta_model="synthetic-model-alone" if key_present else None,
    )
    assert await runtime.run(value, once=True, worker_id="disabled") == 2
    assert "disabled" in capsys.readouterr().out


def test_policy_composition_captures_only_explicit_server_values_and_redacts_key():
    value = enabled_settings()
    probe, readiness, operator = configured_meta_probe_policies(value)
    assert probe.paid_probe_enabled and probe.configured
    assert probe.model_id == value.meta_model
    assert probe.native_account_id == value.native_budget_account_id
    assert probe.api_key is value.model_api_key
    assert readiness.allowed_actor_ids == ("budget-operator",)
    assert operator.actor_id == "budget-operator" and operator.configured
    assert value.model_api_key.get_secret_value() not in repr(probe)
    assert BOOTSTRAP not in repr(operator)


@pytest.mark.asyncio
async def test_explicit_finalize_only_uses_known_store_without_key_policy_or_model(
    monkeypatch, capsys
):
    run_id = uuid.uuid4()
    actions = []
    engine = SimpleNamespace(dispose=lambda: actions.append("dispose"))

    class Store:
        def __init__(self, received_engine):
            assert received_engine is engine
            actions.append("store-without-policies")

        def finalize_known(self, received_run):
            assert received_run == run_id
            actions.append("finalize")
            return SimpleNamespace(to_response=lambda: {"native_status": "passed"})

    def forbidden(*args, **kwargs):
        pytest.fail("Free finalization attempted fresh execution composition")

    monkeypatch.setattr(runtime, "build_engine", lambda _: engine)
    monkeypatch.setattr(runtime, "MetaProbeStore", Store)
    monkeypatch.setattr(runtime, "configured_meta_probe_policies", forbidden)
    monkeypatch.setattr(runtime, "MetaModel", forbidden)
    monkeypatch.setattr(runtime, "MetaProbeWorker", forbidden)
    assert await runtime.run(settings(), once=False, worker_id="free", finalize_only=run_id) == 0
    assert actions == ["store-without-policies", "finalize", "dispose"]
    assert "passed" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_finalize_unknown_stops_with_sanitized_error_and_disposes_engine(monkeypatch, capsys):
    disposed = []
    engine = SimpleNamespace(dispose=lambda: disposed.append(True))

    class Store:
        def __init__(self, _):
            pass

        def finalize_known(self, _):
            raise ValueError("synthetic-private-message-not-for-stdout")

    monkeypatch.setattr(runtime, "build_engine", lambda _: engine)
    monkeypatch.setattr(runtime, "MetaProbeStore", Store)
    assert (
        await runtime.run(settings(), once=False, worker_id="free", finalize_only=uuid.uuid4()) == 2
    )
    output = capsys.readouterr().out
    assert "unavailable" in output and "synthetic-private" not in output
    assert disposed == [True]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--model", "unapproved-model"],
        ["--api-key", "unapproved-key"],
        ["--endpoint", "https://example.org"],
        ["--account", "different-ledger"],
        ["--once", "--finalize-only", "00000000-0000-0000-0000-000000000001"],
        ["--finalize-only", "not-a-uuid"],
        ["--worker-id", "contains whitespace"],
    ],
)
def test_cli_rejects_overrides_and_invalid_modes_before_settings_or_database(
    monkeypatch, arguments
):
    monkeypatch.setattr(sys, "argv", ["meta-probe-worker", *arguments])
    monkeypatch.setattr(
        runtime, "Settings", lambda: pytest.fail("Invalid CLI loaded configuration")
    )
    with pytest.raises(SystemExit) as error:
        runtime.main()
    assert error.value.code == 2


def test_cli_help_never_loads_settings_or_execution_dependencies(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["meta-probe-worker", "--help"])
    monkeypatch.setattr(runtime, "Settings", lambda: pytest.fail("Help loaded configuration"))
    monkeypatch.setattr(runtime, "build_engine", lambda _: pytest.fail("Help opened database"))
    with pytest.raises(SystemExit) as error:
        runtime.main()
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "--finalize-only" in output and "--once" in output
    assert "--api-key" not in output and "--model" not in output


def test_cli_sanitizes_unexpected_configuration_or_database_failure(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["meta-probe-worker", "--once"])

    def broken_configuration():
        raise ValueError("synthetic-private-config-or-credential-message")

    monkeypatch.setattr(runtime, "Settings", broken_configuration)
    assert runtime.main() == 2
    output = capsys.readouterr().out
    assert "unavailable" in output and "synthetic-private" not in output


@pytest.mark.parametrize(
    "changes",
    [
        {"lease_seconds": True},
        {"lease_seconds": 0},
        {"heartbeat_seconds": True},
        {"heartbeat_seconds": float("nan")},
        {"heartbeat_seconds": 30},
    ],
)
def test_invalid_lease_configuration_is_rejected_before_repositories(changes):
    with pytest.raises(ValueError, match="invalid_probe_worker"):
        MetaProbeWorker(None, **changes)
