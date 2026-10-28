"""The discovery extension requires a fresh exact native approval, without sends."""

import pytest
from test_meta_readiness import readiness as readiness_fixture
from test_meta_research_api import (
    PREFIX,
    count,
    preview_body,
    private,
)
from test_meta_research_api import research_api as research_api_fixture
from test_worker import database as database_fixture

from polymath.meta_research_models import NativeResearchRunBinding
from polymath.models import ResearchRun

database = database_fixture
readiness = readiness_fixture
research_api = research_api_fixture
pytestmark = pytest.mark.integration


def test_discovery_extension_is_exactly_previewed_and_separately_approved(research_api):
    active, factory, headers = research_api
    client = factory()
    before = count(active.engine, ResearchRun), count(active.engine, NativeResearchRunBinding)
    body = preview_body(active)
    ordinary = client.post(PREFIX + "/preview", headers=headers, json=body)
    assert ordinary.status_code == 200, ordinary.text
    assert "discovery_ladder_profile" not in ordinary.json()["manifest"]
    discovery = {"max_stages": 4, "max_depth": 3}
    extended = client.post(
        PREFIX + "/preview", headers=headers, json={**body, "discovery": discovery}
    )
    assert extended.status_code == 200, extended.text
    private(extended)
    preview = extended.json()
    manifest = preview["manifest"]
    assert manifest["task_profiles"] == ordinary.json()["manifest"]["task_profiles"]
    assert preview["manifest_sha256"] != ordinary.json()["manifest_sha256"]
    binding = manifest["discovery_ladder_profile"]
    assert binding["profile"]["max_stages"] == 4
    assert binding["profile"]["max_depth"] == 3
    assert not preview["research_execution_available"]
    # Merely preparing the extension leaves the original approved current scope.
    assert client.get(PREFIX + "/current").json()["activation"]["manifest_sha256"] == (
        active.activation_sha256
    )
    approved = client.post(
        PREFIX + "/activations",
        headers=headers,
        json={"manifest": manifest, "expected_manifest_sha256": preview["manifest_sha256"]},
    )
    assert approved.status_code == 201, approved.text
    private(approved)
    assert approved.json()["manifest_sha256"] == preview["manifest_sha256"]
    assert count(active.engine, ResearchRun) == before[0]
    assert count(active.engine, NativeResearchRunBinding) == before[1]


@pytest.mark.parametrize(
    "discovery",
    [
        {"max_stages": 5, "max_depth": 3},
        {"max_stages": 3, "max_depth": 4},
        {"max_stages": True, "max_depth": 2},
        {"max_stages": 3, "max_depth": 2, "auto_publish": True},
    ],
)
def test_malformed_discovery_approval_cannot_create_work(research_api, discovery):
    active, factory, headers = research_api
    client = factory()
    before = count(active.engine, ResearchRun), count(active.engine, NativeResearchRunBinding)
    response = client.post(
        PREFIX + "/preview",
        headers=headers,
        json={**preview_body(active), "discovery": discovery},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "meta_research_invalid_request"}
    private(response)
    assert count(active.engine, ResearchRun) == before[0]
    assert count(active.engine, NativeResearchRunBinding) == before[1]
