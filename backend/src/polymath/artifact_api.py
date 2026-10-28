"""Owned, bounded document jobs and exact immutable exports.

HTTP requests enqueue work only. They never construct a model or authorize paid
execution. The shared queue lock covers research and artifact jobs together.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Engine, func, select, text, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.access_models import ResearchRunAccess
from polymath.artifact_models import ArtifactRequest, GeneratedArtifact
from polymath.artifacts import ArtifactError, ArtifactStore, bind_request, source_for_request
from polymath.jobs import _event
from polymath.models import ResearchRun
from polymath.run_api import (
    ACTIVE_STATES,
    QUEUE_LOCK_KEY,
    _authenticated,
    _authorize_write,
    _owned,
    _private_response,
    _PrivateHTTPException,
    _PrivateRoute,
    _summary,
)

ARTIFACT_KINDS = ("formalise", "write_summary", "write_paper")
ARTIFACT_GOALS = {
    "formalise": "Formalise saved finding",
    "write_summary": "Write plain summary",
    "write_paper": "Write formal paper",
}


class CreateArtifactJob(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    kind: Literal["formalise", "write_summary", "write_paper"]
    source_result_id: str = Field(min_length=36, max_length=36)
    formalisation_id: str | None = Field(default=None, min_length=36, max_length=36)

    @model_validator(mode="after")
    def canonical_identifiers(self) -> CreateArtifactJob:
        for value in (self.source_result_id, self.formalisation_id):
            if value is not None and str(uuid.UUID(value)) != value:
                raise ValueError("An exact saved version is required")
        if self.kind == "formalise" and self.formalisation_id is not None:
            raise ValueError("A formalisation cannot use another formalisation as its basis")
        return self


def _missing() -> _PrivateHTTPException:
    return _PrivateHTTPException(404, "artifact_not_found")


def _storage_error() -> _PrivateHTTPException:
    return _PrivateHTTPException(503, "artifact_storage_unavailable")


def _artifact_job(session: Session, request: Request, run_id: uuid.UUID) -> ResearchRun:
    run = _owned(session, _authenticated(session, request), run_id)
    if run.task_kind not in ARTIFACT_KINDS:
        raise _missing()
    return run


def _artifact_run(session: Session, request: Request, artifact_id: uuid.UUID) -> uuid.UUID:
    owner = _authenticated(session, request)
    run_id = session.scalar(
        select(GeneratedArtifact.run_id)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == GeneratedArtifact.run_id)
        .where(
            GeneratedArtifact.id == artifact_id,
            ResearchRunAccess.session_hash == owner.token_hash,
        )
    )
    if run_id is None:
        raise _missing()
    return run_id


def create_artifact_router(
    engine: Engine, availability: Callable[[], bool] | None = None
) -> APIRouter:
    if engine.dialect.name != "postgresql":
        raise ValueError("Artifact routes require PostgreSQL")
    policy = availability or (lambda: False)
    store = ArtifactStore(engine)
    router = APIRouter(
        prefix="/api/v1/artifacts",
        route_class=_PrivateRoute,
        dependencies=[Depends(_private_response)],
    )

    def available() -> bool:
        try:
            return policy() is True
        except Exception:
            raise _PrivateHTTPException(503, "research_unavailable") from None

    @router.post("/jobs", status_code=201)
    def create_job(body: CreateArtifactJob, request: Request) -> dict:
        _authorize_write(request)
        try:
            with Session(engine) as session, session.begin():
                owner = _authenticated(session, request)
                if not available():
                    raise _PrivateHTTPException(503, "research_unavailable")
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
                    raise _PrivateHTTPException(429, "active_run_limit")
                if total_active >= 100:
                    raise _PrivateHTTPException(429, "research_queue_full")
                if not available():
                    raise _PrivateHTTPException(503, "research_unavailable")
                source = source_for_request(
                    session, owner.token_hash, body.source_result_id, body.kind
                )
                source_run = session.get(ResearchRun, source.run_id)
                run = ResearchRun(
                    mode=source_run.mode, task_kind=body.kind, goal=ARTIFACT_GOALS[body.kind]
                )
                session.add(run)
                session.flush()
                session.add(ResearchRunAccess(run_id=run.id, session_hash=owner.token_hash))
                session.flush()
                bind_request(
                    session,
                    run,
                    owner.token_hash,
                    body.kind,
                    body.source_result_id,
                    body.formalisation_id,
                )
                _event(session, run, "created", "queued", f"{ARTIFACT_GOALS[body.kind]} queued")
                return _summary(run)
        except ArtifactError:
            raise _PrivateHTTPException(404, "artifact_source_not_found") from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/jobs")
    def list_jobs(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        before: uuid.UUID | None = None,
        source_result_id: Annotated[str | None, Query(min_length=36, max_length=36)] = None,
    ) -> dict:
        source_id = None
        if source_result_id is not None:
            try:
                source_id = uuid.UUID(source_result_id)
                if str(source_id) != source_result_id:
                    raise ValueError
            except ValueError:
                raise _PrivateHTTPException(422, "invalid_research_request") from None
        try:
            with Session(engine) as session:
                owner = _authenticated(session, request)
                query = (
                    select(ResearchRun)
                    .join(ResearchRunAccess, ResearchRunAccess.run_id == ResearchRun.id)
                    .where(
                        ResearchRunAccess.session_hash == owner.token_hash,
                        ResearchRun.task_kind.in_(ARTIFACT_KINDS),
                    )
                )
                if source_id is not None:
                    query = query.join(
                        ArtifactRequest, ArtifactRequest.run_id == ResearchRun.id
                    ).where(ArtifactRequest.source_result_id == source_id)
                if before is not None:
                    anchor = _artifact_job(session, request, before)
                    query = query.where(
                        tuple_(ResearchRun.created_at, ResearchRun.id)
                        < tuple_(anchor.created_at, anchor.id)
                    )
                rows = session.scalars(
                    query.order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc()).limit(
                        limit + 1
                    )
                ).all()
                return {
                    "jobs": [_summary(run) for run in rows[:limit]],
                    "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
                }
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/jobs/{run_id}")
    def get_job(run_id: uuid.UUID, request: Request) -> dict:
        try:
            with Session(engine) as session:
                return _summary(_artifact_job(session, request, run_id))
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/{artifact_id}")
    def get_artifact(artifact_id: uuid.UUID, request: Request) -> dict:
        try:
            with Session(engine) as session:
                run_id = _artifact_run(session, request, artifact_id)
            saved = store.get(run_id, artifact_id)
            return saved.to_response()
        except ArtifactError:
            raise _missing() from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/{artifact_id}/export")
    def export_artifact(artifact_id: uuid.UUID, request: Request) -> Response:
        try:
            with Session(engine) as session:
                run_id = _artifact_run(session, request, artifact_id)
            saved = store.get(run_id, artifact_id)
            return Response(
                content=saved.markdown.encode("utf-8"),
                media_type="text/markdown; charset=utf-8",
                headers={
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                    "Content-Disposition": f'attachment; filename="artifact-{artifact_id}.md"',
                },
            )
        except ArtifactError:
            raise _missing() from None
        except SQLAlchemyError:
            raise _storage_error() from None

    return router
