"""Config discovery never constructs a paid client or consults the ledger."""

import pytest
from fastapi.testclient import TestClient

from polymath.config import Settings
from polymath.main import create_app
from polymath.research.meta import MetaModel


@pytest.mark.parametrize("enabled", [False, True])
def test_probe_configuration_is_separate_from_research_and_network(monkeypatch, enabled):
    key = "synthetic-key-must-never-be-exposed"
    ledger_id = "synthetic-native-ledger"
    settings = Settings(
        paid_meta_probe_enabled=enabled,
        budget_readiness_enabled=True,
        budget_admin_actor_ids=("publisher",),
        operator_bootstrap_token="Synthetic_Operator_0123456789abcdef-ABCDEF",
        native_budget_account_id=ledger_id,
        model_api_key=key,
        meta_model="synthetic-meta-model",
        _env_file=None,
    )

    def forbidden(*_args, **_kwargs):
        pytest.fail("Capability discovery must not connect to a DB or construct a model")

    monkeypatch.setattr(MetaModel, "__init__", forbidden)
    application = create_app(settings)
    monkeypatch.setattr(application.state.engine, "connect", forbidden)
    with TestClient(application) as client:
        response = client.get("/api/v1/budget/verification/capabilities")
        assert response.status_code == 200
        assert response.json() == {
            "verification_available": enabled,
            "inspection_available": True,
            "cancel_available": True,
            "finalization_available": True,
            "research_execution_available": False,
            "reason": None if enabled else "meta_probe_unavailable",
        }
        assert key not in response.text and ledger_id not in response.text
        assert "credential" not in response.text and "actor" not in response.text
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert client.get("/api/v1/capabilities").json()["research_available"] is False
        assert client.get("/api/v1/budget/capabilities").json()["execution_available"] is False
