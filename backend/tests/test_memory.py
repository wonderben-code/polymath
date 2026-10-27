"""Real PostgreSQL privacy and provenance checks with synthetic saved findings."""

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_evidence import document, result_for
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.evidence import EvidenceError, EvidenceStore
from polymath.jobs import JobRepository, LeaseLost
from polymath.memory import MemoryError, ResearchMemory
from polymath.memory_models import ResearchMemoryRead
from polymath.research.models import InsufficientFinding, MemoryReadReference, ResearchResult, Usage

database = database_fixture
pytestmark = pytest.mark.integration


def owner(engine, label="fixture-session"):
    token_hash = hashlib.sha256(label.encode()).hexdigest()
    with Session(engine) as session, session.begin():
        if session.get(BrowserSession, token_hash) is None:
            session.add(
                BrowserSession(
                    token_hash=token_hash, expires_at=datetime.now(UTC) + timedelta(days=1)
                )
            )
    return token_hash


def assign(engine, run_id, token_hash):
    with Session(engine) as session, session.begin():
        session.add(ResearchRunAccess(run_id=run_id, session_hash=token_hash))


def complete(
    engine,
    token_hash,
    *,
    title="Synthetic memory bridge",
    suffix="",
    extra=False,
    source_title=None,
):
    jobs, store = JobRepository(engine), EvidenceStore(engine)
    run = jobs.create(mode="explore", goal="Synthetic saved finding")
    assign(engine, run.id, token_hash)
    lease = jobs.claim_next("synthetic-memory-parent", lease_seconds=300)
    assert lease.run_id == run.id
    left = document("fixture:left" + suffix)
    if source_title is not None:
        left = left.model_copy(update={"title": source_title})
    right = document("fixture:right" + suffix, text="🌱 Synthetic signals use redundant pathways.")
    result = result_for(left, right)
    result = result.model_copy(
        update={"finding": result.finding.model_copy(update={"title": title})}
    )
    for doc in result.sources:
        store.save_source(lease, doc)
    if extra:
        store.save_source(lease, document("fixture:not-quoted", title="Not quoted by the finding"))
    saved = store.save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    return saved, result


def current(engine, token_hash=None):
    jobs = JobRepository(engine)
    run = jobs.create(mode="explore", goal="Synthetic new investigation")
    if token_hash is not None:
        assign(engine, run.id, token_hash)
    lease = jobs.claim_next("synthetic-memory-reader", lease_seconds=300)
    assert lease.run_id == run.id
    return ResearchMemory(engine, lease), lease


def empty_result(*reads):
    return ResearchResult(
        status="insufficient_evidence",
        finding=InsufficientFinding(
            outcome="insufficient_evidence",
            reason="Synthetic fixtures establish no scientific claim.",
        ),
        memory_reads=tuple(reads),
        usage=Usage(),
    )


def test_search_is_owned_literal_bounded_and_finding_reads_are_idempotent(database):
    token = owner(database)
    saved, _ = complete(database, token, title="Synthetic 50%_bridge")
    foreign, _ = complete(database, owner(database, "another-session"), title="Foreign 50%_bridge")
    memory, lease = current(database, token)
    matches = memory.search("50%_")
    assert [item.result_id for item in matches.findings] == [str(saved.id)]
    assert memory.search("50%_missing").findings == ()
    assert not matches.truncated
    assert memory.validate_scope()
    first = memory.read_finding(str(saved.id))
    assert memory.read_finding(str(saved.id)) == first
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(ResearchMemoryRead)) == 1
    with pytest.raises(MemoryError, match="memory_finding_not_found"):
        memory.read_finding(str(foreign.id))
    assert EvidenceStore(database).list_sources(lease.run_id) == ()


def test_read_original_source_requires_finding_read_and_preserves_exact_snapshot(database):
    token = owner(database)
    saved, result = complete(database, token, extra=True)
    memory, lease = current(database, token)
    artifacts = EvidenceStore(database).list_sources(saved.run_id)
    left = next(item for item in artifacts if item.source_id == result.sources[0].source_id)
    unused = next(item for item in artifacts if item.source_id == "fixture:not-quoted")
    with pytest.raises(MemoryError, match="memory_source_not_found"):
        memory.read_source(str(saved.id), str(left.retrieval_id))
    found = memory.read_finding(str(saved.id))
    assert len(found.sources) == 2
    original = memory.read_source(str(saved.id), str(left.retrieval_id))
    assert original == result.sources[0]
    assert original.content_sha256 == result.sources[0].content_sha256
    assert EvidenceStore(database).list_sources(lease.run_id) == ()
    stored = EvidenceStore(database).save_source(lease, original)
    assert stored.retrieval_id == left.retrieval_id
    with pytest.raises(MemoryError, match="memory_source_not_found"):
        memory.read_source(str(saved.id), str(unused.retrieval_id))


def test_shared_content_does_not_leak_another_owners_retrieval_metadata(database):
    token = owner(database)
    saved, _ = complete(database, token, title="Owned finding")
    foreign, _ = complete(
        database,
        owner(database, "foreign"),
        title="Private foreign finding",
        source_title="Private metadata title visible only to another browser",
    )
    memory, _ = current(database, token)
    assert len(memory.search("Synthetic").findings) == 1
    found = memory.read_finding(str(saved.id))
    foreign_sources = EvidenceStore(database).list_sources(foreign.run_id)
    assert found.reference.title == "Owned finding"
    assert all("Private metadata" not in source.source.title for source in found.sources)
    owned_sources = EvidenceStore(database).list_sources(saved.run_id)
    owned_left = next(item for item in owned_sources if item.source_id == "fixture:left")
    foreign_left = next(item for item in foreign_sources if item.source_id == "fixture:left")
    assert owned_left.version_id == foreign_left.version_id
    assert owned_left.retrieval_id != foreign_left.retrieval_id
    # The same version may legitimately be shared; the read still authorizes the
    # finding/result before it can expose any retrieval metadata or original text.
    with pytest.raises(MemoryError):
        memory.read_source(str(foreign.id), str(foreign_sources[0].retrieval_id))
    with pytest.raises(MemoryError):
        memory.read_source(str(saved.id), str(foreign_left.retrieval_id))


@pytest.mark.parametrize("scope", ["missing", "expired"])
def test_missing_or_expired_session_has_no_memory_scope(database, scope):
    token = owner(database)
    saved, _ = complete(database, token)
    memory, _ = current(database, token if scope == "expired" else None)
    if scope == "expired":
        with Session(database) as session, session.begin():
            session.execute(
                update(BrowserSession)
                .where(BrowserSession.token_hash == token)
                .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
            )
    assert not memory.validate_scope()
    assert memory.scope_fingerprint() is None
    assert memory.search("Synthetic").findings == ()
    with pytest.raises(MemoryError, match="memory_finding_not_found"):
        memory.read_finding(str(saved.id))


def test_scope_fingerprint_is_opaque_and_changes_with_run_ownership(database):
    first, second = owner(database), owner(database, "replacement-session")
    memory, lease = current(database, first)
    fingerprint = memory.scope_fingerprint()
    assert fingerprint is not None and len(fingerprint) == 64
    assert fingerprint != first
    assert memory.scope_fingerprint() == fingerprint
    with Session(database) as session, session.begin():
        session.execute(
            update(ResearchRunAccess)
            .where(ResearchRunAccess.run_id == lease.run_id)
            .values(session_hash=second)
        )
    assert memory.scope_fingerprint() not in (None, fingerprint, second)


def test_memory_excludes_future_results_and_self_even_with_same_session(database):
    token = owner(database)
    saved, result = complete(database, token)
    memory, lease = current(database, token)
    # A future result cannot be a parent of an already-created investigation.
    later, _ = complete(database, token, title="Future synthetic finding", suffix="-later")
    assert [item.result_id for item in memory.search("Synthetic").findings] == [str(saved.id)]
    with pytest.raises(MemoryError):
        memory.read_finding(str(later.id))
    store = EvidenceStore(database)
    for source in result.sources:
        store.save_source(lease, source)
    own = store.save_result(lease, result)
    with pytest.raises(MemoryError):
        memory.read_finding(str(own.id))


def test_stale_lease_cannot_search_read_or_reauthorize_cached_memory(database):
    token = owner(database)
    saved, _ = complete(database, token)
    memory, lease = current(database, token)
    JobRepository(database).request_cancel(lease.run_id)
    for operation in (
        lambda: memory.search("Synthetic"),
        lambda: memory.read_finding(str(saved.id)),
        memory.validate_scope,
    ):
        with pytest.raises(LeaseLost):
            operation()


def test_finding_provenance_cannot_be_omitted_fabricated_or_duplicated(database):
    token = owner(database)
    saved, _ = complete(database, token)
    memory, lease = current(database, token)
    found = memory.read_finding(str(saved.id))
    read = MemoryReadReference(
        result_id=found.reference.result_id,
        run_id=found.reference.run_id,
        content_sha256=found.reference.content_sha256,
    )
    store = EvidenceStore(database)
    for result in (
        empty_result(),
        empty_result(read.model_copy(update={"content_sha256": "0" * 64})),
        empty_result(read, read),
    ):
        with pytest.raises(EvidenceError, match="memory_read_provenance_mismatch"):
            store.save_result(lease, result)
    assert store.save_result(lease, empty_result(read)).payload["memory_reads"] == [
        read.model_dump(mode="json")
    ]


def test_a_fabricated_memory_read_record_cannot_launder_a_hash(database):
    token = owner(database)
    saved, _ = complete(database, token)
    _, lease = current(database, token)
    with Session(database) as session, session.begin():
        session.add(
            ResearchMemoryRead(
                run_id=lease.run_id,
                parent_result_id=saved.id,
                parent_run_id=saved.run_id,
                parent_content_sha256="0" * 64,
            )
        )
    read = MemoryReadReference(
        result_id=str(saved.id), run_id=str(saved.run_id), content_sha256="0" * 64
    )
    with pytest.raises(EvidenceError, match="memory_read_provenance_mismatch"):
        EvidenceStore(database).save_result(lease, empty_result(read))


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_memory_read_history_is_append_only(database, operation):
    token = owner(database)
    saved, _ = complete(database, token)
    memory, lease = current(database, token)
    memory.read_finding(str(saved.id))
    with pytest.raises(IntegrityError), Session(database) as session, session.begin():
        statement = (
            update(ResearchMemoryRead).values(parent_content_sha256="0" * 64)
            if operation == "update"
            else delete(ResearchMemoryRead)
        )
        session.execute(statement.where(ResearchMemoryRead.run_id == lease.run_id))


def test_incompatible_downgrade_preserves_memory_provenance(database):
    token = owner(database)
    saved, _ = complete(database, token)
    memory, _ = current(database, token)
    memory.read_finding(str(saved.id))
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with pytest.raises(RuntimeError, match="would lose data"):
        command.downgrade(config, "0007_answer_evidence")
    assert memory.read_finding(str(saved.id)).reference.result_id == str(saved.id)


def test_search_is_capped_and_read_bound_is_per_run(database):
    token = owner(database)
    saved = [complete(database, token, title=f"Synthetic memory {index}")[0] for index in range(11)]
    memory, _ = current(database, token)
    matches = memory.search("Synthetic")
    assert len(matches.findings) == 10
    assert matches.truncated
    for item in saved[:10]:
        memory.read_finding(str(item.id))
    with pytest.raises(MemoryError, match="memory_read_limit"):
        memory.read_finding(str(saved[10].id))


@pytest.mark.parametrize("query", ["", "x" * 201, "bad\x00query", "bad\nquery", "bad\ud800query"])
def test_invalid_memory_queries_are_rejected_without_exposing_input(database, query):
    memory, _ = current(database)
    with pytest.raises(MemoryError, match="^invalid_memory_query$"):
        memory.search(query)
