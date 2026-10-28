"""An ordinary browser cannot inspect or control its operator-only probe job."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_run_api import api as api_fixture
from test_run_api import bootstrap

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.models import ResearchRun

pytestmark = pytest.mark.integration
api = api_fixture


def test_generic_routes_hide_probe_even_with_matching_browser_ownership(api):
    engine, factory = api
    client = factory(enabled=True)
    headers = bootstrap(client)
    with Session(engine) as session, session.begin():
        owner = session.scalar(select(BrowserSession))
        run = ResearchRun(mode="explore", goal="Synthetic protocol check", task_kind="verify_meta")
        session.add(run)
        session.flush()
        run_id = run.id
        session.add(ResearchRunAccess(run_id=run_id, session_hash=owner.token_hash))
    for suffix in ("", "/result", "/events"):
        response = client.get(f"/api/v1/runs/{run_id}{suffix}")
        assert response.status_code == 404
        assert response.json() == {"detail": "research_run_not_found"}
        assert response.headers["cache-control"] == "no-store"
    assert client.post(f"/api/v1/runs/{run_id}/cancel", headers=headers).status_code == 404
    history = client.get("/api/v1/runs")
    assert history.status_code == 200
    assert str(run_id) not in history.text
    with Session(engine) as session:
        assert session.get(ResearchRun, run_id).status == "queued"
