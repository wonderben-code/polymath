"""Owner-scoped HTTP routes for experimental Frontier programmes.

These routes never create research runs or spend. The owner's browser queues
each due attempt through the ordinary run API, then attaches the new run here.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.frontier import FrontierError, FrontierLimits, FrontierStore
from polymath.run_api import (
    _authenticated,
    _authorize_write,
    _private_response,
    _PrivateHTTPException,
    _PrivateRoute,
)

ERROR_STATUS = {
    "frontier_not_found": 404,
    "frontier_node_not_found": 404,
    "frontier_action_not_due": 409,
    "frontier_run_invalid": 409,
    "frontier_finished": 409,
    "frontier_limit_reached": 429,
    "invalid_frontier_request": 422,
    "invalid_frontier_limits": 422,
    "invalid_frontier_action": 422,
}


class CreateProgramme(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    question: str = Field(min_length=1, max_length=2000)
    success_criteria: str | None = Field(default=None, max_length=1000)
    max_attempts: int = Field(default=24, ge=1, le=60)
    max_depth: int = Field(default=4, ge=1, le=6)
    max_branches: int = Field(default=6, ge=1, le=8)


class AttachRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    node_id: str = Field(min_length=36, max_length=36)
    action: str = Field(min_length=1, max_length=16)
    run_id: str = Field(min_length=36, max_length=36)


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise _PrivateHTTPException(404, "frontier_not_found") from None


def create_frontier_router(
    engine: Engine, availability: Callable[[], bool] | None = None
) -> APIRouter:
    store = FrontierStore(engine)
    policy = availability or (lambda: True)
    router = APIRouter(
        prefix="/api/v1/frontier",
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
            raise _PrivateHTTPException(503, "frontier_unavailable")
        with Session(engine) as session:
            return _authenticated(session, request).token_hash

    def call(operation):
        try:
            return operation()
        except FrontierError as error:
            code = str(error)
            raise _PrivateHTTPException(ERROR_STATUS.get(code, 422), code) from None
        except SQLAlchemyError:
            raise _PrivateHTTPException(503, "frontier_storage_unavailable") from None

    @router.get("/capabilities")
    def capabilities() -> dict:
        active = available()
        return {
            "frontier_available": active,
            "experimental": True,
            "spends_directly": False,
            "defaults": {"max_attempts": 24, "max_depth": 4, "max_branches": 6},
            "reason": None if active else "frontier_unavailable",
        }

    @router.post("", status_code=201)
    def create(body: CreateProgramme, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        return call(
            lambda: store.create(
                owner_hash,
                body.question,
                body.success_criteria or None,
                FrontierLimits(body.max_attempts, body.max_depth, body.max_branches),
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
                _uuid(body.node_id),
                body.action,
                _uuid(body.run_id),
            )
        )

    def change(programme_id: str, request: Request, status: str) -> dict:
        owner_hash = owner(request, write=True)
        return call(lambda: store.set_status(owner_hash, _uuid(programme_id), status))

    @router.post("/{programme_id}/pause")
    def pause(programme_id: str, request: Request) -> dict:
        return change(programme_id, request, "paused")

    @router.post("/{programme_id}/resume")
    def resume(programme_id: str, request: Request) -> dict:
        return change(programme_id, request, "active")

    @router.post("/{programme_id}/stop")
    def stop(programme_id: str, request: Request) -> dict:
        return change(programme_id, request, "stopped")

    @router.post("/{programme_id}/nodes/{node_id}/prune")
    def prune(programme_id: str, node_id: str, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        return call(lambda: store.prune(owner_hash, _uuid(programme_id), _uuid(node_id)))

    return router
