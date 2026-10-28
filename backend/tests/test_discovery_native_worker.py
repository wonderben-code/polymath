"""One real Meta observer/global ledger through the automatic Surprise ladder.

All Meta transport and primary-source bodies are explicitly synthetic fixtures.
"""

import json
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_discovery_controller import DiscoveryFixtureModel, DiscoveryFixtureTools
from test_native_research_worker import enqueue_native
from test_native_research_worker import native as native_fixture
from test_worker import database as database_fixture

import polymath.native_research_worker as native_runtime
from polymath.budget import BudgetService
from polymath.budget_models import BudgetRunLimit
from polymath.discovery_contracts import DiscoveryIntent
from polymath.discovery_models import StageCommit
from polymath.discovery_store import DiscoveryStore
from polymath.jobs import JobRepository
from polymath.meta_research_contracts import NativeResearchReview
from polymath.native_research_worker import build_native_research_worker
from polymath.research.meta import MetaModel

database = database_fixture
native = native_fixture
pytestmark = pytest.mark.integration


def twenty_calls(data):
    data["application_limits"]["max_model_calls"] = 20


def activate_ladder(value):
    intent = DiscoveryIntent()
    original = value.activation.to_response()["manifest"]
    review = NativeResearchReview.model_validate_json(json.dumps(original["review"]))
    preview = value.store.preview(
        value.request,
        original["readiness_id"],
        original["canary"]["receipt_id"],
        review,
        value.manifest.provider_account_attestation,
        discovery=intent,
    )
    value.activation = value.store.activate(
        value.request, preview.manifest, preview.manifest_sha256
    )
    value.activation_sha256 = preview.manifest_sha256
    return intent


def worker(value, monkeypatch, intelligence, *, unknown_at=None):
    requests, models = [], []

    async def handler(request):
        requests.append(request)
        assert str(request.url) == "https://api.meta.ai/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 4096 and payload["model"] == value.proposal.model_id
        if len(requests) == unknown_at:
            raise httpx.ReadTimeout(
                "synthetic unknown response after a committed stage", request=request
            )
        reply = await intelligence.complete(
            payload["messages"], max_output_tokens=payload["max_tokens"]
        )
        return httpx.Response(
            200,
            json={
                "model": payload["model"],
                "choices": [{"finish_reason": "stop", "message": {"content": reply.content}}],
                "usage": {
                    "prompt_tokens": reply.input_tokens,
                    "completion_tokens": reply.output_tokens,
                },
            },
        )

    def constructor(**kwargs):
        models.append(kwargs)
        return MetaModel(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(native_runtime, "MetaModel", constructor)
    result = build_native_research_worker(
        value.engine,
        research_policy=value.policy,
        readiness_policy=value.readiness_policy,
        operator_policy=value.operator_policy,
        tools_factory=lambda _: DiscoveryFixtureTools(),
    )
    return result, requests, models


def stages(value, run_id):
    with Session(value.engine) as session:
        return session.scalars(
            select(StageCommit).where(StageCommit.run_id == run_id).order_by(StageCommit.ordinal)
        ).all()


@pytest.mark.parametrize("native", [twenty_calls], indirect=True)
async def test_native_ladder_uses_one_reviewed_profile_and_no_budget_reset(native, monkeypatch):
    intent = activate_ladder(native)
    run_id = enqueue_native(native, mode="surprise", discovery_intent=intent)
    model = DiscoveryFixtureModel()
    instance, requests, constructed = worker(native, monkeypatch, model)
    outcome = await instance.run_once("native-automatic-discovery")
    rows = stages(native, run_id)
    assert outcome.status == "succeeded" and len(rows) == 3
    assert [row.depth for row in rows] == [1, 1, 2]
    assert outcome.result_id == str(rows[-1].result_id)
    assert model.calls == len(requests) == len(constructed) == 20
    usage = BudgetService(native.engine).usage(native.policy.native_account_id)
    assert usage.reservations == 21  # The original canary remains on this same ledger.
    assert usage.committed_microdollars == 840 and usage.in_flight == 0
    with Session(native.engine) as session:
        assert (
            session.get(BudgetRunLimit, (native.policy.native_account_id, run_id)).max_calls == 20
        )


@pytest.mark.parametrize("native", [twenty_calls], indirect=True)
async def test_native_replay_hides_later_stages_and_never_resends_known_steps(native, monkeypatch):
    intent = activate_ladder(native)
    run_id = enqueue_native(native, mode="surprise", discovery_intent=intent)
    original, first = DiscoveryStore.commit, True

    def lose_second_stage_response(store, lease, packet):
        nonlocal first
        receipt = original(store, lease, packet)
        if first and receipt.ordinal == 1:
            first = False
            raise RuntimeError("synthetic second stage committed before response loss")
        return receipt

    monkeypatch.setattr(DiscoveryStore, "commit", lose_second_stage_response)
    first_model = DiscoveryFixtureModel()
    instance, first_requests, _ = worker(native, monkeypatch, first_model)
    assert (await instance.run_once("native-lost-stage-ack")).status == "interrupted"
    assert len(stages(native, run_id)) == 2 and len(first_requests) == 12
    snapshot = JobRepository(native.engine).get(run_id)
    with Session(native.engine) as session, session.begin():
        native.store.resume_in_session(
            session, native.request, run_id, snapshot.lease_token, native.activation_sha256
        )
    replacement_model = DiscoveryFixtureModel()
    replacement, requests, _ = worker(native, monkeypatch, replacement_model)
    outcome = await replacement.run_once("native-explicit-full-replay")
    assert outcome.status == "succeeded" and uuid.UUID(outcome.result_id)
    assert len(stages(native, run_id)) == 3
    assert len(requests) == replacement_model.calls == 8
    assert BudgetService(native.engine).usage(native.policy.native_account_id).reservations == 21


@pytest.mark.parametrize("native", [twenty_calls], indirect=True)
async def test_unknown_native_response_preserves_stage_and_reports_full_run_hold(
    native, monkeypatch
):
    intent = activate_ladder(native)
    run_id = enqueue_native(native, mode="surprise", discovery_intent=intent)
    instance, requests, _ = worker(native, monkeypatch, DiscoveryFixtureModel(), unknown_at=8)
    outcome = await instance.run_once("native-partial-unknown")
    assert outcome.status == "interrupted" and outcome.result_id is None
    assert len(stages(native, run_id)) == 1 and len(requests) == 8
    summary = DiscoveryStore(native.engine).summary(native.owner_hash, run_id)
    assert summary["selected_final_stage"] is None
    assert summary["total_usage"]["model_calls"] == 8
    assert summary["total_usage"]["calls_without_usage"] == 1
    assert summary["accounting"]["counted_spend_microdollars"] == "280"
    assert summary["accounting"]["unknown_hold_microdollars"] == "308192"
    assert (await instance.run_once("no-unknown-retry")).status == "idle"
    assert len(requests) == 8
