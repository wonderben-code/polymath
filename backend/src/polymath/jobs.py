"""PostgreSQL job ownership and recovery. This module performs no network requests.

Only queued jobs are claimable. A lost worker is interrupted, never automatically
requeued. Any ambiguous external request must be explicitly reconciled before resume.
Every worker mutation checks a live lease and fencing token under a row lock.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from polymath.models import ResearchEvent, ResearchExternalRequest, ResearchRun

TASK_KINDS = frozenset(
    {"research", "formalise", "write_summary", "write_paper", "ingest_literature"}
)


class JobError(Exception):
    """Base class for controlled repository errors."""


class JobNotFound(JobError):
    pass


class LeaseLost(JobError):
    """The caller no longer owns this job and must stop writing or dispatching tools."""


class StateConflict(JobError):
    pass


class CheckpointConflict(JobError):
    pass


class ExternalRequestPending(JobError):
    pass


@dataclass(frozen=True)
class JobLease:
    run_id: uuid.UUID
    worker_id: str
    token: int
    expires_at: datetime


@dataclass(frozen=True)
class RunSnapshot:
    id: uuid.UUID
    mode: str
    goal: str
    status: str
    lease_token: int
    lease_owner: str | None
    lease_expires_at: datetime | None
    checkpoint_version: int
    checkpoint: dict
    evidence_refs: list
    outcome: dict | None
    external_request_in_flight: bool
    external_request_id: str | None
    interruption_reason: str | None
    task_kind: str = "research"


@dataclass(frozen=True)
class PublicEvent:
    run_id: uuid.UUID
    sequence: int
    event_key: str
    kind: str
    summary: str
    artifact_refs: list


@dataclass(frozen=True)
class ExternalRequestSnapshot:
    run_id: uuid.UUID
    request_id: str
    status: str
    observation: dict | None
    reconciliation: dict | None


def _bounded_text(value: str, maximum: int, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be a nonempty string of at most {maximum} characters")
    return value


def _integer(value: int, minimum: int, maximum: int, name: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


def _json_object(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Checkpoint/outcome must be a JSON object")
    try:
        encoded = json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        raise ValueError("Checkpoint/outcome must contain valid JSON values") from None
    if len(encoded.encode("utf-8")) > 262144:
        raise ValueError("Checkpoint/outcome exceeds the 256 KiB limit")
    return json.loads(encoded)


def _references(value: list[str] | None) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 1000:
        raise ValueError("Artifact references must be a list of at most 1000 identifiers")
    return [_bounded_text(item, 512, "Artifact reference") for item in value]


def _now(session: Session) -> datetime:
    return session.scalar(select(func.clock_timestamp()))


def _snapshot(run: ResearchRun) -> RunSnapshot:
    return RunSnapshot(
        id=run.id,
        mode=run.mode,
        goal=run.goal,
        status=run.status,
        lease_token=run.lease_token,
        lease_owner=run.lease_owner,
        lease_expires_at=run.lease_expires_at,
        checkpoint_version=run.checkpoint_version,
        checkpoint=run.checkpoint,
        evidence_refs=run.evidence_refs,
        outcome=run.outcome,
        external_request_in_flight=run.external_request_in_flight,
        external_request_id=run.external_request_id,
        interruption_reason=run.interruption_reason,
        task_kind=run.task_kind,
    )


def _public(event: ResearchEvent) -> PublicEvent:
    return PublicEvent(
        run_id=event.run_id,
        sequence=event.sequence,
        event_key=event.event_key,
        kind=event.kind,
        summary=event.summary,
        artifact_refs=event.artifact_refs,
    )


def _locked(session: Session, run_id: uuid.UUID) -> ResearchRun:
    run = session.scalar(select(ResearchRun).where(ResearchRun.id == run_id).with_for_update())
    if run is None:
        raise JobNotFound("Research run does not exist")
    return run


def _owned(session: Session, lease: JobLease) -> ResearchRun:
    _integer(lease.token, 0, 2**63 - 1, "Lease token")
    run = _locked(session, lease.run_id)
    if (
        run.status != "running"
        or run.lease_token != lease.token
        or run.lease_owner != lease.worker_id
        or run.lease_expires_at is None
        or run.lease_expires_at <= _now(session)
    ):
        raise LeaseLost("Research lease is expired, superseded, cancelled, or no longer running")
    return run


def _clear_lease(run: ResearchRun) -> None:
    run.lease_owner = None
    run.lease_expires_at = None


def _event(
    session: Session,
    run: ResearchRun,
    key: str,
    kind: str,
    summary: str,
    artifact_refs: list[str] | None = None,
) -> PublicEvent:
    key = _bounded_text(key, 256, "Event key")
    kind = _bounded_text(kind, 64, "Event kind")
    summary = _bounded_text(summary, 2000, "Event summary")
    refs = _references(artifact_refs)
    existing = session.scalar(
        select(ResearchEvent).where(ResearchEvent.run_id == run.id, ResearchEvent.event_key == key)
    )
    if existing is not None:
        if (existing.kind, existing.summary, existing.artifact_refs) != (kind, summary, refs):
            raise StateConflict("An event key cannot be reused for different content")
        return _public(existing)
    run.event_sequence += 1
    event = ResearchEvent(
        run_id=run.id,
        sequence=run.event_sequence,
        event_key=key,
        kind=kind,
        summary=summary,
        artifact_refs=refs,
    )
    session.add(event)
    session.flush()
    return _public(event)


class JobRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    def create(self, *, mode: str, goal: str, task_kind: str = "research") -> RunSnapshot:
        if mode not in {"explore", "ask", "surprise"}:
            raise ValueError("Unsupported research mode")
        if task_kind not in TASK_KINDS:
            raise ValueError("Unsupported task kind")
        if not isinstance(goal, str):
            raise ValueError("Research goal must be a string")
        if task_kind != "research" or mode != "surprise" or goal:
            _bounded_text(goal, 4000, "Research goal")
        with Session(self.engine) as session, session.begin():
            run = ResearchRun(mode=mode, goal=goal, task_kind=task_kind)
            session.add(run)
            session.flush()
            _event(session, run, "created", "queued", "Investigation queued")
            return _snapshot(run)

    def get(self, run_id: uuid.UUID) -> RunSnapshot:
        with Session(self.engine) as session:
            run = session.get(ResearchRun, run_id)
            if run is None:
                raise JobNotFound("Research run does not exist")
            return _snapshot(run)

    def claim_next(
        self,
        worker_id: str,
        *,
        lease_seconds: int = 60,
        task_kinds: tuple[str, ...] | None = None,
    ) -> JobLease | None:
        _bounded_text(worker_id, 128, "Worker ID")
        _integer(lease_seconds, 1, 3600, "Lease duration")
        if task_kinds is not None and (
            not isinstance(task_kinds, tuple)
            or not task_kinds
            or any(not isinstance(kind, str) or kind not in TASK_KINDS for kind in task_kinds)
            or len(set(task_kinds)) != len(task_kinds)
        ):
            raise ValueError(
                "Worker task kinds must be a nonempty tuple of distinct supported kinds"
            )
        with Session(self.engine) as session, session.begin():
            eligible = select(ResearchRun).where(ResearchRun.status == "queued")
            if task_kinds is not None:
                eligible = eligible.where(ResearchRun.task_kind.in_(task_kinds))
            run = session.scalar(
                eligible.order_by(ResearchRun.created_at, ResearchRun.id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if run is None:
                return None
            if run.external_request_in_flight:
                raise StateConflict("A queued run cannot have an ambiguous external request")
            now = _now(session)
            run.status = "running"
            run.lease_token += 1
            run.lease_owner = worker_id
            run.lease_expires_at = now + timedelta(seconds=lease_seconds)
            run.heartbeat_at = now
            _event(session, run, f"claimed:{run.lease_token}", "running", "Investigation started")
            return JobLease(run.id, worker_id, run.lease_token, run.lease_expires_at)

    def heartbeat(self, lease: JobLease, *, lease_seconds: int = 60) -> JobLease:
        _integer(lease_seconds, 1, 3600, "Lease duration")
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            now = _now(session)
            run.heartbeat_at = now
            run.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return JobLease(run.id, lease.worker_id, run.lease_token, run.lease_expires_at)

    def save_checkpoint(
        self,
        lease: JobLease,
        *,
        expected_version: int,
        checkpoint: dict,
        evidence_refs: list[str] | None = None,
    ) -> int:
        _integer(expected_version, 0, 2**31 - 1, "Checkpoint version")
        data = _json_object(checkpoint)
        refs = _references(evidence_refs) if evidence_refs is not None else None
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            if run.checkpoint_version != expected_version:
                raise CheckpointConflict("Checkpoint version has changed")
            run.checkpoint = data
            run.checkpoint_version += 1
            if refs is not None:
                run.evidence_refs = refs
            _event(
                session,
                run,
                f"checkpoint:{run.checkpoint_version}",
                "checkpoint",
                "Research progress saved",
                run.evidence_refs,
            )
            return run.checkpoint_version

    def append_event(
        self,
        lease: JobLease,
        *,
        event_key: str,
        kind: str,
        summary: str,
        artifact_refs: list[str] | None = None,
    ) -> PublicEvent:
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            return _event(session, run, event_key, kind, summary, artifact_refs)

    def events_after(
        self, run_id: uuid.UUID, *, sequence: int = 0, limit: int = 100
    ) -> list[PublicEvent]:
        _integer(sequence, 0, 2**63 - 1, "Event cursor")
        _integer(limit, 1, 1000, "Event limit")
        with Session(self.engine) as session:
            events = session.scalars(
                select(ResearchEvent)
                .where(ResearchEvent.run_id == run_id, ResearchEvent.sequence > sequence)
                .order_by(ResearchEvent.sequence)
                .limit(limit)
            )
            return [_public(event) for event in events]

    def begin_external_request(self, lease: JobLease, *, request_id: str) -> None:
        _bounded_text(request_id, 128, "External request ID")
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            if run.external_request_in_flight:
                raise ExternalRequestPending("An external request requires an observed outcome")
            if session.get(ResearchExternalRequest, (run.id, request_id)) is not None:
                raise StateConflict("An external request ID cannot be dispatched twice")
            session.add(
                ResearchExternalRequest(run_id=run.id, request_id=request_id, status="prepared")
            )
            run.external_request_id = request_id
            run.external_request_in_flight = True
            _event(
                session,
                run,
                f"external_started:{request_id}",
                "external_started",
                "External operation recorded before dispatch; its outcome remains pending",
            )

    def complete_external_request(
        self,
        lease: JobLease,
        *,
        request_id: str,
        observation: dict,
        evidence_refs: list[str] | None = None,
    ) -> None:
        data = _json_object(observation)
        refs = _references(evidence_refs) if evidence_refs is not None else None
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            if not run.external_request_in_flight or run.external_request_id != request_id:
                raise StateConflict("No matching external request is pending")
            request = session.get(ResearchExternalRequest, (run.id, request_id))
            if request is None or request.status != "prepared":
                raise StateConflict("No prepared external request was recorded")
            if request.reservation_id is not None:
                raise StateConflict("Budget-bound requests require coordinated completion")
            run.external_request_in_flight = False
            request.observation = data
            request.status = "observed"
            if refs is not None:
                run.evidence_refs = refs
            _event(
                session,
                run,
                f"external_observed:{request_id}",
                "external_observed",
                "The external operation returned an observed outcome",
            )

    def get_external_request(
        self,
        run_id: uuid.UUID,
        *,
        request_id: str,
    ) -> ExternalRequestSnapshot:
        _bounded_text(request_id, 128, "External request ID")
        with Session(self.engine) as session:
            request = session.get(ResearchExternalRequest, (run_id, request_id))
            if request is None:
                raise JobNotFound("External request does not exist")
            return ExternalRequestSnapshot(
                request.run_id,
                request.request_id,
                request.status,
                request.observation,
                request.reconciliation,
            )

    def finish(
        self,
        lease: JobLease,
        *,
        status: Literal["succeeded", "failed"],
        outcome: dict,
        evidence_refs: list[str] | None = None,
    ) -> RunSnapshot:
        if status not in {"succeeded", "failed"}:
            raise ValueError("A completed run must succeed or fail")
        data, refs = _json_object(outcome), _references(evidence_refs)
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            if run.external_request_in_flight:
                raise ExternalRequestPending("Cannot finish with an ambiguous external request")
            run.status = status
            run.outcome = data
            if evidence_refs is not None:
                run.evidence_refs = refs
            run.finished_at = _now(session)
            _clear_lease(run)
            _event(
                session,
                run,
                f"finished:{run.lease_token}",
                status,
                "Investigation completed" if status == "succeeded" else "Investigation failed",
                run.evidence_refs,
            )
            return _snapshot(run)

    def request_cancel(self, run_id: uuid.UUID) -> RunSnapshot:
        with Session(self.engine) as session, session.begin():
            run = _locked(session, run_id)
            if run.status in {"succeeded", "failed", "cancelled", "cancel_requested"}:
                return _snapshot(run)
            run.lease_token += 1  # Invalidate an in-flight worker before any further writes.
            run.status = "cancel_requested" if run.status == "running" else "cancelled"
            _clear_lease(run)
            if run.status == "cancelled":
                run.finished_at = _now(session)
            _event(
                session,
                run,
                f"cancel:{run.lease_token}",
                run.status,
                "Cancellation requested"
                if run.status == "cancel_requested"
                else "Investigation cancelled",
            )
            return _snapshot(run)

    def finalize_cancellation(self, run_id: uuid.UUID) -> RunSnapshot:
        with Session(self.engine) as session, session.begin():
            run = _locked(session, run_id)
            if run.status == "cancelled":
                return _snapshot(run)
            if run.status != "cancel_requested":
                raise StateConflict("Only a requested cancellation can be finalized")
            # An already sent call may still incur cost. Retain its ambiguity marker.
            run.status = "cancelled"
            run.finished_at = _now(session)
            _event(
                session,
                run,
                f"cancelled:{run.lease_token}",
                "cancelled",
                "Investigation cancelled; no further research will be dispatched",
            )
            return _snapshot(run)

    def interrupt_expired(self, *, limit: int = 100) -> list[uuid.UUID]:
        _integer(limit, 1, 1000, "Recovery batch limit")
        with Session(self.engine) as session, session.begin():
            runs = session.scalars(
                select(ResearchRun)
                .where(
                    ResearchRun.status == "running",
                    ResearchRun.lease_expires_at <= func.clock_timestamp(),
                )
                .order_by(ResearchRun.lease_expires_at)
                .with_for_update(skip_locked=True)
                .limit(limit)
            ).all()
            interrupted = []
            for run in runs:
                run.status = "interrupted"
                run.lease_token += 1
                run.interruption_reason = (
                    "external_outcome_unknown"
                    if run.external_request_in_flight
                    else "worker_lease_expired"
                )
                _clear_lease(run)
                _event(
                    session,
                    run,
                    f"interrupted:{run.lease_token}",
                    "interrupted",
                    "Worker ownership expired; investigation awaits explicit recovery",
                )
                interrupted.append(run.id)
            return interrupted

    def interrupt(self, lease: JobLease) -> RunSnapshot:
        """Cooperative shutdown retains state and requires the same explicit resume policy."""
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            run.status = "interrupted"
            run.lease_token += 1
            run.interruption_reason = (
                "external_outcome_unknown" if run.external_request_in_flight else "worker_shutdown"
            )
            _clear_lease(run)
            _event(
                session,
                run,
                f"interrupted:{run.lease_token}",
                "interrupted",
                "Worker stopped; investigation awaits explicit recovery",
            )
            return _snapshot(run)

    def reconcile_external(
        self,
        run_id: uuid.UUID,
        *,
        expected_token: int,
        request_id: str,
        outcome: Literal["succeeded", "failed", "not_sent"],
        summary: str,
    ) -> RunSnapshot:
        _integer(expected_token, 0, 2**63 - 1, "Lease token")
        if outcome not in {"succeeded", "failed", "not_sent"}:
            raise ValueError("External reconciliation requires a known outcome")
        _bounded_text(summary, 2000, "Reconciliation summary")
        with Session(self.engine) as session, session.begin():
            run = _locked(session, run_id)
            if run.lease_token != expected_token or run.status not in {"interrupted", "cancelled"}:
                raise StateConflict("Reconciliation requires the current interrupted/cancelled run")
            if not run.external_request_in_flight or run.external_request_id != request_id:
                raise StateConflict("No matching external request needs reconciliation")
            request = session.get(ResearchExternalRequest, (run.id, request_id))
            if request is None or request.status != "prepared":
                raise StateConflict("No prepared external request was recorded")
            if request.reservation_id is not None:
                raise StateConflict("Budget-bound requests require coordinated reconciliation")
            run.external_request_in_flight = False
            request.reconciliation = {"outcome": outcome, "summary": summary}
            request.status = "reconciled"
            _event(
                session,
                run,
                f"external_reconciled:{request_id}",
                "external_reconciled",
                "An ambiguous external operation was reconciled",
            )
            return _snapshot(run)

    def resume_interrupted(self, run_id: uuid.UUID, *, expected_token: int) -> RunSnapshot:
        _integer(expected_token, 0, 2**63 - 1, "Lease token")
        with Session(self.engine) as session, session.begin():
            run = _locked(session, run_id)
            if run.status != "interrupted" or run.lease_token != expected_token:
                raise StateConflict("Only the current interrupted run can be explicitly resumed")
            if run.external_request_in_flight:
                raise ExternalRequestPending(
                    "Reconcile the ambiguous external request before resume"
                )
            run.status = "queued"
            run.lease_token += 1
            run.interruption_reason = None
            _event(
                session,
                run,
                f"resumed:{run.lease_token}",
                "queued",
                "Investigation explicitly queued from its retained checkpoint",
            )
            return _snapshot(run)
