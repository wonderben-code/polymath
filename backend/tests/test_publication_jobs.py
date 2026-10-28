"""Durable publication boundaries use real PostgreSQL and synthetic remote facts."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_publication import approve, prepare
from test_publication import artifacts_api as artifacts_api_fixture
from test_publication import publication as publication_fixture

from polymath.operator_models import OperatorSession
from polymath.publication import PublicationError
from polymath.publication_contracts import PublicationManifest
from polymath.publication_job_models import (
    PublicationJob,
    PublicationJobEvent,
    PublicationOperation,
    PublicationOperationAudit,
    PublicationRemoteObservation,
)
from polymath.publication_jobs import (
    PUBLICATION_QUEUE_LOCK,
    PublicationJobError,
    PublicationJobRepository,
    PublicationLeaseLost,
)
from polymath.zenodo import canonical_remote_metadata

artifacts_api = artifacts_api_fixture
publication = publication_fixture
pytestmark = pytest.mark.integration
REQUEST_HASH = "a" * 64


def setup_job(case, *, enqueue=True):
    manifest = prepare(case)
    receipt = approve(case, manifest)
    repository = PublicationJobRepository(case.engine, case.store)
    job = (
        repository.enqueue(
            case.owner_hash, manifest.id, receipt.id, manifest.manifest_sha256, case.operator
        )
        if enqueue
        else None
    )
    return repository, manifest, receipt, job


def deposit(manifest, identifier=123, *, submitted=False):
    typed = PublicationManifest.model_validate_json(json.dumps(manifest.manifest))
    return {
        "deposit": {
            "id": identifier,
            "destination": typed.destination,
            "submitted": submitted,
            "state": "done" if submitted else "inprogress",
            "metadata": canonical_remote_metadata(typed),
            "bucket_url": "https://sandbox.zenodo.org/api/files/fixture-private-bucket",
            "doi": "10.5072/zenodo.456",
            "record_url": "https://sandbox.zenodo.org/records/456",
            "files": [],
        },
        "publication_reported": submitted,
    }


def upload(manifest, *, verified=False):
    return {
        "file": {
            "filename": manifest.manifest["file_name"],
            "size": manifest.manifest["markdown_bytes"],
            "checksum": None,
        },
        "remote_deposit_id": 123,
        "verified_sha256": manifest.manifest["markdown_sha256"] if verified else None,
    }


def verified_outcome(manifest, **changes):
    return {
        "verified": True,
        "manifest_sha256": manifest.manifest_sha256,
        "markdown_sha256": manifest.manifest["markdown_sha256"],
        "destination": "sandbox",
        "remote_deposit_id": 123,
        "record_id": 456,
        "doi": "10.5072/zenodo.456",
        "record_url": "https://sandbox.zenodo.org/records/456",
        **changes,
    }


def created(repository, lease, manifest):
    grant = repository.prepare_operation(lease, "create", REQUEST_HASH)
    repository.record_success(grant, deposit(manifest))
    repository.set_remote_deposit(lease, 123)
    return grant


def publish_grant(repository, lease, manifest):
    created(repository, lease, manifest)
    grant = repository.prepare_operation(lease, "upload", "b" * 64)
    repository.record_success(grant, upload(manifest))
    return repository.prepare_operation(lease, "publish", "c" * 64)


def test_concurrent_duplicate_enqueue_and_claim_have_one_winner(publication):
    case = publication
    repository, manifest, receipt, _ = setup_job(case, enqueue=False)
    barrier = Barrier(2)

    def enqueue():
        barrier.wait(timeout=5)
        return repository.enqueue(
            case.owner_hash, manifest.id, receipt.id, manifest.manifest_sha256, case.operator
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(enqueue), pool.submit(enqueue)
        assert first.result(timeout=15).id == second.result(timeout=15).id
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(repository.claim_next, ("worker-one", "worker-two")))
    assert sum(claim is not None for claim in claims) == 1
    with Session(case.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationJob)) == 1


def test_concurrent_stage_grants_never_issue_second_send_permission(publication):
    case = publication
    repository, _, _, job = setup_job(case)
    lease = repository.claim_next("fixture-worker")
    barrier = Barrier(4)

    def attempt():
        barrier.wait(timeout=5)
        try:
            return repository.prepare_operation(lease, "create", REQUEST_HASH)
        except PublicationJobError as error:
            assert str(error) == "publication_operation_unresolved"
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        grants = list(pool.map(lambda _: attempt(), range(4)))
    assert sum(grant is not None for grant in grants) == 1
    with Session(case.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationOperation)) == 1
    assert repository.get(case.owner_hash, job.id).external_outcome_unknown


def test_observed_replay_and_full_verification_are_separate(publication):
    case = publication
    repository, manifest, _, job = setup_job(case)
    lease = repository.claim_next("fixture-worker")
    created(repository, lease, manifest)
    assert repository.get_operation(lease, "create", REQUEST_HASH) == deposit(manifest)
    assert repository.prepare_operation(lease, "create", REQUEST_HASH) is None
    with pytest.raises(PublicationJobError, match="publication_request_changed"):
        repository.get_operation(lease, "create", "d" * 64)
    grant = repository.prepare_operation(lease, "upload", "b" * 64)
    repository.record_success(grant, upload(manifest))
    grant = repository.prepare_operation(lease, "publish", "c" * 64)
    repository.record_success(grant, deposit(manifest, submitted=True))
    reported = repository.get(case.owner_hash, job.id)
    assert reported.status == "running" and reported.remote_status == "publication_reported"
    assert reported.to_response()["doi"] is None
    assert repository.status_for_manifest(case.owner_hash, manifest.id) == "unknown"
    done = repository.finish(lease, outcome=verified_outcome(manifest))
    assert done.remote_status == "published" and done.status == "published"
    assert done.to_response()["record_url"].endswith("/456")
    assert repository.status_for_manifest(case.owner_hash, manifest.id) == "published"
    assert "fixture-private-bucket" not in json.dumps(done.to_response())
    assert case.owner_hash not in json.dumps(done.to_response())
    with pytest.raises(PublicationLeaseLost):
        repository.prepare_operation(lease, "publish", "c" * 64)


@pytest.mark.parametrize(
    "changes",
    [
        {"verified": False},
        {"manifest_sha256": "0" * 64},
        {"markdown_sha256": "0" * 64},
        {"destination": "production"},
        {"record_url": "https://attacker.example/records/456"},
        {"record_id": 789},
        {"record_url": "https://sandbox.zenodo.org/records/456?secret=fixture"},
    ],
)
def test_verified_result_cannot_change_approved_identity_or_safe_record(publication, changes):
    repository, manifest, _, _ = setup_job(publication)
    lease = repository.claim_next("fixture-worker")
    with pytest.raises(PublicationJobError):
        repository.finish(lease, outcome=verified_outcome(manifest, **changes))
    assert repository.snapshot(lease).status == "running"


def test_status_overlay_rejects_a_corrupt_published_claim(publication):
    case = publication
    repository, manifest, _, job = setup_job(case)
    with case.engine.begin() as connection:
        connection.execute(
            update(PublicationJob)
            .where(PublicationJob.id == job.id)
            .values(
                status="published",
                outcome=verified_outcome(manifest, markdown_sha256="0" * 64),
            )
        )
    assert repository.status_for_manifest(case.owner_hash, manifest.id) == "unknown"


@pytest.mark.parametrize("change", ["revoke", "logout", "rotate", "hash"])
def test_invalidated_approval_cannot_authorize_any_remote_stage(publication, change):
    case = publication
    repository, manifest, receipt, _ = setup_job(case)
    lease = repository.claim_next("fixture-worker")
    if change == "revoke":
        case.store.revoke(case.owner_hash, receipt.id, case.operator)
    elif change == "logout":
        with case.engine.begin() as connection:
            connection.execute(update(OperatorSession).values(revoked_at=func.clock_timestamp()))
    elif change == "rotate":
        case.store.operator_policy = replace(
            case.operator_policy,
            bootstrap_token=SecretStr(
                "Rotated_fixture_operator_0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            ),
        )
    else:
        with pytest.raises(IntegrityError), case.engine.begin() as connection:
            connection.execute(update(PublicationJob).values(manifest_sha256="0" * 64))
        lease = replace(lease, token=lease.token + 1)
    with pytest.raises((PublicationError, PublicationJobError)):
        repository.prepare_operation(lease, "create", REQUEST_HASH)
    with Session(case.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationOperation)) == 0


def test_approval_expiring_while_waiting_for_job_lock_cannot_grant(publication, monkeypatch):
    import polymath.publication as publications

    case = publication
    case.store.policy = replace(case.policy, approval_seconds=60)
    repository, _, receipt, job = setup_job(case)
    lease = repository.claim_next("fixture-worker", lease_seconds=300)
    reached, future_time = Event(), [None]
    original_now, original_validate = publications._now, case.store.validate_approval

    def now(session):
        return future_time[0] or original_now(session)

    def validate(*args, **kwargs):
        result = original_validate(*args, **kwargs)
        reached.set()
        return result

    monkeypatch.setattr(publications, "_now", now)
    monkeypatch.setattr(case.store, "validate_approval", validate)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with case.engine.begin() as blocker:
            blocker.execute(
                select(PublicationJob).where(PublicationJob.id == job.id).with_for_update()
            )
            pending = pool.submit(repository.prepare_operation, lease, "create", REQUEST_HASH)
            assert reached.wait(timeout=5)
            future_time[0] = receipt.expires_at + timedelta(seconds=1)
        with pytest.raises(PublicationError):
            pending.result(timeout=10)
    assert repository.heartbeat(lease, lease_seconds=300).token == lease.token
    with Session(case.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationOperation)) == 0


@pytest.mark.parametrize("duplicate", [False, True])
def test_approval_expiring_while_waiting_for_queue_lock_cannot_enqueue(
    publication, monkeypatch, duplicate
):
    import polymath.publication as publications

    case = publication
    case.store.policy = replace(case.policy, approval_seconds=60)
    repository, manifest, receipt, _ = setup_job(case, enqueue=duplicate)
    reached, future_time = Event(), [None]
    original_now, original_validate = publications._now, case.store.validate_approval

    def validate(*args, **kwargs):
        result = original_validate(*args, **kwargs)
        reached.set()
        return result

    monkeypatch.setattr(
        publications, "_now", lambda session: future_time[0] or original_now(session)
    )
    monkeypatch.setattr(case.store, "validate_approval", validate)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with case.engine.begin() as blocker:
            blocker.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": PUBLICATION_QUEUE_LOCK}
            )
            pending = pool.submit(
                repository.enqueue,
                case.owner_hash,
                manifest.id,
                receipt.id,
                manifest.manifest_sha256,
                case.operator,
            )
            assert reached.wait(timeout=5)
            future_time[0] = receipt.expires_at + timedelta(seconds=1)
        with pytest.raises(PublicationError):
            pending.result(timeout=10)
    with Session(case.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationJob)) == int(duplicate)


def test_unknown_stages_require_positive_readonly_reconciliation_before_manual_resume(publication):
    case = publication
    repository, manifest, receipt, job = setup_job(case)
    lease = repository.claim_next("first-worker")
    grant = repository.prepare_operation(lease, "create", REQUEST_HASH)
    repository.record_unknown(grant)
    repository.interrupt(lease)
    with pytest.raises(PublicationJobError, match="publication_operation_unresolved"):
        repository.resume(case.owner_hash, job.id, case.operator)
    assert repository.claim_next("automatic-retry") is None
    duplicate = repository.enqueue(
        case.owner_hash, manifest.id, receipt.id, manifest.manifest_sha256, case.operator
    )
    assert duplicate.id == job.id and duplicate.status == "interrupted"
    repository.enqueue_reconciliation(case.owner_hash, job.id, case.operator)
    reading = repository.claim_next("read-only-worker")
    with pytest.raises(PublicationJobError, match="publication_read_only_job"):
        repository.prepare_operation(reading, "upload", "b" * 64)
    with pytest.raises(PublicationJobError, match="publication_reconciliation_evidence_required"):
        repository.record_reconciled(reading, "create", {"search_hits": 0})
    assert repository.snapshot(reading).unresolved_operations
    repository.record_reconciled(reading, "create", deposit(manifest))
    repository.set_remote_deposit(reading, 123)
    stopped = repository.interrupt(reading, reason="reconciliation_complete")
    assert stopped.work_kind == "reconcile" and stopped.can_resume
    assert repository.claim_next("still-no-auto-retry") is None
    case.store.revoke(case.owner_hash, receipt.id, case.operator)
    replacement = approve(case, manifest)
    repository.resume(case.owner_hash, job.id, case.operator, replacement.id)
    resumed = repository.claim_next("human-resumed-worker")
    assert repository.get_operation(resumed, "create", REQUEST_HASH) == deposit(manifest)
    assert repository.prepare_operation(resumed, "create", REQUEST_HASH) is None
    with Session(case.engine) as session:
        operation = session.scalar(select(PublicationOperation))
        assert operation.approval_id == receipt.id
        assert session.get(PublicationJob, job.id).approval_id == replacement.id


def test_cancelled_late_response_is_audited_without_stale_checkpoint_update(publication):
    case = publication
    repository, manifest, _, job = setup_job(case)
    lease = repository.claim_next("cancelled-worker")
    grant = repository.prepare_operation(lease, "create", REQUEST_HASH)
    repository.cancel(case.owner_hash, job.id)
    repository.record_success(grant, deposit(manifest))
    snapshot = repository.get(case.owner_hash, job.id)
    assert snapshot.status == "cancelled" and snapshot.remote_status == "draft"
    with pytest.raises(PublicationLeaseLost):
        repository.set_remote_deposit(lease, 123)
    with Session(case.engine) as session:
        assert session.get(PublicationJob, job.id).remote_deposit_id is None
        assert session.scalar(select(func.count()).select_from(PublicationOperationAudit)) == 1


def test_cancelled_publish_report_is_not_hidden_or_falsely_verified(publication):
    case = publication
    repository, manifest, _, job = setup_job(case)
    lease = repository.claim_next("cancelled-publisher")
    grant = publish_grant(repository, lease, manifest)
    repository.cancel(case.owner_hash, job.id)
    repository.record_success(grant, deposit(manifest, submitted=True))
    cancelled = repository.get(case.owner_hash, job.id)
    assert cancelled.publication_reported and cancelled.external_outcome_unknown
    assert cancelled.to_response()["doi"] is None
    assert repository.status_for_manifest(case.owner_hash, manifest.id) == "unknown"
    repository.enqueue_reconciliation(case.owner_hash, job.id, case.operator)
    reading = repository.claim_next("publication-verifier")
    repository.record_remote_observation(reading, deposit(manifest, submitted=True))
    verified = repository.finish(reading, outcome=verified_outcome(manifest))
    assert verified.status == "published" and verified.outcome["record_id"] == "456"


def test_late_get_report_after_cancellation_retains_submission_without_grant(publication):
    case = publication
    repository, manifest, _, job = setup_job(case)
    lease = repository.claim_next("late-read-worker")
    repository.cancel(case.owner_hash, job.id)
    reported = deposit(manifest, submitted=True)
    reported["deposit"]["metadata"]["title"] = "A different document"
    repository.record_remote_observation(lease, reported)
    snapshot = repository.get(case.owner_hash, job.id)
    assert snapshot.publication_reported and snapshot.external_outcome_unknown
    assert repository.status_for_manifest(case.owner_hash, manifest.id) == "unknown"
    assert snapshot.to_response()["doi"] is None
    with Session(case.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationRemoteObservation)) == 1
        assert session.scalar(select(func.count()).select_from(PublicationOperation)) == 0
    with pytest.raises(PublicationLeaseLost, match="publication_lease_not_issued"):
        repository.record_remote_observation(replace(lease, worker_id="forged-worker"), reported)


def test_readonly_verified_get_can_record_existing_publication_without_fake_mutation_grants(
    publication,
):
    case = publication
    repository, manifest, _, job = setup_job(case)
    lease = repository.claim_next("initial-worker")
    repository.interrupt(lease)
    repository.enqueue_reconciliation(case.owner_hash, job.id, case.operator)
    reading = repository.claim_next("read-only-verifier")
    wrong = deposit(manifest, submitted=True)
    wrong["deposit"]["metadata"]["title"] = "Not the approved document"
    repository.record_remote_observation(reading, wrong)
    with pytest.raises(PublicationJobError, match="publication_remote_identity_not_observed"):
        repository.set_remote_deposit(reading, 123)
    repository.record_remote_observation(reading, deposit(manifest, submitted=True))
    repository.set_remote_deposit(reading, 123)
    assert repository.finish(reading, outcome=verified_outcome(manifest)).status == "published"
    with Session(case.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationOperation)) == 0
        assert session.scalar(select(func.count()).select_from(PublicationRemoteObservation)) == 2


def test_conflicting_late_identity_freezes_but_retains_both_observations(publication):
    case = publication
    repository, manifest, _, job = setup_job(case)
    lease = repository.claim_next("first-worker")
    grant = repository.prepare_operation(lease, "create", REQUEST_HASH)
    repository.record_unknown(grant)
    repository.interrupt(lease)
    repository.enqueue_reconciliation(case.owner_hash, job.id, case.operator)
    reading = repository.claim_next("reconciler")
    repository.record_reconciled(reading, "create", deposit(manifest, identifier=123))
    with pytest.raises(PublicationJobError, match="publication_reconciliation_conflict"):
        repository.record_success(grant, deposit(manifest, identifier=789))
    frozen = repository.get(case.owner_hash, job.id)
    assert frozen.frozen and frozen.status == "interrupted"
    with pytest.raises(PublicationJobError, match="publication_job_frozen"):
        repository.resume(case.owner_hash, job.id, case.operator)
    with Session(case.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationOperationAudit)) == 4


def test_expired_workers_are_interrupted_and_cannot_overwrite_observed_response(publication):
    case = publication
    repository, manifest, _, job = setup_job(case)
    lease = repository.claim_next("expired-worker")
    grant = repository.prepare_operation(lease, "create", REQUEST_HASH)
    with case.engine.begin() as connection:
        connection.execute(
            update(PublicationJob).values(
                lease_expires_at=func.clock_timestamp() - text("interval '1 second'")
            )
        )
    assert repository.interrupt_expired() == 1
    assert repository.claim_next("unsafe-auto-restart") is None
    repository.record_success(grant, deposit(manifest))
    with pytest.raises(PublicationLeaseLost):
        repository.heartbeat(lease)
    repository.resume(case.owner_hash, job.id, case.operator)
    newer = repository.claim_next("replacement-worker")
    assert newer.token > lease.token
    assert repository.get_operation(newer, "create", REQUEST_HASH) == deposit(manifest)
    assert repository.prepare_operation(newer, "create", REQUEST_HASH) is None


def test_known_rejection_never_becomes_new_send_permission(publication):
    case = publication
    repository, _, _, job = setup_job(case)
    lease = repository.claim_next("rejected-worker")
    grant = repository.prepare_operation(lease, "create", REQUEST_HASH)
    repository.record_rejected(grant, {"status_code": 403, "code": "access_denied"})
    assert repository.get(case.owner_hash, job.id).status == "failed"
    assert repository.claim_next("automatic-retry") is None
    with pytest.raises(PublicationJobError, match="publication_job_state_conflict"):
        repository.resume(case.owner_hash, job.id, case.operator)


def test_local_cancellation_frees_quota_without_erasing_unknown_remote_operations(publication):
    case = publication
    repository = PublicationJobRepository(case.engine, case.store)
    jobs = []
    for index in range(2):
        manifest = prepare(case, title=f"Synthetic queued document {index}")
        receipt = approve(case, manifest)
        job = repository.enqueue(
            case.owner_hash, manifest.id, receipt.id, manifest.manifest_sha256, case.operator
        )
        lease = repository.claim_next(f"fixture-worker-{index}")
        grant = repository.prepare_operation(lease, "create", REQUEST_HASH)
        repository.record_unknown(grant)
        stopped = repository.interrupt(lease)
        assert stopped.can_cancel and stopped.external_outcome_unknown
        jobs.append(job)
    third = prepare(case, title="Synthetic third document")
    receipt = approve(case, third)
    with pytest.raises(PublicationJobError, match="publication_owner_limit"):
        repository.enqueue(
            case.owner_hash, third.id, receipt.id, third.manifest_sha256, case.operator
        )
    cancelled = repository.cancel(case.owner_hash, jobs[0].id)
    assert cancelled.status == "cancelled" and cancelled.external_outcome_unknown
    assert not cancelled.can_resume
    repository.enqueue(case.owner_hash, third.id, receipt.id, third.manifest_sha256, case.operator)
    with Session(case.engine) as session:
        operations = session.scalars(select(PublicationOperation)).all()
        assert len(operations) == 2 and all(
            operation.status == "unknown" for operation in operations
        )


@pytest.mark.parametrize(
    "model,statement",
    [
        (PublicationJob, lambda: update(PublicationJob).values(manifest_sha256="0" * 64)),
        (PublicationOperation, lambda: update(PublicationOperation).values(request_hash="0" * 64)),
        (PublicationOperationAudit, lambda: delete(PublicationOperationAudit)),
        (PublicationJobEvent, lambda: delete(PublicationJobEvent)),
        (PublicationRemoteObservation, lambda: delete(PublicationRemoteObservation)),
    ],
)
def test_execution_bindings_and_audits_cannot_be_rewritten(publication, model, statement):
    repository, manifest, _, _ = setup_job(publication)
    lease = repository.claim_next("immutable-audit-worker")
    created(repository, lease, manifest)
    repository.record_remote_observation(lease, deposit(manifest))
    with pytest.raises(IntegrityError), publication.engine.begin() as connection:
        connection.execute(statement())


def test_events_are_owned_bounded_and_replay_in_persisted_order(publication):
    case = publication
    repository, _, _, job = setup_job(case)
    lease = repository.claim_next("event-worker")
    repository.set_phase(lease, "verify", "Checking uploaded file bytes.")
    repository.set_phase(lease, "verify", "Checking the published file bytes.")
    records = repository.events_after(case.owner_hash, job.id, after=1)
    assert [record.sequence for record in records] == [2, 3, 4]
    assert all("event_key" not in record.to_response() for record in records)
    with pytest.raises(PublicationError):
        repository.get("f" * 64, job.id)
    with pytest.raises(PublicationJobError):
        repository.prepare_operation(lease, "unexpected-stage", REQUEST_HASH)


def test_each_cancellation_after_manual_continuation_has_its_own_audit_event(publication):
    case = publication
    repository, manifest, _, job = setup_job(case)
    lease = repository.claim_next("first-cancellation-fixture")
    created(repository, lease, manifest)
    first = repository.cancel(case.owner_hash, job.id)
    assert repository.cancel(case.owner_hash, job.id).event_sequence == first.event_sequence
    repository.enqueue_reconciliation(case.owner_hash, job.id, case.operator)
    read_lease = repository.claim_next("read-only-reconciliation-fixture")
    repository.interrupt(read_lease, reason="read_only_check_complete")
    repository.resume(case.owner_hash, job.id, case.operator)
    second = repository.cancel(case.owner_hash, job.id)
    events = repository.events_after(case.owner_hash, job.id)
    cancelled = [event for event in events if event.kind == "cancelled"]
    assert len(cancelled) == 2
    assert cancelled[1].sequence == second.event_sequence > first.event_sequence
    assert second.updated_at > first.updated_at
    with Session(case.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationOperation)) == 1


def test_downgrade_refuses_to_discard_any_publication_execution_history(publication):
    setup_job(publication)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with pytest.raises(Exception, match="Cannot discard publication execution"):
        command.downgrade(config, "0012_publication_approval")
