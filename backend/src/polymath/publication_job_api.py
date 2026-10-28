"""Owned explicit publication jobs, with a disabled-by-default execution gate.

Routes only persist queue/control decisions. Remote work belongs to the fenced
worker; neither login, manifest preparation nor approval implicitly publishes.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.operator_auth import OperatorPolicy, require_operator
from polymath.publication import PublicationError, PublicationStore
from polymath.publication_api import (
    _private,
    _publication_error,
    _PublicationHTTPException,
    _PublicationRoute,
)
from polymath.publication_contracts import PublicationPolicy
from polymath.publication_jobs import PublicationJobError, PublicationJobRepository
from polymath.run_api import _authenticated, _authorize_write

JOB_STATUSES = {"queued", "running", "interrupted", "published", "failed", "cancelled"}
JOB_STAGES = {"queued", "create", "upload", "verify", "publish", "reconcile", "complete"}
TERMINAL_STATES = {"interrupted", "published", "failed", "cancelled"}
MAX_CURSOR = 2**63 - 1
DOI = re.compile(r"10\.[0-9]{4,9}/[A-Za-z0-9][A-Za-z0-9._;()/:+\-]{0,180}")


class ResumePublicationJob(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    approval_id: str = Field(min_length=36, max_length=36)
    expected_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def exact_receipt(self) -> ResumePublicationJob:
        if str(uuid.UUID(self.approval_id)) != self.approval_id:
            raise ValueError("An exact approval receipt is required")
        return self


class CreatePublicationJob(ResumePublicationJob):
    manifest_id: str = Field(min_length=36, max_length=36)

    @model_validator(mode="after")
    def exact_manifest(self) -> CreatePublicationJob:
        if str(uuid.UUID(self.manifest_id)) != self.manifest_id:
            raise ValueError("An exact publication manifest is required")
        return self


def _storage_error() -> _PublicationHTTPException:
    return _PublicationHTTPException(503, "publication_job_storage_unavailable")


def _job_error(error: PublicationJobError) -> _PublicationHTTPException:
    code = str(error)
    if code in ("publication_job_not_found", "publication_not_found"):
        return _PublicationHTTPException(404, "publication_job_not_found")
    if code in ("operator_session_required", "research_session_required"):
        return _PublicationHTTPException(401, code)
    if code in (
        "publication_job_queue_full",
        "publication_queue_full",
        "publication_job_limit",
        "publication_owner_limit",
    ):
        return _PublicationHTTPException(429, code)
    if code in (
        "publication_job_conflict",
        "publication_job_state_conflict",
        "publication_operation_pending",
        "publication_approval_required",
        "publication_approval_invalid",
        "publication_job_frozen",
        "publication_reconciliation_required",
        "publication_manifest_changed",
        "publication_job_not_resumable",
        "publication_operation_unresolved",
        "publication_operation_rejected",
        "publication_request_changed",
        "publication_read_only_job",
        "publication_reconciliation_conflict",
    ):
        return _PublicationHTTPException(409, code)
    return _storage_error()


def create_publication_job_router(
    engine: Engine,
    *,
    policy: PublicationPolicy | None = None,
    operator_policy: OperatorPolicy | None = None,
    availability: Callable[[], bool] | None = None,
) -> APIRouter:
    if engine.dialect.name != "postgresql":
        raise ValueError("Publication jobs require PostgreSQL")
    store = PublicationStore(engine, policy, operator_policy=operator_policy)
    jobs = PublicationJobRepository(engine, store)
    router = APIRouter(
        prefix="/api/v1/publication-jobs",
        route_class=_PublicationRoute,
        dependencies=[Depends(_private)],
    )

    def capability() -> dict:
        reason = None
        if policy is None or not policy.configured:
            reason = "publication_unavailable"
        elif operator_policy is None or not operator_policy.configured:
            reason = "operator_unavailable"
        else:
            try:
                enabled = availability is not None and availability() is True
            except Exception:
                enabled = False
            if not enabled:
                reason = "publication_execution_disabled"
        return {"execution_available": reason is None, "reason": reason}

    def require_execution() -> None:
        current = capability()
        if not current["execution_available"]:
            raise _PublicationHTTPException(503, current["reason"])

    def read_scope(request: Request) -> tuple[str, bool]:
        with Session(engine) as session:
            owner_hash = _authenticated(session, request).token_hash
            try:
                require_operator(session, request, write=False, policy=operator_policy)
                operator_active = True
            except HTTPException:
                operator_active = False
            return owner_hash, operator_active

    def write_scope(request: Request, *, remote: bool = True):
        _authorize_write(request)
        with Session(engine) as session:
            owner_hash = _authenticated(session, request).token_hash
            operator = require_operator(session, request, write=True, policy=operator_policy)
        # Do not retain the authentication row lock across the repository's
        # independent transaction; it repeats the active session checks itself.
        if remote:
            require_execution()
        return owner_hash, operator

    def public_job(snapshot, owner_hash: str, operator_active: bool) -> dict:
        data = snapshot.to_response()
        if data["status"] not in JOB_STATUSES or data["stage"] not in JOB_STAGES:
            raise _storage_error()
        record = store.get(owner_hash, uuid.UUID(data["manifest_id"]))
        manifest = record.manifest
        destination = manifest["destination"]
        raw = getattr(snapshot, "outcome", None)
        outcome = raw if isinstance(raw, dict) else {}
        deposit_id = outcome.get("remote_deposit_id")
        deposit_text = (
            str(deposit_id)
            if type(deposit_id) is int and deposit_id > 0
            else deposit_id
            if isinstance(deposit_id, str) and re.fullmatch(r"[1-9][0-9]{0,19}", deposit_id)
            else None
        )
        host = "sandbox.zenodo.org" if destination == "sandbox" else "zenodo.org"
        doi = outcome.get("doi")
        record_url = outcome.get("record_url")
        record_id = outcome.get("record_id")
        record_text = (
            str(record_id)
            if type(record_id) is int and record_id > 0
            else record_id
            if isinstance(record_id, str) and re.fullmatch(r"[1-9][0-9]{0,19}", record_id)
            else None
        )
        safe_record_url = False
        if isinstance(record_url, str) and len(record_url) <= 2048:
            try:
                parsed = urlsplit(record_url)
                path = re.fullmatch(r"/(?:record|records)/([1-9][0-9]{0,19})/?", parsed.path)
                safe_record_url = bool(
                    parsed.scheme == "https"
                    and parsed.netloc == host
                    and not parsed.query
                    and not parsed.fragment
                    and path
                    and (record_id is None or path.group(1) == record_text)
                )
            except ValueError:
                pass
        verified = bool(
            outcome.get("verified") is True
            and outcome.get("manifest_sha256") == record.manifest_sha256 == data["manifest_sha256"]
            and outcome.get("markdown_sha256") == manifest["markdown_sha256"]
            and outcome.get("destination") == destination
            and deposit_text is not None
            and deposit_text == str(getattr(snapshot, "remote_deposit_id", None))
            and isinstance(doi, str)
            and DOI.fullmatch(doi)
            and safe_record_url
        )
        frozen = data.get("frozen") is True
        reported = getattr(snapshot, "publication_reported", False) is True
        unknown = data.get("external_outcome_unknown") is True or (reported and not verified)
        execution = capability()["execution_available"]
        status = data["status"]
        receipt_valid = record.approval is not None and record.approval.valid
        reason = data.get("reason")
        if reason is not None and (
            not isinstance(reason, str) or re.fullmatch(r"[a-z_]{1,100}", reason) is None
        ):
            reason = "publication_job_needs_review"
        return {
            "id": data["id"],
            "manifest_id": data["manifest_id"],
            "approval_id": data["approval_id"],
            "manifest_sha256": data["manifest_sha256"],
            "status": status,
            "work_kind": data["work_kind"],
            "stage": data["stage"],
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
            "destination": destination,
            "sandbox": destination == "sandbox",
            "reason": reason,
            "event_sequence": data["event_sequence"],
            "frozen": frozen,
            "publication_reported": reported,
            "external_outcome_unknown": unknown,
            "verified": verified,
            "doi": doi if verified else None,
            "record_url": record_url if verified else None,
            "can_cancel": operator_active and snapshot.can_cancel,
            "can_reconcile": bool(operator_active and execution and snapshot.can_reconcile),
            "can_resume": bool(
                operator_active
                and execution
                and snapshot.can_resume
                and not frozen
                and not unknown
                and not reported
                and receipt_valid
            ),
        }

    @router.get("/capabilities")
    def capabilities() -> dict:
        return capability()

    @router.post("", status_code=201)
    def enqueue(body: CreatePublicationJob, request: Request) -> dict:
        try:
            owner_hash, operator = write_scope(request)
            snapshot = jobs.enqueue(
                owner_hash,
                uuid.UUID(body.manifest_id),
                uuid.UUID(body.approval_id),
                body.expected_manifest_sha256,
                operator,
            )
            return public_job(snapshot, owner_hash, True)
        except PublicationJobError as error:
            raise _job_error(error) from None
        except PublicationError as error:
            raise _publication_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("")
    def list_jobs(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        before: uuid.UUID | None = None,
        manifest_id: uuid.UUID | None = None,
    ) -> dict:
        try:
            owner_hash, operator_active = read_scope(request)
            records = jobs.list(owner_hash, limit=limit + 1, before=before, manifest_id=manifest_id)
            return {
                "jobs": [
                    public_job(record, owner_hash, operator_active) for record in records[:limit]
                ],
                "next_cursor": str(records[limit - 1].id) if len(records) > limit else None,
            }
        except PublicationJobError as error:
            raise _job_error(error) from None
        except PublicationError as error:
            raise _publication_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/{job_id}")
    def get_job(job_id: uuid.UUID, request: Request) -> dict:
        try:
            owner_hash, operator_active = read_scope(request)
            return public_job(jobs.get(owner_hash, job_id), owner_hash, operator_active)
        except PublicationJobError as error:
            raise _job_error(error) from None
        except PublicationError as error:
            raise _publication_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/{job_id}/events")
    def events(
        job_id: uuid.UUID,
        request: Request,
        after: Annotated[int, Query(ge=0, le=MAX_CURSOR)] = 0,
    ) -> dict:
        try:
            owner_hash, _ = read_scope(request)
            snapshot = jobs.get(owner_hash, job_id)
            records = jobs.events_after(owner_hash, job_id, after=after, limit=100)
            return {
                "events": [record.to_response() for record in records],
                "next_cursor": records[-1].sequence if records else after,
                "terminal": snapshot.status in TERMINAL_STATES,
            }
        except PublicationJobError as error:
            raise _job_error(error) from None
        except PublicationError as error:
            raise _publication_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.post("/{job_id}/cancel")
    def cancel(job_id: uuid.UUID, request: Request) -> dict:
        try:
            owner_hash, _ = write_scope(request, remote=False)
            return public_job(jobs.cancel(owner_hash, job_id), owner_hash, True)
        except PublicationJobError as error:
            raise _job_error(error) from None
        except PublicationError as error:
            raise _publication_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.post("/{job_id}/reconcile")
    def reconcile(job_id: uuid.UUID, request: Request) -> dict:
        try:
            owner_hash, operator = write_scope(request)
            snapshot = jobs.enqueue_reconciliation(owner_hash, job_id, operator)
            return public_job(snapshot, owner_hash, True)
        except PublicationJobError as error:
            raise _job_error(error) from None
        except PublicationError as error:
            raise _publication_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.post("/{job_id}/resume")
    def resume(job_id: uuid.UUID, body: ResumePublicationJob, request: Request) -> dict:
        try:
            owner_hash, operator = write_scope(request)
            current = jobs.get(owner_hash, job_id)
            if current.manifest_sha256 != body.expected_manifest_sha256:
                raise _PublicationHTTPException(409, "publication_manifest_changed")
            snapshot = jobs.resume(
                owner_hash, job_id, operator, new_approval_id=uuid.UUID(body.approval_id)
            )
            return public_job(snapshot, owner_hash, True)
        except PublicationJobError as error:
            raise _job_error(error) from None
        except PublicationError as error:
            raise _publication_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    return router
