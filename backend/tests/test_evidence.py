"""Real PostgreSQL evidence integrity tests with explicitly synthetic source text."""

import copy
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from polymath.evidence import EvidenceError, EvidenceStore
from polymath.evidence_models import EvidenceResult, SourceRetrieval, SourceVersion
from polymath.jobs import JobRepository, LeaseLost
from polymath.research.models import (
    BridgeFinding,
    EvidenceQuote,
    Mapping,
    ResearchEvent,
    ResearchResult,
    SourceDocument,
    Usage,
    VerifiedQuote,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def evidence(monkeypatch: pytest.MonkeyPatch):
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL for real PostgreSQL evidence checks")
    url = make_url(database_url)
    assert url.drivername == "postgresql+psycopg"
    schema = "polymath_evidence_" + uuid.uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_engine(scoped, hide_parameters=True)
    try:
        command.upgrade(config, "head")
        jobs = JobRepository(engine)
        run = jobs.create(mode="explore", goal="Synthetic evidence persistence exercise")
        lease = jobs.claim_next("fixture-worker", lease_seconds=300)
        assert lease.run_id == run.id
        yield EvidenceStore(engine), jobs, lease, engine, config
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def document(source_id="fixture:left", **overrides) -> SourceDocument:
    values = {
        "source_id": source_id,
        "title": "Synthetic fixture, not a real paper",
        "url": f"https://example.org/fixture/{source_id}",
        "text": "🌌 Synthetic redundancy protects fixture messages against noise.",
        "content_kind": "abstract",
        "retrieved_at": datetime(2026, 10, 27, tzinfo=UTC),
        "extraction_method": "synthetic_fixture/v1",
    }
    values.update(overrides)
    return SourceDocument(**values)


def result_for(left: SourceDocument, right: SourceDocument) -> ResearchResult:
    left_quote = left.text[2:]
    right_quote = right.text[2:]
    quotes = []
    evidence = []
    for side, doc, quote in (("left", left, left_quote), ("right", right, right_quote)):
        evidence.append(
            EvidenceQuote(
                source_id=doc.source_id,
                side=side,
                quote=quote,
                supports="A fixture structural comparison",
            )
        )
        start = doc.text.find(quote)
        quotes.append(
            VerifiedQuote(
                source_id=doc.source_id,
                side=side,
                quote=quote,
                supports="A fixture structural comparison",
                start=start,
                end=start + len(quote),
                source_sha256=doc.content_sha256,
                content_kind=doc.content_kind,
            )
        )
    return ResearchResult(
        status="bridge",
        finding=BridgeFinding(
            outcome="bridge",
            title="Synthetic analogy, not a scientific discovery",
            field_left="Fixture domain A",
            field_right="Fixture domain B",
            explanation="Fixture-only mapping used to test provenance persistence.",
            mappings=[
                Mapping(
                    left="Fixture message",
                    right="Fixture signal",
                    assumption="Requires a defined fixture noise model",
                )
            ],
            open_question="Would this hypothetical mapping survive a real source audit?",
            strongest_objection="These fixtures are not scientific evidence.",
            objection_verdict="No real discovery or scientific verification is claimed.",
            novelty_limitations="Synthetic fixtures cannot establish novelty.",
            evidence=evidence,
        ),
        quotes=tuple(quotes),
        sources=(left, right),
        usage=Usage(model_calls=2),
    )


def observed_result(store, lease):
    left = document()
    right = document(
        "fixture:right", text="🌱 Synthetic pathways carry fixture signals under noise."
    )
    store.save_source(lease, left)
    store.save_source(lease, right)
    return result_for(left, right)


def test_identical_source_retry_is_idempotent_and_metadata_provenance_survives(evidence):
    store, _, lease, engine, _ = evidence
    original = document()
    first = store.save_source(lease, original)
    assert store.save_source(lease, original) == first
    changed = original.model_copy(
        update={
            "title": "A corrected synthetic title",
            "retrieved_at": original.retrieved_at + timedelta(hours=1),
            "extraction_method": "synthetic_fixture/v2",
        }
    )
    second = store.save_source(lease, changed)
    assert second.version_id == first.version_id
    assert second.retrieval_id != first.retrieval_id
    assert store.get_document(lease.run_id, first.retrieval_id) == original
    assert store.get_document(lease.run_id, second.retrieval_id) == changed
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SourceVersion)) == 1
        assert session.scalar(select(func.count()).select_from(SourceRetrieval)) == 2


def test_changed_content_and_text_kind_create_immutable_distinct_versions(evidence):
    store, _, lease, _, _ = evidence
    original = document()
    first = store.save_source(lease, original)
    changed = document(text="A revised synthetic source contradicts the earlier text.")
    second = store.save_source(lease, changed)
    third = store.save_source(lease, original.model_copy(update={"content_kind": "full_text"}))
    assert len({first.version_id, second.version_id, third.version_id}) == 3
    assert store.get_document(lease.run_id, first.retrieval_id) == original
    assert store.get_document(lease.run_id, second.retrieval_id) == changed


def test_database_prevents_text_or_provenance_mutation_and_false_hashes(evidence):
    store, _, lease, engine, _ = evidence
    artifact = store.save_source(lease, document())
    for statement in (
        update(SourceVersion)
        .where(SourceVersion.id == artifact.version_id)
        .values(source_text="Changed fixture text"),
        update(SourceRetrieval)
        .where(SourceRetrieval.id == artifact.retrieval_id)
        .values(title="Overwritten provenance"),
    ):
        with engine.begin() as connection, pytest.raises(IntegrityError):
            connection.execute(statement)
    with Session(engine) as session, pytest.raises(IntegrityError):
        session.add(
            SourceVersion(
                id=uuid.uuid4(),
                source_id="fixture:false-hash",
                content_sha256="0" * 64,
                content_kind="abstract",
                source_text="The digest is not the hash of this text.",
            )
        )
        session.commit()


def test_findings_require_previously_observed_sources_and_preserve_exact_offsets(evidence):
    store, _, lease, engine, _ = evidence
    left, right = document(), document("fixture:right")
    candidate = result_for(left, right)
    with pytest.raises(EvidenceError, match="source_not_observed"):
        store.save_result(lease, candidate)
    for source in candidate.sources:
        store.save_source(lease, source)
    saved = store.save_result(lease, candidate)
    assert saved.payload == candidate.model_dump(mode="json")
    assert store.get_result(lease.run_id, saved.id) == saved
    with engine.connect() as connection:
        rows = connection.execute(
            text("SELECT start_offset, end_offset, quote FROM evidence_quotes ORDER BY ordinal")
        ).all()
    assert all(row.start_offset == 2 for row in rows)
    assert rows[0].quote == left.text[rows[0].start_offset : rows[0].end_offset]


@pytest.mark.parametrize(
    "field,value",
    [
        ("start", -1),
        ("end", 9999),
        ("source_sha256", "f" * 64),
        ("content_kind", "full_text"),
        ("quote", "Invented fixture quotation"),
        ("supports", "An unsupported substitution"),
        ("source_id", "fixture:unread"),
    ],
)
def test_mismatched_verified_quote_never_creates_a_finding(evidence, field, value):
    store, _, lease, engine, _ = evidence
    candidate = observed_result(store, lease)
    quotes = list(candidate.quotes)
    quotes[0] = quotes[0].model_copy(update={field: value})
    invalid = candidate.model_copy(update={"quotes": tuple(quotes)})
    with pytest.raises(EvidenceError):
        store.save_result(lease, invalid)
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(EvidenceResult)) == 0
    assert len(store.list_sources(lease.run_id)) == 2


def test_result_retries_and_revisions_preserve_history(evidence):
    store, _, lease, engine, _ = evidence
    candidate = observed_result(store, lease)
    original = store.save_result(lease, candidate)
    assert store.save_result(lease, candidate).id == original.id
    changed = candidate.model_copy(
        update={
            "finding": candidate.finding.model_copy(
                update={
                    "open_question": "A different fixture question following additional scrutiny."
                }
            )
        }
    )
    revision = store.save_result(lease, changed)
    assert revision.id != original.id
    assert len(store.list_results(lease.run_id)) == 2
    assert store.get_result(lease.run_id, original.id).payload == original.payload
    with engine.begin() as connection, pytest.raises(IntegrityError):
        connection.execute(
            update(EvidenceResult)
            .where(EvidenceResult.id == original.id)
            .values(payload={"overwritten": True})
        )


def test_cancelled_or_expired_lease_cannot_write_but_prior_artifacts_remain(evidence):
    store, jobs, lease, engine, _ = evidence
    candidate = observed_result(store, lease)
    original = store.save_result(lease, candidate)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE research_runs SET lease_expires_at=clock_timestamp()-interval '1 second' "
                "WHERE id=:run_id"
            ),
            {"run_id": lease.run_id},
        )
    with pytest.raises(LeaseLost):
        store.save_source(lease, document("fixture:stale"))
    jobs.request_cancel(lease.run_id)
    with pytest.raises(LeaseLost):
        store.save_result(lease, candidate)
    assert store.get_result(lease.run_id, original.id) == original


def test_source_reads_are_run_scoped_even_with_shared_global_version(evidence):
    store, jobs, lease, _, _ = evidence
    source = document()
    first = store.save_source(lease, source)
    second_run = jobs.create(mode="ask", goal="Another fixture investigation")
    second_lease = jobs.claim_next("second-worker")
    assert second_lease.run_id == second_run.id
    with pytest.raises(EvidenceError, match="source_not_found"):
        store.get_document(second_run.id, first.retrieval_id)
    second = store.save_source(second_lease, source)
    assert second == first
    assert store.get_document(second_run.id, second.retrieval_id) == source


def test_failed_result_can_retain_observed_partial_sources_without_a_finding(evidence):
    store, _, lease, _, _ = evidence
    source = document()
    store.save_source(lease, source)
    partial = ResearchResult(
        status="failed",
        sources=(source,),
        usage=Usage(model_calls=1, calls_without_usage=1),
        error_code="model_timeout_outcome_unknown",
    )
    saved = store.save_result(lease, partial)
    assert saved.payload == partial.model_dump(mode="json")
    assert saved.payload["finding"] is None


def test_oversized_result_does_not_erase_previously_observed_sources(evidence):
    store, _, lease, engine, _ = evidence
    candidate = observed_result(store, lease)
    oversized = candidate.model_copy(
        update={
            "events": tuple(
                ResearchEvent(sequence=n + 1, kind="fixture", summary="x" * 2000)
                for n in range(200)
            )
        }
    )
    with pytest.raises(EvidenceError, match="result_artifact_too_large"):
        store.save_result(lease, oversized)
    assert len(store.list_sources(lease.run_id)) == 2
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(EvidenceResult)) == 0


def test_source_limit_is_transactional_and_retries_do_not_consume_it(evidence, monkeypatch):
    store, _, lease, engine, _ = evidence
    monkeypatch.setattr("polymath.evidence.MAX_RUN_RETRIEVALS", 2)
    store.save_source(lease, document())
    store.save_source(lease, document())
    store.save_source(lease, document("fixture:second"))
    with pytest.raises(EvidenceError, match="run_source_limit"):
        store.save_source(lease, document("fixture:third"))
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SourceVersion)) == 2


def test_concurrent_runs_share_immutable_version_without_duplicate_retrievals(evidence):
    store, jobs, lease, engine, _ = evidence
    jobs.create(mode="ask", goal="A concurrent fixture investigation")
    other_lease = jobs.claim_next("other-worker")
    start = Barrier(2)

    def save(active_lease):
        start.wait(timeout=10)
        return store.save_source(active_lease, document())

    with ThreadPoolExecutor(max_workers=2) as executor:
        artifacts = list(executor.map(save, [lease, other_lease]))
    assert artifacts[0] == artifacts[1]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SourceVersion)) == 1
        assert session.scalar(select(func.count()).select_from(SourceRetrieval)) == 1


def test_evidence_migration_repeats_reverses_and_matches_metadata(evidence):
    _, jobs, lease, engine, config = evidence
    command.upgrade(config, "head")
    command.check(config)
    command.downgrade(config, "0004_dispatch_binding")
    assert "evidence_source_versions" not in inspect(engine).get_table_names()
    # The current ORM includes columns added after this historical schema.
    # Check preserved job data through the schema's actual columns until upgrade.
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT status FROM research_runs WHERE id = :run_id"),
                {"run_id": lease.run_id},
            )
            == "running"
        )
    command.upgrade(config, "head")
    command.check(config)
    assert jobs.get(lease.run_id).status == "running"


@pytest.mark.parametrize(
    "change",
    [
        {"text": "invalid\x00document"},
        {"title": "invalid\x00title"},
        {"source_id": "invalid\x00identity"},
    ],
)
def test_invalid_source_text_does_not_write_partial_rows(evidence, change):
    store, _, lease, engine, _ = evidence
    with pytest.raises(EvidenceError, match="invalid_source_document"):
        store.save_source(lease, document().model_copy(update=change))
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(SourceVersion)) == 0


def test_caller_cannot_mutate_a_loaded_result_to_change_the_saved_version(evidence):
    store, _, lease, _, _ = evidence
    original = store.save_result(lease, observed_result(store, lease))
    expected = copy.deepcopy(original.payload)
    original.payload["finding"]["title"] = "Changed only in caller memory"
    assert store.get_result(lease.run_id, original.id).payload == expected
    assert (
        json.loads(json.dumps(expected))["validation_scope"]
        == "source_identity_and_quote_integrity"
    )


@pytest.mark.parametrize("failure", ["nul", "usage", "error", "missing_quotes", "source_duplicate"])
def test_invalid_final_artifact_is_rejected_without_losing_completed_reads(evidence, failure):
    store, _, lease, engine, _ = evidence
    candidate = observed_result(store, lease)
    if failure == "nul":
        invalid = candidate.model_copy(
            update={
                "finding": candidate.finding.model_copy(
                    update={"open_question": "A question containing an invalid\x00character"}
                )
            }
        )
    elif failure == "usage":
        invalid = candidate.model_copy(update={"usage": Usage(model_calls=-1)})
    elif failure == "error":
        invalid = candidate.model_copy(update={"error_code": "a raw exception must not be stored"})
    elif failure == "missing_quotes":
        invalid = candidate.model_copy(update={"quotes": candidate.quotes[:1]})
    else:
        invalid = candidate.model_copy(
            update={"sources": candidate.sources + candidate.sources[:1]}
        )
    with pytest.raises(EvidenceError):
        store.save_result(lease, invalid)
    assert len(store.list_sources(lease.run_id)) == 2
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(EvidenceResult)) == 0
