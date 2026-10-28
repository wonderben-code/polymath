"""Fenced publication jobs and one-use mutation grants; no external calls.

Every new mutation grant retains the current operator and exact-manifest locks
through its commit. A committed grant is the send boundary: cancellation cannot
unsend it. Unknown outcomes retain their unique stage and require explicit,
read-only reconciliation. Raw publishing responses are not verified artifacts.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import Engine, func, select, text, tuple_
from sqlalchemy.orm import Session

from polymath.evidence import EvidenceError, _canonical, _hash
from polymath.operator_models import OperatorSession
from polymath.publication import (
    PublicationError,
    PublicationStore,
    _artifact,
    _browser,
    _manifest,
    _owned_manifest,
)
from polymath.publication_job_models import (
    PublicationJob,
    PublicationJobEvent,
    PublicationOperation,
    PublicationOperationAudit,
    PublicationRemoteObservation,
)
from polymath.publication_models import PublicationApproval
from polymath.zenodo import canonical_remote_metadata

PUBLICATION_QUEUE_LOCK = 7_691_121_407
MAX_JSON_BYTES = 524_288
ACTIVE_STATES = ("queued", "running", "interrupted")
MUTATION_STAGES = ("create", "upload", "publish")
PHASES = ("queued", "create", "upload", "verify", "publish", "reconcile", "complete")


class PublicationJobError(Exception):
    """Static code, never a URL, credential, document, or remote response."""


class PublicationLeaseLost(PublicationJobError):
    pass


@dataclass(frozen=True)
class PublicationJobLease:
    job_id: uuid.UUID
    worker_id: str
    token: int
    expires_at: datetime


PublicationLease = PublicationJobLease


@dataclass(frozen=True)
class PublicationOperationGrant:
    id: uuid.UUID
    job_id: uuid.UUID
    stage: str
    request_hash: str
    token: int
    approval_id: uuid.UUID
    manifest_sha256: str

    @property
    def lease_token(self) -> int:
        return self.token


@dataclass(frozen=True)
class PublicationOperationSnapshot:
    id: uuid.UUID
    stage: str
    status: str
    request_hash: str
    observation: dict | None
    reason: str | None


@dataclass(frozen=True)
class PublicationJobEventSnapshot:
    sequence: int
    kind: str
    summary: str
    created_at: datetime
    event_key: str

    def to_response(self) -> dict:
        return {
            "sequence": self.sequence,
            "kind": self.kind,
            "summary": self.summary,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class PublicationJobSnapshot:
    id: uuid.UUID
    owner_session_hash: str
    manifest_id: uuid.UUID
    approval_id: uuid.UUID
    manifest_sha256: str
    status: str
    work_kind: str
    lease_token: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    remote_deposit_id: str | None
    outcome: dict
    reason: str | None
    event_sequence: int
    frozen: bool
    stage: str
    external_outcome_unknown: bool
    unresolved_operations: bool
    has_operations: bool
    publication_reported: bool
    destination: str
    created_at: datetime
    updated_at: datetime

    @property
    def job_id(self) -> uuid.UUID:
        return self.id

    @property
    def remote_status(self) -> str:
        if self.status == "published" and self.outcome.get("verified") is True:
            return "published"
        if self.publication_reported:
            return "publication_reported"
        if self.external_outcome_unknown:
            return "unknown"
        return "draft" if self.remote_deposit_id is not None else "not_created"

    @property
    def can_resume(self) -> bool:
        return bool(
            self.status == "interrupted" and not self.frozen and not self.external_outcome_unknown
        )

    @property
    def can_reconcile(self) -> bool:
        return bool(
            self.status in ("interrupted", "cancelled", "failed")
            and (self.has_operations or self.external_outcome_unknown)
        )

    @property
    def can_cancel(self) -> bool:
        return self.status in ("queued", "running", "interrupted", "failed")

    def to_response(self) -> dict:
        verified = self.outcome if self.remote_status == "published" else {}
        return {
            "id": str(self.id),
            "manifest_id": str(self.manifest_id),
            "approval_id": str(self.approval_id),
            "manifest_sha256": self.manifest_sha256,
            "status": self.status,
            "work_kind": self.work_kind,
            "destination": self.destination,
            "stage": self.stage,
            "reason": self.reason,
            "event_sequence": self.event_sequence,
            "frozen": self.frozen,
            "external_outcome_unknown": self.external_outcome_unknown,
            "publication_reported": self.publication_reported,
            "remote_status": self.remote_status,
            "can_cancel": self.can_cancel,
            "can_resume": self.can_resume,
            "can_reconcile": self.can_reconcile,
            "doi": verified.get("doi"),
            "record_url": verified.get("record_url"),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


def _now(session: Session) -> datetime:
    return session.scalar(select(func.clock_timestamp()))


def _integer(value: int, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise PublicationJobError("invalid_publication_job_request")
    return value


def _text(value: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise PublicationJobError("invalid_publication_job_request")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise PublicationJobError("invalid_publication_job_request") from None
    if any(ord(character) < 32 for character in value):
        raise PublicationJobError("invalid_publication_job_request")
    return value


def _code(value: str | None) -> str | None:
    if value is not None and (
        not isinstance(value, str) or not re.fullmatch(r"[a-z_]{1,100}", value)
    ):
        raise PublicationJobError("invalid_publication_job_request")
    return value


def _digest(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise PublicationJobError("invalid_publication_job_request")
    return value


def _json(value: dict) -> dict:
    try:
        if not isinstance(value, dict):
            raise ValueError
        encoded = _canonical(value)
        if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
            raise ValueError
        return json.loads(encoded)
    except (EvidenceError, TypeError, ValueError):
        raise PublicationJobError("invalid_publication_observation") from None


def _remote_id(value: str | int) -> str:
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or re.fullmatch(r"[1-9][0-9]{0,19}", value) is None:
        raise PublicationJobError("invalid_remote_deposit_id")
    return value


def _locked(session: Session, job_id: uuid.UUID) -> PublicationJob:
    row = session.scalar(
        select(PublicationJob)
        .where(PublicationJob.id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise PublicationJobError("publication_job_not_found")
    return row


def _owned(session: Session, lease: PublicationJobLease) -> PublicationJob:
    if not isinstance(lease, PublicationJobLease):
        raise PublicationLeaseLost("publication_lease_lost")
    _integer(lease.token, 1, 2**63 - 1)
    _text(lease.worker_id, 128)
    row = _locked(session, lease.job_id)
    if (
        row.status != "running"
        or row.lease_owner != lease.worker_id
        or row.lease_token != lease.token
        or row.lease_expires_at is None
        or row.lease_expires_at <= _now(session)
        or (row.frozen and row.work_kind != "reconcile")
    ):
        raise PublicationLeaseLost("publication_lease_lost")
    return row


def _clear_lease(row: PublicationJob) -> None:
    row.lease_owner = None
    row.lease_expires_at = None


def _event(
    session: Session, row: PublicationJob, event_key: str, kind: str, summary: str
) -> PublicationJobEventSnapshot:
    event_key, kind, summary = _text(event_key, 256), _text(kind, 64), _text(summary, 2000)
    existing = session.scalar(
        select(PublicationJobEvent).where(
            PublicationJobEvent.job_id == row.id, PublicationJobEvent.event_key == event_key
        )
    )
    if existing is not None:
        if (existing.kind, existing.summary) != (kind, summary):
            raise PublicationJobError("publication_event_conflict")
        return _event_snapshot(existing)
    row.event_sequence += 1
    row.updated_at = _now(session)
    event = PublicationJobEvent(
        job_id=row.id, sequence=row.event_sequence, event_key=event_key, kind=kind, summary=summary
    )
    session.add(event)
    session.flush()
    return _event_snapshot(event)


def _event_snapshot(row: PublicationJobEvent) -> PublicationJobEventSnapshot:
    return PublicationJobEventSnapshot(
        row.sequence, row.kind, row.summary, row.created_at, row.event_key
    )


def _snapshot(session: Session, row: PublicationJob) -> PublicationJobSnapshot:
    operations = session.scalars(
        select(PublicationOperation).where(PublicationOperation.job_id == row.id)
    ).all()
    unresolved = any(operation.status in ("pending", "unknown") for operation in operations)
    remote_id = row.remote_deposit_id
    if remote_id is None:
        for operation in operations:
            if operation.status in ("observed", "reconciled"):
                remote_id = _observation_remote_id(operation.observation)
                if remote_id is not None:
                    break
    manifest_row = _owned_manifest(session, row.owner_session_hash, row.manifest_id)
    manifest = _manifest(manifest_row)
    if row.status == "published":
        _verified_outcome(session, row, row.outcome)
    publish_attempted = any(
        operation.stage == "publish" and operation.status != "rejected" for operation in operations
    )
    uncertain = (
        unresolved
        or row.frozen
        or ((row.publication_reported or publish_attempted) and row.status != "published")
    )
    return PublicationJobSnapshot(
        id=row.id,
        owner_session_hash=row.owner_session_hash,
        manifest_id=row.manifest_id,
        approval_id=row.approval_id,
        manifest_sha256=row.manifest_sha256,
        status=row.status,
        work_kind=row.work_kind,
        lease_token=row.lease_token,
        lease_owner=row.lease_owner,
        lease_expires_at=row.lease_expires_at,
        remote_deposit_id=remote_id,
        outcome=_json(row.outcome),
        reason=row.reason,
        event_sequence=row.event_sequence,
        frozen=row.frozen,
        stage=row.stage,
        external_outcome_unknown=uncertain,
        unresolved_operations=unresolved,
        has_operations=bool(operations),
        publication_reported=row.publication_reported,
        destination=manifest.destination,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _operation(session: Session, job_id: uuid.UUID, stage: str) -> PublicationOperation | None:
    if stage not in MUTATION_STAGES:
        raise PublicationJobError("invalid_publication_stage")
    return session.scalar(
        select(PublicationOperation).where(
            PublicationOperation.job_id == job_id, PublicationOperation.stage == stage
        )
    )


def _audit(session: Session, operation: PublicationOperation, kind: str, observation: dict) -> None:
    observation = _json(observation)
    digest = _hash(_canonical(observation))
    existing = session.scalar(
        select(PublicationOperationAudit.id).where(
            PublicationOperationAudit.operation_id == operation.id,
            PublicationOperationAudit.kind == kind,
            PublicationOperationAudit.observation_sha256 == digest,
        )
    )
    if existing is None:
        session.add(
            PublicationOperationAudit(
                operation_id=operation.id,
                kind=kind,
                observation_sha256=digest,
                observation=observation,
            )
        )


def _freeze(session: Session, row: PublicationJob) -> None:
    row.frozen = True
    if row.status != "published":
        row.status = "interrupted"
    row.reason = "publication_reconciliation_conflict"
    _clear_lease(row)
    _event(
        session,
        row,
        "conflict",
        "interrupted",
        "Conflicting remote evidence was retained; manual resolution is required.",
    )


class PublicationJobRepository:
    def __init__(self, engine: Engine, store: PublicationStore):
        if engine.dialect.name != "postgresql" or not isinstance(store, PublicationStore):
            raise ValueError("Publication jobs require PostgreSQL and explicit approval policy")
        self.engine, self.store = engine, store

    @staticmethod
    def _receipt_operator(
        session: Session, approval_id: uuid.UUID, operator_session: OperatorSession
    ) -> None:
        receipt = session.get(PublicationApproval, approval_id)
        if receipt is None or receipt.operator_session_hash != getattr(
            operator_session, "token_hash", None
        ):
            raise PublicationError("operator_session_required")

    def get(self, owner_hash: str, job_id: uuid.UUID) -> PublicationJobSnapshot:
        with Session(self.engine) as session:
            _browser(session, owner_hash)
            row = session.scalar(
                select(PublicationJob).where(
                    PublicationJob.id == job_id, PublicationJob.owner_session_hash == owner_hash
                )
            )
            if row is None:
                raise PublicationJobError("publication_job_not_found")
            return _snapshot(session, row)

    def get_internal(self, job_id: uuid.UUID) -> PublicationJobSnapshot:
        with Session(self.engine) as session:
            row = session.get(PublicationJob, job_id)
            if row is None:
                raise PublicationJobError("publication_job_not_found")
            return _snapshot(session, row)

    def list(
        self,
        owner_hash: str,
        *,
        limit: int = 20,
        before: uuid.UUID | None = None,
        manifest_id: uuid.UUID | None = None,
    ) -> tuple[PublicationJobSnapshot, ...]:
        _integer(limit, 1, 21)
        with Session(self.engine) as session:
            _browser(session, owner_hash)
            statement = select(PublicationJob).where(
                PublicationJob.owner_session_hash == owner_hash
            )
            if manifest_id is not None:
                statement = statement.where(PublicationJob.manifest_id == manifest_id)
            if before is not None:
                anchor = session.scalar(
                    select(PublicationJob).where(
                        PublicationJob.id == before, PublicationJob.owner_session_hash == owner_hash
                    )
                )
                if anchor is None:
                    raise PublicationJobError("publication_job_not_found")
                statement = statement.where(
                    tuple_(PublicationJob.created_at, PublicationJob.id)
                    < tuple_(anchor.created_at, anchor.id)
                )
            rows = session.scalars(
                statement.order_by(
                    PublicationJob.created_at.desc(), PublicationJob.id.desc()
                ).limit(limit)
            ).all()
            return tuple(_snapshot(session, row) for row in rows)

    def enqueue(
        self,
        owner_hash: str,
        manifest_id: uuid.UUID,
        approval_id: uuid.UUID,
        expected_manifest_sha256: str,
        operator_session: OperatorSession,
    ) -> PublicationJobSnapshot:
        _digest(expected_manifest_sha256)
        with Session(self.engine) as session, session.begin():
            self.store.validate_approval(
                owner_hash,
                approval_id,
                manifest_id=manifest_id,
                expected_manifest_sha256=expected_manifest_sha256,
                session=session,
            )
            self._receipt_operator(session, approval_id, operator_session)
            self.store._operator(session, owner_hash, operator_session)
            session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": PUBLICATION_QUEUE_LOCK}
            )
            self.store.validate_approval(
                owner_hash,
                approval_id,
                manifest_id=manifest_id,
                expected_manifest_sha256=expected_manifest_sha256,
                session=session,
            )
            self.store._operator(session, owner_hash, operator_session)
            existing = session.scalar(
                select(PublicationJob).where(PublicationJob.manifest_id == manifest_id)
            )
            if existing is not None:
                if existing.owner_session_hash != owner_hash:
                    raise PublicationJobError("publication_job_not_found")
                return _snapshot(session, existing)
            _browser(session, owner_hash)
            active = (
                select(func.count())
                .select_from(PublicationJob)
                .where(PublicationJob.status.in_(ACTIVE_STATES))
            )
            if session.scalar(active) >= 50:
                raise PublicationJobError("publication_queue_full")
            if session.scalar(active.where(PublicationJob.owner_session_hash == owner_hash)) >= 2:
                raise PublicationJobError("publication_owner_limit")
            # Lock wait time is not authority: validate the current receipt again
            # immediately before committing this queue entry.
            self.store.validate_approval(
                owner_hash,
                approval_id,
                manifest_id=manifest_id,
                expected_manifest_sha256=expected_manifest_sha256,
                session=session,
            )
            row = PublicationJob(
                owner_session_hash=owner_hash,
                manifest_id=manifest_id,
                approval_id=approval_id,
                manifest_sha256=expected_manifest_sha256,
            )
            session.add(row)
            session.flush()
            _event(session, row, f"created:{approval_id}", "queued", "Approved publication queued.")
            return _snapshot(session, row)

    def claim_next(
        self, worker_id: str, *, lease_seconds: int = 60, work_kind: str | None = None
    ) -> PublicationJobLease | None:
        _text(worker_id, 128)
        _integer(lease_seconds, 3, 3600)
        if work_kind is not None and work_kind not in ("publish", "reconcile"):
            raise PublicationJobError("invalid_publication_job_request")
        with Session(self.engine) as session, session.begin():
            query = select(PublicationJob).where(
                PublicationJob.status == "queued",
                (PublicationJob.frozen.is_(False) | (PublicationJob.work_kind == "reconcile")),
            )
            if work_kind is not None:
                query = query.where(PublicationJob.work_kind == work_kind)
            row = session.scalar(
                query.order_by(PublicationJob.created_at, PublicationJob.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return None
            now = _now(session)
            row.status = "running"
            row.lease_token += 1
            row.lease_owner = worker_id
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            row.heartbeat_at = now
            row.reason = None
            if row.work_kind == "reconcile":
                row.stage = "reconcile"
            _event(
                session,
                row,
                f"claimed:{row.lease_token}:{_hash(worker_id)}",
                "running",
                "Read-only reconciliation started."
                if row.work_kind == "reconcile"
                else "Approved publication started.",
            )
            return PublicationJobLease(row.id, worker_id, row.lease_token, row.lease_expires_at)

    def snapshot(self, lease: PublicationJobLease) -> PublicationJobSnapshot:
        with Session(self.engine) as session, session.begin():
            return _snapshot(session, _owned(session, lease))

    def heartbeat(
        self, lease: PublicationJobLease, *, lease_seconds: int = 60
    ) -> PublicationJobLease:
        _integer(lease_seconds, 3, 3600)
        with Session(self.engine) as session, session.begin():
            row = _owned(session, lease)
            now = _now(session)
            row.heartbeat_at = now
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return PublicationJobLease(row.id, lease.worker_id, lease.token, row.lease_expires_at)

    def append_event(
        self, lease: PublicationJobLease, *, event_key: str, kind: str, summary: str
    ) -> PublicationJobEventSnapshot:
        with Session(self.engine) as session, session.begin():
            row = _owned(session, lease)
            if kind in PHASES:
                row.stage = kind
            return _event(session, row, event_key, kind, summary)

    def set_phase(self, lease: PublicationJobLease, phase: str, summary: str) -> None:
        if phase not in PHASES:
            raise PublicationJobError("invalid_publication_stage")
        self.append_event(
            lease, event_key=f"phase:{phase}:{_hash(summary)}", kind=phase, summary=summary
        )

    def events_after(
        self, owner_hash: str, job_id: uuid.UUID, *, after: int = 0, limit: int = 100
    ) -> tuple[PublicationJobEventSnapshot, ...]:
        _integer(after, 0, 2**63 - 1)
        _integer(limit, 1, 200)
        self.get(owner_hash, job_id)
        with Session(self.engine) as session:
            rows = session.scalars(
                select(PublicationJobEvent)
                .where(PublicationJobEvent.job_id == job_id, PublicationJobEvent.sequence > after)
                .order_by(PublicationJobEvent.sequence)
                .limit(limit)
            ).all()
            return tuple(_event_snapshot(row) for row in rows)

    def list_operations(
        self, lease: PublicationJobLease
    ) -> tuple[PublicationOperationSnapshot, ...]:
        with Session(self.engine) as session, session.begin():
            _owned(session, lease)
            rows = session.scalars(
                select(PublicationOperation)
                .where(PublicationOperation.job_id == lease.job_id)
                .order_by(PublicationOperation.created_at, PublicationOperation.stage)
            ).all()
            return tuple(
                PublicationOperationSnapshot(
                    row.id,
                    row.stage,
                    row.status,
                    row.request_hash,
                    _json(row.observation) if row.observation is not None else None,
                    row.reason,
                )
                for row in rows
            )

    def get_operation(
        self, lease: PublicationJobLease, stage: str, request_hash: str
    ) -> dict | None:
        _digest(request_hash)
        with Session(self.engine) as session, session.begin():
            _owned(session, lease)
            operation = _operation(session, lease.job_id, stage)
            if operation is None:
                return None
            if operation.request_hash != request_hash:
                raise PublicationJobError("publication_request_changed")
            if operation.status in ("pending", "unknown"):
                raise PublicationJobError("publication_operation_unresolved")
            if operation.status == "rejected":
                raise PublicationJobError("publication_operation_rejected")
            if operation.observation is None:
                raise PublicationJobError("publication_operation_unresolved")
            return _json(operation.observation)

    def prepare_operation(
        self, lease: PublicationJobLease, stage: str, request_hash: str
    ) -> PublicationOperationGrant | None:
        _digest(request_hash)
        if stage not in MUTATION_STAGES:
            raise PublicationJobError("invalid_publication_stage")
        with Session(self.engine) as session, session.begin():
            # Read only immutable pointers before acquiring authority locks.
            pointer = session.get(PublicationJob, lease.job_id)
            if pointer is None:
                raise PublicationJobError("publication_job_not_found")
            binding = (
                pointer.owner_session_hash,
                pointer.manifest_id,
                pointer.approval_id,
                pointer.manifest_sha256,
            )
            self.store.validate_approval(
                binding[0],
                binding[2],
                manifest_id=binding[1],
                expected_manifest_sha256=binding[3],
                session=session,
            )
            row = _owned(session, lease)
            if (
                row.owner_session_hash,
                row.manifest_id,
                row.approval_id,
                row.manifest_sha256,
            ) != binding:
                raise PublicationJobError("publication_request_changed")
            if row.work_kind != "publish":
                raise PublicationJobError("publication_read_only_job")
            operation = _operation(session, row.id, stage)
            if operation is not None:
                if operation.request_hash != request_hash:
                    raise PublicationJobError("publication_request_changed")
                if operation.status in ("pending", "unknown"):
                    raise PublicationJobError("publication_operation_unresolved")
                if operation.status == "rejected":
                    raise PublicationJobError("publication_operation_rejected")
                return None
            unresolved = session.scalar(
                select(PublicationOperation.id)
                .where(
                    PublicationOperation.job_id == row.id,
                    PublicationOperation.status.in_(("pending", "unknown")),
                )
                .limit(1)
            )
            if unresolved is not None or row.publication_reported:
                raise PublicationJobError("publication_operation_unresolved")
            if stage != "create":
                required = ("create",) if stage == "upload" else ("create", "upload")
                if row.remote_deposit_id is None or any(
                    (prior := _operation(session, row.id, preceding)) is None
                    or prior.status not in ("observed", "reconciled")
                    for preceding in required
                ):
                    raise PublicationJobError("publication_stage_out_of_order")
            # A live lease is not a live approval. The job lock may have waited
            # past browser, operator, or receipt expiry; retain all authority
            # locks and recheck the exact receipt at the actual grant boundary.
            self.store.validate_approval(
                row.owner_session_hash,
                row.approval_id,
                manifest_id=row.manifest_id,
                expected_manifest_sha256=row.manifest_sha256,
                session=session,
            )
            operation = PublicationOperation(
                job_id=row.id,
                stage=stage,
                request_hash=request_hash,
                lease_token=lease.token,
                approval_id=row.approval_id,
                manifest_sha256=row.manifest_sha256,
            )
            session.add(operation)
            session.flush()
            row.stage = stage
            _event(
                session,
                row,
                f"grant:{stage}",
                f"{stage}_authorized",
                f"Authorized one {stage} request for the exact approved document.",
            )
            return PublicationOperationGrant(
                operation.id,
                row.id,
                stage,
                request_hash,
                lease.token,
                row.approval_id,
                row.manifest_sha256,
            )

    def _bound_grant(
        self, session: Session, grant: PublicationOperationGrant
    ) -> tuple[PublicationJob, PublicationOperation]:
        if not isinstance(grant, PublicationOperationGrant):
            raise PublicationJobError("invalid_publication_grant")
        _integer(grant.token, 1, 2**63 - 1)
        _digest(grant.request_hash)
        _digest(grant.manifest_sha256)
        row = _locked(session, grant.job_id)
        operation = session.get(PublicationOperation, grant.id)
        if operation is None or (
            operation.job_id,
            operation.stage,
            operation.request_hash,
            operation.lease_token,
            operation.approval_id,
            operation.manifest_sha256,
        ) != (
            grant.job_id,
            grant.stage,
            grant.request_hash,
            grant.token,
            grant.approval_id,
            grant.manifest_sha256,
        ):
            raise PublicationJobError("invalid_publication_grant")
        return row, operation

    def record_remote_observation(self, lease: PublicationJobLease, observation: dict) -> None:
        observation = _json(observation)
        digest = _hash(_canonical(observation))
        with Session(self.engine) as session, session.begin():
            row = _locked(session, lease.job_id)
            issued = session.scalar(
                select(PublicationJobEvent.sequence).where(
                    PublicationJobEvent.job_id == row.id,
                    PublicationJobEvent.event_key
                    == f"claimed:{lease.token}:{_hash(lease.worker_id)}",
                )
            )
            if issued is None:
                raise PublicationLeaseLost("publication_lease_not_issued")
            existing = session.scalar(
                select(PublicationRemoteObservation.id).where(
                    PublicationRemoteObservation.job_id == row.id,
                    PublicationRemoteObservation.observation_sha256 == digest,
                )
            )
            if existing is None:
                session.add(
                    PublicationRemoteObservation(
                        job_id=row.id,
                        lease_token=lease.token,
                        observation_sha256=digest,
                        observation=observation,
                    )
                )
            deposit = observation.get("deposit", observation)
            if isinstance(deposit, dict) and (
                deposit.get("submitted") is True or deposit.get("state") == "done"
            ):
                row.publication_reported = True
            if observation.get("publication_reported") is True:
                row.publication_reported = True
            _event(
                session,
                row,
                f"remote_observed:{digest}",
                "remote_status_observed",
                "Saved the publisher's remote state; exact-document verification is separate.",
            )

    def record_success(self, grant: PublicationOperationGrant, observation: dict) -> None:
        observation = _json(observation)
        conflict = False
        with Session(self.engine) as session, session.begin():
            row, operation = self._bound_grant(session, grant)
            _audit(session, operation, "observed", observation)
            if operation.status == "rejected" or (
                operation.observation is not None
                and not _compatible(operation.stage, operation.observation, observation)
            ):
                _audit(session, operation, "conflict", observation)
                _freeze(session, row)
                conflict = True
            else:
                if operation.status not in ("observed", "reconciled"):
                    operation.observation = observation
                    operation.status = "observed"
                    operation.reason = None
                # This is an audit fact, not a stale worker's checkpoint or a
                # claim that the approved bytes have actually been published.
                if _reported(observation):
                    row.publication_reported = True
                _event(
                    session,
                    row,
                    f"response:{operation.id}",
                    "remote_response_observed",
                    "Saved a remote mutation response; publication still requires verification.",
                )
        if conflict:
            raise PublicationJobError("publication_reconciliation_conflict")

    def record_unknown(
        self, grant: PublicationOperationGrant, reason: str = "remote_outcome_unknown"
    ) -> None:
        reason = _code(reason)
        with Session(self.engine) as session, session.begin():
            row, operation = self._bound_grant(session, grant)
            if operation.status in ("observed", "reconciled", "rejected"):
                return
            _audit(session, operation, "unknown", {"reason": reason})
            operation.status = "unknown"
            operation.reason = reason
            _event(
                session,
                row,
                f"unknown:{operation.id}",
                "remote_outcome_unknown",
                "A remote request has an unknown outcome; it will not be resent automatically.",
            )

    def record_rejected(self, grant: PublicationOperationGrant, observation: dict) -> None:
        observation = _json(observation)
        conflict = False
        with Session(self.engine) as session, session.begin():
            row, operation = self._bound_grant(session, grant)
            _audit(session, operation, "rejected", observation)
            if operation.status in ("observed", "reconciled"):
                _audit(session, operation, "conflict", observation)
                _freeze(session, row)
                conflict = True
            elif operation.status == "rejected" and operation.observation != observation:
                _audit(session, operation, "conflict", observation)
                _freeze(session, row)
                conflict = True
            else:
                operation.status = "rejected"
                operation.observation = observation
                operation.reason = "remote_request_rejected"
                if row.status not in ("published", "cancelled"):
                    row.status = "failed"
                    row.reason = "remote_request_rejected"
                    _clear_lease(row)
                _event(
                    session,
                    row,
                    f"rejected:{operation.id}",
                    "remote_request_rejected",
                    "The publisher rejected a remote request; no automatic retry is permitted.",
                )
        if conflict:
            raise PublicationJobError("publication_reconciliation_conflict")

    def record_reconciled(
        self,
        lease: PublicationJobLease,
        stage: str,
        observation: dict,
        *,
        request_hash: str | None = None,
    ) -> None:
        observation = _json(observation)
        conflict = False
        with Session(self.engine) as session, session.begin():
            row = _owned(session, lease)
            if row.work_kind != "reconcile":
                raise PublicationJobError("publication_reconciliation_required")
            operation = _operation(session, row.id, stage)
            if operation is None:
                raise PublicationJobError("publication_operation_not_found")
            if request_hash is not None and operation.request_hash != _digest(request_hash):
                raise PublicationJobError("publication_request_changed")
            _positive_reconciliation(session, row, stage, observation)
            _audit(session, operation, "reconciled", observation)
            if operation.status == "rejected" or (
                operation.observation is not None
                and not _compatible(stage, operation.observation, observation)
            ):
                _audit(session, operation, "conflict", observation)
                _freeze(session, row)
                conflict = True
            else:
                operation.observation = observation
                operation.status = "reconciled"
                operation.reason = None
                if _reported(observation):
                    row.publication_reported = True
                _event(
                    session,
                    row,
                    f"reconciled:{operation.id}:{_hash(_canonical(observation))}",
                    "remote_operation_reconciled",
                    "Read-only evidence reconciled a remote operation; no mutation was sent.",
                )
        if conflict:
            raise PublicationJobError("publication_reconciliation_conflict")

    def set_remote_deposit(self, lease: PublicationJobLease, remote_id: str | int) -> None:
        remote_id = _remote_id(remote_id)
        conflict = False
        with Session(self.engine) as session, session.begin():
            row = _owned(session, lease)
            known = session.scalars(
                select(PublicationOperation).where(
                    PublicationOperation.job_id == row.id,
                    PublicationOperation.status.in_(("observed", "reconciled")),
                )
            ).all()
            manifest = _manifest(_owned_manifest(session, row.owner_session_hash, row.manifest_id))
            observed = any(
                _matching_deposit(item.observation, manifest, remote_id) for item in known
            )
            if not observed:
                reads = session.scalars(
                    select(PublicationRemoteObservation).where(
                        PublicationRemoteObservation.job_id == row.id
                    )
                ).all()
                observed = any(
                    _matching_deposit(item.observation, manifest, remote_id) for item in reads
                )
            if not observed:
                raise PublicationJobError("publication_remote_identity_not_observed")
            if row.remote_deposit_id is not None and row.remote_deposit_id != remote_id:
                _freeze(session, row)
                conflict = True
            else:
                row.remote_deposit_id = remote_id
                row.updated_at = _now(session)
        if conflict:
            raise PublicationJobError("publication_reconciliation_conflict")

    def finish(
        self,
        lease: PublicationJobLease,
        *,
        status: str = "published",
        outcome: dict | None = None,
        reason: str | None = None,
    ) -> PublicationJobSnapshot:
        if status not in ("published", "failed"):
            raise PublicationJobError("invalid_publication_job_request")
        outcome, reason = _json(outcome or {}), _code(reason)
        with Session(self.engine) as session, session.begin():
            row = _owned(session, lease)
            if row.frozen:
                raise PublicationJobError("publication_job_frozen")
            if status == "published":
                outcome = _verified_outcome(session, row, outcome)
                row.remote_deposit_id = outcome["remote_deposit_id"]
                row.publication_reported = True
                row.stage = "complete"
            row.outcome = outcome
            row.status = status
            row.reason = reason
            _clear_lease(row)
            _event(
                session,
                row,
                f"finished:{lease.token}",
                status,
                "Verified the published record, reviewed metadata, and exact approved file bytes."
                if status == "published"
                else "Publication stopped without a verified published result.",
            )
            return _snapshot(session, row)

    def interrupt(
        self, lease: PublicationJobLease, reason: str = "worker_interrupted"
    ) -> PublicationJobSnapshot:
        reason = _code(reason)
        with Session(self.engine) as session, session.begin():
            row = _owned(session, lease)
            row.status = "interrupted"
            row.reason = reason
            _clear_lease(row)
            _event(
                session,
                row,
                f"interrupted:{lease.token}",
                "interrupted",
                "Publication work stopped explicitly; a human must choose any next action.",
            )
            return _snapshot(session, row)

    def interrupt_expired(self) -> int:
        with Session(self.engine) as session, session.begin():
            rows = session.scalars(
                select(PublicationJob)
                .where(
                    PublicationJob.status == "running",
                    PublicationJob.lease_expires_at <= _now(session),
                )
                .with_for_update(skip_locked=True)
            ).all()
            for row in rows:
                row.status = "interrupted"
                row.reason = "worker_lease_expired"
                _clear_lease(row)
                _event(
                    session,
                    row,
                    f"expired:{row.lease_token}",
                    "interrupted",
                    "The worker lease expired; remote requests will not be replayed automatically.",
                )
            return len(rows)

    def cancel(self, owner_hash: str, job_id: uuid.UUID) -> PublicationJobSnapshot:
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            row = _locked(session, job_id)
            if row.owner_session_hash != owner_hash:
                raise PublicationJobError("publication_job_not_found")
            _browser(session, owner_hash)
            if row.status not in ("published", "cancelled"):
                row.status = "cancelled"
                row.reason = "cancelled_by_owner"
                row.lease_token += 1
                _clear_lease(row)
                _event(
                    session,
                    row,
                    f"cancelled:{row.lease_token}",
                    "cancelled",
                    "Cancelled future actions; already granted requests may still complete.",
                )
            return _snapshot(session, row)

    def enqueue_reconciliation(
        self, owner_hash: str, job_id: uuid.UUID, operator_session: OperatorSession
    ) -> PublicationJobSnapshot:
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            self.store._operator(session, owner_hash, operator_session)
            pointer = session.get(PublicationJob, job_id)
            if pointer is None or pointer.owner_session_hash != owner_hash:
                raise PublicationJobError("publication_job_not_found")
            manifest = _owned_manifest(session, owner_hash, pointer.manifest_id, lock=True)
            if _manifest(manifest) is None or manifest.manifest_sha256 != pointer.manifest_sha256:
                raise PublicationJobError("publication_request_changed")
            _browser(session, owner_hash)
            row = _locked(session, job_id)
            _browser(session, owner_hash)
            self.store._operator(session, owner_hash, operator_session)
            if row.status not in ("interrupted", "cancelled", "failed"):
                raise PublicationJobError("publication_job_state_conflict")
            row.status = "queued"
            row.work_kind = "reconcile"
            row.stage = "reconcile"
            row.reason = None
            row.lease_token += 1
            _clear_lease(row)
            _event(
                session,
                row,
                f"reconciliation_queued:{row.lease_token}",
                "queued",
                "Human-requested read-only reconciliation queued; it cannot publish or upload.",
            )
            return _snapshot(session, row)

    def resume(
        self,
        owner_hash: str,
        job_id: uuid.UUID,
        operator_session: OperatorSession,
        new_approval_id: uuid.UUID | None = None,
    ) -> PublicationJobSnapshot:
        with Session(self.engine) as session, session.begin():
            pointer = session.get(PublicationJob, job_id)
            if pointer is None or pointer.owner_session_hash != owner_hash:
                raise PublicationJobError("publication_job_not_found")
            approval_id = new_approval_id or pointer.approval_id
            self.store.validate_approval(
                owner_hash,
                approval_id,
                manifest_id=pointer.manifest_id,
                expected_manifest_sha256=pointer.manifest_sha256,
                session=session,
            )
            self._receipt_operator(session, approval_id, operator_session)
            self.store._operator(session, owner_hash, operator_session)
            row = _locked(session, job_id)
            if row.status != "interrupted":
                raise PublicationJobError("publication_job_state_conflict")
            if row.frozen:
                raise PublicationJobError("publication_job_frozen")
            snapshot = _snapshot(session, row)
            if snapshot.external_outcome_unknown:
                raise PublicationJobError("publication_operation_unresolved")
            self.store.validate_approval(
                owner_hash,
                approval_id,
                manifest_id=row.manifest_id,
                expected_manifest_sha256=row.manifest_sha256,
                session=session,
            )
            row.approval_id = approval_id
            row.status = "queued"
            row.work_kind = "publish"
            row.reason = None
            row.lease_token += 1
            _clear_lease(row)
            _event(
                session,
                row,
                f"resumed:{row.lease_token}:{approval_id}",
                "queued",
                "Human-requested continuation queued with a current exact-document approval.",
            )
            return _snapshot(session, row)

    def status_for_manifest(self, owner_hash: str, manifest_id: uuid.UUID) -> str:
        with Session(self.engine) as session:
            _browser(session, owner_hash)
            _owned_manifest(session, owner_hash, manifest_id)
            row = session.scalar(
                select(PublicationJob).where(
                    PublicationJob.manifest_id == manifest_id,
                    PublicationJob.owner_session_hash == owner_hash,
                )
            )
            if row is None:
                return "not_published"
            if row.status == "published":
                try:
                    _verified_outcome(session, row, row.outcome)
                except (PublicationJobError, PublicationError):
                    return "unknown"
                return "published"
            if (
                row.frozen
                or row.publication_reported
                or session.scalar(
                    select(PublicationOperation.id)
                    .where(
                        PublicationOperation.job_id == row.id,
                        PublicationOperation.status.in_(("pending", "unknown"))
                        | (
                            (PublicationOperation.stage == "publish")
                            & (PublicationOperation.status != "rejected")
                        ),
                    )
                    .limit(1)
                )
                is not None
            ):
                return "unknown"
            return "not_published"


def _observation_remote_id(observation: dict | None) -> str | None:
    if not isinstance(observation, dict):
        return None
    deposit = observation.get("deposit")
    value = deposit.get("id") if isinstance(deposit, dict) else observation.get("remote_deposit_id")
    try:
        return _remote_id(value) if value is not None else None
    except PublicationJobError:
        return None


def _reported(observation: dict) -> bool:
    deposit = observation.get("deposit", observation)
    return bool(
        observation.get("publication_reported") is True
        or (
            isinstance(deposit, dict)
            and (deposit.get("submitted") is True or deposit.get("state") == "done")
        )
    )


def _positive_reconciliation(
    session: Session, row: PublicationJob, stage: str, observation: dict
) -> None:
    manifest = _manifest(_owned_manifest(session, row.owner_session_hash, row.manifest_id))
    remote_id = _observation_remote_id(observation)
    if remote_id is None:
        raise PublicationJobError("publication_reconciliation_evidence_required")
    if row.remote_deposit_id is not None and remote_id != row.remote_deposit_id:
        raise PublicationJobError("publication_remote_identity_conflict")
    if stage == "upload":
        file = observation.get("file")
        if not isinstance(file, dict) or (
            file.get("filename") != manifest.file_name
            or file.get("size") != manifest.markdown_bytes
            or observation.get("verified_sha256") != manifest.markdown_sha256
        ):
            raise PublicationJobError("publication_reconciliation_evidence_required")
    else:
        deposit = observation.get("deposit")
        if not isinstance(deposit, dict) or (
            deposit.get("destination") != manifest.destination
            or deposit.get("metadata") != canonical_remote_metadata(manifest)
        ):
            raise PublicationJobError("publication_reconciliation_evidence_required")
        if stage == "publish" and not _reported(observation):
            raise PublicationJobError("publication_reconciliation_evidence_required")


def _matching_deposit(observation: dict | None, manifest, remote_id: str) -> bool:
    if not isinstance(observation, dict):
        return False
    deposit = observation.get("deposit", observation)
    if not isinstance(deposit, dict):
        return False
    return bool(
        _observation_remote_id({"deposit": deposit}) == remote_id
        and deposit.get("destination") == manifest.destination
        and deposit.get("metadata") == canonical_remote_metadata(manifest)
    )


def _compatible(stage: str, first: dict, second: dict) -> bool:
    if first == second:
        return True
    left, right = first.get("deposit"), second.get("deposit")
    if isinstance(left, dict) and isinstance(right, dict):
        if (left.get("id"), left.get("destination")) != (right.get("id"), right.get("destination")):
            return False
        left_meta, right_meta = left.get("metadata", {}), right.get("metadata", {})
        reviewed = (
            "title",
            "description",
            "creators",
            "keywords",
            "access_right",
            "license",
            "upload_type",
            "publication_type",
            "notes",
        )
        return (
            isinstance(left_meta, dict)
            and isinstance(right_meta, dict)
            and all(left_meta.get(key) == right_meta.get(key) for key in reviewed)
        )
    left, right = first.get("file"), second.get("file")
    if stage == "upload" and isinstance(left, dict) and isinstance(right, dict):
        if (_observation_remote_id(first), left.get("filename"), left.get("size")) != (
            _observation_remote_id(second),
            right.get("filename"),
            right.get("size"),
        ):
            return False
        for a, b in (
            (left.get("checksum"), right.get("checksum")),
            (first.get("verified_sha256"), second.get("verified_sha256")),
        ):
            if a is not None and b is not None and a != b:
                return False
        return True
    return False


def _verified_outcome(session: Session, row: PublicationJob, outcome: dict) -> dict:
    manifest = _manifest(_owned_manifest(session, row.owner_session_hash, row.manifest_id))
    artifact = _artifact(session, row.owner_session_hash, uuid.UUID(manifest.artifact_id))
    if (
        outcome.get("verified") is not True
        or outcome.get("manifest_sha256") != row.manifest_sha256
        or outcome.get("markdown_sha256") != manifest.markdown_sha256
        or outcome.get("destination") != manifest.destination
        or artifact.markdown_sha256 != manifest.markdown_sha256
        or len(artifact.markdown.encode("utf-8")) != manifest.markdown_bytes
        or artifact.payload_sha256 != manifest.payload_sha256
        or artifact.basis_sha256 != manifest.basis_sha256
    ):
        raise PublicationJobError("publication_verification_required")
    remote_id = _remote_id(outcome.get("remote_deposit_id"))
    if row.remote_deposit_id is not None and row.remote_deposit_id != remote_id:
        raise PublicationJobError("publication_remote_identity_conflict")
    doi, record_url = outcome.get("doi"), outcome.get("record_url")
    if (
        not isinstance(doi, str)
        or re.fullmatch(r"10\.[0-9]{4,9}/[A-Za-z0-9][A-Za-z0-9._;()/:+\-]{0,170}", doi) is None
    ):
        raise PublicationJobError("invalid_verified_publication")
    try:
        parsed = urlsplit(record_url)
        expected_host = "sandbox.zenodo.org" if manifest.destination == "sandbox" else "zenodo.org"
        match = re.fullmatch(r"/records?/([1-9][0-9]{0,19})", parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.hostname != expected_host
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or match is None
        ):
            raise ValueError
        record_id = match.group(1)
        if outcome.get("record_id") is not None and _remote_id(outcome["record_id"]) != record_id:
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise PublicationJobError("invalid_verified_publication") from None
    return {
        "verified": True,
        "manifest_sha256": row.manifest_sha256,
        "markdown_sha256": manifest.markdown_sha256,
        "destination": manifest.destination,
        "remote_deposit_id": remote_id,
        "record_id": record_id,
        "doi": doi,
        "record_url": record_url,
    }
