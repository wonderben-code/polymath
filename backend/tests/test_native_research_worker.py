"""Real native Meta adapter, fixed provider parsers and PG scientific task journeys.

The synthetic intelligence below exists only behind mock HTTP transport. Product
composition always uses MetaModel and the existing scientific controllers.
"""

import asyncio
import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from test_artifact_worker import ArtifactFixture
from test_assessment_controller import AssessmentFixtureModel
from test_evidence import document
from test_full_text import FullTextFixtureModel, full_text_fixture_transport
from test_meta_probe_worker import ACTION, prepared_probe
from test_research_modes import fixture_answer, fixture_bridge
from test_worker import database as database_fixture

import polymath.native_research_worker as runtime
from polymath.access_models import ResearchRunAccess
from polymath.artifacts import ArtifactStore
from polymath.artifacts import bind_request as bind_artifact
from polymath.assessments import AssessmentStore
from polymath.assessments import bind_request as bind_assessment
from polymath.budget_models import BudgetReservation, BudgetRunLimit
from polymath.evidence import EvidenceStore
from polymath.ingestion import IngestionRepository
from polymath.jobs import JobRepository
from polymath.lineage import bind_parents
from polymath.lineage_models import ResearchRunParent
from polymath.meta_probe_worker import MetaProbeWorker
from polymath.meta_research import MetaResearchStore, native_run_input_sha256
from polymath.meta_research_contracts import NativeResearchPolicy, NativeResearchReview
from polymath.meta_research_dispatch import MetaResearchDispatcher
from polymath.meta_research_models import NativeResearchRunBinding
from polymath.models import ResearchExternalRequest, ResearchRun
from polymath.native_research_worker import (
    NativeRecordedModel,
    build_native_research_worker,
    original_runtime_profile,
)
from polymath.research.meta import MetaModel, ModelReply
from polymath.run_api import QUEUE_LOCK_KEY
from polymath.worker import _RunContext

database = database_fixture
pytestmark = pytest.mark.integration


class ScientificHttpFixture:
    """Respond to actual discovery/text observations with exact fixture citations."""

    def __init__(self):
        self.calls = 0

    async def complete(self, messages, *, max_output_tokens):
        self.calls += 1
        initial = json.loads(messages[1]["content"])
        mode = initial["mode"]
        observations = [
            value["observation"]
            for message in messages
            if message["role"] == "user"
            if "observation" in (value := json.loads(message["content"]))
        ]
        searches = [item for item in observations if item.get("kind") == "search_results"]
        documents = {
            item["source_id"]: item for item in observations if item.get("kind") == "source_text"
        }
        discovery = next((item for item in searches if item["purpose"] == "discovery"), None)
        selection = next(
            (item for item in observations if item.get("kind") == "fields_selected"), None
        )
        read_parents = {
            item["reference"]["result_id"]
            for item in observations
            if item.get("kind") == "memory_finding"
        }
        unread_parent = next(
            (item for item in initial["parent_result_ids"] if item not in read_parents), None
        )
        if unread_parent:
            action = {"kind": "read_memory_finding", "result_id": unread_parent}
        elif mode == "surprise" and selection is None:
            action = {
                "kind": "select_fields",
                "fields": ["Information theory", "Biology"],
                "rationale": "Synthetic test compares redundant representations across fields.",
            }
        elif discovery is None:
            action = {
                "kind": "search_literature",
                "query": "Synthetic native source evidence",
                "purpose": "discovery",
            }
        elif (
            unread := next(
                (item for item in discovery["sources"] if item["source_id"] not in documents), None
            )
        ) is not None:
            action = {"kind": "read_source", "source_id": unread["source_id"]}
        elif "counterevidence" not in {item["purpose"] for item in searches}:
            action = {
                "kind": "search_literature",
                "query": "Synthetic native counterevidence",
                "purpose": "counterevidence",
            }
        elif "prior_work" not in {item["purpose"] for item in searches}:
            action = {
                "kind": "search_literature",
                "query": "Synthetic native prior work",
                "purpose": "prior_work",
            }
        else:
            selected = list(documents.values())[:2]
            action = fixture_answer(initial["research_goal"]) if mode == "ask" else fixture_bridge()

            def replace_evidence(value):
                if isinstance(value, dict):
                    if value.get("source_id") in {"left", "right"}:
                        source = selected[0 if value["source_id"] == "left" else 1]
                        value["source_id"] = source["source_id"]
                        value["quote"] = source["text"][:100]
                    for nested in value.values():
                        replace_evidence(nested)
                elif isinstance(value, list):
                    for nested in value:
                        replace_evidence(nested)

            replace_evidence(action)
        return ModelReply(json.dumps(action), 10, 15)


@pytest.fixture
async def native(database, monkeypatch, request):
    with prepared_probe(database, change_proposal=getattr(request, "param", None)) as probe:

        async def canary_http(request):
            return httpx.Response(
                200,
                json={
                    "model": probe.proposal.model_id,
                    "choices": [{"finish_reason": "stop", "message": {"content": ACTION}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 15},
                },
            )

        canary_worker = MetaProbeWorker(
            database,
            probe_policy=probe.policy,
            readiness_policy=probe.readiness_policy,
            operator_policy=probe.operator_policy,
            model_factory=lambda _: MetaModel(
                api_key=probe.policy.api_key,
                model=probe.policy.model_id,
                live_enabled=True,
                transport=httpx.MockTransport(canary_http),
                request_timeout_seconds=probe.manifest.request_timeout_seconds,
            ),
        )
        outcome = await canary_worker.run_once("synthetic-required-canary")
        assert outcome.native_status == "passed"
        policy = NativeResearchPolicy(
            paid_research_enabled=True,
            native_account_id=probe.policy.native_account_id,
            model_id=probe.policy.model_id,
            api_key=probe.policy.api_key,
        )
        store = MetaResearchStore(database, policy, probe.readiness_policy, probe.operator_policy)
        attestation = probe.manifest.provider_account_attestation
        review = NativeResearchReview(
            model_wire_usage_confirmed=True,
            pricing_bounds_confirmed=True,
            funding_cap_confirmed=True,
            observed_at=attestation.observed_at,
            valid_until=attestation.valid_until,
            evidence=attestation.evidence,
        )
        preview = store.preview(
            probe.request,
            probe.manifest.readiness_id,
            outcome.receipt_id,
            review,
            attestation,
        )
        activation = store.activate(probe.request, preview.manifest, preview.manifest_sha256)
        with Session(database) as session:
            owner_hash = session.get(ResearchRunAccess, probe.run_id).session_hash
        yield SimpleNamespace(
            **{
                **vars(probe),
                "policy": policy,
                "store": store,
                "activation": activation,
                "activation_sha256": preview.manifest_sha256,
                "owner_hash": owner_hash,
            }
        )


def enqueue_native(
    native,
    *,
    mode="explore",
    kind="research",
    parents=(),
    source_result_id=None,
    formalisation_id=None,
    watch_id=None,
    source_version_ids=(),
):
    with Session(native.engine) as session, session.begin():
        admission = native.store.begin_admission(session, native.request, native.activation_sha256)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUEUE_LOCK_KEY})
        native.store.lock_account_in_session(session, admission)
        run = ResearchRun(mode=mode, task_kind=kind, goal="Synthetic native research question?")
        session.add(run)
        session.flush()
        session.add(ResearchRunAccess(run_id=run.id, session_hash=native.owner_hash))
        session.flush()
        if parents:
            bind_parents(session, run, native.owner_hash, list(map(str, parents)))
        if kind in {"formalise", "write_summary", "write_paper"}:
            bind_artifact(
                session,
                run,
                native.owner_hash,
                kind,
                str(source_result_id),
                str(formalisation_id) if formalisation_id else None,
            )
        if kind == "reassess_finding":
            bind_assessment(
                session,
                run,
                native.owner_hash,
                source_result_id,
                watch_id,
                tuple(source_version_ids),
                0,
            )
        session.flush()
        native.store.bind_run_in_session(
            session, admission, run, native_run_input_sha256(session, run)
        )
        return run.id


def actual_worker(native, monkeypatch, intelligence, *, change_response=None):
    state = SimpleNamespace(requests=[], constructed=[], source_requests=[])

    async def meta_http(request):
        state.requests.append(request)
        assert str(request.url) == "https://api.meta.ai/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == native.proposal.model_id
        assert body["max_tokens"] == native.proposal.application_limits.max_output_tokens
        assert body["stream"] is False
        reply = await intelligence.complete(body["messages"], max_output_tokens=body["max_tokens"])
        response = {
            "model": body["model"],
            "choices": [{"finish_reason": "stop", "message": {"content": reply.content}}],
            "usage": {
                "prompt_tokens": reply.input_tokens,
                "completion_tokens": reply.output_tokens,
            },
        }
        if change_response is not None:
            change_response(response)
        return httpx.Response(200, json=response)

    def constructor(**kwargs):
        state.constructed.append(kwargs.copy())
        return MetaModel(**kwargs, transport=httpx.MockTransport(meta_http))

    monkeypatch.setattr(runtime, "MetaModel", constructor)
    from polymath.literature import OpenLiteratureTools

    worker = build_native_research_worker(
        native.engine,
        research_policy=native.policy,
        readiness_policy=native.readiness_policy,
        operator_policy=native.operator_policy,
        tools_factory=lambda _: OpenLiteratureTools(
            transport=full_text_fixture_transport(state.source_requests)
        ),
    )
    return worker, state


def native_reservations(native, run_id):
    with Session(native.engine) as session:
        return session.scalars(
            select(BudgetReservation).where(BudgetReservation.run_id == run_id)
        ).all()


@pytest.mark.parametrize("mode", ["explore", "ask", "surprise"])
async def test_all_three_modes_use_actual_native_adapter_and_fixed_provider_clients(
    native, monkeypatch, mode
):
    run_id = enqueue_native(native, mode=mode)
    intelligence = ScientificHttpFixture()
    worker, calls = actual_worker(native, monkeypatch, intelligence)
    outcome = await worker.run_once("native-all-modes")
    assert outcome.status == "succeeded" and outcome.result_id
    saved = EvidenceStore(native.engine).get_result(run_id, uuid.UUID(outcome.result_id))
    assert saved.payload["status"] == ("answer" if mode == "ask" else "bridge")
    assert saved.payload["mode"] == mode
    assert saved.payload["quotes"]
    assert len(calls.requests) == intelligence.calls == len(native_reservations(native, run_id))
    assert all(
        row.status == "settled" and row.actual_microdollars == 40
        for row in native_reservations(native, run_id)
    )
    assert {request.url.host for request in calls.source_requests} == {
        "api.openalex.org",
        "export.arxiv.org",
    }
    with Session(native.engine) as session:
        assert session.get(NativeResearchRunBinding, run_id) is not None
        assert (
            session.get(BudgetRunLimit, (native.policy.native_account_id, run_id)).max_calls == 12
        )


async def test_native_full_text_windows_use_existing_exact_snapshot_and_quote_pipeline(
    native, monkeypatch
):
    run_id = enqueue_native(native)
    worker, calls = actual_worker(native, monkeypatch, FullTextFixtureModel())
    outcome = await worker.run_once("native-full-text")
    assert outcome.status == "succeeded" and outcome.result_id
    saved = EvidenceStore(native.engine).get_result(run_id, uuid.UUID(outcome.result_id))
    assert saved.payload["status"] == "bridge"
    assert len([source for source in saved.payload["sources"] if source.get("coverage")]) == 2
    assert any(request.url.host == "arxiv.org" for request in calls.source_requests)
    assert len(calls.requests) == 8


@pytest.mark.parametrize("kind", ["formalise", "write_summary", "write_paper"])
async def test_all_artifact_tasks_use_existing_controllers_with_native_model(
    native, monkeypatch, kind
):
    initial = enqueue_native(native)
    first, _ = actual_worker(native, monkeypatch, ScientificHttpFixture())
    result = await first.run_once("native-artifact-basis")
    assert result.status == "succeeded"
    run_id = enqueue_native(native, kind=kind, source_result_id=uuid.UUID(result.result_id))
    worker, calls = actual_worker(native, monkeypatch, ArtifactFixture())
    outcome = await worker.run_once("native-artifact")
    assert outcome.status == "succeeded" and outcome.artifact_id
    artifact = ArtifactStore(native.engine).get(run_id, uuid.UUID(outcome.artifact_id))
    assert artifact.status == "ready"
    assert artifact.kind == kind
    assert (
        len(calls.requests)
        == len(native_reservations(native, run_id))
        == (2 if kind == "formalise" else 1)
    )
    assert initial != run_id


async def test_native_reassessment_uses_owned_watch_and_existing_atomic_head_rules(
    native, monkeypatch
):
    first_run = enqueue_native(native)
    first, _ = actual_worker(native, monkeypatch, ScientificHttpFixture())
    result = await first.run_once("native-assessment-basis")
    assert result.status == "succeeded"
    source_result_id = uuid.UUID(result.result_id)
    ingestion = IngestionRepository(native.engine)
    watch = ingestion.create(
        native.owner_hash,
        label="Synthetic native watch",
        query="Synthetic fresh evidence",
        source_result_id=source_result_id,
    )
    tick = ingestion.run_now(native.owner_hash, watch.id)
    jobs = JobRepository(native.engine)
    lease = jobs.claim_next("free-watch-fixture", task_kinds=("ingest_literature",))
    assert lease.run_id == tick.run_id
    artifact = EvidenceStore(native.engine).save_source(
        lease,
        document("fixture:native-fresh", text="New synthetic evidence preserves this analogy."),
    )
    ingestion.commit_page(
        lease,
        "openalex",
        ingestion.snapshot(lease).cursors["openalex"],
        None,
        (artifact,),
        exhausted=True,
    )
    jobs.finish(lease, status="succeeded", outcome={"model_calls": 0})
    run_id = enqueue_native(
        native,
        kind="reassess_finding",
        source_result_id=source_result_id,
        watch_id=watch.id,
        source_version_ids=(artifact.version_id,),
    )
    worker, calls = actual_worker(native, monkeypatch, AssessmentFixtureModel())
    outcome = await worker.run_once("native-reassessment")
    assert outcome.status == "succeeded" and outcome.assessment_id
    assessment = AssessmentStore(native.engine).get(
        native.owner_hash, uuid.UUID(outcome.assessment_id)
    )
    assert assessment.applied and assessment.disposition == "unchanged"
    assert len(calls.requests) == 3 and len(native_reservations(native, run_id)) == 3
    assert first_run != run_id


@pytest.mark.parametrize("failure", ["model", "usage", "bound"])
async def test_unknown_or_overbound_usage_stops_the_entire_paid_loop(native, monkeypatch, failure):
    run_id = enqueue_native(native)

    def change(response):
        if failure == "model":
            response["model"] = "different-native-model"
        elif failure == "usage":
            response["usage"]["prompt_tokens"] = True
        else:
            response["usage"] = {"prompt_tokens": 400000, "completion_tokens": 5000}

    worker, calls = actual_worker(
        native, monkeypatch, ScientificHttpFixture(), change_response=change
    )
    outcome = await worker.run_once("native-unsafe-usage")
    assert outcome.status == "interrupted"
    assert len(calls.requests) == 1
    rows = native_reservations(native, run_id)
    assert len(rows) == 1 and rows[0].status == ("settled" if failure == "bound" else "unknown")
    assert rows[0].actual_microdollars == (410000 if failure == "bound" else None)
    assert not calls.source_requests


async def test_invalid_finish_is_billed_before_original_controller_failure(native, monkeypatch):
    run_id = enqueue_native(native)
    worker, calls = actual_worker(
        native,
        monkeypatch,
        ScientificHttpFixture(),
        change_response=lambda body: body["choices"][0].update(finish_reason="length"),
    )
    outcome = await worker.run_once("native-invalid-finish")
    assert outcome.status == "failed" and len(calls.requests) == 1
    rows = native_reservations(native, run_id)
    assert len(rows) == 1 and rows[0].status == "settled" and rows[0].actual_microdollars == 40
    with Session(native.engine) as session:
        journal = session.get(ResearchExternalRequest, (run_id, "model-0001"))
        assert journal.status == "observed" and journal.observation["finish_reason"] == "length"


async def test_disabled_native_composition_never_claims_constructs_model_or_touches_sources(
    native, monkeypatch
):
    from dataclasses import replace

    run_id = enqueue_native(native)
    monkeypatch.setattr(runtime, "MetaModel", lambda **_: pytest.fail("Disabled native model"))
    worker = build_native_research_worker(
        native.engine,
        research_policy=replace(native.policy, paid_research_enabled=False),
        readiness_policy=native.readiness_policy,
        operator_policy=native.operator_policy,
    )
    assert (await worker.run_once("disabled-native-research")).status == "disabled"
    assert JobRepository(native.engine).get(run_id).status == "queued"
    assert native_reservations(native, run_id) == []


async def test_two_original_findings_can_seed_higher_level_native_research(native, monkeypatch):
    parents = []
    for index in range(2):
        enqueue_native(native)
        worker, _ = actual_worker(native, monkeypatch, ScientificHttpFixture())
        outcome = await worker.run_once(f"native-parent-{index}")
        assert outcome.status == "succeeded"
        parents.append(uuid.UUID(outcome.result_id))
    run_id = enqueue_native(native, parents=tuple(parents))
    worker, calls = actual_worker(native, monkeypatch, ScientificHttpFixture())
    outcome = await worker.run_once("native-higher-level")
    assert outcome.status == "succeeded" and len(calls.requests) == 8
    saved = EvidenceStore(native.engine).get_result(run_id, uuid.UUID(outcome.result_id))
    assert {item["result_id"] for item in saved.payload["memory_reads"]} == set(map(str, parents))
    with Session(native.engine) as session:
        assert set(
            session.scalars(
                select(ResearchRunParent.parent_result_id).where(ResearchRunParent.run_id == run_id)
            )
        ) == set(parents)


async def test_known_step_replays_original_facts_after_revocation_without_key_or_factory(
    native,
):
    run_id = enqueue_native(native)
    lease = JobRepository(native.engine).claim_next(
        "native-known-replay", task_kinds=("research",), native_only=True
    )
    assert lease.run_id == run_id
    original = native.store.load_original_input(lease)
    profile = original_runtime_profile(original)
    context = _RunContext(native.engine, lease, 60)
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "model": original.proposal.model_id,
                "choices": [{"finish_reason": "stop", "message": {"content": ACTION}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 15},
            },
        )

    def factory(value, grant):
        return MetaModel(
            api_key=grant.api_key,
            model=grant.model_id,
            request_timeout_seconds=grant.request_timeout_seconds,
            live_enabled=True,
            transport=httpx.MockTransport(handler),
        )

    model = NativeRecordedModel(
        context,
        native.store,
        MetaResearchDispatcher(
            native.engine, native.policy, native.readiness_policy, native.operator_policy
        ),
        profile,
        factory,
    )
    messages = [{"role": "user", "content": "Synthetic immutable cached native request."}]
    reply = await model.complete(messages, max_output_tokens=4096)
    await asyncio.to_thread(native.store.revoke, native.request, native.activation.id)
    no_key_store = MetaResearchStore(native.engine)

    def forbidden_factory(*args):
        pytest.fail("Known replay constructed a model without current authority")

    replay = NativeRecordedModel(
        context, no_key_store, MetaResearchDispatcher(native.engine), profile, forbidden_factory
    )
    recovered = await replay.complete(messages, max_output_tokens=4096)
    assert recovered == reply and recovered.content == ACTION
    assert recovered.input_tokens == 10 and recovered.output_tokens == 15
    record = await asyncio.to_thread(no_key_store.finalize_known_call, run_id, "model-0001")
    assert record.to_response()["scientific_completion_implied"] is False
    assert len(requests) == 1 and len(native_reservations(native, run_id)) == 1
    assert native_reservations(native, run_id)[0].actual_microdollars == 40


async def test_malformed_known_action_uses_existing_bounded_repair_with_a_new_fresh_grant(
    native, monkeypatch
):
    class RepairFixture(ScientificHttpFixture):
        def __init__(self):
            super().__init__()
            self.first = True

        async def complete(self, messages, *, max_output_tokens):
            if self.first:
                self.first = False
                return ModelReply("synthetic malformed action JSON", 10, 15)
            return await super().complete(messages, max_output_tokens=max_output_tokens)

    run_id = enqueue_native(native)
    worker, calls = actual_worker(native, monkeypatch, RepairFixture())
    outcome = await worker.run_once("native-bounded-action-repair")
    assert outcome.status == "succeeded" and outcome.result_id
    assert len(calls.requests) == len(native_reservations(native, run_id)) == 7
    assert all(
        row.status == "settled" and row.actual_microdollars == 40
        for row in native_reservations(native, run_id)
    )
    saved = EvidenceStore(native.engine).get_result(run_id, uuid.UUID(outcome.result_id))
    assert any(item["kind"] == "action_rejected" for item in saved.payload["events"])
