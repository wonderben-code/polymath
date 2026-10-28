"""Real API/Meta adapter/PG collections; every external response is synthetic."""

import copy
import json
import uuid
from dataclasses import asdict
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_discovery_controller import DiscoveryFixtureTools
from test_discovery_native_worker import twenty_calls
from test_explore_collection_api import approve_collection, create_collection
from test_explore_collection_controller import (
    GOAL,
    insufficient,
    prefix,
    ranking_action,
    second_branch,
)
from test_meta_readiness import readiness as readiness_fixture
from test_native_research_worker import native as native_fixture
from test_native_research_worker import native_reservations
from test_public_delegations import public_active
from test_public_native_worker import expire_issuer
from test_run_api import bootstrap
from test_scientific_native_worker import ledger_snapshot, scientific_client
from test_worker import database as database_fixture

import polymath.native_research_worker as runtime
from polymath.budget import BudgetService
from polymath.budget_models import BudgetRunLimit
from polymath.discovery_models import StageCommit
from polymath.evidence_models import EvidenceResult
from polymath.explore_collection_models import (
    ExploreCollectionFinish,
    ExploreRankingRecord,
)
from polymath.explore_collection_store import ExploreCollectionStore
from polymath.jobs import JobRepository
from polymath.meta_research import _original_input, native_run_input_sha256
from polymath.meta_research_models import NativeResearchRunBinding
from polymath.models import ResearchRun
from polymath.research.meta import MetaModel, ModelReply
from polymath.worker import _digest

database = database_fixture
native = native_fixture
readiness = readiness_fixture
pytestmark = pytest.mark.integration


class ExploreHttpFixture:
    """Choose from replayed conversation position, not the number of HTTP sends.

    Cached responses are replayed by NativeRecordedModel without re-entering this
    fixture. Real newly sent requests still contain all original assistant turns.
    Dynamic rankings use only the actual acknowledged stage references.
    """

    def __init__(self, actions):
        self.actions = actions
        self.messages = []
        self.calls = 0

    async def complete(self, messages, *, max_output_tokens):
        self.calls += 1
        self.messages.append(copy.deepcopy(messages))
        assert json.loads(messages[1]["content"])["research_goal"] == GOAL
        index = sum(item["role"] == "assistant" for item in messages)
        action = self.actions[index]
        if callable(action):
            action = action(messages)
        return ModelReply(json.dumps(action, ensure_ascii=False), 10, 15)


def worker_for(value, monkeypatch, actions, *, public=False, unknown_at=None):
    intelligence = ExploreHttpFixture(actions)
    state = SimpleNamespace(requests=[], models=[], intelligence=intelligence)

    async def handler(request):
        state.requests.append(request)
        assert str(request.url) == "https://api.meta.ai/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == value.proposal.model_id
        assert payload["max_tokens"] == value.proposal.application_limits.max_output_tokens
        if len(state.requests) == unknown_at:
            raise httpx.ReadTimeout("Synthetic collection response lost", request=request)
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
        state.models.append(kwargs)
        return MetaModel(**kwargs, transport=httpx.MockTransport(handler))

    monkeypatch.setattr(runtime, "MetaModel", constructor)
    instance = runtime.build_native_research_worker(
        value.engine,
        research_policy=value.policy,
        readiness_policy=value.readiness_policy,
        operator_policy=value.operator_policy,
        tools_factory=lambda _: DiscoveryFixtureTools(),
        **({"public_policy": value.public_policy} if public else {}),
    )
    return instance, state


def rows(value, kind, run_id):
    with Session(value.engine) as session:
        query = select(kind).where(kind.run_id == run_id)
        if kind is StageCommit:
            query = query.order_by(kind.ordinal)
        elif kind is ExploreRankingRecord:
            query = query.order_by(kind.action_ordinal)
        return session.scalars(query).all()


def admitted(value, client, *, maximum=3, public=False, headers=None):
    response = create_collection(
        client,
        value,
        public=public,
        headers=headers,
        intent={"protocol": "polymath-explore-collection-v1", "max_bridges": maximum},
    )
    assert response.status_code == 201, response.text
    return uuid.UUID(response.json()["id"])


def assert_original_profile(value, run_id, *, maximum, public=False):
    record = value.public_activation if public else value.activation
    approved = record.to_response()["manifest"]["explore_collection_profile"]
    with Session(value.engine) as session:
        binding = session.get(NativeResearchRunBinding, run_id)
        run = session.get(ResearchRun, run_id)
        assert binding.profile == approved["profile"]
        assert binding.profile_sha256 == approved["profile_sha256"]
        assert binding.profile["controller_protocol"] == "polymath-meta-explore-collection-v1"
        assert binding.profile["max_actions"] == 16
        assert binding.profile["max_bridges"] >= maximum
        assert native_run_input_sha256(session, run) == binding.input_sha256
        original = _original_input(session, run_id, check_input=True)
        profile = runtime.original_runtime_profile(original)
        cap = session.get(BudgetRunLimit, (binding.account_id, run_id))
        assert cap.max_calls == approved["profile"]["max_model_calls"]
        assert cap.cap_microdollars == value.proposal.account.run_cap_microdollars
        # The run's narrower immutable intent changes only its input/fingerprint,
        # never the quantitative profile and reviewed profile SHA.
        fingerprint = {
            "protocol": "polymath-worker-full-text-v8",
            "mode": "explore",
            "task_kind": "research",
            "parents": [],
            "artifact_basis": None,
            "assessment_basis": None,
            "policy": asdict(profile.policy),
            "limits": asdict(profile.limits),
            "artifact_limits": asdict(profile.artifact_limits),
            "assessment_limits": None,
            "native_run_binding": profile.protocol_binding,
            "explore_collection_intent": {
                "protocol": "polymath-explore-collection-v1",
                "max_bridges": maximum,
            },
        }
        assert run.checkpoint["worker_protocol"] == _digest(fingerprint)
        legacy_input = {
            "protocol": "polymath-native-scientific-input-v1",
            "mode": "explore",
            "task_kind": "research",
            "goal": GOAL,
            "parents": [],
        }
        assert binding.input_sha256 == _digest(
            legacy_input | {"explore_collection_intent": fingerprint["explore_collection_intent"]}
        )
        assert binding.input_sha256 != _digest(legacy_input)


@pytest.mark.parametrize("native", [twenty_calls], indirect=True)
async def test_sixteen_actions_keep_one_native_profile_ledger_and_model_authored_order(
    native, monkeypatch
):
    approve_collection(native, max_bridges=4)
    caps = ledger_snapshot(native)
    with scientific_client(native) as client:
        run_id = admitted(native, client, maximum=2)
        actions = [
            *prefix(notes=True),
            *second_branch(),
            lambda messages: ranking_action(messages, finish=False),
            ranking_action,
        ]
        instance, state = worker_for(native, monkeypatch, actions)
        outcome = await instance.run_once("native-sixteen-action-collection")
        assert outcome.status == "succeeded", JobRepository(native.engine).get(run_id)
        stages = rows(native, StageCommit, run_id)
        ranks = rows(native, ExploreRankingRecord, run_id)
        assert [item.depth for item in stages] == [1, 1]
        assert [item.model_request_id for item in stages] == ["model-0009", "model-0014"]
        assert [item.action_ordinal for item in ranks] == [15, 16]
        assert stages[0].receipt["assessment"]["confidence_score"] == 80
        assert stages[1].receipt["assessment"]["confidence_score"] == 25
        assert [item["reference"]["result_id"] for item in ranks[-1].receipt["ranked"]] == [
            str(stages[1].result_id),
            str(stages[0].result_id),
        ]
        assert outcome.result_id == str(stages[1].result_id)
        assert len(rows(native, EvidenceResult, run_id)) == 2
        assert len(state.requests) == len(state.models) == state.intelligence.calls == 16
        assert_original_profile(native, run_id, maximum=2)
        summary = client.get(f"/api/v1/runs/{run_id}/explore-collection")
        assert summary.status_code == 200, summary.text
        body = summary.json()
        assert len(body["stages"]) == len(body["rankings"]) == 2
        assert body["selected_final_ranking"] == ranks[-1].receipt
        assert body["total_usage"]["model_calls"] == 16
        assert body["accounting"]["counted_spend_microdollars"] == "640"
        assert any(
            event.kind == "research_note" and event.summary.startswith("discarded:")
            for event in JobRepository(native.engine).events_after(run_id)
        )
        saved = client.get(f"/api/v1/runs/{run_id}/result")
        assert saved.status_code == 200 and saved.json()["finding"]["title"].endswith("bridge2")
    usage = BudgetService(native.engine).usage(native.policy.native_account_id)
    assert (usage.reservations, usage.committed_microdollars, usage.in_flight) == (17, 680, 0)
    assert all(row.status == "settled" for row in native_reservations(native, run_id))
    assert ledger_snapshot(native) == caps


@pytest.mark.parametrize("case", ["empty", "excluded", "partial"])
async def test_native_collection_preserves_empty_excluded_and_unranked_candidates(
    native, monkeypatch, case
):
    approve_collection(native)
    actions = {
        "empty": [lambda messages: ranking_action(messages, order=())],
        "excluded": [*prefix(), lambda messages: ranking_action(messages, order=(), excluded=(0,))],
        "partial": [*prefix(), insufficient()],
    }[case]
    with scientific_client(native) as client:
        run_id = admitted(native, client)
        instance, state = worker_for(native, monkeypatch, actions)
        outcome = await instance.run_once("honest-native-" + case)
        assert outcome.status == "succeeded" and outcome.result_id is None
        assert len(rows(native, StageCommit, run_id)) == (0 if case == "empty" else 1)
        assert len(rows(native, EvidenceResult, run_id)) == (0 if case == "empty" else 1)
        assert len(state.requests) == (1 if case == "empty" else 8)
        summary = client.get(f"/api/v1/runs/{run_id}/explore-collection").json()
        final = summary["selected_final_ranking"]
        if case == "partial":
            assert final is None and summary["rankings"] == []
        else:
            assert final["ranked"] == []
            assert len(final["excluded"]) == (1 if case == "excluded" else 0)
            assert rows(native, ExploreCollectionFinish, run_id)[0].selected_result_id is None
        assert JobRepository(native.engine).get(run_id).outcome["result_id"] is None


@pytest.mark.parametrize("operation", ["commit", "rank", "finish"])
async def test_native_replay_uses_original_saved_acknowledgements_without_resending(
    native, monkeypatch, operation
):
    approve_collection(native)
    actions = [*prefix()]
    if operation == "rank":
        actions.append(lambda messages: ranking_action(messages, finish=False))
    actions.append(ranking_action)
    original, first = getattr(ExploreCollectionStore, operation), True

    def lose_acknowledgement(store, *args, **kwargs):
        nonlocal first
        receipt = original(store, *args, **kwargs)
        if first:
            first = False
            raise RuntimeError("Synthetic committed collection acknowledgement lost")
        return receipt

    monkeypatch.setattr(ExploreCollectionStore, operation, lose_acknowledgement)
    with scientific_client(native) as client:
        run_id = admitted(native, client)
        initial, initial_state = worker_for(native, monkeypatch, actions)
        assert (await initial.run_once("native-collection-lost-ack")).status == "interrupted"
        snapshot = JobRepository(native.engine).get(run_id)
        checkpoint_hash = snapshot.checkpoint["worker_protocol"]
        stages = rows(native, StageCommit, run_id)
        assert len(stages) == 1
        with Session(native.engine) as session, session.begin():
            native.store.resume_in_session(
                session, native.request, run_id, snapshot.lease_token, native.activation_sha256
            )
        replacement, state = worker_for(native, monkeypatch, actions)
        outcome = await replacement.run_once("native-collection-explicit-replay")
        assert outcome.status == "succeeded" and outcome.result_id == str(stages[0].result_id)
        assert len(state.requests) == (0 if operation == "finish" else 1)
        assert len(initial_state.requests) + len(state.requests) == len(actions)
        assert (
            len(rows(native, StageCommit, run_id)) == len(rows(native, EvidenceResult, run_id)) == 1
        )
        assert len(rows(native, ExploreRankingRecord, run_id)) == (2 if operation == "rank" else 1)
        assert len(rows(native, ExploreCollectionFinish, run_id)) == 1
        assert (
            JobRepository(native.engine).get(run_id).checkpoint["worker_protocol"]
            == checkpoint_hash
        )
        assert len(native_reservations(native, run_id)) == len(actions)
        if state.requests:
            replayed = json.loads(state.requests[0].content)["messages"]
            assert any(
                message["role"] == "user"
                and json.loads(message["content"]).get("observation", {}).get("kind")
                == "stage_committed"
                for message in replayed
            )


@pytest.mark.parametrize("native", [twenty_calls], indirect=True)
async def test_native_replay_keeps_early_rank_coverage_separate_from_later_committed_bridge(
    native, monkeypatch
):
    approve_collection(native)
    actions = [
        *prefix(),
        lambda messages: ranking_action(messages, finish=False),
        *second_branch(),
        lambda messages: ranking_action(messages, order=(1,), excluded=(0,)),
    ]
    original, first = ExploreCollectionStore.commit, True

    def lose_later_stage_ack(store, *args, **kwargs):
        nonlocal first
        receipt = original(store, *args, **kwargs)
        if first and receipt.ordinal == 1:
            first = False
            raise RuntimeError("Synthetic later bridge committed before acknowledgement loss")
        return receipt

    monkeypatch.setattr(ExploreCollectionStore, "commit", lose_later_stage_ack)
    with scientific_client(native) as client:
        run_id = admitted(native, client)
        initial, sent = worker_for(native, monkeypatch, actions)
        assert (await initial.run_once("collection-later-stage-ack-loss")).status == "interrupted"
        assert len(sent.requests) == 13 and len(rows(native, StageCommit, run_id)) == 2
        old_rank = rows(native, ExploreRankingRecord, run_id)[0]
        assert old_rank.covered_through_ordinal == 0 and old_rank.ranked_count == 1
        snapshot = JobRepository(native.engine).get(run_id)
        with Session(native.engine) as session, session.begin():
            native.store.resume_in_session(
                session, native.request, run_id, snapshot.lease_token, native.activation_sha256
            )
        replacement, fresh = worker_for(native, monkeypatch, actions)
        outcome = await replacement.run_once("collection-exact-early-rank-replay")
        stages = rows(native, StageCommit, run_id)
        assert outcome.status == "succeeded" and outcome.result_id == str(stages[1].result_id)
        assert len(fresh.requests) == fresh.intelligence.calls == 1
        observations = [
            item
            for message in fresh.intelligence.messages[0]
            if message["role"] == "user"
            if (item := json.loads(message["content"]).get("observation")) is not None
        ]
        prior_ranks = [item for item in observations if item["kind"] == "explore_ranking_saved"]
        assert len(prior_ranks) == 1
        assert prior_ranks[0]["covered_through_ordinal"] == 0
        assert prior_ranks[0]["ranked"] == old_rank.receipt["ranked"]
        assert len([item for item in observations if item["kind"] == "stage_committed"]) == 2
        final_rank = rows(native, ExploreRankingRecord, run_id)[-1]
        assert final_rank.covered_through_ordinal == 1
        assert final_rank.receipt["excluded"][0]["reference"]["result_id"] == str(
            stages[0].result_id
        )
        assert len(native_reservations(native, run_id)) == 14
        assert len(rows(native, EvidenceResult, run_id)) == 2


async def test_native_unknown_after_a_saved_bridge_keeps_hold_and_no_selected_result(
    native, monkeypatch
):
    approve_collection(native)
    with scientific_client(native) as client:
        run_id = admitted(native, client)
        instance, state = worker_for(native, monkeypatch, [*prefix(), ranking_action], unknown_at=8)
        outcome = await instance.run_once("native-collection-unknown")
        assert outcome.status == "interrupted" and outcome.result_id is None
        summary = client.get(f"/api/v1/runs/{run_id}/explore-collection").json()
        assert len(summary["stages"]) == 1 and summary["rankings"] == []
        assert summary["selected_final_ranking"] is None
        assert summary["total_usage"]["model_calls"] == 8
        assert summary["total_usage"]["calls_without_usage"] == 1
        assert summary["accounting"]["counted_spend_microdollars"] == "280"
        assert summary["accounting"]["unknown_hold_microdollars"] == "308192"
        assert (await instance.run_once("never-retry-unknown-collection")).status == "idle"
        assert len(state.requests) == 8


async def test_public_collection_uses_exact_grant_after_issuer_logout_without_operator_cookie(
    readiness, monkeypatch
):
    public = approve_collection(public_active(readiness, short_issuer=False), public=True)
    caps = ledger_snapshot(public)
    with scientific_client(public, public=True) as visitor:
        headers = bootstrap(visitor)
        capability = visitor.get("/api/v1/research/explore-protocols").json()
        assert capability["activation_sha256"] == public.public_activation_sha256
        run_id = admitted(public, visitor, public=True, headers=headers)
        expire_issuer(public)
        instance, state = worker_for(public, monkeypatch, [*prefix(), ranking_action], public=True)
        result = await instance.run_once("public-collection-after-issuer-logout")
        assert result.status == "succeeded" and result.result_id
        assert len(state.requests) == len(state.models) == 8
        assert "polymath_operator" not in dict(visitor.cookies)
        assert_original_profile(public, run_id, maximum=3, public=True)
        summary = visitor.get(f"/api/v1/runs/{run_id}/explore-collection")
        assert summary.status_code == 200, summary.text
        assert summary.json()["selected_final_ranking"]["ranked"][0]["reference"]["result_id"] == (
            result.result_id
        )
    assert ledger_snapshot(public) == caps
    assert all(
        row.status == "settled" and row.actual_microdollars == 40
        for row in native_reservations(public, run_id)
    )
