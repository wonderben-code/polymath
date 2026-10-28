"""Owned, explicitly requested reassessment; no HTTP handler runs a model.

Previously saved source versions are candidates, not revised conclusions. The
same research availability and queue limits apply before creating a paid-capable
job. The default router cannot enqueue one, and collection never calls it.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Engine, func, select, text, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.access_models import ResearchRunAccess
from polymath.assessment_models import AssessmentInputSource, AssessmentRequest, FindingAssessment
from polymath.assessments import (
    AssessmentError,
    AssessmentStore,
    assessment_source,
    bind_request,
)
from polymath.evidence import EvidenceError, EvidenceStore
from polymath.evidence_models import EvidenceResult
from polymath.jobs import _event
from polymath.models import ResearchRun
from polymath.run_api import (
    ACTIVE_STATES,
    QUEUE_LOCK_KEY,
    _authenticated,
    _authorize_write,
    _PrivateHTTPException,
    _summary,
)


class _AssessmentHTTPException(_PrivateHTTPException):
    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code, detail)
        self.headers["X-Content-Type-Options"] = "nosniff"


class _AssessmentRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                raise _AssessmentHTTPException(422, "invalid_assessment_request") from None
            except HTTPException as error:
                error.headers = {
                    **(error.headers or {}),
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                }
                raise

        return handle


class CreateAssessmentJob(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    source_result_id: str = Field(min_length=36, max_length=36)
    watch_id: str = Field(min_length=36, max_length=36)
    new_source_version_ids: list[str] = Field(min_length=1, max_length=5)
    expected_head_revision: int = Field(ge=0, le=2**31 - 1)

    @model_validator(mode="after")
    def exact_owned_versions(self) -> CreateAssessmentJob:
        for value in (self.source_result_id, self.watch_id, *self.new_source_version_ids):
            if str(uuid.UUID(value)) != value:
                raise ValueError("Exact saved versions are required")
        if len(set(self.new_source_version_ids)) != len(self.new_source_version_ids):
            raise ValueError("New source versions must be distinct")
        return self


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"


def _missing() -> _AssessmentHTTPException:
    return _AssessmentHTTPException(404, "assessment_not_found")


def _storage_error() -> _AssessmentHTTPException:
    return _AssessmentHTTPException(503, "assessment_storage_unavailable")


def _assessment_error(error: AssessmentError) -> _AssessmentHTTPException:
    code = str(error)
    if code.endswith("_not_found") or code == "finding_not_available":
        return _missing()
    if code in (
        "assessment_head_changed",
        "assessment_source_changed",
        "assessment_basis_changed",
        "assessment_input_changed",
        "assessment_source_not_current",
    ):
        return _AssessmentHTTPException(409, code)
    if code in ("active_run_limit", "research_queue_full", "assessment_queue_full"):
        return _AssessmentHTTPException(429, code)
    if code in (
        "invalid_assessment_request",
        "assessment_source_already_assessed",
        "assessment_source_already_cited",
        "assessment_invalid_sources",
        "assessment_input_too_large",
    ):
        return _AssessmentHTTPException(422, code)
    return _storage_error()


def _job(session: Session, owner_hash: str, run_id: uuid.UUID) -> ResearchRun:
    run = session.scalar(
        select(ResearchRun)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == ResearchRun.id)
        .where(
            ResearchRun.id == run_id,
            ResearchRunAccess.session_hash == owner_hash,
            ResearchRun.task_kind == "reassess_finding",
        )
    )
    if run is None:
        raise _missing()
    return run


def _job_summary(session: Session, run: ResearchRun) -> dict:
    request = session.get(AssessmentRequest, run.id)
    if request is None:
        raise _missing()
    assessment_id = None
    if isinstance(run.outcome, dict):
        try:
            assessment_id = str(uuid.UUID(run.outcome["assessment_id"]))
        except (ValueError, TypeError, AttributeError, KeyError):
            pass
    versions = session.scalars(
        select(AssessmentInputSource.version_id)
        .where(AssessmentInputSource.run_id == run.id, AssessmentInputSource.role == "new")
        .order_by(AssessmentInputSource.ordinal)
    ).all()
    return {
        **_summary(run),
        "assessment_id": assessment_id,
        "root_result_id": str(request.root_result_id),
        "source_result_id": str(request.source_result_id),
        "source_sha256": request.source_sha256,
        "watch_id": str(request.watch_id),
        "new_source_version_ids": [str(version) for version in versions],
        "expected_head_revision": request.expected_head_revision,
    }


def create_assessment_router(
    engine: Engine, availability: Callable[[], bool] | None = None
) -> APIRouter:
    if engine.dialect.name != "postgresql":
        raise ValueError("Finding reassessment requires PostgreSQL")
    policy = availability or (lambda: False)
    store = AssessmentStore(engine)
    router = APIRouter(
        prefix="/api/v1/assessments",
        route_class=_AssessmentRoute,
        dependencies=[Depends(_private)],
    )

    def head_summary(owner_hash: str, result_id: uuid.UUID) -> dict:
        state = store.head(owner_hash, result_id)
        with Session(engine) as session:
            current = session.scalar(
                select(EvidenceResult)
                .join(ResearchRunAccess, ResearchRunAccess.run_id == EvidenceResult.run_id)
                .where(
                    EvidenceResult.id == uuid.UUID(state["current_result_id"]),
                    ResearchRunAccess.session_hash == owner_hash,
                )
            )
            if current is None:
                raise _missing()
        return {
            **state,
            "requested_result_id": str(result_id),
            "current_run_id": str(current.run_id),
            "can_generate": state["is_current"] and state["is_supported"],
        }

    def available() -> bool:
        try:
            return policy() is True
        except Exception:
            return False

    @router.get("/capabilities")
    def capabilities() -> dict:
        enabled = available()
        return {
            "execution_available": enabled,
            "reason": None if enabled else "research_unavailable",
            "automatic_reassessment": False,
        }

    @router.post("/jobs", status_code=201)
    def enqueue(body: CreateAssessmentJob, request: Request) -> dict:
        _authorize_write(request)
        try:
            with Session(engine) as session, session.begin():
                owner = _authenticated(session, request)
                if not available():
                    raise _AssessmentHTTPException(503, "research_unavailable")
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUEUE_LOCK_KEY})
                session.refresh(owner)
                _authenticated(session, request)
                own_active = session.scalar(
                    select(func.count())
                    .select_from(ResearchRun)
                    .join(ResearchRunAccess, ResearchRunAccess.run_id == ResearchRun.id)
                    .where(
                        ResearchRunAccess.session_hash == owner.token_hash,
                        ResearchRun.status.in_(ACTIVE_STATES),
                    )
                )
                total_active = session.scalar(
                    select(func.count())
                    .select_from(ResearchRun)
                    .where(ResearchRun.status.in_(ACTIVE_STATES))
                )
                if own_active >= 1:
                    raise _AssessmentHTTPException(429, "active_run_limit")
                if total_active >= 100:
                    raise _AssessmentHTTPException(429, "research_queue_full")
                if not available():
                    raise _AssessmentHTTPException(503, "research_unavailable")
                source = assessment_source(
                    session, owner.token_hash, uuid.UUID(body.source_result_id)
                )
                source_run = session.get(ResearchRun, source.run_id)
                run = ResearchRun(
                    mode=source_run.mode,
                    task_kind="reassess_finding",
                    goal=source_run.goal,
                )
                session.add(run)
                session.flush()
                session.add(ResearchRunAccess(run_id=run.id, session_hash=owner.token_hash))
                session.flush()
                bind_request(
                    session,
                    run,
                    owner.token_hash,
                    uuid.UUID(body.source_result_id),
                    uuid.UUID(body.watch_id),
                    tuple(uuid.UUID(value) for value in body.new_source_version_ids),
                    body.expected_head_revision,
                )
                _event(
                    session,
                    run,
                    "created",
                    "queued",
                    "Explicit reassessment queued against exact saved evidence.",
                )
                return _job_summary(session, run)
        except AssessmentError as error:
            raise _assessment_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/jobs")
    def list_jobs(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        before: uuid.UUID | None = None,
        watch_id: uuid.UUID | None = None,
        source_result_id: uuid.UUID | None = None,
        root_result_id: uuid.UUID | None = None,
    ) -> dict:
        try:
            with Session(engine) as session:
                owner_hash = _authenticated(session, request).token_hash
                query = (
                    select(ResearchRun)
                    .join(ResearchRunAccess, ResearchRunAccess.run_id == ResearchRun.id)
                    .join(AssessmentRequest, AssessmentRequest.run_id == ResearchRun.id)
                    .where(
                        ResearchRunAccess.session_hash == owner_hash,
                        ResearchRun.task_kind == "reassess_finding",
                    )
                )
                for column, value in (
                    (AssessmentRequest.watch_id, watch_id),
                    (AssessmentRequest.source_result_id, source_result_id),
                    (AssessmentRequest.root_result_id, root_result_id),
                ):
                    if value is not None:
                        query = query.where(column == value)
                if before is not None:
                    anchor = _job(session, owner_hash, before)
                    query = query.where(
                        tuple_(ResearchRun.created_at, ResearchRun.id)
                        < tuple_(anchor.created_at, anchor.id)
                    )
                records = session.scalars(
                    query.order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc()).limit(
                        limit + 1
                    )
                ).all()
                return {
                    "jobs": [_job_summary(session, run) for run in records[:limit]],
                    "next_cursor": str(records[limit - 1].id) if len(records) > limit else None,
                }
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/jobs/{run_id}")
    def get_job(run_id: uuid.UUID, request: Request) -> dict:
        try:
            with Session(engine) as session:
                return _job_summary(
                    session, _job(session, _authenticated(session, request).token_hash, run_id)
                )
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/heads/{result_id}")
    def head(result_id: uuid.UUID, request: Request) -> dict:
        try:
            with Session(engine) as session:
                owner_hash = _authenticated(session, request).token_hash
            return head_summary(owner_hash, result_id)
        except AssessmentError as error:
            raise _assessment_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/findings/{result_id}")
    def get_finding(result_id: uuid.UUID, request: Request) -> dict:
        try:
            with Session(engine) as session:
                owner_hash = _authenticated(session, request).token_hash
                row = session.execute(
                    select(EvidenceResult, ResearchRun)
                    .join(ResearchRun, ResearchRun.id == EvidenceResult.run_id)
                    .join(ResearchRunAccess, ResearchRunAccess.run_id == EvidenceResult.run_id)
                    .where(
                        EvidenceResult.id == result_id,
                        EvidenceResult.status.in_(("bridge", "answer")),
                        ResearchRun.status == "succeeded",
                        ResearchRunAccess.session_hash == owner_hash,
                    )
                ).first()
                if row is None:
                    raise _missing()
                result, run = row
                if run.task_kind != "research":
                    applied = session.scalar(
                        select(FindingAssessment.id).where(
                            FindingAssessment.run_id == run.id,
                            FindingAssessment.candidate_result_id == result_id,
                            FindingAssessment.applied.is_(True),
                        )
                    )
                    if run.task_kind != "reassess_finding" or applied is None:
                        raise _missing()
                run_id, public_run = run.id, _summary(run)
            saved = EvidenceStore(engine).get_result(run_id, result_id)
            return {
                "id": str(result_id),
                "run_id": str(run_id),
                "task_kind": public_run["task_kind"],
                "run": public_run,
                "payload": saved.payload,
                "assessment": head_summary(owner_hash, result_id),
            }
        except AssessmentError as error:
            raise _assessment_error(error) from None
        except EvidenceError:
            raise _storage_error() from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/heads/{result_id}/history")
    def history(
        result_id: uuid.UUID,
        request: Request,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        before: uuid.UUID | None = None,
    ) -> dict:
        try:
            with Session(engine) as session:
                owner_hash = _authenticated(session, request).token_hash
            records = store.history(owner_hash, result_id, limit=limit + 1, before=before)
            return {
                "assessments": [record.to_response() for record in records[:limit]],
                "next_cursor": str(records[limit - 1].id) if len(records) > limit else None,
            }
        except AssessmentError as error:
            raise _assessment_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/{assessment_id}")
    def get_assessment(assessment_id: uuid.UUID, request: Request) -> dict:
        try:
            with Session(engine) as session:
                owner_hash = _authenticated(session, request).token_hash
            return store.get(owner_hash, assessment_id).to_response()
        except AssessmentError as error:
            raise _assessment_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    return router
