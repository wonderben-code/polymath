"""Supported ladder chronology preserves publication authority and exact bytes.

Synthetic intelligence and real PostgreSQL are used; no publisher client or
external model/provider request is constructed.
"""

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_artifact_api import artifact_request, complete_artifact
from test_discovery_boundaries import BibliographicTools
from test_discovery_controller import DiscoveryFixtureModel
from test_discovery_worker import worker
from test_memory import assign
from test_publication import approve, prepare
from test_publication import artifacts_api as artifacts_api_fixture
from test_publication import publication as publication_fixture
from test_research_controller import source
from test_run_api import bootstrap

from polymath.discovery_admission import bind_discovery_intent
from polymath.discovery_contracts import DiscoveryIntent
from polymath.discovery_models import StageCommit
from polymath.discovery_store import DiscoveryStore
from polymath.evidence import EvidenceStore
from polymath.jobs import JobRepository, LeaseLost, StateConflict
from polymath.models import ResearchRun
from polymath.publication_job_models import PublicationJob, PublicationOperation
from polymath.research.models import SourceBibliography

artifacts_api = artifacts_api_fixture
publication = publication_fixture
pytestmark = pytest.mark.integration


async def stage_result(original, monkeypatch, *, finish):
    jobs = JobRepository(original.engine)
    run = jobs.create(mode="surprise", goal="Synthetic ladder publication provenance")
    assign(original.engine, run.id, original.owner_hash)
    intent = DiscoveryIntent()
    with Session(original.engine) as session, session.begin():
        bind_discovery_intent(session, session.get(ResearchRun, run.id), intent)
    leases = []
    original_save = EvidenceStore.save_source

    def remember(store, lease, document):
        if lease.run_id == run.id:
            leases.append(lease)
        return original_save(store, lease, document)

    monkeypatch.setattr(EvidenceStore, "save_source", remember)
    investigator = worker(
        original.engine,
        DiscoveryFixtureModel(stop_after_stage=1 if finish else None),
        calls=20 if finish else 7,
    )
    investigator.tools_factory = lambda _: BibliographicTools()
    result = await investigator.run_once("publication-stage-prefix")
    assert result.status == ("succeeded" if finish else "interrupted")
    with Session(original.engine) as session:
        stage = session.scalar(select(StageCommit).where(StageCommit.run_id == run.id))
        stage_id, result_id = stage.id, stage.result_id
    return SimpleNamespace(
        **(
            vars(original)
            | {
                "source_run_id": run.id,
                "source_stage_id": stage_id,
                "source_result_id": result_id,
                "source_lease": leases[0],
            }
        )
    )


def connector():
    return source(
        "later-publication-alias", "Uncited metadata links the earlier paper identifiers."
    ).model_copy(
        update={
            "bibliography": SourceBibliography(
                doi="10.1234/synthetic-discovery",
                arxiv_id="2401.00001",
            )
        }
    )


@pytest.mark.asyncio
async def test_completed_stage_publication_keeps_exact_bytes_and_source_run_cannot_reopen(
    publication, monkeypatch
):
    case = await stage_result(publication, monkeypatch, finish=True)
    _, artifact = complete_artifact(
        case.engine, case.client, case.source_result_id, kind="write_paper"
    )
    case.artifact = artifact
    manifest = prepare(case)
    receipt = approve(case, manifest)
    jobs = JobRepository(case.engine)
    snapshot = jobs.get(case.source_run_id)
    with pytest.raises(StateConflict):
        jobs.resume_interrupted(case.source_run_id, expected_token=snapshot.lease_token)
    with pytest.raises(LeaseLost):
        EvidenceStore(case.engine).save_source(case.source_lease, connector())
    saved = case.store.get(case.owner_hash, manifest.id)
    assert saved.manifest == manifest.manifest
    assert saved.manifest_sha256 == manifest.manifest_sha256
    assert saved.approval.id == receipt.id and saved.approval.valid
    exported = case.client.get(f"/api/v1/artifacts/{artifact.id}/export")
    assert exported.status_code == 200
    assert exported.content == artifact.markdown.encode("utf-8")
    with Session(case.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationOperation)) == 0
        assert session.scalar(select(func.count()).select_from(PublicationJob)) == 0


@pytest.mark.asyncio
async def test_interrupted_source_cannot_admit_artifact_before_further_alias_reads(
    publication, monkeypatch
):
    case = await stage_result(publication, monkeypatch, finish=False)
    created = artifact_request(case.client, case.source_result_id, kind="write_paper")
    assert created.status_code == 429
    assert created.json() == {"detail": "active_run_limit"}
    with pytest.raises(LeaseLost):
        EvidenceStore(case.engine).save_source(case.source_lease, connector())
    # Explicit repository recovery grants the only supported new read lease;
    # no artifact was admitted while this investigation was unfinished.
    jobs = JobRepository(case.engine)
    snapshot = jobs.get(case.source_run_id)
    jobs.resume_interrupted(case.source_run_id, expected_token=snapshot.lease_token)
    lease = jobs.claim_next("later-owned-source-metadata", lease_seconds=300)
    assert lease.run_id == case.source_run_id
    EvidenceStore(case.engine).save_source(lease, connector())
    detail = DiscoveryStore(case.engine).get_stage(
        case.owner_hash, case.source_run_id, case.source_stage_id
    )
    assert detail["status"] == "needs_review"
    cancelled = case.client.post(
        f"/api/v1/runs/{case.source_run_id}/cancel", headers=bootstrap(case.client)
    )
    assert cancelled.status_code == 200
    jobs.finalize_cancellation(case.source_run_id)
    denied = artifact_request(case.client, case.source_result_id, kind="write_paper")
    assert denied.status_code == 404
    with Session(case.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationOperation)) == 0
        assert session.scalar(select(func.count()).select_from(PublicationJob)) == 0
