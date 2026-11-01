"""Owner-scoped HTTP routes for Solve (docs/SOLVE.md).

These routes never create research runs or spend. The visitor's browser queues
each due stage through the ordinary run API, then attaches the new run here.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.run_api import (
    _authenticated,
    _authorize_write,
    _private_response,
    _PrivateHTTPException,
    _PrivateRoute,
)
from polymath.solve import SolveError, SolveStore

ERROR_STATUS = {
    "solve_not_found": 404,
    "solve_frontier_step_not_found": 404,
    "solve_step_not_due": 409,
    "solve_run_invalid": 409,
    "solve_finished": 409,
    "solve_limit_reached": 429,
    "invalid_solve_request": 422,
    "invalid_solve_stage": 422,
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class CreateProgramme(Strict):
    problem: str | None = Field(default=None, min_length=1, max_length=2000)
    frontier_programme_id: str | None = Field(default=None, min_length=36, max_length=36)
    frontier_node_id: str | None = Field(default=None, min_length=36, max_length=36)


class AttachRun(Strict):
    step_id: str = Field(min_length=36, max_length=36)
    stage: Literal["find", "fit", "forge"]
    run_id: str = Field(min_length=36, max_length=36)


def _uuid(value: str | None, code: str = "solve_not_found") -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise _PrivateHTTPException(404, code) from None


def create_solve_router(
    engine: Engine, availability: Callable[[], bool] | None = None
) -> APIRouter:
    store = SolveStore(engine)
    policy = availability or (lambda: True)
    router = APIRouter(
        prefix="/api/v1/solve",
        route_class=_PrivateRoute,
        dependencies=[Depends(_private_response)],
    )

    def available() -> bool:
        try:
            return policy() is True
        except Exception:
            return False

    def owner(request: Request, *, write=False) -> str:
        if write:
            _authorize_write(request)
        if not available():
            raise _PrivateHTTPException(503, "solve_unavailable")
        with Session(engine) as session:
            return _authenticated(session, request).token_hash

    def call(operation):
        try:
            return operation()
        except SolveError as error:
            code = str(error)
            raise _PrivateHTTPException(ERROR_STATUS.get(code, 422), code) from None
        except SQLAlchemyError:
            raise _PrivateHTTPException(503, "solve_storage_unavailable") from None

    @router.get("/capabilities")
    def capabilities() -> dict:
        active = available()
        return {
            "solve_available": active,
            "spends_directly": False,
            "max_investigations": 5,
            "reason": None if active else "solve_unavailable",
        }

    @router.post("", status_code=201)
    def create(body: CreateProgramme, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        frontier = _uuid(body.frontier_programme_id, "solve_frontier_step_not_found")
        node = _uuid(body.frontier_node_id, "solve_frontier_step_not_found")
        return call(
            lambda: store.create(
                owner_hash, body.problem, frontier_programme_id=frontier, frontier_node_id=node
            )
        )

    @router.get("")
    def list_programmes(request: Request) -> dict:
        owner_hash = owner(request)
        return {"programmes": call(lambda: store.list(owner_hash))}

    @router.get("/{programme_id}")
    def get(programme_id: str, request: Request) -> dict:
        owner_hash = owner(request)
        return call(lambda: store.get(owner_hash, _uuid(programme_id)))

    @router.post("/{programme_id}/next")
    def next_action(programme_id: str, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        identifier = _uuid(programme_id)
        due = call(lambda: store.next_action(owner_hash, identifier))
        return {"next": due, "programme": call(lambda: store.get(owner_hash, identifier))}

    @router.post("/{programme_id}/attach")
    def attach(programme_id: str, body: AttachRun, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        return call(
            lambda: store.attach(
                owner_hash,
                _uuid(programme_id),
                _uuid(body.step_id),
                body.stage,
                _uuid(body.run_id),
            )
        )

    for status, path in (("paused", "pause"), ("active", "resume"), ("stopped", "stop")):

        def change(programme_id: str, request: Request, status: str = status) -> dict:
            owner_hash = owner(request, write=True)
            return call(lambda: store.set_status(owner_hash, _uuid(programme_id), status))

        router.add_api_route(f"/{{programme_id}}/{path}", change, methods=["POST"])

    return router
