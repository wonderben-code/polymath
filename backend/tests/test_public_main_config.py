"""Public application assembly never turns configuration discovery into work."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from test_public_research_contracts import public_fixture

from polymath.config import Settings
from polymath.main import _reviewed_public_resources, create_app
from polymath.public_research_contracts import public_research_manifest_sha256
from polymath.research.meta import MetaModel


@pytest.mark.parametrize("public_enabled", [False, True])
def test_all_configuration_signals_remain_database_and_model_free(monkeypatch, public_enabled):
    key = "Synthetic-model-credential-not-a-real-key"
    settings = Settings(
        paid_meta_research_enabled=True,
        budget_readiness_enabled=True,
        budget_admin_actor_ids=("publisher",),
        operator_bootstrap_token="Synthetic_Operator_0123456789abcdef-ABCDEF",
        native_budget_account_id="synthetic-private-ledger",
        model_api_key=key,
        meta_model="synthetic-explicit-model",
        public_meta_research_enabled=public_enabled,
        public_resource_limits_enabled=public_enabled,
        _env_file=None,
    )

    def forbidden(*_, **__):
        pytest.fail("Configuration discovery cannot connect or construct Meta")

    monkeypatch.setattr(MetaModel, "__init__", forbidden)
    application = create_app(settings)
    monkeypatch.setattr(application.state.engine, "connect", forbidden)
    assert application.state.public_policy.enabled is public_enabled
    assert application.state.public_resource_controls.policy.enabled is public_enabled
    assert application.state.native_research_store.public_policy is application.state.public_policy
    assert (
        application.state.native_research_store.resource_controls
        is application.state.public_resource_controls
    )
    with TestClient(application) as client:
        for path in (
            "/api/v1/health",
            "/api/v1/capabilities",
            "/api/v1/budget/research/capabilities",
            "/api/v1/budget/research/public/capabilities",
        ):
            response = client.get(path)
            assert response.status_code == 200
            assert key not in response.text and "synthetic-private-ledger" not in response.text
        assert not client.cookies


def test_resource_projection_uses_historical_hash_without_current_scientific_derivation(
    monkeypatch,
):
    value = public_fixture()
    row = SimpleNamespace(
        manifest=value.manifest.model_dump(mode="json"),
        manifest_sha256=public_research_manifest_sha256(value.manifest),
    )
    session = SimpleNamespace(scalar=lambda _: row)

    def forbidden(*_, **__):
        pytest.fail("Historical resource extraction cannot derive current research profiles")

    monkeypatch.setattr("polymath.meta_research_contracts.derive_task_profiles", forbidden)
    monkeypatch.setattr(
        "polymath.meta_research_contracts.derive_discovery_ladder_profile", forbidden
    )
    assert (
        _reviewed_public_resources(session, value.manifest.account_id)
        == value.public_limits.resources
    )
    row.manifest_sha256 = "0" * 64
    assert _reviewed_public_resources(session, value.manifest.account_id) is None
    assert _reviewed_public_resources(session, None) is None
