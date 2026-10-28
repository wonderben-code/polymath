"""Provider metadata stays immutable, hash-bound and distinct from paper counts."""

import hashlib
import json
from datetime import date

import pytest
from alembic import command
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_evidence import document, result_for
from test_evidence import evidence as evidence_fixture

from polymath.evidence import EvidenceError, _canonical, _metadata
from polymath.evidence_models import SourceRetrieval
from polymath.research.models import SourceAuthor, SourceBibliography

evidence = evidence_fixture
pytestmark = pytest.mark.integration


def bibliography(**changes):
    return SourceBibliography(
        authors=(SourceAuthor(name="Synthetic Fixture Author"),),
        publication_year=2026,
        publication_date=date(2026, 10, 27),
        venue="Synthetic fixture venue",
        **changes,
    )


def test_metadata_round_trip_and_original_hash_compatibility(evidence):
    store, _, lease, engine, _ = evidence
    original = document()
    legacy_metadata = {
        "title": original.title,
        "url": original.url,
        "retrieved_at": original.retrieved_at.isoformat(),
        "extraction_method": original.extraction_method,
    }
    old = store.save_source(lease, original)
    assert _metadata(original) == legacy_metadata
    assert "bibliography" not in original.model_dump(mode="json")
    with Session(engine) as session:
        row = session.get(SourceRetrieval, old.retrieval_id)
        assert row.bibliography is None
        assert (
            row.metadata_sha256 == hashlib.sha256(_canonical(legacy_metadata).encode()).hexdigest()
        )
    enriched = original.model_copy(update={"bibliography": bibliography(doi="10.1234/fixture")})
    new = store.save_source(lease, enriched)
    assert new.version_id == old.version_id
    assert new.retrieval_id != old.retrieval_id
    assert store.get_document(lease.run_id, new.retrieval_id) == enriched
    assert store.get_document(lease.run_id, old.retrieval_id) == original
    assert store.save_source(lease, enriched) == new
    with Session(engine) as session:
        assert session.get(SourceRetrieval, new.retrieval_id).bibliography == (
            enriched.bibliography.model_dump(mode="json")
        )


def test_changed_bibliography_needs_its_actual_durable_read(evidence):
    store, _, lease, _, _ = evidence
    left, right = document(), document("fixture:right")
    store.save_source(lease, left)
    store.save_source(lease, right)
    enriched = left.model_copy(update={"bibliography": bibliography(doi="10.1234/fixture")})
    with pytest.raises(EvidenceError, match="source_not_observed"):
        store.save_result(lease, result_for(enriched, right))
    store.save_source(lease, enriched)
    saved = store.save_result(lease, result_for(enriched, right))
    assert saved.payload["sources"][0]["bibliography"]["doi"] == "10.1234/fixture"
    assert store.get_result(lease.run_id, saved.id) == saved


def test_bibliography_is_immutable_with_its_retrieval(evidence):
    store, _, lease, engine, _ = evidence
    doc = document(bibliography=bibliography(doi="10.1234/fixture"))
    artifact = store.save_source(lease, doc)
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.execute(
            update(SourceRetrieval)
            .where(SourceRetrieval.id == artifact.retrieval_id)
            .values(bibliography={"doi": "10.1234/changed"})
        )
    assert store.get_document(lease.run_id, artifact.retrieval_id) == doc


def test_two_versions_of_one_paper_cannot_support_both_bridge_sides(evidence):
    store, _, lease, _, _ = evidence
    left = document(
        "arxiv:2401.00001",
        url="https://arxiv.org/abs/2401.00001v1",
        bibliography=bibliography(doi="10.1234/fixture", arxiv_id="2401.00001", arxiv_version=1),
    )
    right = document(
        "doi:10.1234/fixture",
        url="https://doi.org/10.1234/fixture",
        content_kind="full_text",
        bibliography=bibliography(doi="10.1234/fixture"),
    )
    store.save_source(lease, left)
    store.save_source(lease, right)
    with pytest.raises(EvidenceError, match="distinct_evidence_sources_required"):
        store.save_result(lease, result_for(left, right))


def test_bibliographic_aliases_do_not_merge_unrelated_papers_by_title(evidence):
    store, _, lease, _, _ = evidence
    left = document("fixture:left", bibliography=bibliography(doi="10.1234/first"))
    right = document("fixture:right", bibliography=bibliography(doi="10.1234/second"))
    assert left.title == right.title
    store.save_source(lease, left)
    store.save_source(lease, right)
    assert store.save_result(lease, result_for(left, right)).status == "bridge"


def test_uncited_durable_read_can_reveal_that_two_citations_are_one_paper(evidence):
    store, _, lease, _, _ = evidence
    left = document("fixture:doi", bibliography=bibliography(doi="10.1234/fixture"))
    right = document("fixture:arxiv", bibliography=bibliography(arxiv_id="2401.00001"))
    connector = document(
        "fixture:connector",
        bibliography=bibliography(doi="10.1234/fixture", arxiv_id="2401.00001"),
    )
    for doc in (left, right, connector):
        store.save_source(lease, doc)
    # A caller cannot restore apparent independence by omitting the connector
    # from result.sources. The store uses all actual run-bound observations.
    with pytest.raises(EvidenceError, match="distinct_evidence_sources_required"):
        store.save_result(lease, result_for(left, right))


def test_downgrade_refuses_to_discard_saved_bibliography(evidence):
    store, _, lease, engine, config = evidence
    artifact = store.save_source(lease, document(bibliography=bibliography(doi="10.1234/fixture")))
    with engine.connect() as connection:
        original_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
    with pytest.raises(Exception, match="Cannot discard saved source bibliography"):
        command.downgrade(config, "0013_publication_jobs")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == original_head
    with Session(engine) as session:
        stored = session.scalar(
            select(SourceRetrieval).where(SourceRetrieval.id == artifact.retrieval_id)
        )
        assert json.loads(json.dumps(stored.bibliography))["doi"] == "10.1234/fixture"
