"""Real isolated PostgreSQL; synthetic science, no external HTTP or paid models."""

import uuid
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, update
from sqlalchemy.orm import Session
from test_artifact_api import artifacts_api as artifacts_api_fixture
from test_artifact_api import complete_artifact
from test_assessments import assessment_result, enqueue, prepared, revised_result
from test_discovery_controller import DiscoveryFixtureModel
from test_discovery_worker import commits, queued, worker
from test_evidence import document, result_for
from test_knowledge import seed
from test_memory import assign, complete, owner
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.assessment_models import AssessmentRequest, FindingAssessment
from polymath.assessments import AssessmentStore
from polymath.discovery_models import StageCommit, StageParent
from polymath.evidence import EvidenceStore, _canonical, _hash
from polymath.evidence_models import EvidenceQuote, EvidenceResult, SourceRetrieval, SourceVersion
from polymath.ingestion import IngestionRepository
from polymath.jobs import JobRepository
from polymath.lineage import bind_parents
from polymath.memory import ResearchMemory
from polymath.models import ResearchRun
from polymath.scientific_context_builder import (
    ScientificContextError,
    build_artifact_scientific_context,
    validated_owned_result_packet,
)
from polymath.scientific_context_contracts import (
    bind_scientific_context,
    scientific_context_sha256,
)

database = database_fixture
artifacts_api = artifacts_api_fixture
pytestmark = pytest.mark.integration


def build(engine, token, result_id, **kwargs):
    with Session(engine) as session:
        return build_artifact_scientific_context(
            session, owner_hash=token, source_result_id=result_id, **kwargs
        )


def watch_new(engine, token, source_id, *, suffix="new"):
    repository = IngestionRepository(engine)
    watch = repository.create(
        token,
        label="Synthetic context provenance",
        query="Synthetic evidence",
        source_result_id=source_id,
    )
    tick = repository.run_now(token, watch.id)
    jobs = JobRepository(engine)
    lease = jobs.claim_next(
        "context-ingestion", lease_seconds=60, task_kinds=("ingest_literature",)
    )
    assert lease.run_id == tick.run_id
    artifact = EvidenceStore(engine).save_source(
        lease,
        document(
            f"fixture:context:{suffix}", text="🌱 New saved evidence limits a synthetic premise."
        ),
    )
    repository.commit_page(
        lease,
        "openalex",
        repository.snapshot(lease).cursors["openalex"],
        None,
        (artifact,),
        exhausted=True,
    )
    jobs.finish(lease, status="succeeded", outcome={"model_calls": 0})
    return watch, artifact


def f3_child(engine, token, parents):
    jobs, evidence = JobRepository(engine), EvidenceStore(engine)
    run = jobs.create(mode="explore", goal="Synthetic higher bridge")
    assign(engine, run.id, token)
    with Session(engine) as session, session.begin():
        bind_parents(session, session.get(ResearchRun, run.id), token, [str(p.id) for p in parents])
    lease = jobs.claim_next("context-parent-child", lease_seconds=60)
    memory = ResearchMemory(engine, lease)
    reads = tuple(memory.read_finding(str(parent.id)) for parent in parents)
    left = memory.read_source(str(parents[0].id), reads[0].sources[0].retrieval_id)
    right = memory.read_source(str(parents[1].id), reads[1].sources[1].retrieval_id)
    # MemoryFindingReference has additional display fields. Retain only the
    # actual immutable provenance type when constructing scientific output.
    from polymath.research.models import MemoryReadReference

    result = result_for(left, right).model_copy(
        update={
            "memory_reads": tuple(
                MemoryReadReference(
                    result_id=str(p.id),
                    run_id=str(p.run_id),
                    content_sha256=reads[i].reference.content_sha256,
                )
                for i, p in enumerate(parents)
            )
        }
    )
    for source in result.sources:
        evidence.save_source(lease, source)
    saved = evidence.save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    return saved


def test_legacy_context_is_owned_deterministic_read_only_and_not_a_read_receipt(database):
    token = owner(database)
    saved, original = complete(database, token, extra=True)
    unquoted = next(
        artifact
        for artifact in EvidenceStore(database).list_sources(saved.run_id)
        if artifact.source_id == "fixture:not-quoted"
    )
    statements, body_queries = [], []

    def capture(_, __, statement, parameters, *args):
        statements.append(statement)
        if "evidence_source_versions.source_text" in statement:
            body_queries.append(parameters)

    event.listen(database, "before_cursor_execute", capture)
    try:
        with Session(database) as session:
            pending = ResearchRun(mode="explore", goal="Unrelated caller change; do not flush")
            session.add(pending)
            context = build_artifact_scientific_context(
                session, owner_hash=token, source_result_id=saved.id
            )
            assert tuple(session.new) == (pending,) and pending.id is None
            assert not session.dirty and not session.deleted
    finally:
        event.remove(database, "before_cursor_execute", capture)
    assert statements and all(
        statement.lstrip().upper().startswith("SELECT") for statement in statements
    )
    assert all(
        "budget_" not in statement and "research_events" not in statement
        for statement in statements
    )
    assert len(body_queries) == 2
    assert all(str(unquoted.retrieval_id) not in str(parameters) for parameters in body_queries)
    assert context.ancestry_nodes[0].finding == original.finding
    assert len(context.ancestry_nodes) == 1 and context.ancestry_edges == ()
    assert context.stage_origin is None and context.source_revision.revision == 0
    assert build(database, token, saved.id) == context
    assert bind_scientific_context(context).context_sha256 == scientific_context_sha256(context)
    encoded = context.model_dump_json()
    assert (
        "fixture:not-quoted" not in encoded
        and "model_calls" not in encoded
        and "events" not in encoded
    )


@pytest.mark.parametrize("which", ["foreign", "missing", "unsupported", "losing_candidate"])
def test_source_must_be_owned_current_supported_and_actually_applied(database, which):
    token, prior, _, watch, versions, _ = prepared(database)
    target = prior.id
    if which == "foreign":
        token = owner(database, "foreign-context-owner")
    elif which == "missing":
        target = uuid.uuid4()
    else:
        first = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
        store = AssessmentStore(database)
        if which == "unsupported":
            store.save(first, assessment_result(database, store, first))
        else:
            second = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
            candidate = revised_result(database, store, second)
            store.save(first, assessment_result(database, store, first, disposition="unchanged"))
            loser = store.save(second, candidate)
            assert not loser.applied
            target = loser.candidate_result_id
    with pytest.raises(ScientificContextError, match="artifact_context_"):
        build(database, token, target)


def test_applied_unchanged_chain_included_but_failed_cas_rationale_never_leaks(
    database, monkeypatch
):
    token, prior, _, watch, versions, _ = prepared(database)
    first = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    second = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    one = assessment_result(database, store, first, disposition="unchanged")
    two = assessment_result(database, store, second, disposition="unchanged")
    two = two.model_copy(update={"limitations": ("CAS_LOSER_PRIVATE_RATIONALE",)})
    accepted, rejected = store.save(first, one), store.save(second, two)
    assert accepted.applied and not rejected.applied
    context = build(database, token, prior.id)
    assert context.source_revision.revision == 1
    projected = context.source_revision.applied_assessments
    assert [item.assessment_id for item in projected] == [str(accepted.id)]
    assert projected[0].disposition == "unchanged" and projected[0].quotes
    assert "CAS_LOSER_PRIVATE_RATIONALE" not in context.model_dump_json()
    assert len(context.ancestry_nodes) == 1
    import polymath.scientific_context_builder as builder_module

    monkeypatch.setattr(builder_module, "MAX_APPLIED_ASSESSMENTS", 0)
    with pytest.raises(ScientificContextError, match="artifact_context_too_large"):
        build(database, token, prior.id)


async def test_stage_specific_graph_excludes_other_stages_and_binds_original_score(
    database, monkeypatch
):
    token, run_id = queued(database)
    result = await worker(database, DiscoveryFixtureModel()).run_once("scientific-context-ladder")
    assert result.status == "succeeded"
    stages = commits(database, run_id)
    first = build(database, token, stages[0].result_id)
    assert len(first.ancestry_nodes) == 1 and not first.ancestry_edges
    assert stages[1].result_id.hex not in first.model_dump_json()
    top = build(database, token, stages[2].result_id)
    assert [item.reference.result_id for item in top.ancestry_nodes] == [
        str(stages[i].result_id) for i in (2, 0, 1)
    ]
    assert [edge.parent.result_id for edge in top.ancestry_edges] == [
        str(stage.result_id) for stage in stages[:2]
    ]
    assert all(edge.kind == "premise" for edge in top.ancestry_edges)
    assert top.stage_origin.relationship == "exact_stage_result"
    assert top.stage_origin.receipt.assessment.confidence_score == 23
    assert top.stage_origin.receipt.reference == top.source_reference
    import polymath.scientific_context_builder as builder_module

    # Every admission cap rejects the entire graph, never clips provenance.
    for field, limit in (
        ("MAX_CONTEXT_NODES", 2),
        ("MAX_CONTEXT_EDGES", 1),
        ("MAX_CONTEXT_BYTES", 100),
    ):
        with monkeypatch.context() as patch:
            patch.setattr(builder_module, field, limit)
            with pytest.raises(ScientificContextError, match="artifact_context_too_large"):
                build(database, token, stages[2].result_id)


async def test_revised_stage_retains_old_confidence_as_historical_not_own_score(database):
    token, run_id = queued(database)
    assert (
        await worker(database, DiscoveryFixtureModel(stop_after_stage=1)).run_once(
            "context-stage-origin"
        )
    ).status == "succeeded"
    stage = commits(database, run_id)[0]
    watch, version = watch_new(database, token, stage.result_id)
    lease = enqueue(database, token, stage.result_id, watch.id, [version.version_id])
    store = AssessmentStore(database)
    proposal = revised_result(database, store, lease)
    # Existing helper creates an Explore candidate; retain the actual pinned
    # Surprise fields/mode so the ordinary independent source validator applies.
    candidate = proposal.revised_result.model_copy(update={"mode": "surprise"})
    candidate = candidate.model_copy(
        update={
            "finding": candidate.finding.model_copy(
                update={"field_left": "Information theory", "field_right": "Biology"}
            )
        }
    )
    saved = store.save(lease, proposal.model_copy(update={"revised_result": candidate}))
    assert saved.applied
    context = build(database, token, saved.candidate_result_id)
    assert context.stage_origin.relationship == "reassessed_descendant"
    assert context.stage_origin.receipt.result_id == str(stage.result_id)
    assert context.stage_origin.receipt.reference != context.source_reference
    assert context.ancestry_nodes[0].stage_origin is None
    assert context.ancestry_nodes[1].stage_origin.relationship == "exact_stage_result"
    assert context.ancestry_edges[0].kind == "reassessment"
    assert context.ancestry_edges[0].assessment_id == str(saved.id)
    assert (
        context.source_revision.applied_assessments[0].candidate_reference
        == context.source_reference
    )


@pytest.mark.parametrize("final_status", ["interrupted", "cancelled"])
async def test_partial_committed_stage_retains_historical_and_current_context_eligibility(
    database, final_status
):
    token, run_id = queued(database)
    outcome = await worker(database, DiscoveryFixtureModel(), calls=7).run_once(
        "context-partial-stage"
    )
    assert outcome.status == "interrupted"
    if final_status == "cancelled":
        JobRepository(database).request_cancel(run_id)
    assert JobRepository(database).get(run_id).status == final_status
    stage = commits(database, run_id)[0]
    context = build(database, token, stage.result_id)
    assert context.stage_origin.receipt.result_id == str(stage.result_id)
    assert len(context.ancestry_nodes) == 1
    with Session(database) as session:
        packet = validated_owned_result_packet(session, owner_hash=token, result_id=stage.result_id)
    assert packet.status == "bridge"


def test_owned_historical_packet_ignores_expiry_and_support_without_granting_fresh_use(database):
    token, prior, original, watch, versions, _ = prepared(database)
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    assert store.save(lease, assessment_result(database, store, lease)).applied
    with database.begin() as connection:
        connection.execute(
            update(BrowserSession)
            .where(BrowserSession.token_hash == token)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
    with Session(database) as session:
        packet = validated_owned_result_packet(session, owner_hash=token, result_id=prior.id)
        assert packet.finding == original.finding and packet.quotes == original.quotes
        with pytest.raises(ScientificContextError):
            build_artifact_scientific_context(session, owner_hash=token, source_result_id=prior.id)
    with Session(database) as session, pytest.raises(ScientificContextError):
        validated_owned_result_packet(session, owner_hash="f" * 64, result_id=prior.id)


def test_f3_ancestry_keeps_unsupported_parent_as_historical_hypothesis(database):
    token = owner(database)
    parents = [complete(database, token, suffix=suffix)[0] for suffix in ("first", "second")]
    child = f3_child(database, token, parents)
    watch, version = watch_new(database, token, parents[0].id)
    lease = enqueue(database, token, parents[0].id, watch.id, [version.version_id])
    store = AssessmentStore(database)
    assert store.save(lease, assessment_result(database, store, lease)).applied
    context = build(database, token, child.id)
    assert [item.reference.result_id for item in context.ancestry_nodes] == [
        str(child.id),
        *(str(parent.id) for parent in parents),
    ]
    assert [edge.kind for edge in context.ancestry_edges] == ["premise", "premise"]
    assert store.head(token, parents[0].id)["state"] == "unsupported"


@pytest.mark.parametrize(
    "corruption",
    [
        "result_hash",
        "quote_row",
        "assessment_payload",
        "foreign_ancestor",
        "source_text",
        "source_metadata",
        "observed_metadata",
    ],
)
def test_corrupt_owned_immutable_bindings_fail_closed_without_writes(database, corruption):
    token, prior, _, watch, versions, _ = prepared(database)
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    accepted = store.save(lease, revised_result(database, store, lease))
    with Session(database) as session:
        if corruption == "result_hash":
            session.get(EvidenceResult, accepted.candidate_result_id).content_sha256 = "0" * 64
        elif corruption == "quote_row":
            session.get(
                EvidenceQuote, (accepted.candidate_result_id, 0)
            ).quote = "Incorrect stored quotation."
        elif corruption == "assessment_payload":
            record = session.get(FindingAssessment, accepted.id)
            record.payload = {**record.payload, "verified_passages": []}
            record.payload_sha256 = _hash(_canonical(record.payload))
        elif corruption == "foreign_ancestor":
            foreign, _ = complete(database, owner(database, "foreign-ancestor"))
            request = session.get(AssessmentRequest, lease.run_id)
            request.source_result_id = foreign.id
        elif corruption in ("source_text", "source_metadata"):
            quote = session.get(EvidenceQuote, (accepted.candidate_result_id, 0))
            if corruption == "source_text":
                session.get(
                    SourceVersion, quote.version_id
                ).source_text = "A different immutable source body."
            else:
                session.get(
                    SourceRetrieval, quote.retrieval_id
                ).title = "Unexpected retrieval metadata"
        else:
            record = session.get(FindingAssessment, accepted.id)
            record.payload = deepcopy(record.payload)
            record.payload["observed_sources"][0]["title"] = "Unexpected saved observation metadata"
            record.payload_sha256 = _hash(_canonical(record.payload))
        with pytest.raises(ScientificContextError, match="artifact_context_"):
            build_artifact_scientific_context(
                session, owner_hash=token, source_result_id=accepted.candidate_result_id
            )
        # Deliberate in-memory corruption was never flushed. The DB remains
        # unchanged and a clean read still succeeds.
    assert build(database, token, accepted.candidate_result_id)


async def test_stage_parent_hash_and_cycle_checks_do_not_use_run_union(database):
    token, run_id = queued(database)
    assert (
        await worker(database, DiscoveryFixtureModel()).run_once("context-cycle-fixture")
    ).status == "succeeded"
    stages = commits(database, run_id)
    with Session(database) as session:
        parent = session.get(StageParent, (stages[2].id, 0))
        parent.parent_sha256 = "0" * 64
        with pytest.raises(ScientificContextError):
            build_artifact_scientific_context(
                session, owner_hash=token, source_result_id=stages[2].result_id
            )
    with Session(database) as session:
        stage = session.get(StageCommit, stages[2].id)
        stage.receipt = deepcopy(stage.receipt)
        stage.receipt["parent_refs"][0] = {
            "result_id": str(stages[2].result_id),
            "run_id": str(run_id),
            "content_sha256": stages[2].content_sha256,
        }
        stage.receipt_sha256 = _hash(_canonical(stage.receipt))
        with pytest.raises(ScientificContextError):
            build_artifact_scientific_context(
                session, owner_hash=token, source_result_id=stages[2].result_id
            )


def test_prior_formalisation_pins_exact_saved_hashes_and_never_accepts_other_source(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, source_id = seed(engine, client)
    _, artifact = complete_artifact(engine, client, source_id, kind="formalise")
    from polymath.run_api import COOKIE_NAME, _hash_token

    token = _hash_token(client.cookies.get(COOKIE_NAME))
    context = build(engine, token, source_id, formalisation_id=artifact.id)
    prior = context.prior_formalisation
    assert prior.artifact_id == str(artifact.id)
    assert (
        prior.payload_sha256 == artifact.payload_sha256
        and prior.basis_sha256 == artifact.basis_sha256
    )
    assert prior.context_sha256 is None
    other, _ = complete(engine, token, suffix="different-source")
    with pytest.raises(ScientificContextError):
        build(engine, token, other.id, formalisation_id=artifact.id)


def test_later_unchanged_assessment_preserves_original_formalisation_context(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, source_id = seed(engine, client)
    intent = {"protocol": "polymath-artifact-scientific-context-v1"}
    _, prior = complete_artifact(
        engine, client, source_id, kind="formalise", scientific_context_intent=intent
    )
    from polymath.run_api import COOKIE_NAME, _hash_token

    token = _hash_token(client.cookies.get(COOKIE_NAME))
    original_context = prior.basis["scientific_context"]
    assert original_context["context"]["source_revision"]["revision"] == 0
    watch, version = watch_new(engine, token, source_id)
    lease = enqueue(engine, token, source_id, watch.id, [version.version_id])
    store = AssessmentStore(engine)
    saved = store.save(lease, assessment_result(engine, store, lease, disposition="unchanged"))
    assert saved.applied
    context = build(engine, token, source_id, formalisation_id=prior.id)
    assert context.source_revision.revision == 1
    assert context.source_revision.applied_assessments[0].disposition == "unchanged"
    assert context.prior_formalisation.context_sha256 == original_context["context_sha256"]
    _, written = complete_artifact(
        engine,
        client,
        source_id,
        formalisation_id=str(prior.id),
        scientific_context_intent=intent,
    )
    current_context = written.basis["scientific_context"]
    assert current_context["context"]["source_revision"]["revision"] == 1
    assert current_context["context_sha256"] != original_context["context_sha256"]
    assert original_context["context_sha256"] in written.markdown
    assert current_context["context_sha256"] in written.markdown
    assert "original context is distinct" in written.markdown
