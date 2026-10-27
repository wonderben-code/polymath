"""Session-owned research routes. Default construction never enables execution.

The browser bearer cookie is never returned in JSON, URLs, logs, or the database.
Read-only SSE reports persisted events; it cannot start a model or manufacture
activity. Actual execution availability is an explicit injected policy.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Engine, func, select, text, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.jobs import JobNotFound, JobRepository, _event
from polymath.models import ResearchEvent, ResearchRun

COOKIE_NAME = "polymath_session"
SESSION_SECONDS = 7 * 24 * 60 * 60
MAX_CURSOR = 2**63 - 1
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
ACTIVE_STATES = ("queued", "running", "waiting_retry", "cancel_requested", "interrupted")
STREAM_TERMINAL = ("succeeded", "failed", "cancelled", "interrupted")
QUEUE_LOCK_KEY = 7_691_121_406


class _PrivateHTTPException(HTTPException):
    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code, detail, headers={"Cache-Control": "no-store"})


class _PrivateRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                # Do not echo private research input in validation error bodies.
                raise _PrivateHTTPException(422, "invalid_research_request") from None

        return handle


class CreateRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    mode: Literal["explore", "ask", "surprise"]
    goal: str = Field(max_length=4000)

    @model_validator(mode="after")
    def validate_goal(self) -> CreateRun:
        self.goal = self.goal.strip()
        if not self.goal and self.mode != "surprise":
            raise ValueError("A research goal is required")
        if any(ord(character) < 32 and character not in "\n\t\r" for character in self.goal):
            raise ValueError("Unsupported characters in research goal")
        return self


def _token(request: Request) -> str | None:
    value = request.cookies.get(COOKIE_NAME)
    return value if value is not None and TOKEN_PATTERN.fullmatch(value) else None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _csrf(token: str) -> str:
    return hashlib.sha256(("csrf:" + token).encode("ascii")).hexdigest()


def _not_found() -> HTTPException:
    return _PrivateHTTPException(status_code=404, detail="research_run_not_found")


def _storage_error() -> HTTPException:
    return _PrivateHTTPException(status_code=503, detail="research_storage_unavailable")


def _private_response(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _now(session: Session) -> datetime:
    return session.scalar(select(func.clock_timestamp()))


def _authenticated(session: Session, request: Request) -> BrowserSession:
    token = _token(request)
    row = session.get(BrowserSession, _hash_token(token)) if token else None
    if row is None or row.expires_at <= _now(session):
        raise _PrivateHTTPException(status_code=401, detail="research_session_required")
    return row


def _authorize_write(request: Request) -> None:
    token = _token(request)
    supplied = request.headers.get("x-csrf-token", "")
    if (
        token is None
        or re.fullmatch(r"[a-f0-9]{64}", supplied) is None
        or not hmac.compare_digest(supplied, _csrf(token))
    ):
        raise _PrivateHTTPException(status_code=403, detail="csrf_check_failed")
    origin = request.headers.get("origin")
    if origin is not None:
        try:
            actual = urlsplit(origin)
            expected = urlsplit(str(request.base_url))
        except ValueError:
            raise _PrivateHTTPException(status_code=403, detail="origin_check_failed") from None
        if (
            actual.scheme != expected.scheme
            or actual.netloc != expected.netloc
            or actual.path not in ("", "/")
            or actual.query
            or actual.fragment
        ):
            raise _PrivateHTTPException(status_code=403, detail="origin_check_failed")
    if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
        raise _PrivateHTTPException(status_code=403, detail="origin_check_failed")


def _owned(session: Session, owner: BrowserSession, run_id: uuid.UUID) -> ResearchRun:
    run = session.scalar(
        select(ResearchRun)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == ResearchRun.id)
        .where(ResearchRun.id == run_id, ResearchRunAccess.session_hash == owner.token_hash)
    )
    if run is None:
        raise _not_found()
    return run


def _summary(run: ResearchRun) -> dict:
    result_id = None
    error_code = None
    if isinstance(run.outcome, dict):
        try:
            result_id = str(uuid.UUID(run.outcome["result_id"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
        code = run.outcome.get("error_code")
        if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,100}", code):
            error_code = code
    return {
        "id": str(run.id),
        "mode": run.mode,
        "goal": run.goal,
        "status": run.status,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "result_id": result_id,
        "error_code": error_code,
        "external_outcome_unknown": run.external_request_in_flight,
    }


def create_run_router(
    engine: Engine,
    availability: Callable[[], bool] | None = None,
    *,
    poll_seconds: float = 0.5,
    heartbeat_seconds: float = 15.0,
) -> APIRouter:
    """Mount under the existing app; only explicit policy can accept new jobs.

    This router performs no model calls and does not authorize a budget account.
    Tests inject availability=True with fixture workers. Production callers must
    keep the default false until execution/pricing/access gates are verified.
    """
    if engine.dialect.name != "postgresql":
        raise ValueError("Research routes require PostgreSQL")
    if not 0.001 <= poll_seconds <= 10 or not 0.001 <= heartbeat_seconds <= 60:
        raise ValueError("Invalid event polling interval")
    policy = availability or (lambda: False)
    router = APIRouter(
        prefix="/api/v1", dependencies=[Depends(_private_response)], route_class=_PrivateRoute
    )
    jobs = JobRepository(engine)

    def available() -> bool:
        try:
            return policy() is True
        except Exception:
            raise _PrivateHTTPException(status_code=503, detail="research_unavailable") from None

    @router.get("/session")
    def browser_session(request: Request, response: Response) -> dict:
        try:
            with Session(engine) as session, session.begin():
                token = _token(request)
                row = session.get(BrowserSession, _hash_token(token)) if token else None
                now = _now(session)
                if row is None or row.expires_at <= now:
                    token = secrets.token_urlsafe(32)
                    row = BrowserSession(
                        token_hash=_hash_token(token),
                        expires_at=now + timedelta(seconds=SESSION_SECONDS),
                    )
                    session.add(row)
                remaining = max(0, int((row.expires_at - now).total_seconds()))
                response.set_cookie(
                    COOKIE_NAME,
                    token,
                    max_age=remaining,
                    httponly=True,
                    secure=request.url.scheme == "https",
                    samesite="lax",
                    path="/api/v1",
                )
                response.headers["Cache-Control"] = "no-store"
                return {"csrf_token": _csrf(token), "expires_at": row.expires_at.isoformat()}
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.post("/runs", status_code=201)
    def create_run(body: CreateRun, request: Request) -> dict:
        _authorize_write(request)
        try:
            with Session(engine) as session, session.begin():
                owner = _authenticated(session, request)
                if not available():
                    raise _PrivateHTTPException(status_code=503, detail="research_unavailable")
                # One transaction covers quota checks, job creation, ownership, and event.
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
                    raise _PrivateHTTPException(status_code=429, detail="active_run_limit")
                if total_active >= 100:
                    raise _PrivateHTTPException(status_code=429, detail="research_queue_full")
                # Recheck after waiting for the quota lock: availability may have changed.
                if not available():
                    raise _PrivateHTTPException(status_code=503, detail="research_unavailable")
                run = ResearchRun(mode=body.mode, goal=body.goal)
                session.add(run)
                session.flush()
                session.add(ResearchRunAccess(run_id=run.id, session_hash=owner.token_hash))
                _event(session, run, "created", "queued", "Investigation queued")
                return _summary(run)
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/runs")
    def list_runs(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=50)] = 20,
        before: uuid.UUID | None = None,
    ) -> dict:
        try:
            with Session(engine) as session:
                owner = _authenticated(session, request)
                query = (
                    select(ResearchRun)
                    .join(ResearchRunAccess, ResearchRunAccess.run_id == ResearchRun.id)
                    .where(ResearchRunAccess.session_hash == owner.token_hash)
                )
                if before is not None:
                    anchor = _owned(session, owner, before)
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
                    "runs": [_summary(run) for run in rows[:limit]],
                    "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
                }
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/runs/{run_id}")
    def get_run(run_id: uuid.UUID, request: Request) -> dict:
        try:
            with Session(engine) as session:
                return _summary(_owned(session, _authenticated(session, request), run_id))
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: uuid.UUID, request: Request) -> dict:
        _authorize_write(request)
        try:
            with Session(engine) as session:
                _owned(session, _authenticated(session, request), run_id)
            jobs.request_cancel(run_id)
            with Session(engine) as session:
                return _summary(_owned(session, _authenticated(session, request), run_id))
        except JobNotFound:
            raise _not_found() from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/runs/{run_id}/result")
    def get_result(run_id: uuid.UUID, request: Request) -> dict:
        # Lazy import keeps router construction independent from storage migration timing.
        from polymath.evidence import EvidenceError, EvidenceStore

        try:
            with Session(engine) as session:
                run = _owned(session, _authenticated(session, request), run_id)
                result_id = _summary(run)["result_id"]
            if result_id is None:
                raise _PrivateHTTPException(status_code=404, detail="research_result_not_available")
            stored = EvidenceStore(engine).get_result(run_id, uuid.UUID(result_id))
            return stored.payload
        except EvidenceError as error:
            if str(error) == "result_not_found":
                raise _PrivateHTTPException(
                    status_code=404, detail="research_result_not_available"
                ) from None
            raise _storage_error() from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/runs/{run_id}/events")
    async def events(
        run_id: uuid.UUID,
        request: Request,
        after: Annotated[int, Query(ge=0, le=MAX_CURSOR)] = 0,
        last_event_id: Annotated[str | None, Header()] = None,
    ) -> StreamingResponse:
        if last_event_id is not None:
            if not re.fullmatch(r"[0-9]{1,19}", last_event_id) or int(last_event_id) > MAX_CURSOR:
                raise _PrivateHTTPException(status_code=400, detail="invalid_event_cursor")
            after = max(after, int(last_event_id))
        token = _token(request)

        def poll(cursor: int) -> tuple[list[dict], bool] | None:
            try:
                with Session(engine) as session:
                    owner = _authenticated(session, request)
                    run = _owned(session, owner, run_id)
                    records = session.scalars(
                        select(ResearchEvent)
                        .where(ResearchEvent.run_id == run_id, ResearchEvent.sequence > cursor)
                        .order_by(ResearchEvent.sequence)
                        .limit(100)
                    ).all()
                    batch = [
                        {
                            "sequence": event.sequence,
                            "kind": event.kind,
                            "summary": event.summary,
                            "artifact_refs": event.artifact_refs,
                        }
                        for event in records
                    ]
                    drained = not records or records[-1].sequence >= run.event_sequence
                    return batch, run.status in STREAM_TERMINAL and drained
            except HTTPException:
                return None

        # Fail authentication/ownership before sending stream headers.
        try:
            with Session(engine) as session:
                _owned(session, _authenticated(session, request), run_id)
        except SQLAlchemyError:
            raise _storage_error() from None

        async def stream():
            cursor = after
            last_sent = time.monotonic()
            while not await request.is_disconnected():
                try:
                    current = await asyncio.to_thread(poll, cursor)
                except SQLAlchemyError:
                    yield 'event: error\ndata: {"code":"research_storage_unavailable"}\n\n'
                    return
                if current is None or _token(request) != token:
                    yield "event: session_expired\ndata: {}\n\n"
                    return
                batch, terminal = current
                for event in batch:
                    cursor = event["sequence"]
                    yield f"id: {cursor}\nevent: activity\ndata: {json.dumps(event)}\n\n"
                    last_sent = time.monotonic()
                if terminal:
                    yield "event: end\ndata: {}\n\n"
                    return
                if time.monotonic() - last_sent >= heartbeat_seconds:
                    yield ": heartbeat\nevent: heartbeat\ndata: {}\n\n"
                    last_sent = time.monotonic()
                await asyncio.sleep(poll_seconds)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router
