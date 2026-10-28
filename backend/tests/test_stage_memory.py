"""Metadata-only ordinary memory lead closure, including Ask, without PG."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from test_evidence import document

from polymath.evidence import _canonical, _hash, _metadata
from polymath.memory_models import ResearchMemoryRead
from polymath.research.models import SourceBibliography
from polymath.stage_memory import StageMemoryError, owned_memory_identity_references


def fixture(*, status="answer"):
    run = SimpleNamespace(id=uuid.uuid4(), created_at=datetime(2026, 10, 28, tzinfo=UTC))
    documents = [
        document("lead-a").model_copy(
            update={"bibliography": SourceBibliography(doi="10.1234/alias")}
        ),
        document("lead-b").model_copy(
            update={"bibliography": SourceBibliography(doi="10.1234/alias", arxiv_id="2401.00001")}
        ),
    ]
    handles = [(uuid.uuid4(), uuid.uuid4()) for _ in documents]
    parent = SimpleNamespace(
        id=uuid.uuid4(),
        run_id=uuid.uuid4(),
        status=status,
        created_at=run.created_at - timedelta(minutes=1),
        payload={
            "source_artifacts": [
                {"version_id": str(version), "retrieval_id": str(retrieval)}
                for version, retrieval in handles
            ]
        },
    )
    parent.content_sha256 = _hash(_canonical(parent.payload))
    read = ResearchMemoryRead(
        run_id=run.id,
        parent_result_id=parent.id,
        parent_run_id=parent.run_id,
        parent_content_sha256=parent.content_sha256,
    )
    metadata = [
        SimpleNamespace(
            version_id=version,
            retrieval_id=retrieval,
            source_id=doc.source_id,
            content_kind=doc.content_kind,
            title=doc.title,
            url=doc.url,
            bibliography=doc.bibliography.model_dump(mode="json"),
            coverage=None,
            full_text_snapshot_id=None,
            retrieved_at=doc.retrieved_at,
            extraction_method=doc.extraction_method,
            metadata_sha256=_hash(_canonical(_metadata(doc))),
        )
        for (version, retrieval), doc in zip(handles, documents, strict=True)
    ]

    class Rows:
        def __init__(self, values):
            self.values = values

        def all(self):
            return self.values

    class SavedMetadata:
        def __init__(self):
            self.reads = [read]
            self.statements = []
            self.parent = parent

        def scalar(self, query):
            sql = str(query)
            self.statements.append(sql)
            return "a" * 64 if "SELECT research_run_access.session_hash" in sql else self.parent

        def scalars(self, query):
            self.statements.append(str(query))
            return Rows(self.reads)

        def get(self, cls, identifier):
            return SimpleNamespace(task_kind="research")

        def execute(self, query):
            sql = str(query)
            assert "source_text" not in sql and "browser_sessions" not in sql
            self.statements.append(sql)
            return Rows(metadata)

    return SavedMetadata(), run, parent, read, metadata


@pytest.mark.parametrize("status", ("bridge", "answer"))
def test_owned_bridge_and_ask_leads_preserve_every_alias_without_loading_text(status):
    session, run, _, _, _ = fixture(status=status)
    refs = owned_memory_identity_references(session, run)
    assert len(refs) == 2
    assert refs[0].bibliography.doi == refs[1].bibliography.doi
    assert refs[1].bibliography.arxiv_id == "2401.00001"
    assert all("source_text" not in query for query in session.statements)
    assert all(
        "finding_heads" not in query and "budget_" not in query for query in session.statements
    )


@pytest.mark.parametrize(
    "problem",
    (
        "parent_hash",
        "metadata_hash",
        "unbound_handle",
        "missing_parent",
        "same_run",
        "future",
        "bad_status",
    ),
)
def test_bad_actual_memory_provenance_fails_closed(problem):
    session, run, parent, read, metadata = fixture()
    if problem == "parent_hash":
        read.parent_content_sha256 = "0" * 64
    elif problem == "metadata_hash":
        metadata[0].metadata_sha256 = "0" * 64
    elif problem == "unbound_handle":
        metadata[0].version_id = uuid.uuid4()
    elif problem == "missing_parent":
        session.parent = None
    elif problem == "same_run":
        parent.run_id = read.parent_run_id = run.id
    elif problem == "future":
        parent.created_at = run.created_at
    else:
        parent.status = "failed"
    with pytest.raises(StageMemoryError, match="stage_memory_integrity_failed"):
        owned_memory_identity_references(session, run)


def test_fresh_identity_ceiling_rejects_without_silently_dropping_connector_metadata():
    session, run, *_ = fixture()
    with pytest.raises(StageMemoryError, match="source_identity_context_limit"):
        owned_memory_identity_references(session, run, identity_limit=1)
    assert len(owned_memory_identity_references(session, run, identity_limit=4096)) == 2


def test_memory_receipt_bound_remains_ten_and_never_filters_bad_rows_out():
    session, run, _, read, _ = fixture()
    session.reads = [read] * 11
    with pytest.raises(StageMemoryError, match="stage_memory_integrity_failed"):
        owned_memory_identity_references(session, run)
