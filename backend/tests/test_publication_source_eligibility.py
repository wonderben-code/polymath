"""Current scientific inputs gate new sends; immutable remote facts stay readable."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_publication import approve, prepare
from test_publication import artifacts_api as artifacts_api_fixture
from test_publication import publication as publication_fixture
from test_publication_jobs import deposit, publish_grant, setup_job, verified_outcome

from polymath.assessment_models import FindingHead
from polymath.publication import PublicationError
from polymath.publication_job_models import PublicationOperation
from polymath.publication_jobs import PublicationJobRepository

artifacts_api = artifacts_api_fixture
publication = publication_fixture
pytestmark = pytest.mark.integration


def root_id(case):
    return uuid.UUID(case.artifact.basis["reference"]["result_id"])


def unsupported(case, session=None):
    def change(owned):
        head = owned.get(FindingHead, root_id(case))
        if head is None:
            owned.add(
                FindingHead(
                    root_result_id=root_id(case),
                    current_result_id=root_id(case),
                    state="unsupported",
                    revision=1,
                )
            )
        else:
            head.state = "unsupported"
            head.revision += 1

    if session is not None:
        change(session)
    else:
        with Session(case.engine) as owned, owned.begin():
            change(owned)


def test_unsupported_source_blocks_new_preparation_and_preserves_exact_export(publication):
    case = publication
    unsupported(case)
    with pytest.raises(PublicationError, match="publication_source_not_current"):
        prepare(case)
    exported = case.client.get(f"/api/v1/artifacts/{case.artifact.id}/export")
    assert exported.status_code == 200
    assert exported.content == case.artifact.markdown.encode("utf-8")


def test_unsupported_source_invalidates_receipt_and_blocks_fresh_approval(publication):
    case = publication
    manifest = prepare(case)
    receipt = approve(case, manifest)
    unsupported(case)
    record = case.store.get(case.owner_hash, manifest.id)
    assert record.manifest_sha256 == manifest.manifest_sha256
    assert record.approval.id == receipt.id and not record.approval.valid
    with pytest.raises(PublicationError, match="publication_source_not_current"):
        approve(case, manifest)
    assert case.store.revoke(case.owner_hash, receipt.id, case.operator).revoked_at is not None


def test_currentness_is_rechecked_before_enqueue(publication):
    case = publication
    manifest, receipt = prepare(case), None
    receipt = approve(case, manifest)
    unsupported(case)
    repository = PublicationJobRepository(case.engine, case.store)
    with pytest.raises(PublicationError, match="publication_source_not_current"):
        repository.enqueue(
            case.owner_hash, manifest.id, receipt.id, manifest.manifest_sha256, case.operator
        )


def test_existing_job_cannot_obtain_new_send_permission_after_negative_assessment(publication):
    case = publication
    repository, _, _, _ = setup_job(case)
    lease = repository.claim_next("synthetic-publisher")
    unsupported(case)
    with pytest.raises(PublicationError, match="publication_source_not_current"):
        repository.prepare_operation(lease, "create", "a" * 64)
    with Session(case.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationOperation)) == 0


def test_head_lock_wait_rechecks_currentness_before_grant(publication, monkeypatch):
    import polymath.publication as publications

    case = publication
    repository, _, _, _ = setup_job(case)
    lease = repository.claim_next("synthetic-publisher")
    reached = Event()
    original = publications.lock_current_source

    def waiting(*args, **kwargs):
        reached.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(publications, "lock_current_source", waiting)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(case.engine) as held, held.begin():
            held.scalar(
                select(FindingHead)
                .where(FindingHead.root_result_id == root_id(case))
                .with_for_update()
            )
            pending = pool.submit(repository.prepare_operation, lease, "create", "a" * 64)
            assert reached.wait(timeout=5)
            unsupported(case, held)
        with pytest.raises(PublicationError, match="publication_source_not_current"):
            pending.result(timeout=10)
    with Session(case.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationOperation)) == 0


def test_late_verified_publication_truth_survives_source_becoming_unsupported(publication):
    case = publication
    repository, manifest, _, job = setup_job(case)
    lease = repository.claim_next("synthetic-publisher")
    grant = publish_grant(repository, lease, manifest)
    # A prior authorized request cannot be unsent when later evidence changes.
    unsupported(case)
    repository.record_success(grant, deposit(manifest, submitted=True))
    done = repository.finish(lease, outcome=verified_outcome(manifest))
    assert done.status == "published"
    assert repository.get(case.owner_hash, job.id).to_response()["doi"] == ("10.5072/zenodo.456")
    assert repository.status_for_manifest(case.owner_hash, manifest.id) == "published"
    assert not case.store.get(case.owner_hash, manifest.id).approval.valid


def test_read_only_reconciliation_still_allowed_after_negative_assessment(publication):
    case = publication
    repository, _, _, job = setup_job(case)
    lease = repository.claim_next("synthetic-publisher")
    repository.interrupt(lease)
    unsupported(case)
    reading = repository.enqueue_reconciliation(case.owner_hash, job.id, case.operator)
    assert reading.work_kind == "reconcile"
    with pytest.raises(PublicationError, match="publication_source_not_current"):
        repository.resume(case.owner_hash, job.id, case.operator)
