"""Synthetic Zenodo HTTP + real PostgreSQL publication worker journeys.

All clients use httpx.MockTransport and synthetic sandbox records/DOIs. No model
or external publisher request occurs in this module.
"""

import asyncio
import copy
import hashlib
import json
from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session
from test_artifact_api import artifacts_api as artifacts_api_fixture
from test_publication import approve, prepare
from test_publication import publication as publication_fixture

from polymath.operator_models import OperatorSession
from polymath.publication_contracts import PublicationManifest
from polymath.publication_job_models import (
    PublicationJob,
    PublicationOperation,
    PublicationOperationAudit,
)
from polymath.publication_jobs import PublicationJobRepository
from polymath.publication_worker import PublicationWorker, PublicationWorkerPolicy
from polymath.zenodo import ZenodoClient, canonical_remote_metadata

artifacts_api = artifacts_api_fixture
publication = publication_fixture
pytestmark = pytest.mark.integration
SYNTHETIC_DOI = "10.5072/polymath.synthetic.fixture909"


class SyntheticPublisher:
    def __init__(self, manifest):
        self.manifest = manifest
        self.metadata = canonical_remote_metadata(manifest)
        self.exists = self.published = False
        self.content = None
        self.calls = []
        self.lose_create = self.lose_publish = self.lose_upload = False
        self.hold_create = False
        self.pause_reads = self.hold_publish = False
        self.read_started, self.publish_started = asyncio.Event(), asyncio.Event()
        self.release_publish = asyncio.Event()
        self.create_started = asyncio.Event()
        self.corrupt_upload = self.corrupt_published = self.extra_files = False
        self.wrong_metadata = self.duplicate_marker = self.empty_lookup = False
        self.reject_create = False
        self.submission_pending = False
        self.on_upload = None

    def file(self):
        return {
            "filename": self.manifest.file_name,
            "filesize": len(self.content),
            "checksum": "md5:" + hashlib.md5(self.content, usedforsecurity=False).hexdigest(),
        }

    def deposit(self, identifier=101):
        metadata = copy.deepcopy(self.metadata)
        if self.wrong_metadata and self.content is not None:
            metadata["title"] = "A changed synthetic remote title"
        metadata["prereserve_doi"] = {"doi": SYNTHETIC_DOI, "recid": 909}
        result = {
            "id": identifier,
            "submitted": self.published,
            "state": "done" if self.published and not self.submission_pending else "inprogress",
            "metadata": metadata,
            "files": [self.file()] if self.content is not None else [],
            "links": {
                "bucket": "https://sandbox.zenodo.org/api/files/11111111-1111-4111-8111-111111111111",
                "self": f"https://sandbox.zenodo.org/api/deposit/depositions/{identifier}",
            },
        }
        if self.extra_files and self.content is not None:
            result["files"].append({"filename": "unapproved.txt", "filesize": 1})
        if self.published and not self.submission_pending:
            result.update(
                doi=SYNTHETIC_DOI,
                record_id=909,
                record_url="https://sandbox.zenodo.org/records/909",
            )
        return result

    async def handle(self, request):
        method, path = request.method, request.url.path
        self.calls.append((method, path))
        if path == "/api/deposit/depositions":
            if method == "POST":
                if self.reject_create:
                    return httpx.Response(400, json={"message": "Synthetic rejection"})
                assert json.loads(request.content)["metadata"] == self.metadata
                self.exists = True
                if self.hold_create:
                    self.create_started.set()
                    await asyncio.Event().wait()
                if self.lose_create:
                    self.lose_create = False
                    raise httpx.ReadTimeout("Synthetic create response lost after remote success")
                return httpx.Response(201, json=self.deposit())
            if method == "GET":
                if not self.exists or self.empty_lookup:
                    return httpx.Response(200, json=[])
                matches = [self.deposit()]
                if self.duplicate_marker:
                    matches.append(self.deposit(identifier=202))
                return httpx.Response(200, json=matches)
        if path == "/api/deposit/depositions/101" and method == "GET":
            if self.pause_reads:
                self.read_started.set()
                await asyncio.Event().wait()
            return httpx.Response(200, json=self.deposit())
        if path.endswith("/actions/publish") and method == "POST":
            self.published = True
            if self.hold_publish:
                self.publish_started.set()
                try:
                    await self.release_publish.wait()
                except asyncio.CancelledError:
                    # Deliberately simulate a provider response arriving after
                    # cancellation. The worker must retain this late audit.
                    await self.release_publish.wait()
            if self.lose_publish:
                self.lose_publish = False
                raise httpx.ReadTimeout("Synthetic publish response lost after remote success")
            return httpx.Response(200, json=self.deposit())
        if path.startswith("/api/files/"):
            assert path.endswith("/" + self.manifest.file_name)
            if method == "PUT":
                self.content = request.content
                assert hashlib.sha256(self.content).hexdigest() == self.manifest.markdown_sha256
                reply = self.file()
                if self.on_upload:
                    self.on_upload()
                if self.lose_upload:
                    self.lose_upload = False
                    raise httpx.ReadTimeout("Synthetic upload response lost after remote success")
                return httpx.Response(201, json=reply)
            if method == "GET":
                content = self.content
                if self.corrupt_upload or (self.published and self.corrupt_published):
                    content = b"X" + content[1:]
                return httpx.Response(200, content=content)
        raise AssertionError("Unexpected synthetic publisher route")

    def count(self, method, suffix):
        return sum(verb == method and path.endswith(suffix) for verb, path in self.calls)


@pytest.fixture
def pipeline(publication):
    case = publication
    manifest = prepare(case)
    receipt = approve(case, manifest)
    repo = PublicationJobRepository(case.engine, case.store)
    job = repo.enqueue(
        case.owner_hash, manifest.id, receipt.id, manifest.manifest_sha256, case.operator
    )
    native_manifest = PublicationManifest.model_validate_json(json.dumps(manifest.manifest))
    publisher = SyntheticPublisher(native_manifest)
    transport = httpx.MockTransport(publisher.handle)

    def factory(_):
        return ZenodoClient(
            "sandbox",
            SecretStr("synthetic-zenodo-fixture-token"),
            live_enabled=True,
            transport=transport,
        )

    worker = PublicationWorker(
        case.engine,
        publication_policy=case.policy,
        operator_policy=case.operator_policy,
        client_factory=factory,
        execution_enabled=True,
        policy=PublicationWorkerPolicy(lease_seconds=30, heartbeat_seconds=0.05),
    )
    return case, manifest, receipt, repo, job, publisher, worker


@pytest.mark.asyncio
async def test_disabled_worker_never_claims_or_constructs_a_client(pipeline):
    case, _, _, repo, job, publisher, worker = pipeline
    worker.execution_enabled = False
    worker.client_factory = lambda _: pytest.fail("Disabled worker constructed a client")
    assert (await worker.run_once("disabled-publication")).status == "disabled"
    assert repo.get_internal(job.id).status == "queued"
    assert publisher.calls == []


@pytest.mark.asyncio
async def test_complete_pipeline_verifies_actual_bytes_and_distinct_record_id(pipeline):
    case, manifest, _, repo, job, publisher, worker = pipeline
    outcome = await worker.run_once("synthetic-publication")
    assert outcome.status == "published", outcome
    saved = repo.get_internal(job.id)
    assert saved.outcome["verified"] is True
    assert saved.outcome["manifest_sha256"] == manifest.manifest_sha256
    assert saved.outcome["markdown_sha256"] == case.artifact.markdown_sha256
    assert str(saved.outcome["remote_deposit_id"]) == "101"
    assert str(saved.outcome["record_id"]) == "909"
    assert saved.to_response()["doi"] == SYNTHETIC_DOI
    assert saved.to_response()["record_url"] == "https://sandbox.zenodo.org/records/909"
    assert publisher.count("POST", "/api/deposit/depositions") == 1
    assert publisher.count("PUT", worker_input_filename(pipeline)) == 1
    assert publisher.count("POST", "/actions/publish") == 1
    assert publisher.count("GET", worker_input_filename(pipeline)) == 2
    assert (await worker.run_once("idle-publication")).status == "idle"


def worker_input_filename(pipeline):
    return pipeline[1].manifest["file_name"]


@pytest.mark.asyncio
async def test_restart_replays_observed_create_without_second_post(pipeline):
    case, _, _, repo, job, publisher, worker = pipeline
    publisher.pause_reads = True
    task = asyncio.create_task(worker.run_once("publication-before-restart"))
    await asyncio.wait_for(publisher.read_started.wait(), timeout=10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert repo.get_internal(job.id).status == "interrupted"
    assert not repo.get_internal(job.id).external_outcome_unknown
    publisher.pause_reads = False
    repo.resume(case.owner_hash, job.id, case.operator)
    assert (await worker.run_once("publication-after-restart")).status == "published"
    assert publisher.count("POST", "/api/deposit/depositions") == 1


@pytest.mark.asyncio
async def test_lost_create_response_reconciles_readonly_then_resumes_without_duplicate(pipeline):
    case, _, _, repo, job, publisher, worker = pipeline
    publisher.lose_create = True
    assert (await worker.run_once("publication-lost-create")).status == "interrupted"
    assert repo.get_internal(job.id).external_outcome_unknown
    assert publisher.count("POST", "/api/deposit/depositions") == 1
    repo.enqueue_reconciliation(case.owner_hash, job.id, case.operator)
    before = list(publisher.calls)
    assert (await worker.run_once("publication-reconcile-create")).status == "interrupted"
    assert all(method == "GET" for method, _ in publisher.calls[len(before) :])
    reconciled = repo.get_internal(job.id)
    assert not reconciled.external_outcome_unknown
    assert str(reconciled.remote_deposit_id) == "101"
    repo.resume(case.owner_hash, job.id, case.operator)
    assert (await worker.run_once("publication-resume-create")).status == "published"
    assert publisher.count("POST", "/api/deposit/depositions") == 1


@pytest.mark.asyncio
async def test_lost_publish_response_recovers_real_publication_without_second_publish(pipeline):
    case, _, _, repo, job, publisher, worker = pipeline
    publisher.lose_publish = True
    assert (await worker.run_once("publication-lost-publish")).status == "interrupted"
    assert repo.get_internal(job.id).external_outcome_unknown
    assert repo.get_internal(job.id).to_response()["doi"] is None
    repo.enqueue_reconciliation(case.owner_hash, job.id, case.operator)
    before = list(publisher.calls)
    assert (await worker.run_once("publication-reconcile-published")).status == "published"
    assert all(method == "GET" for method, _ in publisher.calls[len(before) :])
    assert publisher.count("POST", "/actions/publish") == 1


@pytest.mark.asyncio
async def test_lost_upload_response_reconciles_bytes_without_second_put(pipeline):
    case, _, _, repo, job, publisher, worker = pipeline
    publisher.lose_upload = True
    assert (await worker.run_once("publication-lost-upload")).status == "interrupted"
    assert repo.get_internal(job.id).external_outcome_unknown
    repo.enqueue_reconciliation(case.owner_hash, job.id, case.operator)
    before = list(publisher.calls)
    assert (await worker.run_once("publication-reconcile-upload")).status == "interrupted"
    assert all(method == "GET" for method, _ in publisher.calls[len(before) :])
    assert not repo.get_internal(job.id).external_outcome_unknown
    repo.resume(case.owner_hash, job.id, case.operator)
    assert (await worker.run_once("publication-resume-upload")).status == "published"
    assert publisher.count("PUT", worker_input_filename(pipeline)) == 1


@pytest.mark.asyncio
async def test_readonly_reconciliation_preserves_publication_after_approval_expiry(
    pipeline, monkeypatch
):
    import polymath.publication as publications

    case, manifest, receipt, repo, job, publisher, worker = pipeline
    publisher.lose_publish = True
    await worker.run_once("publication-published-before-approval-expiry")
    repo.enqueue_reconciliation(case.owner_hash, job.id, case.operator)
    future = receipt.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(publications, "_now", lambda _: future)
    assert not case.store.get(case.owner_hash, manifest.id).approval.valid
    before = list(publisher.calls)
    assert (await worker.run_once("publication-reconcile-expired-approval")).status == "published"
    assert all(method == "GET" for method, _ in publisher.calls[len(before) :])
    assert publisher.count("POST", "/actions/publish") == 1


@pytest.mark.asyncio
async def test_shutdown_preserves_inflight_grant_and_never_replays_post(pipeline):
    _, _, _, repo, job, publisher, worker = pipeline
    publisher.hold_create = True
    stop = asyncio.Event()
    task = asyncio.create_task(
        worker.run_until_stopped("publication-cooperative-shutdown", stop, poll_seconds=0.01)
    )
    await asyncio.wait_for(publisher.create_started.wait(), timeout=10)
    stop.set()
    await asyncio.wait_for(task, timeout=10)
    assert repo.get_internal(job.id).status == "interrupted"
    assert repo.get_internal(job.id).external_outcome_unknown
    assert publisher.count("POST", "/api/deposit/depositions") == 1
    assert publisher.count("POST", "/actions/publish") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["corrupt_upload", "extra_files", "wrong_metadata"])
async def test_changed_remote_file_or_metadata_prevents_publish(pipeline, failure):
    _, _, _, repo, job, publisher, worker = pipeline
    setattr(publisher, failure, True)
    assert (await worker.run_once("publication-mismatch")).status == "interrupted"
    assert publisher.count("POST", "/actions/publish") == 0
    assert repo.get_internal(job.id).to_response()["doi"] is None


@pytest.mark.asyncio
async def test_corruption_after_publish_is_reported_but_unverified_and_no_doi(pipeline):
    _, _, _, repo, job, publisher, worker = pipeline
    publisher.corrupt_published = True
    assert (await worker.run_once("publication-postpublish-mismatch")).status == "interrupted"
    saved = repo.get_internal(job.id)
    assert saved.publication_reported
    assert saved.remote_status == "publication_reported"
    assert saved.to_response()["doi"] is None
    assert saved.to_response()["record_url"] is None
    assert saved.outcome.get("verified") is not True
    assert publisher.count("POST", "/actions/publish") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["wrong_metadata", "submission_pending"])
async def test_get_reported_submission_is_retained_before_any_verification_failure(
    pipeline, failure
):
    _, _, _, repo, job, publisher, worker = pipeline
    setattr(publisher, failure, True)

    def externally_submit():
        publisher.published = True

    publisher.on_upload = externally_submit
    assert (await worker.run_once("publication-get-reported-submission")).status == "interrupted"
    saved = repo.get_internal(job.id)
    assert saved.publication_reported
    assert saved.remote_status == "publication_reported"
    assert not saved.can_resume
    assert saved.to_response()["doi"] is None
    assert saved.to_response()["record_url"] is None
    assert publisher.count("POST", "/actions/publish") == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["revoke", "expire"])
async def test_expired_or_revoked_approval_blocks_new_publish_grant(pipeline, change):
    case, _, receipt, repo, job, publisher, worker = pipeline

    def change_authority():
        if change == "revoke":
            case.store.revoke(case.owner_hash, receipt.id, case.operator)
        else:
            with Session(case.engine) as session, session.begin():
                operator = session.get(OperatorSession, case.operator.token_hash)
                operator.expires_at = session.scalar(select(func.clock_timestamp()))

    publisher.on_upload = change_authority
    assert (await worker.run_once("publication-authority-changed")).status == "interrupted"
    assert publisher.count("POST", "/actions/publish") == 0
    assert repo.get_internal(job.id).to_response()["doi"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["empty_lookup", "duplicate_marker"])
async def test_inconclusive_creation_lookup_never_retries_mutation(pipeline, failure):
    case, _, _, repo, job, publisher, worker = pipeline
    publisher.lose_create = True
    await worker.run_once("publication-ambiguous-create")
    setattr(publisher, failure, True)
    repo.enqueue_reconciliation(case.owner_hash, job.id, case.operator)
    before = list(publisher.calls)
    assert (await worker.run_once("publication-inconclusive-reconcile")).status == "interrupted"
    assert all(method == "GET" for method, _ in publisher.calls[len(before) :])
    assert repo.get_internal(job.id).external_outcome_unknown
    assert publisher.count("POST", "/api/deposit/depositions") == 1


@pytest.mark.asyncio
async def test_known_rejection_fails_without_retrying_or_claiming_remote_effect(pipeline):
    _, _, _, repo, job, publisher, worker = pipeline
    publisher.reject_create = True
    assert (await worker.run_once("publication-known-rejection")).status == "failed"
    assert not repo.get_internal(job.id).external_outcome_unknown
    assert publisher.count("POST", "/api/deposit/depositions") == 1
    assert publisher.count("POST", "/actions/publish") == 0


@pytest.mark.asyncio
async def test_late_publish_after_cancel_retains_report_and_needs_get_verification(pipeline):
    case, _, _, repo, job, publisher, worker = pipeline
    publisher.hold_publish = True
    task = asyncio.create_task(worker.run_once("publication-cancelled-in-flight"))
    await asyncio.wait_for(publisher.publish_started.wait(), timeout=10)
    repo.cancel(case.owner_hash, job.id)
    publisher.release_publish.set()
    outcome = await asyncio.wait_for(task, timeout=10)
    assert outcome.status == "cancelled"
    saved = repo.get_internal(job.id)
    assert saved.publication_reported
    assert saved.to_response()["doi"] is None
    repo.enqueue_reconciliation(case.owner_hash, job.id, case.operator)
    assert (await worker.run_once("publication-reconcile-late-response")).status == "published"
    assert publisher.count("POST", "/actions/publish") == 1
    with Session(case.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationOperation)) == 3
        assert session.scalar(select(func.count()).select_from(PublicationOperationAudit)) >= 3


@pytest.mark.asyncio
async def test_replacement_worker_interrupts_expired_work_without_resending_a_pending_grant(
    pipeline,
):
    case, _, _, repo, job, publisher, worker = pipeline
    lease = repo.claim_next("crashed-publication-process")
    repo.prepare_operation(lease, "create", "a" * 64)
    with Session(case.engine) as session, session.begin():
        session.execute(
            update(PublicationJob)
            .where(PublicationJob.id == job.id)
            .values(lease_expires_at=func.clock_timestamp() - text("interval '1 second'"))
        )
    assert (await worker.run_once("replacement-publication-process")).status == "idle"
    saved = repo.get(case.owner_hash, job.id)
    assert saved.status == "interrupted"
    assert saved.external_outcome_unknown
    assert not saved.can_resume
    assert publisher.calls == []
