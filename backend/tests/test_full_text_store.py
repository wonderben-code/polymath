"""Immutable full-text storage tests with synthetic HTML text and real PostgreSQL."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from test_evidence import document, result_for
from test_memory import assign, owner
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.evidence import EvidenceError, EvidenceStore, _canonical, _hash, _metadata
from polymath.evidence_models import SourceRetrieval, SourceVersion
from polymath.full_text import FullTextError, FullTextStore, owned_identity_context
from polymath.full_text_contracts import ExtractedFullText, FullTextHeading
from polymath.full_text_models import FullTextSnapshot, RunFullTextIndex
from polymath.jobs import JobRepository, LeaseLost
from polymath.literature.identity import distinct_paper_count
from polymath.models import ResearchRun
from polymath.research.models import SourceBibliography, SourceReference

pytestmark = pytest.mark.integration
database = database_fixture


def reference(*, title="Synthetic arXiv fixture", source_id="arxiv:2610.12345", **changes):
    values = {
        "source_id": source_id,
        "title": title,
        "url": "https://arxiv.org/abs/2610.12345",
        "bibliography": SourceBibliography(arxiv_id="2610.12345", doi="10.1234/synthetic"),
    }
    values.update(changes)
    return SourceReference(**values)


def extracted(*, body=None, version=2, retrieved_at=None, **changes):
    body = body or (
        "🌌 Introduction\nSynthetic redundancy protects messages against noise.\n"
        "Methods\nSynthetic comparison evaluates explicit assumptions only.\n"
        "Limitations\nThese synthetic documents establish no scientific result."
    )
    headings = tuple(
        FullTextHeading(
            title=title, level=1, start=body.index(title), end=body.index(title) + len(title)
        )
        for title in ("Introduction", "Methods", "Limitations")
        if title in body
    )
    values = {
        "arxiv_id": "2610.12345",
        "arxiv_version": version,
        "url": f"https://arxiv.org/html/2610.12345v{version}",
        "text": body,
        "raw_html_sha256": hashlib.sha256(("<article>" + body + "</article>").encode()).hexdigest(),
        "retrieved_at": retrieved_at or datetime(2026, 10, 28, tzinfo=UTC),
        "headings": headings,
        "limitations": ("Synthetic extracted HTML, not a real paper.",),
    }
    values.update(changes)
    return ExtractedFullText(**values)


def reader(engine, identity="full-text-owner"):
    token = owner(engine, identity)
    jobs = JobRepository(engine)
    run = jobs.create(mode="explore", goal="Synthetic exact full-text windows")
    assign(engine, run.id, token)
    lease = jobs.claim_next("synthetic-full-text", lease_seconds=300, task_kinds=("research",))
    assert lease.run_id == run.id
    return token, lease, FullTextStore(engine)


def indexed(engine, **changes):
    token, lease, store = reader(engine)
    body = extracted(**changes)
    index = store.save_snapshot(lease, reference(), body)
    return token, lease, store, body, index


def test_index_is_durable_metadata_and_replay_preserves_exact_pinned_body(database):
    _, lease, store, body, index = indexed(database)
    assert store.get_index(lease, reference().source_id) == index
    assert index.total_chars == len(body.text)
    assert index.snapshot_sha256 == hashlib.sha256(body.text.encode()).hexdigest()
    assert "text" not in index.model_dump(mode="json")
    assert EvidenceStore(database).list_sources(lease.run_id) == ()
    changed = extracted(body=body.text + " Updated later.", version=3)
    assert store.save_snapshot(lease, reference(), changed) == index
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(FullTextSnapshot)) == 1
        assert session.scalar(select(func.count()).select_from(RunFullTextIndex)) == 1
        snapshot = session.get(FullTextSnapshot, uuid.UUID(index.snapshot_id))
        assert snapshot.source_text == body.text
        context = owned_identity_context(session, lease.run_id)
        assert context[0].bibliography.arxiv_version == 2


def test_exact_unicode_windows_are_saved_reads_with_complete_optional_provenance(database):
    token, lease, store, body, index = indexed(database)
    window = store.read_window(lease, index.snapshot_id, 0, 68)
    assert window.text == body.text[:68] and window.text.startswith("🌌")
    assert window.coverage.total == len(body.text)
    assert window.coverage.end == len(window.text) == 68
    assert window.source_id == f"arxiv-html:{index.snapshot_id}:0:68"
    assert window.url == index.url and window.bibliography.arxiv_version == 2
    sources = EvidenceStore(database).list_sources(lease.run_id)
    assert len(sources) == 1
    assert store.get_saved_window(token, lease.run_id, sources[0].retrieval_id) == window
    assert store.read_window(lease, index.snapshot_id, 0, 68) == window
    with Session(database) as session:
        retrieval = session.get(SourceRetrieval, sources[0].retrieval_id)
        assert retrieval.coverage == window.coverage.model_dump(mode="json")
        assert str(retrieval.full_text_snapshot_id) == index.snapshot_id
        assert retrieval.metadata_sha256 == _hash(_canonical(_metadata(window)))


@pytest.mark.parametrize(
    "start,end",
    [(True, 20), (0, False), (-1, 10), (10, 10), (10, 9), (0, 8001), (0.0, 20), (0, 1000)],
)
def test_window_boundaries_reject_invalid_or_overwide_ranges(database, start, end):
    _, lease, store, _, index = indexed(database)
    with pytest.raises(FullTextError, match="full_text_invalid_window"):
        store.read_window(lease, index.snapshot_id, start, end)
    assert EvidenceStore(database).list_sources(lease.run_id) == ()


def test_multiple_windows_are_same_paper_and_cannot_fake_independent_evidence(database):
    _, lease, store, body, index = indexed(database)
    one = store.read_window(lease, index.snapshot_id, 0, 68)
    two = store.read_window(lease, index.snapshot_id, 68, len(body.text))
    assert one.source_id != two.source_id
    assert one.url == two.url and distinct_paper_count((one, two)) == 1
    with pytest.raises(EvidenceError, match="distinct_evidence_sources_required"):
        EvidenceStore(database).save_result(lease, result_for(one, two))


def test_independent_papers_allow_multiple_windows_from_one_snapshot(database):
    from polymath.research.models import EvidenceQuote, VerifiedQuote

    _, lease, store, body, index = indexed(database)
    one = store.read_window(lease, index.snapshot_id, 0, 68)
    two = store.read_window(lease, index.snapshot_id, 68, len(body.text))
    independent = document(
        "fixture:independent", text="🌱 A different synthetic paper models pathways."
    )
    EvidenceStore(database).save_source(lease, independent)
    result = result_for(one, independent)
    added = EvidenceQuote(
        source_id=two.source_id,
        side="left",
        quote=two.text[2:],
        supports="An additional window from the same paper, not corroboration.",
    )
    start = 2
    quote = VerifiedQuote(
        source_id=two.source_id,
        side="left",
        quote=added.quote,
        supports=added.supports,
        start=start,
        end=len(two.text),
        source_sha256=two.content_sha256,
        content_kind="full_text",
    )
    result = result.model_copy(
        update={
            "finding": result.finding.model_copy(
                update={"evidence": [result.finding.evidence[0], added, result.finding.evidence[1]]}
            ),
            "quotes": (result.quotes[0], quote, result.quotes[1]),
            "sources": (one, two, independent),
        }
    )
    saved = EvidenceStore(database).save_result(lease, result)
    assert saved.status == "bridge" and len(saved.payload["sources"]) == 3


def test_forged_slice_hash_and_owned_metadata_cannot_be_saved(database):
    _, lease, store, _, index = indexed(database)
    window = store.read_window(lease, index.snapshot_id, 0, 68)
    for forged in (
        window.model_copy(update={"text": "x" * len(window.text)}),
        window.model_copy(update={"title": "An invented retrieval title"}),
        window.model_copy(
            update={"coverage": window.coverage.model_copy(update={"snapshot_sha256": "0" * 64})}
        ),
    ):
        with pytest.raises(EvidenceError, match="source_coverage_not_authorized"):
            EvidenceStore(database).save_source(lease, forged)
    assert len(EvidenceStore(database).list_sources(lease.run_id)) == 1


def test_shared_snapshot_dedup_keeps_each_owners_metadata_and_retrieval_time(database):
    first_token, first, first_store, body, original_index = indexed(database)
    second_token, second, store = reader(database, "second-full-text-owner")
    other_reference = reference(title="Second owner's frozen title")
    other_body = extracted(retrieved_at=body.retrieved_at + timedelta(hours=3))
    index = store.save_snapshot(second, other_reference, other_body)
    assert index.snapshot_id == original_index.snapshot_id
    assert index.source.title == other_reference.title
    assert index.retrieved_at == other_body.retrieved_at
    assert first_store.get_index(first, reference().source_id) == original_index
    window = store.read_window(second, index.snapshot_id, 0, 68)
    assert window.title == other_reference.title and window.retrieved_at == other_body.retrieved_at
    first_window = first_store.read_window(first, original_index.snapshot_id, 0, 68)
    assert first_window.title != window.title
    first_source = EvidenceStore(database).list_sources(first.run_id)[0]
    with pytest.raises(FullTextError, match="full_text_not_found"):
        store.get_saved_window(second_token, first.run_id, first_source.retrieval_id)
    assert first_token != second_token
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(FullTextSnapshot)) == 1


def test_same_snapshot_second_alias_index_is_explicitly_rejected(database):
    _, lease, store, body, index = indexed(database)
    alias = reference(
        source_id="doi:10.1234/synthetic",
        title="Alias frozen metadata",
        url="https://doi.org/10.1234/synthetic",
    )
    with pytest.raises(FullTextError, match="full_text_snapshot_already_indexed"):
        store.save_snapshot(lease, alias, body)
    assert store.read_window(lease, index.snapshot_id, 0, 68).title == reference().title


def test_owned_historical_window_copy_does_not_grant_index_or_new_ranges(database):
    token, first, store, body, index = indexed(database)
    window = store.read_window(first, index.snapshot_id, 0, 68)
    _, later, later_store = reader(database)
    # Same browser owns both runs; exact saved window imports are allowed.
    copied = EvidenceStore(database).save_source(later, window)
    assert later_store.get_saved_window(token, later.run_id, copied.retrieval_id) == window
    assert later_store.get_index(later, reference().source_id) is None
    with pytest.raises(FullTextError, match="full_text_not_found"):
        later_store.read_window(later, index.snapshot_id, 68, len(body.text))
    other_window = store.read_window(first, index.snapshot_id, 68, len(body.text))
    foreign, foreign_lease, foreign_store = reader(database, "foreign-full-text")
    with pytest.raises(EvidenceError, match="source_coverage_not_authorized"):
        EvidenceStore(database).save_source(foreign_lease, other_window)
    assert foreign_store.get_index(foreign_lease, reference().source_id) is None
    assert foreign != token


def test_forged_new_range_cannot_expand_an_owned_historical_copy(database):
    _, first, store, body, index = indexed(database)
    window = store.read_window(first, index.snapshot_id, 0, 68)
    _, later, _ = reader(database)
    EvidenceStore(database).save_source(later, window)
    coverage = window.coverage.model_copy(update={"start": 68, "end": len(body.text)})
    forged = window.model_copy(
        update={
            "source_id": f"arxiv-html:{index.snapshot_id}:68:{len(body.text)}",
            "coverage": coverage,
            "text": body.text[68:],
        }
    )
    with pytest.raises(EvidenceError, match="source_coverage_not_authorized"):
        EvidenceStore(database).save_source(later, forged)


def test_index_and_window_operations_are_lease_and_browser_fenced(database):
    _, lease, store, body, index = indexed(database)
    JobRepository(database).request_cancel(lease.run_id)
    for operation in (
        lambda: store.get_index(lease, reference().source_id),
        lambda: store.save_snapshot(lease, reference(), body),
        lambda: store.read_window(lease, index.snapshot_id, 0, 68),
    ):
        with pytest.raises(LeaseLost):
            operation()


def test_expired_browser_cannot_index_read_or_copy_windows(database):
    token, lease, store, body, index = indexed(database)
    window = store.read_window(lease, index.snapshot_id, 0, 68)
    with database.begin() as connection:
        connection.execute(
            update(BrowserSession)
            .where(BrowserSession.token_hash == token)
            .values(expires_at=func.clock_timestamp())
        )
    for operation in (
        lambda: store.get_index(lease, reference().source_id),
        lambda: store.read_window(lease, index.snapshot_id, 0, 68),
        lambda: store.save_snapshot(lease, reference(), body),
    ):
        with pytest.raises(FullTextError, match="full_text_not_found"):
            operation()
    with pytest.raises(EvidenceError, match="source_coverage_not_authorized"):
        EvidenceStore(database).save_source(lease, window)


def test_snapshot_index_and_window_provenance_are_database_immutable(database):
    _, lease, store, _, index = indexed(database)
    store.read_window(lease, index.snapshot_id, 0, 68)
    for model, values in (
        (FullTextSnapshot, {"source_text": "Different body"}),
        (RunFullTextIndex, {"reference_sha256": "0" * 64}),
        (SourceRetrieval, {"coverage": None}),
    ):
        for statement in (update(model).values(**values), delete(model)):
            with pytest.raises(IntegrityError):
                with database.begin() as connection:
                    connection.execute(statement)


def test_legacy_absence_preserves_exact_source_hash_and_source_json(database):
    _, lease, _ = reader(database)
    legacy = document()
    assert "coverage" not in legacy.model_dump(mode="json")
    expected = {
        "title": legacy.title,
        "url": legacy.url,
        "retrieved_at": legacy.retrieved_at.astimezone(UTC).isoformat(),
        "extraction_method": legacy.extraction_method,
    }
    assert _metadata(legacy) == expected
    saved = EvidenceStore(database).save_source(lease, legacy)
    assert EvidenceStore(database).get_document(lease.run_id, saved.retrieval_id) == legacy
    with Session(database) as session:
        retrieval = session.get(SourceRetrieval, saved.retrieval_id)
        assert retrieval.coverage is None and retrieval.full_text_snapshot_id is None
        assert retrieval.metadata_sha256 == _hash(_canonical(expected))


def test_migration_matches_models_and_refuses_full_text_history_loss(database):
    indexed(database)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.check(config)
    with database.connect() as connection:
        before = connection.scalar(text("SELECT version_num FROM alembic_version"))
    with pytest.raises(DBAPIError):
        command.downgrade(config, "0016_finding_assessments")
    with database.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == before


def test_coverage_cannot_be_stripped_to_bypass_authorization(database):
    _, lease, store, _, index = indexed(database)
    window = store.read_window(lease, index.snapshot_id, 0, 68)
    stripped = window.model_copy(update={"coverage": None})
    with pytest.raises(EvidenceError, match="invalid_source_document"):
        EvidenceStore(database).save_source(lease, stripped)


def test_database_rejects_forged_window_body_even_with_valid_coverage_json(database):
    _, lease, store, _, index = indexed(database)
    window = store.read_window(lease, index.snapshot_id, 0, 68)
    wrong = "x" * len(window.text)
    with pytest.raises(IntegrityError):
        with Session(database) as session, session.begin():
            version = SourceVersion(
                source_id=window.source_id,
                content_sha256=_hash(wrong),
                content_kind="full_text",
                source_text=wrong,
            )
            session.add(version)
            session.flush()
            session.add(
                SourceRetrieval(
                    version_id=version.id,
                    metadata_sha256="1" * 64,
                    title=window.title,
                    url=window.url,
                    retrieved_at=window.retrieved_at,
                    extraction_method=window.extraction_method,
                    bibliography=window.bibliography.model_dump(mode="json"),
                    coverage=window.coverage.model_dump(mode="json"),
                    full_text_snapshot_id=uuid.UUID(index.snapshot_id),
                )
            )


@pytest.mark.parametrize(
    "changes",
    [
        {"source_id": "arxiv:2610.54321"},
        {"url": "https://arxiv.org/abs/2610.12345v1"},
        {"bibliography": SourceBibliography(arxiv_id="2610.54321")},
        {"bibliography": SourceBibliography(arxiv_id="2610.12345", arxiv_version=1)},
    ],
)
def test_index_cannot_mint_conflicting_known_arxiv_identity(database, changes):
    _, lease, store = reader(database)
    with pytest.raises(FullTextError, match="full_text_identity_conflict"):
        store.save_snapshot(lease, reference(**changes), extracted())
    assert store.get_index(lease, reference().source_id) is None
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(FullTextSnapshot)) == 0


def test_index_alias_context_prevents_two_old_ids_counting_as_two_papers(database):
    _, lease, store = reader(database)
    arxiv_only = document(
        "arxiv:2610.12345",
        url="https://arxiv.org/abs/2610.12345",
        bibliography=SourceBibliography(arxiv_id="2610.12345"),
    )
    doi_only = document(
        "doi:10.1234/synthetic",
        url="https://doi.org/10.1234/synthetic",
        bibliography=SourceBibliography(doi="10.1234/synthetic"),
        text="🌱 The same synthetic paper can have a DOI and arXiv alias.",
    )
    evidence = EvidenceStore(database)
    evidence.save_source(lease, arxiv_only)
    evidence.save_source(lease, doi_only)
    assert distinct_paper_count((arxiv_only, doi_only)) == 2
    # This index is identity metadata only, and reveals the alias connector.
    store.save_snapshot(lease, reference(), extracted())
    assert len(evidence.list_sources(lease.run_id)) == 2
    with pytest.raises(EvidenceError, match="distinct_evidence_sources_required"):
        evidence.save_result(lease, result_for(arxiv_only, doi_only))


def test_snapshot_conflict_wait_rechecks_browser_before_owned_index_commit(database):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import event

    first_token, first, first_store = reader(database)
    body, ref = extracted(), reference()
    # Build a correct uncommitted shared body in a separate transaction, so the
    # worker waits on ON CONFLICT rather than a run/browser lock.
    from polymath.full_text import _provenance

    snapshot_id = uuid.uuid4()
    waiting = Event()

    def observe_insert(connection, cursor, statement, parameters, context, executemany):
        if "INSERT INTO arxiv_full_text_snapshots" in statement and "ON CONFLICT" in statement:
            waiting.set()

    event.listen(database, "before_cursor_execute", observe_insert)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with Session(database) as blocker, blocker.begin():
                blocker.add(
                    FullTextSnapshot(
                        id=snapshot_id,
                        arxiv_id=body.arxiv_id,
                        arxiv_version=body.arxiv_version,
                        url=body.url,
                        source_text=body.text,
                        snapshot_sha256=body.snapshot_sha256,
                        raw_html_sha256=body.raw_html_sha256,
                        extractor_version=body.extractor_version,
                        headings=[item.model_dump(mode="json") for item in body.headings],
                        headings_truncated=body.headings_truncated,
                        limitations=list(body.limitations),
                        provenance_sha256=_hash(_canonical(_provenance(body))),
                    )
                )
                blocker.flush()
                future = pool.submit(first_store.save_snapshot, first, ref, body)
                assert waiting.wait(timeout=10)
                with database.begin() as connection:
                    connection.execute(
                        update(BrowserSession)
                        .where(BrowserSession.token_hash == first_token)
                        .values(expires_at=func.clock_timestamp())
                    )
            with pytest.raises(FullTextError, match="full_text_not_found"):
                future.result(timeout=10)
    finally:
        event.remove(database, "before_cursor_execute", observe_insert)
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(RunFullTextIndex)) == 0
        assert session.scalar(select(func.count()).select_from(FullTextSnapshot)) == 1


def test_frozen_successful_search_candidate_is_identity_context_without_index_or_evidence(database):
    from polymath.research.models import ArxivFullTextLocator, FullTextAvailability, SearchResults

    _, lease, store = reader(database)
    arxiv_only = document(
        "arxiv:2610.12345",
        url="https://arxiv.org/abs/2610.12345",
        bibliography=SourceBibliography(arxiv_id="2610.12345"),
    )
    doi_only = document(
        "doi:10.1234/synthetic",
        url="https://doi.org/10.1234/synthetic",
        bibliography=SourceBibliography(doi="10.1234/synthetic"),
        text="🌱 A provider-declared DOI alias still names the same paper.",
    )
    evidence = EvidenceStore(database)
    evidence.save_source(lease, arxiv_only)
    evidence.save_source(lease, doi_only)
    source = SourceReference(
        source_id=doi_only.source_id,
        title=doi_only.title,
        url=doi_only.url,
        bibliography=doi_only.bibliography,
    )
    payload = SearchResults(
        sources=(source,),
        full_text=(
            FullTextAvailability(
                source_id=source.source_id,
                status="candidate",
                locator=ArxivFullTextLocator(arxiv_id="2610.12345"),
            ),
        ),
    ).model_dump(mode="json")
    with database.begin() as connection:
        connection.execute(
            update(ResearchRun)
            .where(ResearchRun.id == lease.run_id)
            .values(
                checkpoint={
                    "tool_observations": {
                        "tool-0001": {"hash": "1" * 64, "payload": payload},
                        "tool-0002": {"hash": "2" * 64, "payload": payload},
                    }
                }
            )
        )
    context = store.owned_identity_context(lease)
    assert len(context) == 1  # Identical alias sets are deduplicated, never truncated.
    assert distinct_paper_count((arxiv_only, doi_only), context=context) == 1
    assert store.get_index(lease, source.source_id) is None
    assert len(evidence.list_sources(lease.run_id)) == 2
    with pytest.raises(EvidenceError, match="distinct_evidence_sources_required"):
        evidence.save_result(lease, result_for(arxiv_only, doi_only))


def test_ambiguous_frozen_search_candidate_adds_no_guessed_paper_alias(database):
    from polymath.research.models import FullTextAvailability, SearchResults

    _, lease, store = reader(database)
    source = reference(
        source_id="doi:10.1234/synthetic",
        url="https://doi.org/10.1234/synthetic",
        bibliography=SourceBibliography(doi="10.1234/synthetic"),
    )
    payload = SearchResults(
        sources=(source,),
        full_text=(FullTextAvailability(source_id=source.source_id, status="ambiguous"),),
    ).model_dump(mode="json")
    with database.begin() as connection:
        connection.execute(
            update(ResearchRun)
            .where(ResearchRun.id == lease.run_id)
            .values(
                checkpoint={
                    "tool_observations": {"tool-0001": {"hash": "1" * 64, "payload": payload}}
                }
            )
        )
    assert store.owned_identity_context(lease) == ()


def test_identity_context_overflow_fails_closed_without_truncating_connectors(database):
    from polymath.research.models import ArxivFullTextLocator, FullTextAvailability, SearchResults

    _, lease, store = reader(database)
    observations = {}
    for index in range(257):
        source = reference(
            source_id=f"fixture:identity:{index}",
            url=f"https://example.org/identity/{index}",
            bibliography=None,
        )
        payload = SearchResults(
            sources=(source,),
            full_text=(
                FullTextAvailability(
                    source_id=source.source_id,
                    status="candidate",
                    locator=ArxivFullTextLocator(arxiv_id="2610.12345"),
                ),
            ),
        ).model_dump(mode="json")
        observations[f"tool-{index:04d}"] = {"hash": "1" * 64, "payload": payload}
    with database.begin() as connection:
        connection.execute(
            update(ResearchRun)
            .where(ResearchRun.id == lease.run_id)
            .values(checkpoint={"tool_observations": observations})
        )
    with pytest.raises(FullTextError, match="full_text_identity_context_limit"):
        store.owned_identity_context(lease)


def test_window_source_conflict_wait_rechecks_browser_before_read_commit(database):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import event

    from polymath.evidence_models import RunEvidenceSource
    from polymath.research.models import SourceCoverage, SourceDocument

    token, lease, store, body, index = indexed(database)
    window = SourceDocument(
        source_id=f"arxiv-html:{index.snapshot_id}:0:68",
        title=index.source.title,
        url=index.url,
        text=body.text[:68],
        content_kind="full_text",
        retrieved_at=index.retrieved_at,
        extraction_method=index.extractor_version,
        bibliography=SourceBibliography(
            arxiv_id=index.arxiv_id, arxiv_version=index.arxiv_version, doi="10.1234/synthetic"
        ),
        coverage=SourceCoverage(
            snapshot_id=index.snapshot_id,
            snapshot_sha256=index.snapshot_sha256,
            arxiv_id=index.arxiv_id,
            arxiv_version=index.arxiv_version,
            start=0,
            end=68,
            total=index.total_chars,
        ),
    )
    waiting = Event()

    def observe_insert(connection, cursor, statement, parameters, context, executemany):
        if "INSERT INTO evidence_source_versions" in statement and "ON CONFLICT" in statement:
            waiting.set()

    event.listen(database, "before_cursor_execute", observe_insert)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with Session(database) as blocker, blocker.begin():
                blocker.add(
                    SourceVersion(
                        source_id=window.source_id,
                        content_sha256=window.content_sha256,
                        content_kind="full_text",
                        source_text=window.text,
                    )
                )
                blocker.flush()
                future = pool.submit(EvidenceStore(database).save_source, lease, window)
                assert waiting.wait(timeout=10)
                with database.begin() as connection:
                    connection.execute(
                        update(BrowserSession)
                        .where(BrowserSession.token_hash == token)
                        .values(expires_at=func.clock_timestamp())
                    )
            with pytest.raises(EvidenceError, match="source_coverage_not_authorized"):
                future.result(timeout=10)
    finally:
        event.remove(database, "before_cursor_execute", observe_insert)
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(SourceRetrieval)) == 0
        assert session.scalar(select(func.count()).select_from(RunEvidenceSource)) == 0


def test_window_accepts_exact_eight_thousand_unicode_characters(database):
    body = "🌌" + "x" * 7999 + " remaining text"
    _, lease, store, _, index = indexed(database, body=body)
    window = store.read_window(lease, index.snapshot_id, 0, 8000)
    assert window.text == body[:8000]
    assert len(window.text) == 8000 and len(window.text.encode("utf-8")) > 8000
    assert window.coverage.end == 8000 and window.coverage.total == len(body)


def test_ownerless_legacy_research_identity_context_is_empty_without_full_text_authority(database):
    jobs = JobRepository(database)
    run = jobs.create(mode="explore", goal="Synthetic legacy internal research")
    lease = jobs.claim_next("ownerless-legacy", lease_seconds=300)
    assert lease.run_id == run.id
    store = FullTextStore(database)
    assert store.owned_identity_context(lease) == ()
    for operation in (
        lambda: store.get_index(lease, reference().source_id),
        lambda: store.save_snapshot(lease, reference(), extracted()),
        lambda: store.read_window(lease, uuid.uuid4(), 0, 20),
    ):
        with pytest.raises(FullTextError, match="full_text_not_found"):
            operation()
    with database.begin() as connection:
        connection.execute(
            update(ResearchRun)
            .where(ResearchRun.id == lease.run_id)
            .values(
                checkpoint={
                    "tool_observations": {
                        "tool-0001": {
                            "payload": {
                                "full_text": [
                                    {"source_id": reference().source_id, "status": "candidate"}
                                ]
                            }
                        }
                    }
                }
            )
        )
    with pytest.raises(FullTextError, match="full_text_not_found"):
        store.owned_identity_context(lease)


def test_maximum_unicode_heading_and_limitation_dtos_fit_bounded_storage(database):
    _, lease, store = reader(database)
    titles = [f"{index:02d}" + "🌌" * 198 for index in range(80)]
    body = "\n".join(titles)
    headings, start = [], 0
    for title in titles:
        headings.append(FullTextHeading(title=title, level=1, start=start, end=start + len(title)))
        start += len(title) + 1
    payload = extracted(
        body=body, headings=tuple(headings), limitations=tuple("🧪" * 1000 for _ in range(8))
    )
    index = store.save_snapshot(lease, reference(), payload)
    assert len(index.headings) == 80 and len(index.limitations) == 8
    assert store.get_index(lease, reference().source_id) == index
