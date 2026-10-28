"""Current-head consumers with synthetic, explicitly seeded assessment snapshots.

AssessmentStore tests cover the real cited finalization/CAS transaction. These
tests vary its persisted head and candidate state to exercise existing consumers.
"""

import hashlib
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_artifact_api import artifact_request, complete_artifact, draft_result
from test_artifact_api import artifacts_api as artifacts_api_fixture
from test_evidence import document
from test_knowledge import knowledge as knowledge_fixture
from test_knowledge import seed
from test_lineage import parents_for, selected
from test_memory import assign, complete, current, owner
from test_run_api import bootstrap
from test_worker import database as database_fixture

from polymath.artifact_models import ArtifactRequest
from polymath.artifacts import ArtifactError, ArtifactStore, source_for_request
from polymath.assessment_models import (
    AssessmentInputSource,
    AssessmentRequest,
    FindingAssessment,
    FindingHead,
)
from polymath.evidence import EvidenceStore, _canonical, _hash
from polymath.evidence_models import EvidenceResult
from polymath.ingestion import IngestionError, IngestionRepository
from polymath.jobs import JobRepository
from polymath.lineage import LineageError, get_parent_refs
from polymath.lineage_models import ResearchRunParent
from polymath.memory import MemoryError
from polymath.models import ResearchRun
from polymath.run_api import COOKIE_NAME

knowledge = knowledge_fixture
artifacts_api = artifacts_api_fixture
database = database_fixture
pytestmark = pytest.mark.integration


def browser_owner(client):
    bootstrap(client)
    return hashlib.sha256(client.cookies.get(COOKIE_NAME).encode("ascii")).hexdigest()


def candidate(engine, owner_hash, root_id, *, title="Synthetic revised finding", suffix=""):
    """Seed a stored positive candidate with its pinned root; never run a model."""
    saved, result = complete(engine, owner_hash, title=title, suffix=suffix)
    watch = IngestionRepository(engine).create(
        owner_hash, label="Synthetic assessment input", query="Synthetic literature"
    )
    with Session(engine) as session, session.begin():
        root = session.get(EvidenceResult, root_id)
        session.get(ResearchRun, saved.run_id).task_kind = "reassess_finding"
        session.add(
            AssessmentRequest(
                run_id=saved.run_id,
                root_result_id=root.id,
                source_result_id=root.id,
                source_sha256=root.content_sha256,
                watch_id=watch.id,
                expected_head_revision=0,
                basis={},
                basis_sha256=_hash(_canonical({})),
            )
        )
    return saved, result


def head(engine, root_id, current_id=None, *, state="supported"):
    with Session(engine) as session, session.begin():
        if current_id is not None:
            current_result = session.get(EvidenceResult, current_id)
            current_run = session.get(ResearchRun, current_result.run_id)
            if (
                current_run.task_kind == "reassess_finding"
                and session.scalar(
                    select(FindingAssessment.id).where(
                        FindingAssessment.candidate_result_id == current_id
                    )
                )
                is None
            ):
                # A mutable pointer is insufficient: eligibility also requires
                # an immutable applied assessment proving this exact candidate.
                session.add(
                    FindingAssessment(
                        run_id=current_run.id,
                        disposition="revised",
                        applied=True,
                        head_revision=1,
                        candidate_result_id=current_id,
                        payload={},
                        payload_sha256=_hash(_canonical({})),
                    )
                )
        row = session.get(FindingHead, root_id)
        if row is None:
            session.add(
                FindingHead(
                    root_result_id=root_id,
                    current_result_id=current_id or root_id,
                    state=state,
                    revision=1,
                )
            )
        else:
            row.current_result_id = current_id or root_id
            row.state = state
            row.revision += 1


def finding_ids(client):
    return {
        item["result_id"]
        for item in client.get("/api/v1/knowledge").json()["items"]
        if item["type"] == "finding"
    }


@pytest.mark.parametrize("state", ["supported", "unsupported"])
def test_default_knowledge_only_supported_head_historical_get_is_explicit(knowledge, state):
    engine, factory = knowledge
    client, foreign = factory(), factory()
    _, root_id = seed(engine, client, title="Synthetic historical root")
    replacement, _ = candidate(engine, browser_owner(client), root_id)
    loser, _ = candidate(
        engine, browser_owner(client), root_id, title="Synthetic CAS loser", suffix="-loser"
    )
    head(engine, root_id, replacement.id, state=state)
    expected = {str(replacement.id)} if state == "supported" else set()
    assert finding_ids(client) == expected
    graph = client.get("/api/v1/knowledge/graph").json()
    assert {node["result_id"] for node in graph["nodes"] if node["kind"] == "finding"} == expected
    historical = client.get(f"/api/v1/knowledge/findings/{root_id}")
    assert historical.status_code == 200
    assert historical.headers["cache-control"] == "no-store"
    assert historical.json()["assessment"] == {
        "root_result_id": str(root_id),
        "current_result_id": str(replacement.id),
        "state": state,
        "revision": 1,
        "last_assessment_id": None,
        "is_current": False,
        "is_supported": False,
        "is_historical": True,
    }
    assert historical.json()["history_url"] == (f"/api/v1/assessments/heads/{root_id}/history")
    raw_candidate = client.get(f"/api/v1/knowledge/findings/{loser.id}").json()
    assert raw_candidate["task_kind"] == "reassess_finding"
    assert not raw_candidate["assessment"]["is_supported"]
    assert not raw_candidate["assessment"]["is_current"]
    bootstrap(foreign)
    assert foreign.get(f"/api/v1/knowledge/findings/{root_id}").status_code == 404
    assert foreign.get(f"/api/v1/knowledge/findings/{loser.id}").status_code == 404


@pytest.mark.parametrize("state", ["supported", "unsupported"])
def test_memory_uses_applied_current_head_and_fails_closed_on_old_reads(database, state):
    token = owner(database)
    root, _ = complete(database, token)
    replacement, _ = candidate(database, token, root.id)
    loser, _ = candidate(database, token, root.id, title="Synthetic unapplied candidate")
    head(database, root.id, replacement.id, state=state)
    memory, _ = current(database, token)
    assert [item.result_id for item in memory.search("Synthetic").findings] == (
        [str(replacement.id)] if state == "supported" else []
    )
    for identifier in (root.id, loser.id):
        with pytest.raises(MemoryError, match="memory_finding_not_found"):
            memory.read_finding(str(identifier))
    if state == "supported":
        assert memory.read_finding(str(replacement.id)).reference.result_id == str(replacement.id)
    else:
        with pytest.raises(MemoryError, match="memory_finding_not_found"):
            memory.read_finding(str(replacement.id))


def test_changed_head_invalidates_cached_memory_even_when_new_version_is_after_reader(database):
    token = owner(database)
    root, _ = complete(database, token)
    memory, lease = current(database, token)
    finding = memory.read_finding(str(root.id))
    source_id = finding.sources[0].retrieval_id
    original = memory.read_source(str(root.id), source_id)
    fingerprint = memory.scope_fingerprint()
    replacement, _ = candidate(database, token, root.id)
    head(database, root.id, replacement.id)
    assert memory.scope_fingerprint() != fingerprint
    assert memory.search("Synthetic").findings == ()
    with pytest.raises(MemoryError, match="memory_finding_not_found"):
        memory.read_finding(str(root.id))
    with pytest.raises(MemoryError, match="memory_source_not_found"):
        memory.read_source(str(root.id), source_id)
    # Original saved snapshots remain available for explicit historical inspection.
    assert EvidenceStore(database).get_document(root.run_id, uuid.UUID(source_id)) == original
    assert JobRepository(database).get(lease.run_id).status == "running"


@pytest.mark.parametrize("state", ["supported", "unsupported"])
def test_stale_or_unsupported_parent_cannot_be_bound_atomically(knowledge, state):
    engine, factory = knowledge
    client = factory()
    parents = parents_for(engine, client)
    replacement, _ = candidate(engine, browser_owner(client), parents[0])
    head(engine, parents[0], replacement.id, state=state)
    before = JobRepository(engine).get(replacement.run_id)
    response = selected(client, bootstrap(client), parents)
    assert response.status_code == 404
    assert response.json() == {"detail": "research_basis_not_found"}
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRunParent)) == 0
    assert JobRepository(engine).get(before.id).status == "succeeded"


def test_applied_reassessment_bridge_is_valid_parent_but_bound_stale_parent_reload_fails(knowledge):
    engine, factory = knowledge
    client = factory()
    parents = parents_for(engine, client)
    replacement, _ = candidate(engine, browser_owner(client), parents[0])
    head(engine, parents[0], replacement.id)
    response = selected(client, bootstrap(client), [replacement.id, parents[1]])
    assert response.status_code == 201
    lease = JobRepository(engine).claim_next("synthetic-current-head-reader")
    assert {ref.result_id for ref in get_parent_refs(engine, lease)} == {
        str(replacement.id),
        str(parents[1]),
    }
    head(engine, parents[0], replacement.id, state="unsupported")
    with pytest.raises(LineageError, match="research_basis_not_found"):
        get_parent_refs(engine, lease)


def test_graph_historical_parent_retains_revision_warning(knowledge):
    engine, factory = knowledge
    client = factory()
    parents = parents_for(engine, client)
    selected_run = selected(client, bootstrap(client), parents)
    assert selected_run.status_code == 201
    jobs = JobRepository(engine)
    lease = jobs.claim_next("synthetic-historical-lineage")
    # Explicitly seeded historical child projection; finalization is tested elsewhere.
    child, _ = complete(engine, browser_owner(client), title="Synthetic derived current child")
    with Session(engine) as session, session.begin():
        for ordinal, parent_id in enumerate(parents):
            parent = session.get(EvidenceResult, parent_id)
            session.add(
                ResearchRunParent(
                    run_id=child.run_id,
                    parent_result_id=parent.id,
                    parent_run_id=parent.run_id,
                    ordinal=ordinal,
                    content_sha256=parent.content_sha256,
                )
            )
    jobs.request_cancel(lease.run_id)
    replacement, _ = candidate(engine, browser_owner(client), parents[0])
    head(engine, parents[0], replacement.id)
    graph = client.get("/api/v1/knowledge/graph", params={"q": "derived current child"}).json()
    old = next(node for node in graph["nodes"] if node.get("result_id") == str(parents[0]))
    assert old["assessment"]["is_historical"]
    assert not old["assessment"]["is_supported"]
    assert any(
        edge["target"] == old["id"] and edge["kind"] == "investigated_from"
        for edge in graph["edges"]
    )


@pytest.mark.parametrize("state", ["supported", "unsupported"])
def test_artifact_source_rejects_stale_and_unapplied_versions(artifacts_api, state):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, root_id = seed(engine, client)
    token = browser_owner(client)
    replacement, _ = candidate(engine, token, root_id)
    loser, _ = candidate(engine, token, root_id, title="Synthetic nonapplied document basis")
    head(engine, root_id, replacement.id, state=state)
    for identifier in (root_id, loser.id):
        assert artifact_request(client, identifier).status_code == 404
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ArtifactRequest)) == 0
        if state == "supported":
            assert source_for_request(session, token, str(replacement.id), "formalise").id == (
                replacement.id
            )
        else:
            with pytest.raises(ArtifactError, match="artifact_source_not_found"):
                source_for_request(session, token, str(replacement.id), "write_summary")


def test_bound_artifact_stops_if_source_changes_but_completed_export_is_preserved(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, root_id = seed(engine, client)
    artifact_run, saved = complete_artifact(engine, client, root_id)
    created = artifact_request(client, root_id)
    assert created.status_code == 201
    lease = JobRepository(engine).claim_next("synthetic-pinned-stale-artifact")
    store = ArtifactStore(engine)
    store.load_basis(lease)
    replacement, _ = candidate(engine, browser_owner(client), root_id)
    head(engine, root_id, replacement.id)
    with pytest.raises(ArtifactError, match="artifact_source_not_found"):
        store.load_basis(lease)
    with pytest.raises(ArtifactError, match="artifact_source_not_found"):
        store.save(lease, draft_result())
    historical = store.get(artifact_run, saved.id)
    assert historical.markdown == saved.markdown
    assert historical.markdown_sha256 == saved.markdown_sha256


def test_new_watch_anchor_requires_current_supported_existing_watch_remains_usable(database):
    token = owner(database)
    root, _ = complete(database, token)
    repository = IngestionRepository(database)
    watch = repository.create(
        token, label="Synthetic anchored watch", query="Synthetic", source_result_id=root.id
    )
    replacement, _ = candidate(database, token, root.id)
    loser, _ = candidate(database, token, root.id, title="Synthetic failed-CAS candidate")
    head(database, root.id, replacement.id)
    for identifier in (root.id, loser.id):
        with pytest.raises(IngestionError, match="watch_not_found"):
            repository.create(
                token, label="Rejected anchor", query="Synthetic", source_result_id=identifier
            )
    assert repository.get(token, watch.id).source_result_id == root.id
    assert not repository.pause(token, watch.id).enabled
    assert repository.resume(token, watch.id).enabled
    current_watch = repository.create(
        token, label="Current anchor", query="Synthetic", source_result_id=replacement.id
    )
    assert current_watch.source_result_id == replacement.id
    head(database, root.id, replacement.id, state="unsupported")
    with pytest.raises(IngestionError, match="watch_not_found"):
        repository.create(
            token, label="Unsupported anchor", query="Synthetic", source_result_id=replacement.id
        )


def test_candidate_source_copy_keeps_watch_routing_until_applied_current_copy_exists(knowledge):
    engine, factory = knowledge
    client = factory()
    _, root_id = seed(engine, client)
    token = browser_owner(client)
    repository, jobs, evidence = (
        IngestionRepository(engine),
        JobRepository(engine),
        EvidenceStore(engine),
    )
    watch = repository.create(token, label="Synthetic source watch", query="Synthetic")
    tick = repository.run_now(token, watch.id)
    lease = jobs.claim_next("synthetic-free-watch", task_kinds=("ingest_literature",))
    doc = document("fixture:collected-only", title="Synthetic original watch metadata")
    artifact = evidence.save_source(lease, doc)
    repository.commit_page(
        lease,
        "openalex",
        repository.snapshot(lease).cursors["openalex"],
        None,
        (artifact,),
        exhausted=True,
    )
    jobs.finish(lease, status="succeeded", outcome={"ingestion_status": "complete"})
    copied_run = jobs.create(
        mode="explore", goal="Synthetic reassessment copy", task_kind="reassess_finding"
    )
    assign(engine, copied_run.id, token)
    copied = jobs.claim_next("synthetic-copy", task_kinds=("reassess_finding",))
    newer = doc.model_copy(
        update={
            "title": "Synthetic candidate metadata",
            "retrieved_at": doc.retrieved_at + timedelta(hours=1),
        }
    )
    assert evidence.save_source(copied, newer).version_id == artifact.version_id
    jobs.finish(copied, status="succeeded", outcome={"synthetic_fixture": True})
    item = next(
        item
        for item in client.get("/api/v1/knowledge").json()["items"]
        if item.get("source_version_id") == str(artifact.version_id)
    )
    assert item["run_id"] == str(tick.run_id)
    assert item["task_kind"] == "ingest_literature"
    assert item["watch_id"] == str(watch.id)
    assert item["title"] == doc.title
    # A positively assessed candidate now wins exact quoted-source projection.
    replacement, _ = candidate(engine, token, root_id)
    head(engine, root_id, replacement.id)
    original_left = next(
        source
        for source in evidence.list_sources(replacement.run_id)
        if source.source_id == "fixture:left"
    )
    selected_item = next(
        item
        for item in client.get("/api/v1/knowledge").json()["items"]
        if item.get("source_version_id") == str(original_left.version_id)
    )
    assert selected_item["run_id"] == str(replacement.run_id)
    assert selected_item["task_kind"] == "reassess_finding"
    assert selected_item["assessment_result_id"] == str(replacement.id)
    assert selected_item["assessment_status"] == "current_supported"


@pytest.mark.parametrize(
    ("disposition", "applied", "expected"),
    [
        ("unchanged", True, "assessed"),
        ("unsupported", True, "assessed"),
        ("unchanged", False, "unassessed"),
        ("inconclusive", False, "unassessed"),
    ],
)
def test_anchored_watch_pending_status_changes_only_for_applied_assessment_inputs(
    database, disposition, applied, expected
):
    token = owner(database)
    root, result = complete(database, token)
    repository, jobs, evidence = (
        IngestionRepository(database),
        JobRepository(database),
        EvidenceStore(database),
    )
    watch = repository.create(
        token,
        label="Synthetic anchored pending sources",
        query="Synthetic",
        source_result_id=root.id,
    )
    tick = repository.run_now(token, watch.id)
    lease = jobs.claim_next("synthetic-pending-watch", task_kinds=("ingest_literature",))
    known = evidence.save_source(lease, result.sources[0])
    novel = evidence.save_source(lease, document("fixture:new-unreviewed"))
    repository.commit_page(
        lease,
        "openalex",
        repository.snapshot(lease).cursors["openalex"],
        None,
        (known, novel),
        exhausted=True,
    )
    jobs.finish(lease, status="succeeded", outcome={"ingestion_status": "complete"})
    initial = {item.id: item.to_response() for item in repository.list_sources(token, watch.id)}
    assert initial[known.version_id]["assessment_status"] == "already_cited"
    assert initial[novel.version_id]["assessment_status"] == "unassessed"
    assert initial[novel.version_id]["assessment_root_result_id"] == str(root.id)
    run = jobs.create(
        mode="explore", goal="Synthetic assessment projection", task_kind="reassess_finding"
    )
    assign(database, run.id, token)
    assessment_lease = jobs.claim_next("synthetic-review", task_kinds=("reassess_finding",))
    with Session(database) as session, session.begin():
        session.add(
            AssessmentRequest(
                run_id=run.id,
                root_result_id=root.id,
                source_result_id=root.id,
                source_sha256=session.get(EvidenceResult, root.id).content_sha256,
                watch_id=watch.id,
                expected_head_revision=0,
                basis={},
                basis_sha256=_hash(_canonical({})),
            )
        )
        session.flush()
        session.add(
            AssessmentInputSource(
                run_id=run.id,
                ordinal=0,
                version_id=novel.version_id,
                retrieval_id=novel.retrieval_id,
                role="new",
            )
        )
        session.add(
            FindingAssessment(
                run_id=run.id,
                disposition=disposition,
                applied=applied,
                head_revision=1 if applied else 0,
                candidate_result_id=None,
                payload={},
                payload_sha256=_hash(_canonical({})),
            )
        )
    jobs.finish(assessment_lease, status="succeeded", outcome={"synthetic_fixture": True})
    snapshots = {item.id: item for item in repository.list_sources(token, watch.id)}
    assert snapshots[known.version_id].assessment_status == "already_cited"
    assert snapshots[novel.version_id].assessment_status == expected
    detail = repository.get_source_document(token, watch.id, novel.version_id)
    assert detail["assessment_status"] == expected
    assert detail["assessment_root_result_id"] == str(root.id)
    assert detail["assessment_history_url"] == (f"/api/v1/assessments/heads/{root.id}/history")
    assert detail["run_id"] == str(tick.run_id)
    assert detail["document"] == document("fixture:new-unreviewed").model_dump(mode="json")
    # Unsupported/stale anchors still authorize inspection of their saved history.
    head(database, root.id, state="unsupported")
    assert (
        repository.get_source_document(token, watch.id, novel.version_id)["assessment_status"]
        == expected
    )
