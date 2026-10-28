"""Actual SDK/loopback/PG/Meta adapter journeys; all scientific inputs synthetic."""

import copy
import uuid

import pytest
import test_meta_readiness
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_api_client_http import client_module as client_module_fixture
from test_api_client_http import sdk
from test_api_client_public_http import actual_worker, native_bindings
from test_api_client_public_http import public_http as public_http_fixture
from test_artifact_worker import ArtifactFixture
from test_explore_collection_api import approve_collection
from test_explore_collection_controller import GOAL, prefix, ranking_action, second_branch
from test_explore_native_worker import worker_for
from test_meta_readiness import make_request
from test_meta_readiness import readiness as readiness_fixture
from test_operator_auth import login
from test_public_delegations import public_active
from test_scientific_native_worker import ledger_snapshot
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.budget_models import BudgetReservation
from polymath.jobs import JobRepository
from polymath.meta_research_models import NativeResearchRunBinding

database = database_fixture
readiness = readiness_fixture
client_module = client_module_fixture
public_http = public_http_fixture
pytestmark = pytest.mark.integration


@pytest.fixture
def public(readiness, monkeypatch):
    original = test_meta_readiness.proposal_data

    def proposal(**kwargs):
        data = original(**kwargs)
        data["application_limits"]["max_model_calls"] = 20
        return data

    monkeypatch.setattr(test_meta_readiness, "proposal_data", proposal)
    return approve_collection(
        public_active(readiness, short_issuer=False), public=True, max_bridges=4, scientific=True
    )


def read_counts(public):
    with Session(public.engine) as session:
        return (
            session.scalar(select(func.count()).select_from(BrowserSession)),
            session.scalar(select(func.count()).select_from(BudgetReservation)),
        )


@pytest.mark.parametrize("requested", [None, 2])
async def test_sdk_public_collection_model_order_exact_exports_and_stopped_history(
    public, public_http, client_module, tmp_path, monkeypatch, requested
):
    caps = ledger_snapshot(public)
    with sdk(client_module, public_http, tmp_path / "collection.cookies") as client:
        with pytest.raises(client_module.ClientError, match="research_session_required"):
            client.explore_protocols()
        options = (
            {}
            if requested is None
            else {
                "explore_collection_intent": {
                    "protocol": "polymath-explore-collection-v1",
                    "max_bridges": requested,
                }
            }
        )
        created = client.create_run("explore", GOAL, **options)
        run_id = created["id"]
        native_bindings(public, run_id)
        with Session(public.engine) as session:
            binding = session.get(NativeResearchRunBinding, uuid.UUID(run_id))
            approved = public.public_preview.manifest.explore_collection_profile
            assert binding.profile == approved.profile.model_dump(mode="json")
            assert binding.profile_sha256 == approved.profile_sha256
            assert binding.profile["max_bridges"] == 4
        assert (
            public.issuer_client.post(
                "/api/v1/operator/logout", headers=public.issuer_headers
            ).status_code
            == 200
        )
        instance, calls = worker_for(
            public,
            monkeypatch,
            [*prefix(notes=True), *second_branch(), ranking_action],
            public=True,
        )
        outcome = await instance.run_once("sdk-model-ranked-collection")
        assert outcome.status == "succeeded", JobRepository(public.engine).get(uuid.UUID(run_id))
        counts = read_counts(public)
        summary = client.get_explore_collection(run_id)
        details = client.list_explore_bridges(run_id)
        rankings = client.list_explore_rankings(run_id)
        selected = summary["selected_final_ranking"]["ranked"][0]["reference"]
        assert summary["explore_collection_intent"]["max_bridges"] == (
            3 if requested is None else requested
        )
        assert len(summary["stages"]) == len(details["stages"]) == 2
        assert summary["rankings"] == rankings["rankings"]
        assert (
            summary["stages"][0]["assessment"]["confidence_score"]
            > summary["stages"][1]["assessment"]["confidence_score"]
        )
        assert selected["result_id"] == summary["stages"][1]["result_id"] == outcome.result_id
        first = details["stages"][0]
        assert client.get_explore_bridge(run_id, first["stage"]["stage_id"]) == first
        assert (
            client.get_assessment_finding(first["stage"]["result_id"])["payload"] == first["result"]
        )
        assert client.get_result(run_id) == details["stages"][1]["result"]
        assert (
            client.get_run(run_id)["explore_collection_intent"]
            == summary["explore_collection_intent"]
        )
        assert summary["total_usage"]["model_calls"] == len(calls.requests) == 15
        assert read_counts(public) == counts
        assert ledger_snapshot(public) == caps
        assert (
            len(
                [
                    item
                    for item in public_http.requests
                    if item["path"] == "/api/v1/research/explore-protocols"
                ]
            )
            == 1
        )
        assert (
            len(
                [
                    item
                    for item in public_http.requests
                    if item["path"] == "/api/v1/runs" and item["method"] == "POST"
                ]
            )
            == 1
        )

        # Write from the exact earlier, unselected bridge; no source substitution.
        artifact = client.create_artifact("write_summary", first["stage"]["result_id"])
        artifact_worker, _ = actual_worker(public, ArtifactFixture())
        written = await artifact_worker.run_once("sdk-write-earlier-collection-bridge")
        assert written.status == "succeeded" and written.artifact_id
        assert (
            client.get_artifact_job(artifact["id"])["scientific_context_intent"]["protocol"]
            == "polymath-artifact-scientific-context-v1"
        )
        artifact_detail = client.get_artifact(written.artifact_id)
        assert artifact_detail["source_result_id"] == first["stage"]["result_id"]
        assert (
            artifact_detail["scientific_context"]["context"]["stage_origin"]["receipt"]
            == first["stage"]
        )
        target = tmp_path / "collection-summary.md"
        client.export_artifact(written.artifact_id, target)
        assert target.read_bytes() == artifact_detail["markdown"].encode("utf-8")
        assert b"Synthetic" in target.read_bytes()

        saved = copy.deepcopy(summary)
        headers, _ = login(public.issuer_client)
        public.delegations.revoke(
            make_request(public.issuer_client, headers), public.public_activation.id
        )
        counts = read_counts(public)
        assert client.research_access()["available"] is False
        assert client.get_explore_collection(run_id) == saved
        assert client.get_explore_bridge(run_id, first["stage"]["stage_id"]) == first
        assert client.get_artifact(written.artifact_id) == artifact_detail
        assert read_counts(public) == counts
        assert (await instance.run_once("stopped-collection-no-fresh-model-call")).status == "idle"
        assert len(calls.requests) == 15
    assert not any(
        item["operator_header"] or item["operator_cookie"] for item in public_http.requests
    )


async def test_sdk_unknown_response_retains_unselected_bridge_and_never_resends(
    public, public_http, client_module, tmp_path, monkeypatch
):
    with sdk(client_module, public_http, tmp_path / "partial.cookies") as client:
        created = client.create_run("explore", GOAL)
        instance, calls = worker_for(
            public, monkeypatch, [*prefix(), ranking_action], public=True, unknown_at=8
        )
        outcome = await instance.run_once("sdk-unknown-after-saved-bridge")
        assert outcome.status == "interrupted" and outcome.result_id is None
        summary = client.get_explore_collection(created["id"])
        assert len(summary["stages"]) == 1 and summary["rankings"] == []
        assert summary["selected_final_ranking"] is None
        assert summary["total_usage"]["model_calls"] == 8
        assert summary["total_usage"]["calls_without_usage"] == 1
        assert int(summary["accounting"]["unknown_hold_microdollars"]) > 0
        counts = read_counts(public)
        assert len(client.list_explore_bridges(created["id"])["stages"]) == 1
        assert client.get_explore_collection(created["id"]) == summary
        assert read_counts(public) == counts
        assert (await instance.run_once("never-resend-sdk-unknown-response")).status == "idle"
        assert len(calls.requests) == 8
