"""Owner-scoped HTTP routes for experimental Converge programmes.

These routes never create research runs or spend. The owner's browser queues
each due step through the ordinary run API, then attaches the new run here.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.converge import (
    MAX_CEILING,
    MAX_FIELDS,
    MIN_CEILING,
    MIN_FIELDS,
    STEP_ESTIMATE_MICRODOLLARS,
    ConvergeError,
    ConvergeStore,
    catalogue_size,
)
from polymath.converge_fields import suggest
from polymath.run_api import (
    _authenticated,
    _authorize_write,
    _private_response,
    _PrivateHTTPException,
    _PrivateRoute,
)

ERROR_STATUS = {
    "converge_not_found": 404,
    "converge_step_not_due": 409,
    "converge_run_invalid": 409,
    "converge_finished": 409,
    "converge_limit_reached": 429,
    "invalid_converge_request": 422,
    "invalid_converge_fields": 422,
    "invalid_converge_ceiling": 422,
}


class CreateProgramme(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    theme: str | None = Field(default=None, max_length=2000)
    field_count: int = Field(ge=MIN_FIELDS, le=MAX_FIELDS)
    fields: list[str] = Field(default_factory=list, max_length=MAX_FIELDS)
    ceiling_microdollars: int = Field(ge=MIN_CEILING, le=MAX_CEILING)


class ExtendProgramme(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    fields: list[str] = Field(default_factory=list, max_length=MAX_FIELDS)
    add_count: int = Field(default=0, ge=0, le=MAX_FIELDS)
    ceiling_microdollars: int | None = Field(default=None, ge=MIN_CEILING, le=MAX_CEILING)


class AttachRun(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    kind: str = Field(min_length=1, max_length=8)
    parent_result_ids: list[str] = Field(default_factory=list, max_length=4)
    run_id: str = Field(min_length=36, max_length=36)


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise _PrivateHTTPException(404, "converge_not_found") from None


def create_converge_router(
    engine: Engine, availability: Callable[[], bool] | None = None
) -> APIRouter:
    store = ConvergeStore(engine)
    policy = availability or (lambda: True)
    router = APIRouter(
        prefix="/api/v1/converge",
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
            raise _PrivateHTTPException(503, "converge_unavailable")
        with Session(engine) as session:
            return _authenticated(session, request).token_hash

    def call(operation):
        try:
            return operation()
        except ConvergeError as error:
            code = str(error)
            raise _PrivateHTTPException(ERROR_STATUS.get(code, 422), code) from None
        except SQLAlchemyError:
            raise _PrivateHTTPException(503, "converge_storage_unavailable") from None

    @router.get("/capabilities")
    def capabilities() -> dict:
        active = available()
        return {
            "converge_available": active,
            "experimental": True,
            "spends_directly": False,
            "min_fields": MIN_FIELDS,
            "max_fields": MAX_FIELDS,
            "catalogue_size": catalogue_size(),
            "min_ceiling_microdollars": MIN_CEILING,
            "max_ceiling_microdollars": MAX_CEILING,
            "step_estimate_microdollars": STEP_ESTIMATE_MICRODOLLARS,
            "reason": None if active else "converge_unavailable",
        }

    @router.get("/fields")
    def fields(q: str = Query(default="", max_length=100)) -> dict:
        return {"fields": suggest(q)}

    @router.post("", status_code=201)
    def create(body: CreateProgramme, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        return call(
            lambda: store.create(
                owner_hash,
                theme=body.theme,
                field_count=body.field_count,
                fields=body.fields,
                ceiling_microdollars=body.ceiling_microdollars,
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
                body.kind,
                body.parent_result_ids,
                _uuid(body.run_id),
            )
        )

    @router.post("/{programme_id}/extend")
    def extend(programme_id: str, body: ExtendProgramme, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        return call(
            lambda: store.extend(
                owner_hash,
                _uuid(programme_id),
                fields=body.fields,
                add_count=body.add_count,
                ceiling_microdollars=body.ceiling_microdollars,
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

    return router
