"""Owner-scoped HTTP routes for Innovation Studio (docs/STUDIO.md).

These routes never create research runs or spend. The visitor's browser queues
each due stage through the ordinary run API, then attaches the new run here.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
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
from polymath.studio import MAX_INVESTIGATIONS, MAX_SPECS, StudioError, StudioStore
from polymath.studio_images import StudioImages

ERROR_STATUS = {
    "studio_not_found": 404,
    "studio_step_not_due": 409,
    "studio_run_invalid": 409,
    "studio_finished": 409,
    "studio_limit_reached": 429,
    "invalid_studio_request": 422,
    "invalid_studio_stage": 422,
    "studio_images_unavailable": 503,
    "studio_image_needs_design": 409,
    "studio_image_rendering": 409,
    "studio_image_limit": 429,
    "studio_image_daily_limit": 429,
    "studio_image_budget": 409,
    "studio_image_not_found": 404,
}


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class CreateProgramme(Strict):
    brief: str = Field(min_length=1, max_length=1000)
    specs: list[str] = Field(default_factory=list, max_length=MAX_SPECS)
    wildness: Literal["near", "far", "strange"] = "far"


class AttachRun(Strict):
    step_id: str = Field(min_length=36, max_length=36)
    stage: Literal["brief", "inspire", "design", "engineer", "test"]
    run_id: str = Field(min_length=36, max_length=36)


def _uuid(value: str | None, code: str = "studio_not_found") -> uuid.UUID | None:
    if value is None:
        return None
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise _PrivateHTTPException(404, code) from None


def create_studio_router(
    engine: Engine,
    availability: Callable[[], bool] | None = None,
    images: StudioImages | None = None,
) -> APIRouter:
    store = StudioStore(engine)
    policy = availability or (lambda: True)
    router = APIRouter(
        prefix="/api/v1/studio",
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
            raise _PrivateHTTPException(503, "studio_unavailable")
        with Session(engine) as session:
            return _authenticated(session, request).token_hash

    def call(operation):
        try:
            return operation()
        except StudioError as error:
            code = str(error)
            raise _PrivateHTTPException(ERROR_STATUS.get(code, 422), code) from None
        except SQLAlchemyError:
            raise _PrivateHTTPException(503, "studio_storage_unavailable") from None

    @router.get("/capabilities")
    def capabilities() -> dict:
        active = available()
        return {
            "studio_available": active,
            "spends_directly": False,
            "max_investigations": MAX_INVESTIGATIONS,
            "max_specs": MAX_SPECS,
            "images_available": bool(active and images is not None and images.available),
            "reason": None if active else "studio_unavailable",
        }

    @router.post("", status_code=201)
    def create(body: CreateProgramme, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        return call(lambda: store.create(owner_hash, body.brief, body.specs, body.wildness))

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

    @router.get("/images/{image_id}")
    def image_content(image_id: str, request: Request) -> Response:
        owner_hash = owner(request)
        if images is None:
            raise _PrivateHTTPException(404, "studio_image_not_found")
        content, mime = call(lambda: images.content(owner_hash, _uuid(image_id)))
        return Response(
            content=content,
            media_type=mime,
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.get("/{programme_id}/images")
    def list_images(programme_id: str, request: Request) -> dict:
        owner_hash = owner(request)
        if images is None:
            return {"available": False, "images": []}
        return {
            "available": images.available,
            "images": call(lambda: images.list(owner_hash, _uuid(programme_id))),
        }

    @router.post("/{programme_id}/images", status_code=202)
    def render_image(programme_id: str, request: Request, background: BackgroundTasks) -> dict:
        owner_hash = owner(request, write=True)
        if images is None:
            raise _PrivateHTTPException(503, "studio_images_unavailable")
        image_id, reservation_id = call(lambda: images.request(owner_hash, _uuid(programme_id)))
        # One request, after the reservation is committed; the browser polls the list.
        background.add_task(images.execute, image_id, reservation_id)
        return {"id": str(image_id), "status": "rendering"}

    for status, path in (("paused", "pause"), ("active", "resume"), ("stopped", "stop")):

        def change(programme_id: str, request: Request, status: str = status) -> dict:
            owner_hash = owner(request, write=True)
            return call(lambda: store.set_status(owner_hash, _uuid(programme_id), status))

        router.add_api_route(f"/{{programme_id}}/{path}", change, methods=["POST"])

    return router
