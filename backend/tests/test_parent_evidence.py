"""Higher-level findings must trace each pinned bridge to original source versions."""

import pytest
from sqlalchemy.orm import Session
from test_evidence import document, result_for
from test_memory import assign, complete, empty_result, owner
from test_worker import database as database_fixture

from polymath.evidence import EvidenceError, EvidenceStore
from polymath.jobs import JobRepository
from polymath.lineage import bind_parents
from polymath.lineage_models import ResearchRunParent
from polymath.memory import ResearchMemory
from polymath.models import ResearchRun
from polymath.research.models import EvidenceQuote, MemoryReadReference, VerifiedQuote

database = database_fixture
pytestmark = pytest.mark.integration


def setup_parents(engine, count=2, *, forged_hash=False, extra=False):
    token = owner(engine)
    parents = [
        complete(engine, token, title=f"Synthetic parent {index}", suffix=f"-{index}", extra=extra)
        for index in range(count)
    ]
    jobs = JobRepository(engine)
    run = jobs.create(mode="explore", goal="Synthetic connection between saved bridges")
    assign(engine, run.id, token)
    with Session(engine) as session, session.begin():
        row = session.get(ResearchRun, run.id)
        if forged_hash:
            for ordinal, (saved, _) in enumerate(parents):
                session.add(
                    ResearchRunParent(
                        run_id=run.id,
                        parent_result_id=saved.id,
                        parent_run_id=saved.run_id,
                        ordinal=ordinal,
                        content_sha256="0" * 64,
                    )
                )
        else:
            bind_parents(session, row, token, [str(saved.id) for saved, _ in parents])
    lease = jobs.claim_next("synthetic-parent-evidence", lease_seconds=300)
    assert lease.run_id == run.id
    return parents, ResearchMemory(engine, lease), EvidenceStore(engine), lease


def read_refs(memory, parents):
    refs = []
    for saved, _ in parents:
        found = memory.read_finding(str(saved.id))
        refs.append(
            MemoryReadReference(
                result_id=found.reference.result_id,
                run_id=found.reference.run_id,
                content_sha256=found.reference.content_sha256,
            )
        )
    return tuple(refs)


def child_result(sources, refs):
    result = result_for(*sources[:2])
    evidence, quotes = list(result.finding.evidence), list(result.quotes)
    for source in sources[2:]:
        quote = source.text[2:]
        evidence.append(
            EvidenceQuote(
                source_id=source.source_id,
                side="left",
                quote=quote,
                supports="Synthetic original premise for another selected bridge",
            )
        )
        start = source.text.find(quote)
        quotes.append(
            VerifiedQuote(
                source_id=source.source_id,
                side="left",
                quote=quote,
                supports=evidence[-1].supports,
                start=start,
                end=start + len(quote),
                source_sha256=source.content_sha256,
                content_kind=source.content_kind,
            )
        )
    return result.model_copy(
        update={
            "finding": result.finding.model_copy(update={"evidence": evidence}),
            "quotes": tuple(quotes),
            "sources": tuple(sources),
            "memory_reads": refs,
        }
    )


def persist_sources(store, lease, result):
    for source in result.sources:
        store.save_source(lease, source)


@pytest.mark.parametrize("count", [2, 4])
def test_positive_child_traces_every_selected_parent_to_original_evidence(database, count):
    parents, memory, store, lease = setup_parents(database, count)
    refs = read_refs(memory, parents)
    sources = tuple(result.sources[index % 2] for index, (_, result) in enumerate(parents))
    result = child_result(sources, refs)
    persist_sources(store, lease, result)
    saved = store.save_result(lease, result)
    assert len(saved.payload["memory_reads"]) == count
    assert len(saved.payload["quotes"]) == count


def test_positive_child_cannot_omit_a_selected_parent_read(database):
    parents, memory, store, lease = setup_parents(database)
    refs = read_refs(memory, parents[:1])
    result = child_result((parents[0][1].sources[0], parents[1][1].sources[1]), refs)
    persist_sources(store, lease, result)
    with pytest.raises(EvidenceError, match="parent_findings_not_observed"):
        store.save_result(lease, result)


def test_positive_child_cannot_use_only_one_selected_parents_sources(database):
    parents, memory, store, lease = setup_parents(database)
    refs = read_refs(memory, parents)
    result = child_result(parents[0][1].sources, refs)
    persist_sources(store, lease, result)
    with pytest.raises(EvidenceError, match="parent_original_evidence_missing"):
        store.save_result(lease, result)


def test_unrelated_read_source_does_not_cover_the_missing_parent(database):
    parents, memory, store, lease = setup_parents(database)
    refs = read_refs(memory, parents)
    result = child_result(
        (
            parents[0][1].sources[0],
            document("fixture:unrelated", text="🌠 Unrelated synthetic text."),
        ),
        refs,
    )
    persist_sources(store, lease, result)
    with pytest.raises(EvidenceError, match="parent_original_evidence_missing"):
        store.save_result(lease, result)


def test_a_parents_unquoted_source_read_does_not_cover_its_original_evidence(database):
    parents, memory, store, lease = setup_parents(database, extra=True)
    refs = read_refs(memory, parents)
    saved, _ = parents[1]
    unused = next(
        source
        for source in store.list_sources(saved.run_id)
        if source.source_id == "fixture:not-quoted"
    )
    doc = store.get_document(saved.run_id, unused.retrieval_id)
    result = child_result((parents[0][1].sources[0], doc), refs)
    persist_sources(store, lease, result)
    with pytest.raises(EvidenceError, match="parent_original_evidence_missing"):
        store.save_result(lease, result)


@pytest.mark.parametrize("mutation", ["text", "kind"])
def test_matching_source_id_with_another_version_cannot_cover_a_parent(database, mutation):
    parents, memory, store, lease = setup_parents(database)
    refs = read_refs(memory, parents)
    original = parents[1][1].sources[1]
    changed = original.model_copy(
        update={"text": original.text + " Changed version."}
        if mutation == "text"
        else {"content_kind": "full_text"}
    )
    result = child_result((parents[0][1].sources[0], changed), refs)
    persist_sources(store, lease, result)
    with pytest.raises(EvidenceError, match="parent_original_evidence_missing"):
        store.save_result(lease, result)


def test_parent_original_quote_still_requires_a_current_run_durable_read(database):
    parents, memory, store, lease = setup_parents(database)
    refs = read_refs(memory, parents)
    result = child_result((parents[0][1].sources[0], parents[1][1].sources[1]), refs)
    # A prior run's durable read is never authority to invent this run's read.
    with pytest.raises(EvidenceError, match="source_not_observed"):
        store.save_result(lease, result)


def test_insufficient_child_may_honestly_stop_before_reading_all_selected_parents(database):
    parents, memory, store, lease = setup_parents(database)
    refs = read_refs(memory, parents[:1])
    saved = store.save_result(lease, empty_result(*refs))
    assert saved.status == "insufficient_evidence"
    assert len(saved.payload["memory_reads"]) == 1
    with Session(database) as session:
        assert session.query(ResearchRunParent).filter_by(run_id=lease.run_id).count() == 2


def test_forged_selected_parent_hash_is_rejected_independently_of_the_controller(database):
    parents, memory, store, lease = setup_parents(database, forged_hash=True)
    refs = read_refs(memory, parents)
    result = child_result((parents[0][1].sources[0], parents[1][1].sources[1]), refs)
    persist_sources(store, lease, result)
    with pytest.raises(EvidenceError, match="invalid_parent_basis"):
        store.save_result(lease, result)
