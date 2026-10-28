"""Ladder SDK over real loopback HTTP/PostgreSQL with synthetic intelligence."""

import json
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_api_client_http import api_server as api_server_fixture
from test_api_client_http import client_module as client_module_fixture
from test_api_client_http import database as database_fixture
from test_api_client_http import sdk
from test_discovery_boundaries import BibliographicTools
from test_discovery_controller import DiscoveryFixtureModel
from test_discovery_worker import worker
from test_research_controller import source

from polymath.access_models import BrowserSession
from polymath.budget_models import BudgetReservation
from polymath.discovery_models import DiscoveryIntent, StageCommit
from polymath.evidence import EvidenceStore
from polymath.jobs import JobRepository
from polymath.research.meta import ModelReply
from polymath.research.models import SourceBibliography

database = database_fixture
api_server = api_server_fixture
client_module = client_module_fixture
pytestmark = pytest.mark.integration


@pytest.mark.parametrize("complete", [True, False])
async def test_client_real_http_full_and_cancelled_partial_ladder(
    database, api_server, client_module, tmp_path, complete
):
    intent = {"protocol": "polymath-surprise-ladder-v1", "max_stages": 3, "max_depth": 2}
    cookie_path = tmp_path / "discovery-owner.cookies"
    with sdk(client_module, api_server, cookie_path) as client:
        client.init_session()
        created = client.create_run("surprise", discovery_intent=intent)
        assert created["discovery_intent"] == intent
        run_id = created["id"]
        with Session(database) as session:
            initial = session.get(DiscoveryIntent, uuid.UUID(run_id))
            assert initial.max_stages == 3 and initial.max_depth == 2
        outcome = await worker(
            database, DiscoveryFixtureModel(), calls=20 if complete else 7
        ).run_once("loopback-ladder-sdk")
        assert outcome.status == ("succeeded" if complete else "interrupted")
        if not complete:
            assert client.cancel_run(run_id)["status"] == "cancelled"
        before_reads = len(api_server.requests)
        summary = client.get_discovery(run_id)
        listing = client.list_discovery_stages(run_id)
        assert len(summary["stages"]) == len(listing["stages"]) == (3 if complete else 1)
        assert summary["total_usage"]["model_calls"] == (20 if complete else 7)
        if complete:
            assert summary["selected_final_stage"] == summary["stages"][-1]
            assert summary["selected_final_stage"]["result_id"] == outcome.result_id
            assert listing["stages"][-1]["stage"]["depth"] == 2
            assert len(listing["stages"][-1]["parent_edges"]) == 2
        else:
            assert summary["selected_final_stage"] is None
            assert summary["run_status"] == "cancelled"
        for item in listing["stages"]:
            detail = client.get_discovery_stage(run_id, item["stage"]["stage_id"])
            assert detail == item and detail["status"] == "current_supported"
            assert detail["result"]["finding"]["outcome"] == "bridge"
        assert all(request["method"] == "GET" for request in api_server.requests[before_reads:])
        assert not any(
            request["path"] == "/api/v1/session" for request in api_server.requests[before_reads:]
        )
        with Session(database) as session:
            assert session.scalar(select(func.count()).select_from(StageCommit)) == (
                3 if complete else 1
            )
            assert session.scalar(select(func.count()).select_from(BudgetReservation)) == (
                20 if complete else 7
            )
            assert session.scalar(select(func.count()).select_from(BrowserSession)) == 1

    with sdk(client_module, api_server, cookie_path) as reloaded:
        assert reloaded.get_discovery(run_id) == summary
    with sdk(client_module, api_server, tmp_path / "foreign.cookies") as foreign:
        foreign.init_session()
        with pytest.raises(client_module.ClientError, match="discovery_not_found") as rejected:
            foreign.get_discovery(run_id)
        assert rejected.value.status == 404


async def test_client_reads_genuine_needs_review_without_rewriting_saved_stage(
    database, api_server, client_module, tmp_path
):
    with sdk(client_module, api_server, tmp_path / "review-owner.cookies") as client:
        client.init_session()
        created = client.create_run(
            "surprise",
            discovery_intent={
                "protocol": "polymath-surprise-ladder-v1",
                "max_stages": 3,
                "max_depth": 2,
            },
        )
        run_id = created["id"]
        instance = worker(database, DiscoveryFixtureModel(), calls=7)
        instance.tools_factory = lambda _: BibliographicTools()
        assert (await instance.run_once("sdk-review-stage")).status == "interrupted"
        original = client.list_discovery_stages(run_id)["stages"][0]
        assert original["status"] == "current_supported"
        repository = JobRepository(database)
        identifier = uuid.UUID(run_id)
        snapshot = repository.get(identifier)
        repository.resume_interrupted(identifier, expected_token=snapshot.lease_token)
        lease = repository.claim_next("sdk-later-source", lease_seconds=300)
        assert lease.run_id == identifier
        connector = source("sdk-later-alias", "This uncited source links two paper identifiers.")
        connector = connector.model_copy(
            update={
                "bibliography": SourceBibliography(
                    doi="10.1234/synthetic-discovery", arxiv_id="2401.00001"
                )
            }
        )
        EvidenceStore(database).save_source(lease, connector)
        repository.interrupt(lease)
        assert client.cancel_run(run_id)["status"] == "cancelled"
        reviewed = client.get_discovery_stage(run_id, original["stage"]["stage_id"])
        assert reviewed["status"] == "needs_review"
        assert reviewed["result"] == original["result"]
        assert reviewed["stage"] == original["stage"]
        assert len(reviewed["primary_paper_groups"]) == 1
        assert client.list_discovery_stages(run_id)["stages"] == [reviewed]
        assert client.get_discovery(run_id)["selected_final_stage"] is None


async def test_client_earlier_selected_stage_keeps_actual_later_run_totals(
    database, api_server, client_module, tmp_path
):
    class PreferFirst(DiscoveryFixtureModel):
        async def complete(self, messages, *, max_output_tokens):
            observations = [
                json.loads(item["content"]).get("observation")
                for item in messages
                if item["role"] == "user"
            ]
            stages = [
                item for item in observations if item and item.get("kind") == "stage_committed"
            ]
            if len(stages) == 2 and observations[-1]["kind"] == "stage_committed":
                self.calls += 1
                first = stages[0]
                return ModelReply(
                    json.dumps(
                        {
                            "kind": "finish_saved",
                            "stage": {
                                "result_id": first["result_id"],
                                "run_id": first["run_id"],
                                "content_sha256": first["result_sha256"],
                            },
                            "reason": "Select the first bridge after investigating a second.",
                        }
                    ),
                    10,
                    15,
                )
            return await super().complete(messages, max_output_tokens=max_output_tokens)

    with sdk(client_module, api_server, tmp_path / "earlier-owner.cookies") as client:
        client.init_session()
        created = client.create_run(
            "surprise",
            discovery_intent={
                "protocol": "polymath-surprise-ladder-v1",
                "max_stages": 3,
                "max_depth": 2,
            },
        )
        model = PreferFirst()
        assert (
            await worker(database, model).run_once("sdk-earlier-selection")
        ).status == "succeeded"
        summary = client.get_discovery(created["id"])
        assert len(summary["stages"]) == 2
        assert summary["selected_final_stage"] == summary["stages"][0]
        assert summary["total_usage"]["model_calls"] == model.calls == 13
        assert summary["accounting"]["counted_spend_microdollars"] == "325"
        detail = client.get_discovery_stage(created["id"], summary["stages"][0]["stage_id"])
        assert detail["result"]["usage"]["model_calls"] == 7
        assert client.get_result(created["id"]) == detail["result"]
