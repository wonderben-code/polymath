"""Fenced, injected publication worker. No live client is constructed by default.

Exactly one durable grant precedes each create/upload/publish mutation. An
observed mutation replays without HTTP; unknown outcomes require a separately
queued read-only reconciliation. A provider response alone never verifies a
publication: the worker reads back metadata and the exact approved file bytes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from polymath.access_models import ResearchRunAccess
from polymath.artifacts import ArtifactStore
from polymath.operator_auth import OperatorPolicy
from polymath.publication import PublicationError, PublicationStore, hash_manifest
from polymath.publication_contracts import PublicationManifest, PublicationPolicy
from polymath.publication_jobs import (
    PublicationJobError,
    PublicationJobRepository,
    PublicationLeaseLost,
)
from polymath.publication_models import PublicationManifestRecord
from polymath.zenodo import RemoteFile, ZenodoClient, ZenodoDeposit, ZenodoError, metadata_matches

if TYPE_CHECKING:
    from polymath.publication_jobs import PublicationJobLease, PublicationJobSnapshot

PROTOCOL = "polymath-publication-worker-v1"


@dataclass(frozen=True)
class PublicationWorkerPolicy:
    lease_seconds: int = 60
    heartbeat_seconds: float = 10.0

    def __post_init__(self):
        if type(self.lease_seconds) is not int or not 3 <= self.lease_seconds <= 3600:
            raise ValueError("Publication lease duration must be between3 and3600 seconds")
        if (
            type(self.heartbeat_seconds) not in (int, float)
            or not 0 < self.heartbeat_seconds < self.lease_seconds / 2
        ):
            raise ValueError("Publication heartbeat must be below half the lease duration")


@dataclass(frozen=True)
class PublicationWorkerOutcome:
    status: str
    job_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class _Input:
    manifest: PublicationManifest
    manifest_sha256: str
    content: bytes


@dataclass(frozen=True)
class _Action:
    status: str
    reason: str | None = None
    outcome: dict | None = None


class _Stop(Exception):
    def __init__(self, reason: str, *, failed: bool = False):
        super().__init__(reason)
        self.reason, self.failed = reason, failed


def _digest(value: dict) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _safe_error(error: Exception) -> str:
    code = error.code if isinstance(error, ZenodoError) else str(error)
    if isinstance(code, str) and re.fullmatch(
        r"(?:zenodo|publication|invalid_publication)_[a-z_]{1,80}", code
    ):
        return code
    return "publication_remote_failed"


def _deposit_observation(deposit: ZenodoDeposit) -> dict:
    return {
        "deposit": deposit.model_dump(mode="json"),
        "publication_reported": deposit.submitted or deposit.state == "done",
    }


def _file_observation(file: RemoteFile, deposit_id: int, verified_sha256=None) -> dict:
    return {
        "file": file.model_dump(mode="json"),
        "remote_deposit_id": deposit_id,
        "verified_sha256": verified_sha256,
    }


class PublicationWorker:
    def __init__(
        self,
        engine: Engine,
        *,
        publication_policy: PublicationPolicy | None = None,
        operator_policy: OperatorPolicy | None = None,
        client_factory: Callable[[PublicationJobSnapshot], ZenodoClient],
        execution_enabled: bool = False,
        policy: PublicationWorkerPolicy | None = None,
    ):
        if type(execution_enabled) is not bool:
            raise ValueError("Publication execution gate must be an explicit bool")
        self.engine = engine
        self.store = PublicationStore(engine, publication_policy, operator_policy=operator_policy)
        self.jobs = PublicationJobRepository(engine, self.store)
        self.client_factory = client_factory
        self.execution_enabled = execution_enabled
        self.policy = policy or PublicationWorkerPolicy()

    def _load_input(self, snapshot: PublicationJobSnapshot) -> _Input:
        # This internal immutable read deliberately does not require a still-
        # valid approval. Read-only reconciliation must preserve remote facts
        # even after cancellation or the approval/operator session has expired.
        with Session(self.engine) as session:
            row = session.get(PublicationManifestRecord, snapshot.manifest_id)
            if (
                row is None
                or row.owner_session_hash != snapshot.owner_session_hash
                or row.manifest_sha256 != snapshot.manifest_sha256
            ):
                raise _Stop("publication_input_changed")
            manifest = PublicationManifest.model_validate_json(json.dumps(row.manifest))
            if hash_manifest(manifest) != snapshot.manifest_sha256:
                raise _Stop("publication_input_changed")
            owner = session.scalar(
                select(ResearchRunAccess.session_hash).where(
                    ResearchRunAccess.run_id == uuid.UUID(manifest.artifact_run_id)
                )
            )
            if owner != snapshot.owner_session_hash:
                raise _Stop("publication_input_changed")
        artifact = ArtifactStore(self.engine).get(
            uuid.UUID(manifest.artifact_run_id),
            uuid.UUID(manifest.artifact_id),
        )
        content = artifact.markdown.encode("utf-8")
        if (
            artifact.kind not in ("write_summary", "write_paper")
            or artifact.status != "ready"
            or artifact.payload_sha256 != manifest.payload_sha256
            or artifact.basis_sha256 != manifest.basis_sha256
            or artifact.markdown_sha256 != manifest.markdown_sha256
            or len(content) != manifest.markdown_bytes
            or hashlib.sha256(content).hexdigest() != manifest.markdown_sha256
        ):
            raise _Stop("publication_input_changed")
        return _Input(manifest, snapshot.manifest_sha256, content)

    async def _owned(self, lease: PublicationJobLease) -> PublicationJobSnapshot:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise asyncio.CancelledError
        return await asyncio.to_thread(self.jobs.snapshot, lease)

    async def _phase(self, lease: PublicationJobLease, phase: str, summary: str) -> None:
        await asyncio.to_thread(self.jobs.set_phase, lease, phase, summary)

    async def _mutate(
        self, lease: PublicationJobLease, stage: str, data: _Input, remote_id: int | None, call
    ) -> dict:
        request_hash = _digest(
            {
                "protocol": PROTOCOL,
                "stage": stage,
                "manifest_sha256": data.manifest_sha256,
                "destination": data.manifest.destination,
                "remote_deposit_id": remote_id,
                "file_name": data.manifest.file_name if stage == "upload" else None,
                "markdown_sha256": data.manifest.markdown_sha256 if stage == "upload" else None,
            }
        )
        recorded = await asyncio.to_thread(self.jobs.get_operation, lease, stage, request_hash)
        if recorded is not None:
            if "error_code" in recorded:
                raise _Stop("publication_remote_rejected", failed=True)
            return recorded
        grant = await asyncio.to_thread(self.jobs.prepare_operation, lease, stage, request_hash)
        if grant is None:
            recorded = await asyncio.to_thread(self.jobs.get_operation, lease, stage, request_hash)
            if recorded is None:
                raise _Stop("publication_stage_already_granted")
            return recorded
        try:
            value = await call()
            if stage == "upload":
                if not isinstance(value, RemoteFile):
                    raise _Stop("publication_invalid_remote_response")
                observation = _file_observation(value, remote_id)
            else:
                if not isinstance(value, ZenodoDeposit):
                    raise _Stop("publication_invalid_remote_response")
                observation = _deposit_observation(value)
            # The repository must keep this response as audit even if ownership
            # expired or was cancelled while the remote request was in flight.
            await asyncio.to_thread(self.jobs.record_success, grant, observation)
            await self._owned(lease)
            return observation
        except ZenodoError as error:
            if error.outcome_unknown:
                await asyncio.shield(
                    asyncio.to_thread(self.jobs.record_unknown, grant, reason=_safe_error(error))
                )
                raise _Stop(_safe_error(error)) from None
            await asyncio.shield(
                asyncio.to_thread(
                    self.jobs.record_rejected, grant, {"error_code": _safe_error(error)}
                )
            )
            raise _Stop(_safe_error(error), failed=True) from None
        except BaseException:
            with suppress(Exception):
                await asyncio.shield(
                    asyncio.to_thread(
                        self.jobs.record_unknown, grant, reason="publication_worker_interrupted"
                    )
                )
            raise

    async def _remote(
        self, lease: PublicationJobLease, client: ZenodoClient, identifier: int, data: _Input
    ) -> ZenodoDeposit:
        await self._owned(lease)
        deposit = await client.get_deposit(identifier)
        await asyncio.to_thread(
            self.jobs.record_remote_observation, lease, _deposit_observation(deposit)
        )
        await self._owned(lease)
        if deposit.id != identifier or not metadata_matches(deposit, data.manifest):
            raise _Stop("zenodo_metadata_mismatch")
        return deposit

    async def _verify_file(
        self, lease: PublicationJobLease, client: ZenodoClient, deposit: ZenodoDeposit, data: _Input
    ) -> RemoteFile:
        await self._phase(
            lease, "verify", "Verifying approved metadata and actual remote file bytes."
        )
        if not metadata_matches(deposit, data.manifest):
            raise _Stop("zenodo_metadata_mismatch")
        if (
            len(deposit.files) != 1
            or deposit.files[0].filename != data.manifest.file_name
            or deposit.files[0].size != len(data.content)
        ):
            raise _Stop("zenodo_file_mismatch")
        remote_bytes = await client.get_file_bytes(
            deposit, data.manifest.file_name, maxbytes=data.manifest.markdown_bytes
        )
        await self._owned(lease)
        if (
            len(remote_bytes) != len(data.content)
            or hashlib.sha256(remote_bytes).hexdigest() != data.manifest.markdown_sha256
        ):
            raise _Stop("zenodo_file_mismatch")
        await asyncio.to_thread(
            self.jobs.append_event,
            lease,
            event_key=f"verified-file:{lease.token}:{deposit.id}:{data.manifest.markdown_sha256}",
            kind="file_verified",
            summary="Verified the exact approved file bytes and publication metadata.",
        )
        return deposit.files[0]

    def _published(self, deposit: ZenodoDeposit, data: _Input) -> _Action:
        if (
            not deposit.published
            or not metadata_matches(deposit, data.manifest)
            or type(deposit.record_id) is not int
            or deposit.record_id < 1
        ):
            raise _Stop("publication_verification_pending")
        return _Action(
            "published",
            outcome={
                "verified": True,
                "phase": "complete",
                "manifest_sha256": data.manifest_sha256,
                "markdown_sha256": data.manifest.markdown_sha256,
                "destination": data.manifest.destination,
                "doi": deposit.doi,
                "record_url": deposit.record_url,
                "record_id": deposit.record_id,
                "remote_deposit_id": deposit.id,
            },
        )

    async def _execute(
        self,
        lease: PublicationJobLease,
        snapshot: PublicationJobSnapshot,
        client: ZenodoClient,
        data: _Input,
    ) -> _Action:
        await self._phase(lease, "create", "Preparing the exact approved publisher draft.")
        created = await self._mutate(
            lease, "create", data, None, lambda: client.create_draft(data.manifest)
        )
        deposit = ZenodoDeposit.model_validate_json(json.dumps(created["deposit"]))
        if not metadata_matches(deposit, data.manifest):
            raise _Stop("zenodo_metadata_mismatch")
        await asyncio.to_thread(self.jobs.set_remote_deposit, lease, deposit.id)
        deposit = await self._remote(lease, client, deposit.id, data)
        if deposit.published:
            await self._verify_file(lease, client, deposit, data)
            return self._published(deposit, data)
        if deposit.submitted or deposit.state not in ("unsubmitted", "inprogress"):
            raise _Stop("publication_verification_pending")
        # Never overwrite an unrecorded pre-existing file. If a prior PUT is
        # observed, _mutate replays it without a second HTTP request.
        if deposit.files:
            operations = await asyncio.to_thread(self.jobs.list_operations, lease)
            if not any(operation.stage == "upload" for operation in operations):
                raise _Stop("zenodo_file_mismatch")
        await self._phase(lease, "upload", "Uploading the exact approved document bytes.")

        async def upload():
            return await client.upload_file(
                deposit, data.manifest.file_name, data.content, data.manifest.markdown_sha256
            )

        await self._mutate(lease, "upload", data, deposit.id, upload)
        deposit = await self._remote(lease, client, deposit.id, data)
        await self._verify_file(lease, client, deposit, data)
        if deposit.published:
            return self._published(deposit, data)
        if deposit.submitted or deposit.state not in ("unsubmitted", "inprogress"):
            raise _Stop("publication_verification_pending")
        await self._phase(
            lease, "publish", "Requesting publication of the verified approved document."
        )
        await self._mutate(lease, "publish", data, deposit.id, lambda: client.publish(deposit.id))
        # Even a successful native publish response is only an observation.
        # Inspect actual remote metadata and bytes again before reporting a DOI.
        deposit = await self._remote(lease, client, deposit.id, data)
        await self._verify_file(lease, client, deposit, data)
        return self._published(deposit, data)

    async def _reconcile(
        self,
        lease: PublicationJobLease,
        snapshot: PublicationJobSnapshot,
        client: ZenodoClient,
        data: _Input,
    ) -> _Action:
        await self._phase(
            lease, "reconcile", "Checking the publisher read-only; no remote changes."
        )
        operations = await asyncio.to_thread(self.jobs.list_operations, lease)
        unresolved = {
            operation.stage: operation
            for operation in operations
            if operation.status in ("pending", "unknown")
        }
        identifier = (
            int(snapshot.remote_deposit_id) if snapshot.remote_deposit_id is not None else None
        )
        if identifier is None:
            matches = await client.find_deposits(data.manifest.remote_marker)
            for candidate in matches:
                await asyncio.to_thread(
                    self.jobs.record_remote_observation, lease, _deposit_observation(candidate)
                )
            await self._owned(lease)
            if len(matches) != 1:
                raise _Stop("zenodo_reconciliation_inconclusive")
            candidate = matches[0]
            if not metadata_matches(candidate, data.manifest):
                raise _Stop("zenodo_metadata_mismatch")
            identifier = candidate.id
        deposit = await self._remote(lease, client, identifier, data)
        if "create" in unresolved:
            await asyncio.to_thread(
                self.jobs.record_reconciled,
                lease,
                "create",
                _deposit_observation(deposit),
                request_hash=unresolved["create"].request_hash,
            )
        await asyncio.to_thread(self.jobs.set_remote_deposit, lease, deposit.id)
        file = None
        if deposit.files or "upload" in unresolved or "publish" in unresolved or deposit.published:
            file = await self._verify_file(lease, client, deposit, data)
        if "upload" in unresolved and file is not None:
            await asyncio.to_thread(
                self.jobs.record_reconciled,
                lease,
                "upload",
                _file_observation(file, deposit.id, data.manifest.markdown_sha256),
                request_hash=unresolved["upload"].request_hash,
            )
        if deposit.published:
            if "publish" in unresolved:
                await asyncio.to_thread(
                    self.jobs.record_reconciled,
                    lease,
                    "publish",
                    _deposit_observation(deposit),
                    request_hash=unresolved["publish"].request_hash,
                )
            return self._published(deposit, data)
        if "publish" in unresolved or ("upload" in unresolved and file is None):
            raise _Stop("zenodo_reconciliation_inconclusive")
        # Verified read-only observations do not authorize another mutation.
        # A human must explicitly resume using a current exact approval receipt.
        return _Action("interrupted", reason="publication_reconciliation_complete")

    async def run_once(self, worker_id: str) -> PublicationWorkerOutcome:
        if not self.execution_enabled:
            return PublicationWorkerOutcome("disabled")
        # A crashed process cannot run its own cleanup. Expose expired work as
        # interrupted while retaining every uncertain operation for reconciliation.
        await asyncio.to_thread(self.jobs.interrupt_expired)
        lease = await asyncio.to_thread(
            self.jobs.claim_next, worker_id, lease_seconds=self.policy.lease_seconds
        )
        if lease is None:
            return PublicationWorkerOutcome("idle")
        parent = asyncio.current_task()
        closing, lost = asyncio.Event(), asyncio.Event()

        async def heartbeat():
            while True:
                await asyncio.sleep(self.policy.heartbeat_seconds)
                try:
                    await asyncio.to_thread(
                        self.jobs.heartbeat, lease, lease_seconds=self.policy.lease_seconds
                    )
                except Exception:
                    if closing.is_set():
                        return
                    lost.set()
                    if parent is not None:
                        parent.cancel()
                    return

        pulse = asyncio.create_task(heartbeat())

        async def stop_pulse():
            closing.set()
            pulse.cancel()
            with suppress(asyncio.CancelledError):
                await pulse

        try:
            snapshot = await self._owned(lease)
            data = await asyncio.to_thread(self._load_input, snapshot)
            client = self.client_factory(snapshot)
            if (
                not isinstance(client, ZenodoClient)
                or client.destination != data.manifest.destination
            ):
                raise _Stop("publication_client_unavailable")
            if snapshot.work_kind == "reconcile":
                action = await self._reconcile(lease, snapshot, client, data)
            else:
                action = await self._execute(lease, snapshot, client, data)
            await stop_pulse()
            if action.status == "interrupted":
                await asyncio.to_thread(self.jobs.interrupt, lease, reason=action.reason)
            else:
                await asyncio.to_thread(
                    self.jobs.finish,
                    lease,
                    status=action.status,
                    outcome=action.outcome or {},
                    reason=action.reason,
                )
        except asyncio.CancelledError:
            await stop_pulse()
            await self._stop(lease, "publication_worker_interrupted")
            if not lost.is_set():
                raise
        except _Stop as error:
            await stop_pulse()
            if error.failed:
                with suppress(PublicationJobError):
                    await asyncio.to_thread(
                        self.jobs.finish,
                        lease,
                        status="failed",
                        outcome={"verified": False, "phase": "publish", "error_code": error.reason},
                        reason=error.reason,
                    )
            else:
                await self._stop(lease, error.reason)
        except ZenodoError as error:
            await stop_pulse()
            await self._stop(lease, _safe_error(error))
        except PublicationLeaseLost:
            await stop_pulse()
            await self._stop(lease, "publication_ownership_lost")
        except (PublicationJobError, PublicationError) as error:
            await stop_pulse()
            reason = str(error)
            if re.fullmatch(r"(?:publication|operator)_[a-z_]{1,80}", reason) is None:
                reason = "publication_authorization_failed"
            await self._stop(lease, reason)
        except Exception:
            await stop_pulse()
            await self._stop(lease, "publication_worker_failed")
        finally:
            await stop_pulse()
        current = await asyncio.to_thread(self.jobs.get_internal, lease.job_id)
        return PublicationWorkerOutcome(current.status, str(current.id), current.reason)

    async def _stop(self, lease: PublicationJobLease, reason: str):
        try:
            await asyncio.to_thread(self.jobs.interrupt, lease, reason=reason)
        except PublicationJobError:
            await asyncio.to_thread(self.jobs.interrupt_expired)

    async def run_until_stopped(
        self, worker_id: str, stop: asyncio.Event, *, poll_seconds: float = 1.0
    ):
        if type(poll_seconds) not in (int, float) or not 0 < poll_seconds <= 60:
            raise ValueError("Publication polling interval must be between0 and60 seconds")
        stopping = asyncio.create_task(stop.wait())
        work = None
        try:
            while not stop.is_set():
                work = asyncio.create_task(self.run_once(worker_id))
                done, _ = await asyncio.wait((work, stopping), return_when=asyncio.FIRST_COMPLETED)
                if stopping in done:
                    work.cancel()
                    with suppress(asyncio.CancelledError):
                        await work
                    return
                outcome = await work
                if outcome.status == "disabled":
                    return
                if outcome.status == "idle":
                    with suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        finally:
            stopping.cancel()
            with suppress(asyncio.CancelledError):
                await stopping
            if work is not None and not work.done():
                work.cancel()
                with suppress(asyncio.CancelledError):
                    await work


if __name__ == "__main__":
    raise SystemExit(
        "Publication execution is disabled: configure and verify publisher access, "
        "native sandbox behavior and exact human approval before enabling a live client."
    )
