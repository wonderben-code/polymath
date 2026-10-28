"""Independent preparation boundaries with synthetic attestations and actual PostgreSQL.

No provider request, model construction, or paid execution is used. Prior ledger
facts are created through the existing internal synthetic test integration.
"""

from __future__ import annotations

import copy
import socket

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_budget_admin_api import (
    ADMIN,
    PREFIX,
    PREPARATION,
    proposal_data,
    saved,
)
from test_budget_admin_api import (
    budget_admin as budget_admin_fixture,
)
from test_operator_auth import TOKEN, login
from test_run_api import bootstrap
from test_worker import database as database_fixture

from polymath.budget import BudgetError, BudgetService
from polymath.budget_models import BudgetAccount, BudgetReservation, BudgetRunLimit
from polymath.config import Settings
from polymath.main import create_app
from polymath.models import ResearchEvent, ResearchExternalRequest, ResearchRun
from polymath.readiness_models import MetaReadinessAudit, MetaReadinessProposalRecord
from polymath.research.meta import MetaModel

database = database_fixture
budget_admin = budget_admin_fixture
pytestmark = pytest.mark.integration


@pytest.fixture
def no_native_work(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Readiness attempted model construction or external network activity")

    monkeypatch.setattr(MetaModel, "__init__", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


def counts(engine):
    with Session(engine) as session:
        return {
            model.__tablename__: session.scalar(select(func.count()).select_from(model))
            for model in (
                ResearchRun,
                ResearchEvent,
                ResearchExternalRequest,
                BudgetRunLimit,
                BudgetReservation,
            )
        }


def count(engine, model):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def assert_not_run(value):
    assert value["native_status"] == "not_run"
    assert value["execution_available"] is False


def test_full_operator_readiness_flow_never_constructs_model_or_creates_execution(
    budget_admin, no_native_work
):
    engine, factory = budget_admin
    client = factory(PREPARATION)
    before = counts(engine)
    headers, quote, record = saved(client)
    assert_not_run(quote)
    assert_not_run(record)
    assert_not_run(record["proposal"])
    assert_not_run(record["quote"])
    assert client.get(PREFIX + "/readiness").json()["proposals"] == [record]
    assert client.get(PREFIX + "/readiness/" + record["id"]).json() == record
    assert (
        client.get(PREFIX + "/accounts").json()["accounts"][0]["account_id"] == record["account_id"]
    )
    inspected = client.get(PREFIX + "/accounts/" + record["account_id"])
    assert inspected.status_code == 200
    account = inspected.json()
    assert_not_run(account)
    assert account["execution_enabled"] is account["pricing_verified"] is False
    assert account["kill_switch"] is True and account["frozen"] is False
    assert all(
        account["usage"][name] == "0"
        for name in (
            "committed_microdollars",
            "daily_committed_microdollars",
            "counted_spend_microdollars",
            "open_hold_microdollars",
            "unknown_hold_microdollars",
        )
    )
    for _ in range(2):
        disabled = client.post(
            PREFIX + "/accounts/" + record["account_id"] + "/disable",
            headers=headers,
            json={},
        )
        assert disabled.status_code == 200, disabled.text
        assert_not_run(disabled.json())
        assert disabled.json()["execution_enabled"] is False
        assert disabled.json()["kill_switch"] is True
    retry = client.post(
        PREFIX + "/readiness",
        headers=headers,
        json={
            "proposal": record["proposal"],
            "expected_proposal_sha256": record["proposal_sha256"],
        },
    )
    assert retry.status_code == 201 and retry.json() == record
    assert counts(engine) == before
    assert count(engine, MetaReadinessProposalRecord) == 1
    assert count(engine, MetaReadinessAudit) == 3


@pytest.mark.parametrize(
    "field,value", [("native_status", "verified"), ("execution_available", True)]
)
def test_even_live_budget_operator_cannot_forge_native_or_execution_authority(
    budget_admin, no_native_work, field, value
):
    engine, factory = budget_admin
    client = factory(PREPARATION)
    headers, _ = login(client)
    data = proposal_data()
    data[field] = value
    before = counts(engine)
    for path, body in (
        ("/readiness/preview", {"proposal": data}),
        ("/readiness", {"proposal": data, "expected_proposal_sha256": "0" * 64}),
    ):
        response = client.post(PREFIX + path, headers=headers, json=body)
        assert response.status_code == 422
        assert response.json() == {"detail": "readiness_invalid_request"}
    assert count(engine, BudgetAccount) == count(engine, MetaReadinessProposalRecord) == 0
    assert count(engine, MetaReadinessAudit) == 0
    assert counts(engine) == before


def test_preparation_and_disable_preserve_unknown_holds_and_allow_known_late_accounting(
    budget_admin, no_native_work
):
    engine, factory = budget_admin
    client = factory(PREPARATION)
    data = proposal_data()
    data["account"]["max_in_flight"] = 3
    caps = data["account"]
    budget = BudgetService(engine)
    budget.create_account(
        caps["account_id"],
        hard_cap_microdollars=caps["hard_cap_microdollars"],
        daily_cap_microdollars=caps["daily_cap_microdollars"],
        max_in_flight=3,
    )
    budget.authorize_execution(caps["account_id"], pricing_reference="synthetic-prior-pricing")
    with Session(engine) as session, session.begin():
        run = ResearchRun(mode="ask", goal="Synthetic prior accounting only")
        session.add(run)
        session.flush()
        run_id = run.id
    budget.configure_run(caps["account_id"], run_id, cap_microdollars=5000000, max_calls=3)
    reservations = [
        budget.reserve(
            caps["account_id"],
            run_id,
            call_key=f"synthetic-{index}",
            estimated_microdollars=estimate,
        )
        for index, estimate in enumerate((100, 200, 300))
    ]
    for reservation in reservations:
        assert budget.claim_dispatch(reservation.id)
    budget.settle(
        reservations[0].id,
        actual_microdollars=37,
        settlement_key="synthetic-prior-response",
        prompt_tokens=10,
        completion_tokens=5,
    )
    budget.mark_unknown(reservations[1].id, reason="timeout")
    budget.disable_execution(caps["account_id"])
    with Session(engine) as session, session.begin():
        account = session.get(BudgetAccount, caps["account_id"])
        account.frozen = True
        account.frozen_reason = "reconciliation_conflict"
    before = counts(engine)
    headers, _, _ = saved(client, data)
    inspected = client.get(PREFIX + "/accounts/" + caps["account_id"]).json()
    assert inspected["pricing_verified"] is True
    assert inspected["kill_switch"] is False
    assert inspected["frozen"] is True
    assert inspected["frozen_reason"] == "reconciliation_conflict"
    usage = copy.deepcopy(inspected["usage"])
    assert usage == {
        "committed_microdollars": "537",
        "daily_committed_microdollars": "537",
        "counted_spend_microdollars": "37",
        "open_hold_microdollars": "500",
        "unknown_hold_microdollars": "200",
        "in_flight": 2,
        "reservations": 3,
    }
    disabled = client.post(
        PREFIX + "/accounts/" + caps["account_id"] + "/disable", headers=headers, json={}
    )
    assert disabled.status_code == 200, disabled.text
    after = disabled.json()
    assert after["usage"] == usage
    assert after["kill_switch"] is True and after["execution_enabled"] is False
    assert after["pricing_verified"] is True and after["frozen"] is True
    assert after["pricing_reference"] == "synthetic-prior-pricing"
    assert after["frozen_reason"] == "reconciliation_conflict"
    assert counts(engine) == before
    settled = budget.settle(
        reservations[2].id,
        actual_microdollars=91,
        settlement_key="synthetic-known-late-response",
        prompt_tokens=20,
        completion_tokens=7,
    )
    assert settled.status == "settled"
    final = client.get(PREFIX + "/accounts/" + caps["account_id"]).json()
    assert final["usage"] == {
        "committed_microdollars": "328",
        "daily_committed_microdollars": "328",
        "counted_spend_microdollars": "128",
        "open_hold_microdollars": "200",
        "unknown_hold_microdollars": "200",
        "in_flight": 1,
        "reservations": 3,
    }
    assert final["kill_switch"] is True and final["execution_enabled"] is False
    assert_not_run(final)
    with pytest.raises(BudgetError, match="operator_kill_switch"):
        budget.reserve(
            caps["account_id"], run_id, call_key="fresh-after-disable", estimated_microdollars=1
        )
    with Session(engine) as session:
        unknown = session.get(BudgetReservation, reservations[1].id)
        assert unknown.status == "unknown" and unknown.actual_microdollars is None
        assert unknown.estimated_microdollars == 200 and unknown.unknown_reason == "timeout"
    assert counts(engine) == before


@pytest.mark.parametrize("preparation", [False, True])
def test_production_app_with_model_key_and_readiness_settings_never_enables_research(
    database, no_native_work, preparation
):
    settings = Settings(
        _env_file=None,
        database_url=database.url.render_as_string(hide_password=False),
        model_api_key="synthetic-unused-key",
        meta_model="synthetic-unused-model",
        operator_bootstrap_token=TOKEN,
        operator_actor_id=ADMIN.actor_id,
        budget_admin_actor_ids=(ADMIN.actor_id,),
        budget_readiness_enabled=preparation,
    )
    with TestClient(create_app(settings)) as client:
        capabilities = client.get(PREFIX + "/capabilities").json()
        assert capabilities["preparation_available"] is preparation
        assert_not_run(capabilities)
        assert client.get("/api/v1/capabilities").json()["research_available"] is False
        assert client.get("/api/v1/assessments/capabilities").json()["execution_available"] is False
        assert (
            client.get("/api/v1/publication-jobs/capabilities").json()["execution_available"]
            is False
        )
        response = client.post(
            "/api/v1/runs",
            headers=bootstrap(client),
            json={"mode": "ask", "goal": "A request cannot activate paid execution"},
        )
        assert response.status_code == 503
        assert response.json() == {"detail": "research_unavailable"}
        for path in ("/activate", "/probe", "/accounts/meta/activate"):
            assert client.post(PREFIX + path, json={}).status_code in (404, 405)
    assert all(value == 0 for value in counts(database).values())
    assert count(database, MetaReadinessProposalRecord) == count(database, BudgetAccount) == 0
