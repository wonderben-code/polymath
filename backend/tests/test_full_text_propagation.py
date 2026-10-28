"""Exact excerpt scope survives primary selection, memory, drafts, and history.

All paper text and extraction results are synthetic. No provider/model calls.
"""

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session
from test_artifact_api import draft_result
from test_artifact_controller import artifact_basis
from test_artifacts import replace_paragraph
from test_assessment_controller import assessment_basis, snapshot
from test_assessments import assessment_result, enqueue
from test_evidence import document, result_for
from test_finding_eligibility_integration import browser_owner
from test_knowledge import knowledge as knowledge_fixture
from test_memory import assign, current, owner
from test_worker import database as database_fixture

from polymath.artifact_contracts import ArtifactBasis, ArtifactPassage, CitedText
from polymath.artifacts import ArtifactStore, bind_request, render_artifact
from polymath.assessment_contracts import (
    AssessmentCitation,
    AssessmentResult,
    AssessmentStatement,
    validate_assessment_result,
    verify_assessment_quotes,
)
from polymath.assessments import AssessmentStore
from polymath.evidence import EvidenceStore, _canonical, _hash
from polymath.full_text import FullTextStore
from polymath.full_text_contracts import ExtractedFullText
from polymath.ingestion import IngestionRepository
from polymath.jobs import JobRepository
from polymath.literature.identity import distinct_paper_count, validate_selected_paper_versions
from polymath.models import ResearchRun
from polymath.research.controller import _verify_evidence
from polymath.research.models import (
    EvidenceQuote,
    MemoryReadReference,
    ResearchResult,
    SourceBibliography,
    SourceCoverage,
    SourceDocument,
    SourceReference,
    Usage,
)

database = database_fixture
knowledge = knowledge_fixture
SNAPSHOT_ID = "91111111-1111-4111-8111-111111111111"
FIRST = "🌌 Synthetic first full-text passage describes a bounded mechanism."
SECOND = "🌱 Synthetic second full-text passage states an important limitation."
TEXT = FIRST + "\n\n" + SECOND
WHEN = datetime(2026, 10, 28, tzinfo=UTC)


def window(*, second=False, snapshot_id=SNAPSHOT_ID, arxiv_id="2401.01234", version=2):
    start, text = (len(FIRST) + 2, SECOND) if second else (0, FIRST)
    coverage = SourceCoverage(
        snapshot_id=snapshot_id,
        snapshot_sha256=hashlib.sha256(TEXT.encode()).hexdigest(),
        arxiv_id=arxiv_id,
        arxiv_version=version,
        start=start,
        end=start + len(text),
        total=len(TEXT),
    )
    return SourceDocument(
        source_id=f"arxiv-html:{snapshot_id}:{coverage.start}:{coverage.end}",
        title="Synthetic full-text paper",
        url=f"https://arxiv.org/html/{arxiv_id}v{version}",
        bibliography=SourceBibliography(arxiv_id=arxiv_id, arxiv_version=version),
        coverage=coverage,
        text=text,
        content_kind="full_text",
        retrieved_at=WHEN,
        extraction_method="arxiv_html_latexml/v1",
    )


def reference(identifier, *, bibliography=None):
    return SourceReference(
        source_id=identifier,
        title="Synthetic identity connector",
        url=f"https://example.org/{identifier}",
        bibliography=bibliography,
    )


def test_same_snapshot_windows_are_one_paper_and_independence_still_requires_another():
    first, second = window(), window(second=True)
    validate_selected_paper_versions((first, second))
    assert distinct_paper_count((first, second)) == 1
    finding = result_for(first, second).finding
    with pytest.raises(ValueError, match="distinct_sources_required"):
        _verify_evidence(
            finding,
            {item.source_id: item for item in (first, second)},
            {"counterevidence", "prior_work"},
        )


@pytest.mark.parametrize("problem", ["snapshot", "version", "abstract", "duplicate"])
def test_selected_primary_paper_cannot_mix_versions_or_abstract_with_fulltext(problem):
    first = window()
    if problem == "snapshot":
        other = window(second=True, snapshot_id=str(uuid.uuid4()))
    elif problem == "version":
        other = window(second=True, version=3)
    elif problem == "abstract":
        other = document("arxiv:2401.01234v2", url="https://arxiv.org/abs/2401.01234v2")
    else:
        other = first
    with pytest.raises(ValueError, match="selected_paper_version_conflict"):
        validate_selected_paper_versions((first, other))


def test_full_bounded_identity_context_can_join_aliases_without_becoming_evidence():
    abstract = document("doi:10.1234/synthetic", url="https://doi.org/10.1234/synthetic")
    excerpt = window()
    connector = reference(
        "index-connector",
        bibliography=SourceBibliography(
            doi="10.1234/synthetic",
            arxiv_id="2401.01234",
            arxiv_version=2,
        ),
    )
    context = tuple(reference(f"unrelated-{index}") for index in range(255)) + (connector,)
    assert distinct_paper_count((abstract, excerpt)) == 2
    assert distinct_paper_count((abstract, excerpt), context=context) == 1
    with pytest.raises(ValueError, match="selected_paper_version_conflict"):
        validate_selected_paper_versions((abstract, excerpt), context=context)
    with pytest.raises(ValueError, match="reference limit"):
        distinct_paper_count((excerpt,), context=(*context, reference("overflow")))
    with pytest.raises(ValueError, match="reference limit"):
        validate_selected_paper_versions(reference(str(index)) for index in range(201))


def test_excerpt_export_explicitly_preserves_window_scope_and_legacy_absence():
    first, right = window(), document("fixture:right", text=SECOND)
    finding = result_for(first, right).finding
    basis = ArtifactBasis(
        reference=MemoryReadReference(
            result_id=str(uuid.uuid4()), run_id=str(uuid.uuid4()), content_sha256="a" * 64
        ),
        finding=finding,
        passages=tuple(
            ArtifactPassage(
                **{
                    key: value
                    for key, value in source.model_dump().items()
                    if key in {"source_id", "title", "url", "bibliography", "coverage"}
                },
                ordinal=index,
                content_sha256=source.content_sha256,
                content_kind=source.content_kind,
                quote=evidence.quote,
                supports=evidence.supports,
            )
            for index, (source, evidence) in enumerate(
                zip((first, right), finding.evidence, strict=True)
            )
        ),
    )
    rendered = render_artifact(draft_result(), basis)
    assert "Read scope: FULL TEXT EXCERPT." in rendered
    assert f"[{first.coverage.start}, {first.coverage.end}) of {first.coverage.total}" in rendered
    assert first.coverage.snapshot_sha256 in rendered
    assert "does not indicate that the whole paper was read" in rendered
    assert "may omit equations, figures, and tables" in rendered
    assert "A heading index is metadata" in rendered
    assert basis.model_dump(mode="json")["passages"][0]["coverage"] == (
        first.coverage.model_dump(mode="json")
    )
    historical = artifact_basis().model_dump(mode="json")
    assert "coverage" not in _canonical(historical)
    assert _hash(
        _canonical(
            ArtifactBasis.model_validate_json(_canonical(historical)).model_dump(mode="json")
        )
    ) == _hash(_canonical(historical))


def revised_with_windows(*, conflict=False):
    basis, observed = assessment_basis()
    first = snapshot(window())
    second = snapshot(
        window(second=True, snapshot_id=str(uuid.uuid4()) if conflict else SNAPSHOT_ID)
    )
    observed = {**observed, first.retrieval_id: first, second.retrieval_id: second}
    right = next(item for item in observed.values() if item.document.source_id == "right")
    left_result = result_for(first.document, right.document)
    evidence = EvidenceQuote(
        source_id=second.document.source_id,
        side="left",
        quote=second.document.text[2:],
        supports="Synthetic second window.",
    )
    finding = left_result.finding.model_copy(
        update={"evidence": [*left_result.finding.evidence, evidence]}
    )
    selected = (first, second, right)
    documents = {item.document.source_id: item.document for item in selected}
    quotes = (
        (
            *left_result.quotes,
            left_result.quotes[0].model_copy(
                update={
                    "source_id": second.document.source_id,
                    "quote": evidence.quote,
                    "supports": evidence.supports,
                    "source_sha256": second.document.content_sha256,
                    "end": len(second.document.text),
                }
            ),
        )
        if conflict
        else _verify_evidence(finding, documents, {"counterevidence", "prior_work"})
    )
    revised = ResearchResult(
        status="bridge",
        finding=finding,
        sources=tuple(documents.values()),
        quotes=quotes,
        memory_reads=(basis.prior_reference,),
        usage=Usage(),
    )
    statements = tuple(
        AssessmentStatement(
            text="Synthetic source-bound comparison.",
            citations=(
                AssessmentCitation(
                    retrieval_id=identifier,
                    quote=next(
                        (
                            quote.quote
                            for quote in basis.prior_quotes
                            if quote.source_id == item.document.source_id
                        ),
                        item.document.text,
                    ),
                    supports="Synthetic exact source scope.",
                ),
            ),
        )
        for identifier, item in observed.items()
    )
    result = AssessmentResult(
        status="completed",
        disposition="revised",
        rationale=statements,
        limitations=("Synthetic excerpts do not establish scientific truth.",),
        observed_retrieval_ids=tuple(observed),
        selected_retrieval_ids=tuple(item.retrieval_id for item in selected),
        quotes=verify_assessment_quotes(statements, observed),
        revised_result=revised,
        search_purposes=("counterevidence", "prior_work"),
        usage=Usage(),
    )
    return basis, observed, result


def test_assessment_primary_selection_allows_same_snapshot_windows_and_binds_quote_coverage():
    basis, observed, result = revised_with_windows()
    validate_assessment_result(result, basis, observed)
    excerpts = [quote for quote in result.quotes if quote.coverage is not None]
    assert len(excerpts) == 2
    for quote in excerpts:
        assert quote.coverage == observed[quote.retrieval_id].document.coverage
    assert all(
        "coverage" not in quote.model_dump(mode="json")
        for quote in result.quotes
        if quote.coverage is None
    )
    changed = excerpts[0].coverage.model_copy(update={"snapshot_sha256": "e" * 64})
    tampered_quotes = tuple(
        quote.model_copy(update={"coverage": changed}) if quote is excerpts[0] else quote
        for quote in result.quotes
    )
    with pytest.raises(ValueError, match="assessment_quote_integrity_failed"):
        validate_assessment_result(
            result.model_copy(update={"quotes": tampered_quotes}), basis, observed
        )
    with pytest.raises(ValueError, match="assessment_ambiguous_paper_version"):
        validate_assessment_result(
            result,
            basis,
            observed,
            alias_context=(reference(f"extra-{index}") for index in range(257)),
        )


def test_assessment_primary_selection_rejects_different_full_snapshots():
    basis, observed, result = revised_with_windows(conflict=True)
    with pytest.raises(ValueError, match="assessment_ambiguous_paper_version"):
        validate_assessment_result(result, basis, observed)


def saved_excerpt_finding(engine, token):
    jobs, evidence = JobRepository(engine), EvidenceStore(engine)
    run = jobs.create(mode="explore", goal="Synthetic excerpt research")
    assign(engine, run.id, token)
    lease = jobs.claim_next("synthetic-excerpt-research", lease_seconds=300)
    index = FullTextStore(engine).save_snapshot(
        lease,
        SourceReference(
            source_id="arxiv:2401.01234v2",
            title="Synthetic saved full-text paper",
            url="https://arxiv.org/abs/2401.01234v2",
            bibliography=SourceBibliography(arxiv_id="2401.01234", arxiv_version=2),
        ),
        ExtractedFullText(
            arxiv_id="2401.01234",
            arxiv_version=2,
            url="https://arxiv.org/html/2401.01234v2",
            text=TEXT,
            raw_html_sha256="c" * 64,
            retrieved_at=WHEN,
            headings=(),
            limitations=("Synthetic extraction fixture; only selected windows are read.",),
        ),
    )
    first = FullTextStore(engine).read_window(lease, index.snapshot_id, 0, len(FIRST))
    second = FullTextStore(engine).read_window(lease, index.snapshot_id, len(FIRST) + 2, len(TEXT))
    right = document("fixture:independent-right", text="🌱 Synthetic independent paper evidence.")
    evidence.save_source(lease, right)
    initial = result_for(first, right)
    finding = initial.finding.model_copy(
        update={
            "evidence": [
                *initial.finding.evidence,
                EvidenceQuote(
                    source_id=second.source_id,
                    side="left",
                    quote=second.text[2:],
                    supports="Synthetic second-window limitation.",
                ),
            ]
        }
    )
    documents = {item.source_id: item for item in (first, right, second)}
    result = initial.model_copy(
        update={
            "finding": finding,
            "sources": tuple(documents.values()),
            "quotes": _verify_evidence(finding, documents, {"counterevidence", "prior_work"}),
        }
    )
    saved = evidence.save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    return saved, (first, second), right


@pytest.mark.integration
def test_actual_excerpt_memory_and_artifact_preserve_original_scope(database):
    token = owner(database)
    saved, windows, _ = saved_excerpt_finding(database, token)
    memory, _ = current(database, token)
    remembered = memory.read_finding(str(saved.id))
    refs = {item.source.source_id: item for item in remembered.sources}
    for excerpt in windows:
        reference = refs[excerpt.source_id]
        assert reference.source.coverage == excerpt.coverage
        assert memory.read_source(str(saved.id), reference.retrieval_id) == excerpt
    jobs = JobRepository(database)
    run = jobs.create(
        mode="explore", goal="Synthetic saved excerpt export", task_kind="write_summary"
    )
    assign(database, run.id, token)
    with Session(database) as session, session.begin():
        bind_request(
            session, session.get(ResearchRun, run.id), token, "write_summary", str(saved.id)
        )
    lease = jobs.claim_next("synthetic-excerpt-draft", task_kinds=("write_summary",))
    store = ArtifactStore(database)
    basis = store.load_basis(lease)
    assert [passage.coverage for passage in basis.passages if passage.coverage] == [
        item.coverage for item in windows
    ]
    generated = store.save(
        lease,
        replace_paragraph(
            draft_result(),
            CitedText(
                text="Synthetic bounded interpretation of the saved passages.",
                label="postulated",
                citation_ordinals=(0, 1, 2),
            ),
        ),
    )
    assert generated.markdown.count("FULL TEXT EXCERPT") == 2
    assert store.get(run.id, generated.id).basis == generated.basis
    assert all(item.coverage.snapshot_sha256 in generated.markdown for item in windows)


@pytest.mark.integration
def test_knowledge_keeps_each_excerpt_version_with_validatable_scope_metadata(knowledge):
    engine, factory = knowledge
    client = factory()
    _, windows, _ = saved_excerpt_finding(engine, browser_owner(client))
    items = client.get("/api/v1/knowledge").json()["items"]
    excerpts = [item for item in items if item.get("coverage")]
    assert {item["source_id"] for item in excerpts} == {item.source_id for item in windows}
    for item in excerpts:
        assert item["read_scope"] == "full_text_excerpt"
        assert "FULL TEXT EXCERPT" in item["summary"]
        assert item["bibliography"]["arxiv_id"] == item["coverage"]["arxiv_id"]
        assert item["url"] == "https://arxiv.org/html/2401.01234v2"
    graph = client.get("/api/v1/knowledge/graph").json()
    assert len([node for node in graph["nodes"] if node.get("coverage")]) == 2


@pytest.mark.integration
def test_actual_assessment_basis_and_history_retain_exact_excerpt_coverage(database):
    token = owner(database)
    saved, windows, _ = saved_excerpt_finding(database, token)
    ingestion, jobs, evidence = (
        IngestionRepository(database),
        JobRepository(database),
        EvidenceStore(database),
    )
    watch = ingestion.create(
        token, label="Synthetic excerpt followup", query="Synthetic", source_result_id=saved.id
    )
    ingestion.run_now(token, watch.id)
    lease = jobs.claim_next("synthetic-new-source", task_kinds=("ingest_literature",))
    new = evidence.save_source(
        lease, document("fixture:followup", text="🌱 Synthetic new boundary case.")
    )
    ingestion.commit_page(
        lease,
        "openalex",
        ingestion.snapshot(lease).cursors["openalex"],
        None,
        (new,),
        exhausted=True,
    )
    jobs.finish(lease, status="succeeded", outcome={"model_calls": 0})
    assessment_lease = enqueue(database, token, saved.id, watch.id, [new.version_id])
    store = AssessmentStore(database)
    basis = store.load_basis(assessment_lease)
    assert {ref.coverage.snapshot_id for ref in basis.sources if ref.coverage} == {
        windows[0].coverage.snapshot_id
    }
    result = assessment_result(database, store, assessment_lease, disposition="unchanged")
    stored = store.save(assessment_lease, result)
    assert stored.applied
    assert len([item for item in stored.payload["verified_passages"] if item.get("coverage")]) == 2
    assert len([item for item in stored.payload["observed_sources"] if item.get("coverage")]) == 2
    historical = store.get(token, stored.id)
    assert historical.payload == stored.payload
    assert historical.payload_sha256 == _hash(_canonical(stored.payload))
    assert store.history(token, saved.id)[0].payload == stored.payload
