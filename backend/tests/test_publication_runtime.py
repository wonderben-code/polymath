"""Server composition never activates external work from credentials alone."""

from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

import polymath.publication_runtime as runtime
from polymath.config import Settings
from polymath.database import build_engine


def configured(**changes):
    return Settings(
        **{
            "operator_bootstrap_token": "Synthetic_Operator_0123456789abcdef-ABCDEF",
            "publication_approval_enabled": True,
            "publication_publisher_id": "synthetic-publisher",
            "publication_publisher_name": "Synthetic Publisher",
            "publication_destinations": ("sandbox",),
            **changes,
        },
        _env_file=None,
    )


@pytest.mark.asyncio
async def test_default_worker_does_not_connect_or_construct_a_client(monkeypatch):
    settings = Settings(_env_file=None)
    engine = build_engine(settings)

    def forbidden(*args, **kwargs):
        pytest.fail("Disabled publication runtime attempted external work")

    monkeypatch.setattr(engine, "connect", forbidden)
    monkeypatch.setattr(runtime, "ZenodoClient", forbidden)
    try:
        worker = runtime.build_publication_worker(engine, settings)
        assert (await worker.run_once("disabled-runtime-fixture")).status == "disabled"
        with pytest.raises(ValueError, match="disabled"):
            worker.client_factory(SimpleNamespace(destination="sandbox"))
    finally:
        engine.dispose()


@pytest.mark.parametrize("approval", [False, True])
def test_api_tokens_do_not_enable_publication_or_meta_execution(approval):
    settings = configured(
        publication_approval_enabled=approval,
        zenodo_sandbox_token="synthetic-sandbox-token",
        zenodo_production_token="synthetic-production-token",
    )
    assert not runtime.publication_execution_available(settings)
    assert not settings.meta_configured
    assert "synthetic-sandbox-token" not in repr(settings)
    assert "synthetic-production-token" not in repr(settings)


@pytest.mark.parametrize(
    "changes",
    [
        {"publication_approval_enabled": False},
        {},
        {"zenodo_production_token": "synthetic-production-only"},
        {
            "publication_destinations": ("sandbox", "production"),
            "zenodo_sandbox_token": "synthetic-sandbox-only",
        },
    ],
)
def test_live_execution_requires_explicit_approval_policy_and_destination_credentials(changes):
    with pytest.raises(ValidationError):
        configured(publication_execution_enabled=True, **changes)


def test_explicit_factory_selects_only_the_approved_destination_credential(monkeypatch):
    settings = configured(
        publication_execution_enabled=True,
        publication_destinations=("sandbox", "production"),
        zenodo_sandbox_token=SecretStr("synthetic-sandbox-token"),
        zenodo_production_token=SecretStr("synthetic-production-token"),
    )
    constructions = []

    class ConstructOnlyClient:
        def __init__(self, destination, token, *, live_enabled):
            constructions.append((destination, token, live_enabled))

    monkeypatch.setattr(runtime, "ZenodoClient", ConstructOnlyClient)
    engine = build_engine(settings)
    try:
        worker = runtime.build_publication_worker(engine, settings)
        assert worker.execution_enabled
        assert constructions == []  # Construction never dispatches or consumes a job.
        worker.client_factory(SimpleNamespace(destination="sandbox"))
        worker.client_factory(SimpleNamespace(destination="production"))
        assert [row[0] for row in constructions] == ["sandbox", "production"]
        assert constructions[0][1] is settings.zenodo_sandbox_token
        assert constructions[1][1] is settings.zenodo_production_token
        assert all(row[2] is True for row in constructions)
        with pytest.raises(ValueError, match="disabled"):
            worker.client_factory(SimpleNamespace(destination="foreign-host"))
        assert len(constructions) == 2
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_runtime_command_refuses_disabled_work_without_even_building_an_engine(monkeypatch):
    monkeypatch.setattr(runtime, "build_engine", lambda *args: pytest.fail("Unexpected engine"))
    assert await runtime.run(Settings(_env_file=None), once=True, worker_id="fixture") == 2
