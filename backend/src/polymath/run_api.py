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
from typing import TYPE_CHECKING, Annotated, Literal
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
from polymath.discovery_contracts import DiscoveryIntent
from polymath.jobs import JobNotFound, JobRepository, _event
from polymath.lineage import LineageError, bind_parents, validate_parent_ids
from polymath.meta_research_errors import META_RESEARCH_ERROR_STATUSES, MetaResearchError
from polymath.models import ResearchEvent, ResearchRun

if TYPE_CHECKING:
    from polymath.meta_research import MetaResearchStore

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
            except MetaResearchError as error:
                code = (
                    error.code
                    if error.code in META_RESEARCH_ERROR_STATUSES
                    else ("meta_research_storage_unavailable")
                )
                raise _PrivateHTTPException(META_RESEARCH_ERROR_STATUSES[code], code) from None

        return handle


class CreateRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    mode: Literal["explore", "ask", "surprise"]
    goal: str = Field(max_length=4000)
    parent_result_ids: list[str] = Field(default_factory=list, max_length=4)
    expected_activation_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    discovery_intent: DiscoveryIntent | None = None

    @model_validator(mode="after")
    def validate_goal(self) -> CreateRun:
        self.goal = self.goal.strip()
        if not self.goal and self.mode != "surprise":
            raise ValueError("A research goal is required")
        if any(ord(character) < 32 and character not in "\n\t\r" for character in self.goal):
            raise ValueError("Unsupported characters in research goal")
        validate_parent_ids(self.parent_result_ids, self.mode)
        if self.discovery_intent is not None and self.mode != "surprise":
            raise ValueError("A discovery ladder requires Surprise mode")
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
    if run is None or run.task_kind == "verify_meta":
        raise _not_found()
    return run


def _summary(run: ResearchRun, *, session: Session | None = None) -> dict:
    result_id = None
    artifact_id = None
    error_code = None
    if isinstance(run.outcome, dict):
        try:
            result_id = str(uuid.UUID(run.outcome["result_id"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
        try:
            artifact_id = str(uuid.UUID(run.outcome["artifact_id"]))
        except (KeyError, TypeError, ValueError, AttributeError):
            pass
        code = run.outcome.get("error_code")
        if isinstance(code, str) and re.fullmatch(r"[a-z_]{1,100}", code):
            error_code = code
    result = {
        "id": str(run.id),
        "mode": run.mode,
        "task_kind": run.task_kind,
        "goal": run.goal,
        "status": run.status,
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
        "result_id": result_id if run.task_kind == "research" else None,
        "artifact_id": artifact_id if run.task_kind != "research" else None,
        "error_code": error_code,
        "external_outcome_unknown": run.external_request_in_flight,
    }
    if session is not None:
        from polymath.discovery_admission import load_discovery_intent
        from polymath.meta_research import native_run_controls, native_run_outcome_unknown

        intent = load_discovery_intent(session, run)
        if intent is not None:
            result["discovery_intent"] = intent.model_dump(mode="json")
        controls = native_run_controls(session, run)
        if controls is not None:
            result["native_research"] = controls
            result["external_outcome_unknown"] = native_run_outcome_unknown(session, run)
    return result


class ResumeNativeRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    expected_lease_token: str = Field(pattern=r"^(?:0|[1-9][0-9]{0,18})$")
    expected_activation_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def bounded_lease(self):
        if int(self.expected_lease_token) > 2**63 - 1:
            raise ValueError("Invalid lease token")
        return self


def create_run_router(
    engine: Engine,
    availability: Callable[[], bool] | None = None,
    *,
    poll_seconds: float = 0.5,
    heartbeat_seconds: float = 15.0,
    native_store: MetaResearchStore | None = None,
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
                admission = None
                if native_store is not None:
                    admission = native_store.begin_admission(
                        session, request, body.expected_activation_sha256
                    )
                elif body.expected_activation_sha256 is not None:
                    raise _PrivateHTTPException(503, "meta_research_unavailable")
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
                if admission is not None:
                    native_store.lock_account_in_session(session, admission)
                run = ResearchRun(mode=body.mode, goal=body.goal)
                session.add(run)
                session.flush()
                session.add(ResearchRunAccess(run_id=run.id, session_hash=owner.token_hash))
                if body.discovery_intent is not None:
                    from polymath.discovery_admission import bind_discovery_intent

                    session.flush()
                    bind_discovery_intent(session, run, body.discovery_intent)
                bind_parents(session, run, owner.token_hash, body.parent_result_ids)
                if admission is not None:
                    from polymath.meta_research import native_run_input_sha256

                    session.flush()
                    native_store.bind_run_in_session(
                        session, admission, run, native_run_input_sha256(session, run)
                    )
                _event(session, run, "created", "queued", "Investigation queued")
                return _summary(run, session=session)
        except LineageError:
            raise _PrivateHTTPException(404, "research_basis_not_found") from None
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
                    .where(ResearchRun.task_kind == "research")
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
                    "runs": [_summary(run, session=session) for run in rows[:limit]],
                    "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
                }
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/runs/{run_id}")
    def get_run(run_id: uuid.UUID, request: Request) -> dict:
        try:
            with Session(engine) as session:
                return _summary(
                    _owned(session, _authenticated(session, request), run_id), session=session
                )
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
                return _summary(
                    _owned(session, _authenticated(session, request), run_id), session=session
                )
        except JobNotFound:
            raise _not_found() from None
        except SQLAlchemyError:
            raise _storage_error() from None

    def resume_owner(run_id: uuid.UUID, request: Request) -> None:
        # Ownership/task isolation precedes resume-body validation. Probe jobs
        # remain absent from every generic route, including rejected bodies.
        try:
            with Session(engine) as session:
                _owned(session, _authenticated(session, request), run_id)
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.post("/runs/{run_id}/resume", dependencies=[Depends(resume_owner)])
    def resume_run(run_id: uuid.UUID, body: ResumeNativeRun, request: Request) -> dict:
        _authorize_write(request)
        if native_store is None:
            raise _PrivateHTTPException(503, "meta_research_unavailable")
        try:
            with Session(engine) as session, session.begin():
                # The native helper owns Operator → queue → Account → Run.
                run = native_store.resume_in_session(
                    session,
                    request,
                    run_id,
                    int(body.expected_lease_token),
                    body.expected_activation_sha256,
                )
                return _summary(run, session=session)
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
