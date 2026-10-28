"""Real-PG scientific-context admission, immutable replay and legacy compatibility."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from test_answer_persistence import QUESTION, answer_result
from test_memory import complete, owner
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.artifacts import bind_request
from polymath.assessment_models import AssessmentRequest, FindingHead
from polymath.budget_models import BudgetReservation
from polymath.discovery_contracts import DiscoveryIntent
from polymath.discovery_models import DiscoveryIntent as DiscoveryIntentRow
from polymath.evidence import EvidenceStore
from polymath.evidence_models import EvidenceResult
from polymath.ingestion import IngestionRepository
from polymath.jobs import JobRepository
from polymath.models import ResearchRun
from polymath.research.models import MemoryReadReference
from polymath.scientific_context_contracts import (
    ArtifactScientificContextIntent,
    AskOverviewBinding,
    AskOverviewIntent,
)
from polymath.scientific_context_models import (
    SIDECAR_MODELS,
    ArtifactContextBinding,
    AskOverviewIntentRow,
    AskReassessmentOverviewIntentRow,
)
from polymath.scientific_context_store import (
    AskReassessmentOverviewIntent,
    ContextStoreError,
    bind_artifact_context,
    bind_ask_overview_intent,
    bind_assessment_overview_intent,
    canonical,
    digest,
    load_ask_overview_intent,
    load_assessment_overview_intent,
    load_pinned_artifact_context,
    load_scientific_intent,
    validate_result_overview_for_run,
)

database = database_fixture
pytestmark = pytest.mark.integration


def run(session, token, *, mode="ask", task_kind="research"):
    value = ResearchRun(
        mode=mode, task_kind=task_kind, goal=QUESTION if mode == "ask" else "Fixture"
    )
    session.add(value)
    session.flush()
    session.add(ResearchRunAccess(run_id=value.id, session_hash=token))
    session.flush()
    return value


def artifact(session, token, source):
    parent = session.get(ResearchRun, source.run_id)
    value = run(session, token, mode=parent.mode, task_kind="write_summary")
    bind_request(session, value, token, value.task_kind, str(source.id))
    return value


def bound_answer(engine, token):
    jobs = JobRepository(engine)
    value = jobs.create(mode="ask", goal=QUESTION)
    with Session(engine) as session, session.begin():
        session.add(ResearchRunAccess(run_id=value.id, session_hash=token))
        session.flush()
        bind_ask_overview_intent(
            session,
            run=session.get(ResearchRun, value.id),
            owner_hash=token,
            intent=AskOverviewIntent(),
        )
    lease = jobs.claim_next("synthetic-bound-answer", task_kinds=("research",))
    result = answer_result()
    binding = AskOverviewBinding(claim_ids=(result.finding.claims[0].id,))
    result = result.model_copy(
        update={
            "finding": result.finding.model_copy(update={"summary": result.finding.claims[0].text}),
            "overview_binding": binding,
        }
    )
    store = EvidenceStore(engine)
    for document in result.sources:
        store.save_source(lease, document)
    saved = store.save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    return saved.id, binding.model_dump(mode="json")


def assessment(
    session,
    token,
    source_id,
    watch_id,
    *,
    overview=None,
    include_overview=True,
    forged_finding=False,
):
    source = session.get(EvidenceResult, source_id)
    value = run(session, token, mode="ask", task_kind="reassess_finding")
    raw = source.payload["result"]
    basis = {
        "prior_reference": MemoryReadReference(
            result_id=str(source.id),
            run_id=str(source.run_id),
            content_sha256=source.content_sha256,
        ).model_dump(mode="json"),
        "mode": raw["mode"],
        "prior_finding": deepcopy(raw["finding"]),
        "prior_quotes": deepcopy(raw["quotes"]),
        "field_selections": deepcopy(raw["field_selections"]),
    }
    if forged_finding:
        basis["prior_finding"]["summary"] = "A forged but self-consistently hashed summary."
    if overview is not None and include_overview:
        basis["prior_overview_binding"] = overview
    # This deliberately minimal basis fixture tests the intent/basis FK seam.
    # Full AssessmentBasis/source-input validation remains the core caller's
    # responsibility and is not represented as implemented by this prototype.
    row = AssessmentRequest(
        run_id=value.id,
        root_result_id=source.id,
        source_result_id=source.id,
        source_sha256=source.content_sha256,
        watch_id=watch_id,
        expected_head_revision=0,
        basis=basis,
        basis_sha256=digest(canonical(basis)),
    )
    session.add(row)
    session.flush()
    return value


def test_legacy_reads_omit_all_metadata_and_leave_saved_hashes_and_virtual_head_unchanged(
    database,
):
    engine = database
    token = owner(engine)
    source, _ = complete(engine, token)
    with Session(engine) as session, session.begin():
        value = artifact(session, token, source)
        old = deepcopy(session.get(EvidenceResult, source.id).payload)
        assert load_pinned_artifact_context(session, owner_hash=token, run_id=value.id) is None
        assert load_ask_overview_intent(session, owner_hash=token, run_id=value.id) is None
        assert load_assessment_overview_intent(session, owner_hash=token, run_id=value.id) is None
        assert session.get(EvidenceResult, source.id).payload == old
        assert session.scalar(select(func.count()).select_from(FindingHead)) == 0
        assert all(
            session.scalar(select(func.count()).select_from(cls)) == 0 for cls in SIDECAR_MODELS
        )


def test_context_pins_once_and_replay_reads_immutable_bytes_without_current_head_rebuild(
    database, monkeypatch
):
    engine = database
    token = owner(engine)
    source, _ = complete(engine, token)
    with Session(engine) as session, session.begin():
        value = artifact(session, token, source)
        pinned = bind_artifact_context(
            session, run=value, owner_hash=token, intent=ArtifactScientificContextIntent()
        )
        identifier = value.id
    with Session(engine) as session, session.begin():
        session.add(
            FindingHead(
                root_result_id=source.id,
                current_result_id=source.id,
                revision=0,
                state="unsupported",
            )
        )

    def forbidden(*args, **kwargs):
        pytest.fail("Replay may not reconstruct current scientific context")

    import polymath.scientific_context_builder as builder

    monkeypatch.setattr(builder, "build_artifact_scientific_context", forbidden)
    with Session(engine) as session:
        actual = load_pinned_artifact_context(session, owner_hash=token, run_id=identifier)
        assert actual.model_dump_json() == pinned.model_dump_json()
        assert (
            bind_artifact_context(
                session,
                run=session.get(ResearchRun, identifier),
                owner_hash=token,
                intent=ArtifactScientificContextIntent(),
            )
            == pinned
        )


def test_context_source_finding_is_independently_checked_and_failed_admission_rolls_back(
    database,
):
    engine = database
    token = owner(engine)
    source, _ = complete(engine, token)
    from polymath.scientific_context_builder import build_artifact_scientific_context

    def forged(*args, **kwargs):
        context = build_artifact_scientific_context(*args, **kwargs)
        node = context.ancestry_nodes[0]
        changed = node.model_copy(
            update={"finding": node.finding.model_copy(update={"title": "Forged"})}
        )
        return context.model_copy(update={"ancestry_nodes": (changed,)})

    with Session(engine) as session:
        before = session.scalar(select(func.count()).select_from(ResearchRun))
    with pytest.raises(ContextStoreError, match="scientific_context_integrity_failed"):
        with Session(engine) as session, session.begin():
            value = artifact(session, token, source)
            bind_artifact_context(
                session,
                run=value,
                owner_hash=token,
                intent=ArtifactScientificContextIntent(),
                builder=forged,
            )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == before
        assert session.scalar(select(func.count()).select_from(ArtifactContextBinding)) == 0


def test_pinned_context_rechecks_actual_original_source_ownership_without_current_head_read(
    database,
):
    engine = database
    token, foreign = owner(engine), owner(engine, "foreign-source-owner")
    source, _ = complete(engine, token)
    with Session(engine) as session, session.begin():
        value = artifact(session, token, source)
        bind_artifact_context(
            session, run=value, owner_hash=token, intent=ArtifactScientificContextIntent()
        )
        identifier = value.id
    # A damaged ownership restore must not confer cross-browser authority from
    # an otherwise valid immutable context/request/hash triple.
    with Session(engine) as session, session.begin():
        session.get(ResearchRunAccess, source.run_id).session_hash = foreign
    with (
        Session(engine) as session,
        pytest.raises(ContextStoreError, match="scientific_context_not_available"),
    ):
        load_pinned_artifact_context(session, owner_hash=token, run_id=identifier)


@pytest.mark.parametrize("corruption", ["hash", "missing_protocol", "source_hash", "oversized"])
def test_direct_context_insert_cannot_bypass_database_hash_source_or_size_proofs(
    database, corruption
):
    engine = database
    token = owner(engine)
    source, _ = complete(engine, token)
    from polymath.scientific_context_builder import build_artifact_scientific_context

    with pytest.raises(IntegrityError):
        with Session(engine) as session, session.begin():
            value = artifact(session, token, source)
            context = build_artifact_scientific_context(
                session, owner_hash=token, source_result_id=source.id
            ).model_dump(mode="json")
            source_hash = session.get(EvidenceResult, source.id).content_sha256
            if corruption == "missing_protocol":
                context.pop("protocol")
            elif corruption == "source_hash":
                source_hash = "f" * 64
                context["source_reference"]["content_sha256"] = source_hash
            elif corruption == "oversized":
                context["untrusted_extra"] = "z" * 262144
            encoded = canonical(context)
            session.add(
                ArtifactContextBinding(
                    run_id=value.id,
                    protocol=ArtifactScientificContextIntent().protocol,
                    source_result_id=source.id,
                    source_run_id=source.run_id,
                    source_sha256=source_hash,
                    context=context,
                    canonical_context=encoded,
                    context_sha256="0" * 64 if corruption == "hash" else digest(encoded),
                )
            )
            session.flush()
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ArtifactContextBinding)) == 0


@pytest.mark.parametrize("mode,kind", [("explore", "research"), ("ask", "write_summary")])
def test_ask_intent_rejects_wrong_run_and_database_composite_fk_prevents_kind_forgery(
    database, mode, kind
):
    engine = database
    token = owner(engine)
    with Session(engine) as session, session.begin():
        value = run(session, token, mode=mode, task_kind=kind)
        identifier = value.id
        with pytest.raises(ContextStoreError, match="ask_overview_wrong_run"):
            bind_ask_overview_intent(
                session, run=value, owner_hash=token, intent=AskOverviewIntent()
            )
    with Session(engine) as session, pytest.raises(IntegrityError):
        encoded = canonical(AskOverviewIntent().model_dump(mode="json"))
        session.add(
            AskOverviewIntentRow(
                run_id=identifier,
                protocol=AskOverviewIntent().protocol,
                mode="ask",
                task_kind="research",
                intent=AskOverviewIntent().model_dump(mode="json"),
                canonical_intent=encoded,
                intent_sha256=digest(encoded),
            )
        )
        session.commit()


def test_ask_intent_cannot_be_added_after_dispatch_or_combined_with_ladder(database):
    engine = database
    token = owner(engine)
    with Session(engine) as session, session.begin():
        value = run(session, token)
        session.add(
            DiscoveryIntentRow(
                run_id=value.id, **DiscoveryIntent().model_dump(mode="json"), config_sha256="a" * 64
            )
        )
        session.flush()
        with pytest.raises(ContextStoreError, match="ask_overview_wrong_run"):
            bind_ask_overview_intent(
                session, run=value, owner_hash=token, intent=AskOverviewIntent()
            )
        another = run(session, token)
        another.lease_token = 1
        session.flush()
        with pytest.raises(ContextStoreError, match="scientific_context_admission_closed"):
            bind_ask_overview_intent(
                session, run=another, owner_hash=token, intent=AskOverviewIntent()
            )


def test_fresh_ask_result_checks_derive_expectation_from_saved_intent_in_both_directions(
    database,
):
    engine = database
    token = owner(engine)
    result = answer_result()
    raw = result.model_dump(mode="json")
    raw["finding"]["summary"] = raw["finding"]["claims"][0]["text"]
    bound = raw | {
        "overview_binding": AskOverviewBinding(claim_ids=("known_1",)).model_dump(mode="json")
    }
    with Session(engine) as session, session.begin():
        legacy, current = run(session, token), run(session, token)
        bind_ask_overview_intent(session, run=current, owner_hash=token, intent=AskOverviewIntent())
        validate_result_overview_for_run(session, owner_hash=token, run_id=current.id, result=bound)
        with pytest.raises(ValueError, match="ask_overview_binding_required"):
            validate_result_overview_for_run(
                session, owner_hash=token, run_id=current.id, result=raw
            )
        with pytest.raises(ValueError, match="ask_overview_not_admitted"):
            validate_result_overview_for_run(
                session, owner_hash=token, run_id=legacy.id, result=bound
            )


def test_historical_intent_reads_keep_ownership_after_expiry_but_fresh_binding_is_denied(database):
    engine = database
    token, foreign = owner(engine), owner(engine, "foreign")
    with Session(engine) as session, session.begin():
        value = run(session, token)
        bind_ask_overview_intent(session, run=value, owner_hash=token, intent=AskOverviewIntent())
        identifier = value.id
    with Session(engine) as session:
        with pytest.raises(ContextStoreError, match="scientific_context_not_available"):
            load_ask_overview_intent(session, owner_hash=foreign, run_id=identifier)
    with Session(engine) as session, session.begin():
        session.get(BrowserSession, token).expires_at = datetime.now(UTC) - timedelta(seconds=1)
    with Session(engine) as session:
        assert (
            load_ask_overview_intent(session, owner_hash=token, run_id=identifier)
            == AskOverviewIntent()
        )
        assert (
            load_scientific_intent(session, session.get(ResearchRun, identifier))
            == AskOverviewIntent()
        )
        with pytest.raises(ContextStoreError, match="scientific_context_not_available"):
            bind_ask_overview_intent(
                session,
                run=session.get(ResearchRun, identifier),
                owner_hash=token,
                intent=AskOverviewIntent(),
            )


def test_bound_ask_assessment_requires_explicit_variant_and_exact_prior_basis_binding(
    database,
):
    engine = database
    token = owner(engine)
    source_id, overview = bound_answer(engine, token)
    watch = IngestionRepository(engine).create(
        token, label="Fixture", query="Fixture", source_result_id=source_id
    )
    with Session(engine) as session, session.begin():
        missing = assessment(session, token, source_id, watch.id, overview=overview)
        with pytest.raises(ContextStoreError, match="ask_reassessment_overview_required"):
            bind_assessment_overview_intent(session, run=missing, owner_hash=token)
        wrong_basis = assessment(
            session, token, source_id, watch.id, overview=overview, include_overview=False
        )
        with pytest.raises(ContextStoreError, match="scientific_context_integrity_failed"):
            bind_assessment_overview_intent(
                session, run=wrong_basis, owner_hash=token, intent=AskReassessmentOverviewIntent()
            )
        current = assessment(session, token, source_id, watch.id, overview=overview)
        value = bind_assessment_overview_intent(
            session, run=current, owner_hash=token, intent=AskReassessmentOverviewIntent()
        )
        assert value.protocol == "polymath-ask-reassessment-overview-v1"
        assert (
            load_assessment_overview_intent(session, owner_hash=token, run_id=current.id) == value
        )
        assert (
            session.get(AskReassessmentOverviewIntentRow, current.id).prior_overview_binding
            == overview
        )
        identifier = current.id
    with Session(engine) as session, pytest.raises(DBAPIError):
        session.execute(
            update(AskReassessmentOverviewIntentRow)
            .where(AskReassessmentOverviewIntentRow.run_id == identifier)
            .values(protocol=AskReassessmentOverviewIntentRow.protocol)
        )
        session.commit()
    with Session(engine) as session, pytest.raises(DBAPIError):
        session.execute(
            delete(AskReassessmentOverviewIntentRow).where(
                AskReassessmentOverviewIntentRow.run_id == identifier
            )
        )
        session.commit()


def test_sidecars_are_immutable_and_prototype_downgrade_refuses_history(database):
    engine = database
    token = owner(engine)
    source, _ = complete(engine, token)
    with Session(engine) as session, session.begin():
        value = artifact(session, token, source)
        bind_artifact_context(
            session, run=value, owner_hash=token, intent=ArtifactScientificContextIntent()
        )
        artifact_id = value.id
        ask = run(session, token)
        bind_ask_overview_intent(session, run=ask, owner_hash=token, intent=AskOverviewIntent())
        ask_id = ask.id
    for cls, identifier in ((ArtifactContextBinding, artifact_id), (AskOverviewIntentRow, ask_id)):
        with Session(engine) as session, pytest.raises(DBAPIError):
            session.execute(
                update(cls).where(cls.run_id == identifier).values(protocol=cls.protocol)
            )
            session.commit()
        with Session(engine) as session, pytest.raises(DBAPIError):
            session.execute(delete(cls).where(cls.run_id == identifier))
            session.commit()
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        old_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
        connection.rollback()
        with pytest.raises(DBAPIError, match="Cannot discard scientific context"):
            command.downgrade(config, "0023_public_research")
        connection.rollback()
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == old_head
        connection.rollback()
        command.check(config)


def test_self_hashed_assessment_basis_cannot_replace_its_exact_original_source_finding(
    database,
):
    engine = database
    token = owner(engine)
    source_id, overview = bound_answer(engine, token)
    watch = IngestionRepository(engine).create(
        token, label="Fixture", query="Fixture", source_result_id=source_id
    )
    with Session(engine) as session, session.begin():
        value = assessment(
            session, token, source_id, watch.id, overview=overview, forged_finding=True
        )
        with pytest.raises(ContextStoreError, match="scientific_context_integrity_failed"):
            bind_assessment_overview_intent(
                session, run=value, owner_hash=token, intent=AskReassessmentOverviewIntent()
            )
        assert session.get(AskReassessmentOverviewIntentRow, value.id) is None


def test_all_historical_intents_remain_keyless_after_expiry_without_live_rebuild(
    database, monkeypatch
):
    engine = database
    token = owner(engine)
    bound_source, overview = bound_answer(engine, token)
    source, _ = complete(engine, token)
    watch = IngestionRepository(engine).create(
        token, label="Fixture", query="Fixture", source_result_id=bound_source
    )
    with Session(engine) as session, session.begin():
        document = artifact(session, token, source)
        pinned = bind_artifact_context(
            session, run=document, owner_hash=token, intent=ArtifactScientificContextIntent()
        )
        ask = run(session, token)
        bind_ask_overview_intent(session, run=ask, owner_hash=token, intent=AskOverviewIntent())
        reassessment = assessment(session, token, bound_source, watch.id, overview=overview)
        bind_assessment_overview_intent(
            session, run=reassessment, owner_hash=token, intent=AskReassessmentOverviewIntent()
        )
        identifiers = (document.id, ask.id, reassessment.id)
    with Session(engine) as session, session.begin():
        session.get(BrowserSession, token).expires_at = datetime.now(UTC) - timedelta(seconds=1)

    def forbidden(*args, **kwargs):
        pytest.fail("Historical input reconstruction must not consult live scientific authority")

    import polymath.scientific_context_builder as builder
    import polymath.scientific_context_store as store

    monkeypatch.setattr(builder, "build_artifact_scientific_context", forbidden)
    monkeypatch.setattr(store, "require_current_supported", forbidden)
    with Session(engine) as session:
        assert (
            load_pinned_artifact_context(session, owner_hash=token, run_id=identifiers[0]) == pinned
        )
        assert (
            load_ask_overview_intent(session, owner_hash=token, run_id=identifiers[1])
            == AskOverviewIntent()
        )
        assert (
            load_assessment_overview_intent(session, owner_hash=token, run_id=identifiers[2])
            == AskReassessmentOverviewIntent()
        )
    # The shared native-input/profile reader is stricter about its dependency
    # boundary: it does not call even the historical packet/body validator.
    monkeypatch.setattr(builder, "validated_owned_result_packet", forbidden)
    with Session(engine) as session:
        protocols = [
            load_scientific_intent(session, session.get(ResearchRun, identifier)).protocol
            for identifier in identifiers
        ]
        assert protocols == [
            ArtifactScientificContextIntent().protocol,
            AskOverviewIntent().protocol,
            AskReassessmentOverviewIntent().protocol,
        ]
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == 0
        assert not session.new and not session.dirty and not session.deleted


def test_legacy_unowned_result_validation_and_input_intent_remain_absent(database):
    engine = database
    created = JobRepository(engine).create(mode="ask", goal=QUESTION)
    raw = answer_result().model_dump(mode="json")
    with Session(engine) as session:
        assert load_scientific_intent(session, session.get(ResearchRun, created.id)) is None
        validate_result_overview_for_run(session, owner_hash=None, run_id=created.id, result=raw)
        forged = raw | {
            "overview_binding": AskOverviewBinding(claim_ids=("known_1",)).model_dump(mode="json")
        }
        with pytest.raises(ContextStoreError, match="ask_overview_not_admitted"):
            validate_result_overview_for_run(
                session, owner_hash=None, run_id=created.id, result=forged
            )


@pytest.mark.parametrize("code", ["artifact_context_too_large", "untrusted_internal_detail"])
def test_builder_control_errors_are_sanitized_before_admission_commit(database, code):
    from polymath.scientific_context_builder import ScientificContextError

    engine = database
    token = owner(engine)
    source, _ = complete(engine, token)

    def refused(*args, **kwargs):
        raise ScientificContextError(code)

    with Session(engine) as session:
        before = session.scalar(select(func.count()).select_from(ResearchRun))
    with pytest.raises(ContextStoreError) as raised:
        with Session(engine) as session, session.begin():
            value = artifact(session, token, source)
            bind_artifact_context(
                session,
                run=value,
                owner_hash=token,
                intent=ArtifactScientificContextIntent(),
                builder=refused,
            )
    assert raised.value.code == (
        "artifact_context_too_large"
        if code == "artifact_context_too_large"
        else "scientific_context_integrity_failed"
    )
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == before


def test_metadata_only_migration_can_reverse_pristine_sidecars_without_rewriting_legacy_rows(
    database,
):
    engine = database
    token = owner(engine)
    source, _ = complete(engine, token)
    with Session(engine) as session:
        saved = deepcopy(session.get(EvidenceResult, source.id).payload)
        digest_before = session.get(EvidenceResult, source.id).content_sha256
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0023_public_research")
        command.upgrade(config, "head")
        command.check(config)
    with Session(engine) as session:
        assert session.get(EvidenceResult, source.id).payload == saved
        assert session.get(EvidenceResult, source.id).content_sha256 == digest_before


def test_fresh_context_admission_rechecks_owner_after_builder_wait_and_rolls_back(database):
    from polymath.scientific_context_builder import build_artifact_scientific_context

    engine = database
    token = owner(engine)
    source, _ = complete(engine, token)
    entered, release = Event(), Event()

    def waiting_builder(*args, **kwargs):
        context = build_artifact_scientific_context(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return context

    def admission():
        with Session(engine) as session, session.begin():
            value = artifact(session, token, source)
            return bind_artifact_context(
                session,
                run=value,
                owner_hash=token,
                intent=ArtifactScientificContextIntent(),
                builder=waiting_builder,
            )

    with Session(engine) as session:
        before = session.scalar(select(func.count()).select_from(ResearchRun))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(admission)
        try:
            assert entered.wait(5)
            with Session(engine) as session, session.begin():
                session.get(BrowserSession, token).expires_at = datetime.now(UTC) - timedelta(
                    seconds=1
                )
        finally:
            release.set()
        with pytest.raises(ContextStoreError, match="scientific_context_not_available"):
            future.result(timeout=5)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == before
        assert session.scalar(select(func.count()).select_from(ArtifactContextBinding)) == 0
