"""Real PostgreSQL, synthetic-paper reassessment provenance and head-CAS tests."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session
from test_evidence import document
from test_memory import complete, owner
from test_worker import database as database_fixture

from polymath.assessment_eligibility import (
    EligibilityError,
    current_supported_condition,
    lock_current_source,
    require_current_supported,
    result_assessment_state,
)
from polymath.assessments import AssessmentError, AssessmentStore, bind_request
from polymath.evidence import EvidenceStore
from polymath.evidence_models import EvidenceResult
from polymath.ingestion import IngestionRepository
from polymath.jobs import JobRepository
from polymath.models import ResearchRun

pytestmark = pytest.mark.integration
database = database_fixture


def prepared(engine, *, identity="assessment-owner", new_count=1, new_documents=None):
    token = owner(engine, identity)
    prior, original = complete(engine, token)
    repository = IngestionRepository(engine)
    watch = repository.create(
        token,
        label="Synthetic reassessment watch",
        query="Synthetic evidence",
        source_result_id=prior.id,
    )
    tick = repository.run_now(token, watch.id)
    lease = JobRepository(engine).claim_next(
        "synthetic-ingest", lease_seconds=300, task_kinds=("ingest_literature",)
    )
    assert lease.run_id == tick.run_id
    if new_documents is None:
        new_documents = tuple(
            document(
                f"fixture:new:{identity}:{index}",
                text=f"🌱 New synthetic evidence version {index} limits the analogy.",
            )
            for index in range(new_count)
        )
    artifacts = tuple(EvidenceStore(engine).save_source(lease, doc) for doc in new_documents)
    repository.commit_page(
        lease,
        "openalex",
        repository.snapshot(lease).cursors["openalex"],
        None,
        artifacts,
        exhausted=True,
    )
    JobRepository(engine).finish(lease, status="succeeded", outcome={"model_calls": 0})
    return token, prior, original, watch, artifacts, new_documents


def enqueue(engine, token, source_id, watch_id, versions, *, revision=0):
    with Session(engine) as session, session.begin():
        source = session.get(EvidenceResult, source_id)
        parent = session.get(ResearchRun, source.run_id)
        run = ResearchRun(mode=parent.mode, task_kind="reassess_finding", goal=parent.goal)
        session.add(run)
        session.flush()
        from polymath.access_models import ResearchRunAccess

        session.add(ResearchRunAccess(run_id=run.id, session_hash=token))
        session.flush()
        bind_request(session, run, token, source_id, watch_id, versions, revision)
        run_id = run.id
    lease = JobRepository(engine).claim_next(
        "synthetic-assessment", lease_seconds=300, task_kinds=("reassess_finding",)
    )
    assert lease.run_id == run_id
    return lease


def test_legacy_supported_head_is_virtual_and_lazily_locked(database):
    token = owner(database)
    prior, _ = complete(database, token)
    with Session(database) as session, session.begin():
        state = result_assessment_state(session, prior.id)
        assert state["revision"] == 0 and state["is_supported"]
        assert state["root_result_id"] == str(prior.id)
        assert (
            session.scalar(select(EvidenceResult.id).where(current_supported_condition()))
            == prior.id
        )
        head = lock_current_source(session, prior.id)
        assert head.revision == 0 and head.current_result_id == prior.id
    assert AssessmentStore(database).head(token, prior.id)["can_generate"]
    with Session(database) as session:
        with pytest.raises(EligibilityError, match="finding_not_available"):
            require_current_supported(session, owner(database, "foreign"), prior.id)


def test_bind_owned_exact_new_versions_and_no_source_reads_by_context_alone(database):
    token, prior, _, watch, versions, _ = prepared(database)
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    basis = store.load_basis(lease)
    assert basis.prior_reference.result_id == str(prior.id)
    assert basis.prior_state == "supported"
    assert [ref.role for ref in basis.sources] == ["historical", "historical", "new"]
    assert EvidenceStore(database).list_sources(lease.run_id) == ()
    new = basis.sources[-1]
    actual = store.read_source(lease, new.retrieval_id)
    assert actual.content_sha256 == new.content_sha256
    assert EvidenceStore(database).list_sources(lease.run_id)[0].retrieval_id == uuid.UUID(
        new.retrieval_id
    )
    with pytest.raises(AssessmentError, match="assessment_source_not_found"):
        store.read_source(lease, uuid.uuid4())


def test_bind_rejects_foreign_watch_versions_and_stale_revision(database):
    token, prior, _, watch, versions, _ = prepared(database)
    with pytest.raises(AssessmentError, match="assessment_head_changed"):
        enqueue(database, token, prior.id, watch.id, [versions[0].version_id], revision=1)
    other, _, _, foreign_watch, foreign_versions, _ = prepared(database, identity="foreign")
    assert other != token
    with pytest.raises(AssessmentError, match="assessment_not_found"):
        enqueue(database, token, prior.id, foreign_watch.id, [foreign_versions[0].version_id])
    with pytest.raises(AssessmentError, match="assessment_not_found"):
        enqueue(database, token, prior.id, watch.id, [foreign_versions[0].version_id])


def assessment_result(engine, store, lease, *, disposition="unsupported", read=True):
    from polymath.assessment_contracts import (
        AssessmentCitation,
        AssessmentResult,
        AssessmentSourceSnapshot,
        AssessmentStatement,
        verify_assessment_quotes,
    )
    from polymath.research.models import Usage

    basis = store.load_basis(lease)
    observed = {}
    if read:
        for reference in basis.sources:
            observed[reference.retrieval_id] = AssessmentSourceSnapshot(
                retrieval_id=reference.retrieval_id,
                version_id=reference.version_id,
                document=store.read_source(lease, reference.retrieval_id),
            )
    statements = []
    for reference in basis.sources:
        if reference.retrieval_id not in observed:
            continue
        if reference.role == "historical":
            quote = next(
                quote.quote
                for quote in basis.prior_quotes
                if quote.source_id == reference.source_id
                and quote.source_sha256 == reference.content_sha256
            )
        else:
            quote = observed[reference.retrieval_id].document.text[2:]
        statements.append(
            AssessmentStatement(
                text="Synthetic comparison of the historical premise and selected new evidence.",
                citations=(
                    AssessmentCitation(
                        retrieval_id=reference.retrieval_id,
                        quote=quote,
                        supports="The synthetic comparison is version-bound, not scientific truth.",
                    ),
                ),
            )
        )
    return AssessmentResult(
        status="completed",
        disposition=disposition,
        rationale=tuple(statements),
        limitations=(
            "Only synthetic abstracts were inspected; scientific interpretation is unverified.",
        ),
        observed_retrieval_ids=tuple(observed),
        quotes=verify_assessment_quotes(tuple(statements), observed),
        usage=Usage(),
    )


def test_unsupported_is_atomic_historical_and_new_normal_use_is_denied(database):
    token, prior, _, watch, versions, _ = prepared(database)
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    result = assessment_result(database, store, lease)
    saved = store.save(lease, result)
    assert saved.applied and saved.disposition == "unsupported" and saved.head_revision == 1
    run = JobRepository(database).get(lease.run_id)
    assert run.status == "succeeded" and run.outcome["assessment_id"] == str(saved.id)
    assert run.lease_owner is None
    assert store.get(token, saved.id) == saved
    state = store.head(token, prior.id)
    assert state["state"] == "unsupported" and not state["can_generate"]
    assert state["is_current"] and state["is_historical"]
    assert state["current_result_id"] == str(prior.id)
    with Session(database) as session:
        with pytest.raises(EligibilityError):
            require_current_supported(session, prior.id)
        assert (
            session.scalar(select(EvidenceResult.id).where(current_supported_condition())) is None
        )
    history = store.history(token, prior.id)
    assert history == (saved,)
    assert len(saved.payload["verified_passages"]) == 3
    assert {item["retrieval_id"] for item in saved.payload["observed_sources"]} == set(
        result.observed_retrieval_ids
    )
    assert "text" not in saved.payload["observed_sources"][0]


def test_unchanged_cannot_restore_unsupported_and_consumed_versions_stay_consumed(database):
    token, prior, _, watch, versions, _ = prepared(database, new_count=2)
    store = AssessmentStore(database)
    first = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store.save(first, assessment_result(database, store, first))
    second = enqueue(database, token, prior.id, watch.id, [versions[1].version_id], revision=1)
    assert store.load_basis(second).prior_state == "unsupported"
    saved = store.save(second, assessment_result(database, store, second, disposition="unchanged"))
    assert saved.applied and saved.head_revision == 2
    assert store.head(token, prior.id)["state"] == "unsupported"
    with pytest.raises(AssessmentError, match="assessment_source_already_assessed"):
        enqueue(database, token, prior.id, watch.id, [versions[0].version_id], revision=2)


def test_inconclusive_and_failed_do_not_advance_or_consume_pending_versions(database):
    from polymath.assessment_contracts import AssessmentResult
    from polymath.research.models import Usage

    token, prior, _, watch, versions, _ = prepared(database)
    store = AssessmentStore(database)
    for status in ("failed", "limit_reached", "completed"):
        lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
        partial = assessment_result(database, store, lease, disposition="inconclusive")
        if status != "completed":
            partial = AssessmentResult(
                status=status,
                limitations=("Synthetic bounded stop.",),
                reason="Synthetic source coverage was incomplete.",
                usage=Usage(),
            )
        saved = store.save(lease, partial)
        assert not saved.applied and saved.head_revision == 0
        assert store.head(token, prior.id)["is_supported"]
        assert JobRepository(database).get(lease.run_id).status == (
            "failed" if status == "failed" else "succeeded"
        )
    assert len(store.history(token, prior.id)) == 3


def test_forged_missing_or_foreign_quoted_read_does_not_apply(database):
    token, prior, _, watch, versions, _ = prepared(database)
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    result = assessment_result(database, store, lease)
    tampered = result.model_copy(
        update={
            "quotes": (
                result.quotes[0].model_copy(update={"source_sha256": "0" * 64}),
                *result.quotes[1:],
            )
        }
    )
    with pytest.raises(AssessmentError, match="assessment_result_integrity_failed"):
        store.save(lease, tampered)
    missing = result.model_copy(
        update={"observed_retrieval_ids": result.observed_retrieval_ids[:-1]}
    )
    with pytest.raises(AssessmentError, match="assessment_result_integrity_failed"):
        store.save(lease, missing)
    assert store.head(token, prior.id)["revision"] == 0
    assert JobRepository(database).get(lease.run_id).status == "running"


def test_revision_compare_and_swap_keeps_losing_branch_audit(database):
    token, prior, _, watch, versions, _ = prepared(database)
    one = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    two = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    first = assessment_result(database, store, one)
    second = assessment_result(database, store, two, disposition="unchanged")
    applied = store.save(one, first)
    loser = store.save(two, second)
    assert applied.applied and not loser.applied
    assert loser.expected_head_revision == 0 and loser.head_revision == 1
    state = store.head(token, prior.id)
    assert state["last_assessment_id"] == str(applied.id) and state["state"] == "unsupported"
    assert len(store.history(token, prior.id)) == 2


def test_cancellation_fences_read_save_and_head_changes(database):
    from polymath.jobs import LeaseLost

    token, prior, _, watch, versions, _ = prepared(database)
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    result = assessment_result(database, store, lease)
    JobRepository(database).request_cancel(lease.run_id)
    with pytest.raises(LeaseLost):
        store.read_source(lease, store.head(token, prior.id)["current_result_id"])
    with pytest.raises(LeaseLost):
        store.save(lease, result)
    assert store.head(token, prior.id)["revision"] == 0


def revised_result(engine, store, lease):
    from sqlalchemy import update
    from test_evidence import result_for

    result = assessment_result(engine, store, lease, disposition="unchanged")
    basis = store.load_basis(lease)
    selected = (basis.sources[-1], basis.sources[1])
    candidate = result_for(*(store.read_source(lease, item.retrieval_id) for item in selected))
    candidate = candidate.model_copy(
        update={
            "memory_reads": (basis.prior_reference,),
            "field_selections": basis.field_selections,
        }
    )
    purposes = ("counterevidence", "prior_work")
    with engine.begin() as connection:
        connection.execute(
            update(ResearchRun)
            .where(ResearchRun.id == lease.run_id)
            .values(checkpoint={"assessment_search_purposes": list(purposes)})
        )
    return result.model_copy(
        update={
            "disposition": "revised",
            "revised_result": candidate,
            "selected_retrieval_ids": tuple(item.retrieval_id for item in selected),
            "search_purposes": purposes,
        }
    )


def test_revised_candidate_applies_atomically_and_supersedes_old_positive(database):
    token, prior, _, watch, versions, _ = prepared(database)
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    saved = store.save(lease, revised_result(database, store, lease))
    assert saved.applied and saved.candidate_result_id is not None
    state = store.head(token, saved.candidate_result_id)
    assert state["is_supported"] and state["root_result_id"] == str(prior.id)
    assert state["current_result_id"] == str(saved.candidate_result_id)
    assert not store.head(token, prior.id)["is_supported"]
    with Session(database) as session:
        assert session.scalar(select(EvidenceResult.id).where(current_supported_condition())) == (
            saved.candidate_result_id
        )
        require_current_supported(session, saved.candidate_result_id)
        with pytest.raises(EligibilityError):
            require_current_supported(session, prior.id)
    assert saved.payload["result"]["revised_result"]["sources"] == []
    assert (
        EvidenceStore(database)
        .get_result(lease.run_id, saved.candidate_result_id)
        .payload["sources"]
    )
    with pytest.raises(AssessmentError, match="assessment_source_not_current"):
        enqueue(database, token, prior.id, watch.id, [versions[0].version_id], revision=1)


def test_positive_candidate_saved_before_cas_is_ineligible_until_applied(database):
    token, prior, _, watch, versions, _ = prepared(database)
    one = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    two = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    first = assessment_result(database, store, one)
    replacement = revised_result(database, store, two)
    candidate = EvidenceStore(database).save_result(two, replacement.revised_result)
    with Session(database) as session:
        state = result_assessment_state(session, candidate.id)
        assert not state["is_current"] and not state["is_supported"]
        assert state["root_result_id"] == str(prior.id)
    store.save(one, first)
    loser = store.save(two, replacement)
    assert not loser.applied and loser.candidate_result_id == candidate.id
    assert not store.head(token, candidate.id)["is_supported"]
    assert store.head(token, prior.id)["state"] == "unsupported"


def test_unsupported_can_be_restored_only_by_new_validated_revised_candidate(database):
    token, prior, _, watch, versions, _ = prepared(database, new_count=2)
    store = AssessmentStore(database)
    first = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store.save(first, assessment_result(database, store, first))
    second = enqueue(database, token, prior.id, watch.id, [versions[1].version_id], revision=1)
    saved = store.save(second, revised_result(database, store, second))
    assert saved.applied and saved.head_revision == 2
    assert store.head(token, saved.candidate_result_id)["state"] == "supported"
    assert store.head(token, saved.candidate_result_id)["is_supported"]


def test_forged_successful_search_purpose_is_rejected_before_candidate_save(database):
    from sqlalchemy import update

    token, prior, _, watch, versions, _ = prepared(database)
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    result = revised_result(database, store, lease)
    with database.begin() as connection:
        connection.execute(
            update(ResearchRun).where(ResearchRun.id == lease.run_id).values(checkpoint={})
        )
    with pytest.raises(AssessmentError, match="assessment_search_provenance_mismatch"):
        store.save(lease, result)
    with Session(database) as session:
        assert (
            session.scalar(select(EvidenceResult.id).where(EvidenceResult.run_id == lease.run_id))
            is None
        )


def test_assessment_records_inputs_and_pinned_sources_are_database_immutable(database):
    from sqlalchemy import delete, update
    from sqlalchemy.exc import IntegrityError

    from polymath.assessment_models import (
        AssessmentInputSource,
        AssessmentRequest,
        FindingAssessment,
    )

    token, prior, _, watch, versions, _ = prepared(database)
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    store.save(lease, assessment_result(database, store, lease))
    for model, change in (
        (AssessmentRequest, {"source_sha256": "0" * 64}),
        (AssessmentInputSource, {"role": "historical"}),
        (FindingAssessment, {"applied": False}),
    ):
        for statement in (update(model).values(**change), delete(model)):
            with pytest.raises(IntegrityError):
                with database.begin() as connection:
                    connection.execute(statement)


def test_downgrade_refuses_assessment_history_and_schema_matches_models(database):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text
    from sqlalchemy.exc import DBAPIError

    token, prior, _, watch, versions, _ = prepared(database)
    enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with database.connect() as connection:
        before = connection.scalar(text("SELECT version_num FROM alembic_version"))
    with pytest.raises(DBAPIError):
        command.downgrade(config, "0015_ingestion_watches")
    with database.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == before
    command.check(config)


def test_historical_and_new_same_paper_versions_use_distinct_retrieval_handles(database):
    revised_document = document(
        "fixture:left", text="🌌 Updated synthetic evidence limits the old analogy."
    )
    token, prior, _, watch, versions, _ = prepared(database, new_documents=(revised_document,))
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    basis = store.load_basis(lease)
    same_paper = [ref for ref in basis.sources if ref.source_id == "fixture:left"]
    assert len(same_paper) == 2
    assert same_paper[0].retrieval_id != same_paper[1].retrieval_id
    assert same_paper[0].content_sha256 != same_paper[1].content_sha256
    saved = store.save(lease, revised_result(database, store, lease))
    assert saved.applied
    candidate = EvidenceStore(database).get_result(lease.run_id, saved.candidate_result_id)
    assert [doc["source_id"] for doc in candidate.payload["sources"]] == [
        "fixture:left",
        "fixture:right",
    ]
    assert candidate.payload["sources"][0]["text"] == revised_document.text
    assert (
        len(
            [
                item
                for item in saved.payload["observed_sources"]
                if item["source_id"] == "fixture:left"
            ]
        )
        == 2
    )


def test_original_quoted_version_is_not_new_reassessment_input(database):
    token, prior, _, watch, versions, _ = prepared(
        database, new_documents=(document("fixture:left"),)
    )
    with pytest.raises(AssessmentError, match="assessment_source_already_cited"):
        enqueue(database, token, prior.id, watch.id, [versions[0].version_id])


def test_saved_assessment_head_and_history_are_owned(database):
    token, prior, _, watch, versions, _ = prepared(database)
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    saved = store.save(lease, assessment_result(database, store, lease))
    foreign = owner(database, "foreign-assessment-reader")
    for action in (
        lambda: store.get(foreign, saved.id),
        lambda: store.head(foreign, prior.id),
        lambda: store.history(foreign, prior.id),
        lambda: store.get(token, uuid.uuid4()),
    ):
        with pytest.raises(AssessmentError, match="assessment_not_found"):
            action()


def test_expired_browser_after_head_lock_wait_cannot_apply(database):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import event, func, update

    from polymath.access_models import BrowserSession
    from polymath.assessment_models import FindingHead

    token, prior, _, watch, versions, _ = prepared(database)
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    result = assessment_result(database, store, lease)
    with Session(database) as session, session.begin():
        lock_current_source(session, prior.id)
    waiting = Event()

    def observe_lock(connection, cursor, statement, parameters, context, executemany):
        if "SELECT finding_heads" in statement and "FOR UPDATE" in statement:
            waiting.set()

    event.listen(database, "before_cursor_execute", observe_lock)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with database.begin() as blocker:
                blocker.execute(
                    select(FindingHead)
                    .where(FindingHead.root_result_id == prior.id)
                    .with_for_update()
                )
                # The initial blocker query also fires the event; clear before worker.
                waiting.clear()
                future = pool.submit(store.save, lease, result)
                assert waiting.wait(timeout=10)
                with database.begin() as connection:
                    connection.execute(
                        update(BrowserSession)
                        .where(BrowserSession.token_hash == token)
                        .values(expires_at=func.clock_timestamp())
                    )
            with pytest.raises(AssessmentError, match="assessment_not_found"):
                future.result(timeout=10)
    finally:
        event.remove(database, "before_cursor_execute", observe_lock)
    with Session(database) as session:
        head = session.get(FindingHead, prior.id)
        assert head.revision == 0 and head.state == "supported"


def test_positive_research_cannot_save_after_a_read_premise_becomes_unsupported(database):
    from test_memory import current

    from polymath.evidence import EvidenceError
    from polymath.research.models import (
        InsufficientFinding,
        MemoryReadReference,
        ResearchResult,
        Usage,
    )

    token, prior, original, watch, versions, _ = prepared(database)
    memory, reader = current(database, token)
    finding = memory.read_finding(str(prior.id))
    for source in finding.sources:
        document = memory.read_source(str(prior.id), source.retrieval_id)
        EvidenceStore(database).save_source(reader, document)
    reference = MemoryReadReference(
        result_id=str(prior.id),
        run_id=str(prior.run_id),
        content_sha256=finding.reference.content_sha256,
    )
    proposal = original.model_copy(update={"memory_reads": (reference,)})
    assessment = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store = AssessmentStore(database)
    store.save(assessment, assessment_result(database, store, assessment))
    evidence = EvidenceStore(database)
    with pytest.raises(EvidenceError, match="memory_read_provenance_mismatch"):
        evidence.save_result(reader, proposal)
    # A bounded/empty audit still records which immutable historical finding was read.
    stopped = ResearchResult(
        status="insufficient_evidence",
        finding=InsufficientFinding(
            outcome="insufficient_evidence", reason="The saved premise is no longer current."
        ),
        memory_reads=(reference,),
        usage=Usage(),
    )
    saved = evidence.save_result(reader, stopped)
    assert saved.status == "insufficient_evidence"
    assert evidence.get_result(prior.run_id, prior.id).payload["finding"] == (
        original.finding.model_dump(mode="json")
    )
    assert evidence.list_sources(reader.run_id)


def test_pending_version_status_changes_only_for_an_applied_complete_assessment(database):
    from polymath.assessment_eligibility import assessment_version_status

    token, prior, _, watch, versions, _ = prepared(database)
    store = AssessmentStore(database)
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    basis = store.load_basis(lease)
    with Session(database) as session:
        assert (
            assessment_version_status(session, prior.id, basis.sources[0].version_id)
            == "already_cited"
        )
        assert assessment_version_status(session, prior.id, versions[0].version_id) == "unassessed"
    saved = store.save(lease, assessment_result(database, store, lease, disposition="inconclusive"))
    assert not saved.applied
    with Session(database) as session:
        assert assessment_version_status(session, prior.id, versions[0].version_id) == "unassessed"
    next_lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    store.save(next_lease, assessment_result(database, store, next_lease))
    with Session(database) as session:
        assert assessment_version_status(session, prior.id, versions[0].version_id) == "assessed"
