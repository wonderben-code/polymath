"""Config discovery is explicit; neither capabilities nor keys grant a paid call."""

import pytest
from fastapi.testclient import TestClient

from polymath.config import Settings
from polymath.main import create_app
from polymath.research.meta import MetaModel


@pytest.mark.parametrize("enabled", [False, True])
def test_native_research_discovery_never_uses_database_or_constructs_model(monkeypatch, enabled):
    key = "synthetic-secret-not-a-native-credential"
    settings = Settings(
        paid_meta_research_enabled=enabled,
        budget_readiness_enabled=True,
        budget_admin_actor_ids=("publisher",),
        operator_bootstrap_token="Synthetic_Operator_0123456789abcdef-ABCDEF",
        native_budget_account_id="one-configured-ledger",
        model_api_key=key,
        meta_model="synthetic-explicit-meta-model",
        _env_file=None,
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("Discovering configuration cannot connect, construct, or send")

    monkeypatch.setattr(MetaModel, "__init__", forbidden)
    application = create_app(settings)
    monkeypatch.setattr(application.state.engine, "connect", forbidden)
    assert application.state.native_research_store is not None
    with TestClient(application) as client:
        response = client.get("/api/v1/capabilities")
        assert response.status_code == 200
        assert response.json()["research_available"] is enabled
        assert response.json()["native_research_required"] is True
        caps = client.get("/api/v1/budget/research/capabilities")
        assert caps.status_code == 200
        assert caps.json()["activation_available"] is enabled
        assert caps.json()["public_research_available"] is False
        assert key not in response.text + caps.text
        assert "one-configured-ledger" not in response.text + caps.text
        assert not settings.paid_meta_probe_enabled


def test_explicit_trusted_legacy_fixture_path_cannot_claim_native_configuration():
    application = create_app(Settings(_env_file=None), research_availability=lambda: True)
    assert application.state.native_research_store is None
    with TestClient(application) as client:
        assert client.get("/api/v1/capabilities").json()["native_research_required"] is False
        assert (
            client.get("/api/v1/budget/research/capabilities").json()["activation_available"]
            is False
        )
