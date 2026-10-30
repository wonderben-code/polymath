"""Publisher-only routes for publishing levels and the publishing autopilot.

Every route requires the owning browser session and a live publisher session;
writes also require both CSRF tokens. These routes never write, prepare,
approve, queue or contact Zenodo; the publisher's browser does each step
through the ordinary routes and attaches the exact result here.
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

from polymath.operator_auth import OperatorPolicy, require_operator
from polymath.publication_contracts import PublicationPolicy
from polymath.publishing import PublishingError, PublishingStore
from polymath.run_api import _private_response, _PrivateHTTPException, _PrivateRoute

ERROR_STATUS = {
    "publishing_item_not_found": 404,
    "publishing_stage_not_due": 409,
    "publishing_link_invalid": 409,
    "publishing_destination_unavailable": 409,
    "production_confirmation_required": 409,
    "publishing_metadata_invalid": 422,
    "invalid_publishing_preferences": 422,
}


class CreatorBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=200)
    affiliation: str | None = Field(default=None, max_length=300)
    orcid: str | None = Field(default=None, max_length=19)


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    level: int = Field(ge=1, le=4)
    draft_destination: Literal["sandbox", "production"] = "sandbox"
    write_kind: Literal["write_summary", "write_paper"] = "write_paper"
    full_text_only: bool = False
    daily_cap: int = Field(default=3, ge=1, le=10)
    creators: list[CreatorBody] = Field(default_factory=list, max_length=10)
    license: Literal["cc-by-4.0", "cc0-1.0"] = "cc-by-4.0"
    keywords: list[str] = Field(default_factory=list, max_length=20)
    confirm_production: bool = False


class Attach(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    stage: Literal["write", "prepare", "approve", "queue"]
    ref: str = Field(min_length=36, max_length=36)


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise _PrivateHTTPException(404, "publishing_item_not_found") from None


def create_publishing_router(
    engine: Engine,
    *,
    publication_policy: PublicationPolicy | None = None,
    operator_policy: OperatorPolicy | None = None,
    execution_available: Callable[[], bool] | None = None,
) -> APIRouter:
    publication_policy = publication_policy or PublicationPolicy()
    store = PublishingStore(engine, destinations=tuple(publication_policy.allowed_destinations))
    executing = execution_available or (lambda: False)
    router = APIRouter(
        prefix="/api/v1/publishing",
        route_class=_PrivateRoute,
        dependencies=[Depends(_private_response)],
    )

    def publisher(request: Request, *, write=False) -> tuple[str, str]:
        if not publication_policy.configured:
            raise _PrivateHTTPException(503, "publication_unavailable")
        try:
            with Session(engine) as session, session.begin():
                operator = require_operator(session, request, write=write, policy=operator_policy)
                return operator.browser_session_hash, operator.actor_id
        except SQLAlchemyError:
            raise _PrivateHTTPException(503, "publishing_storage_unavailable") from None

    def call(operation):
        try:
            return operation()
        except PublishingError as error:
            code = str(error)
            raise _PrivateHTTPException(ERROR_STATUS.get(code, 422), code) from None
        except SQLAlchemyError:
            raise _PrivateHTTPException(503, "publishing_storage_unavailable") from None

    @router.get("/status")
    def status() -> dict:
        """What is connected, without needing a publisher session."""
        try:
            running = executing() is True
        except Exception:
            running = False
        return {
            "configured": publication_policy.configured,
            "publisher": publication_policy.publisher.display_name
            if publication_policy.publisher
            else None,
            "destinations": list(publication_policy.allowed_destinations),
            "publishing_runs": running,
        }

    @router.get("/preferences")
    def get_preferences(request: Request) -> dict:
        _, actor = publisher(request)
        return call(lambda: store.preferences(actor))

    @router.put("/preferences")
    def put_preferences(body: Preferences, request: Request) -> dict:
        _, actor = publisher(request, write=True)
        return call(
            lambda: store.set_preferences(
                actor,
                level=body.level,
                draft_destination=body.draft_destination,
                write_kind=body.write_kind,
                full_text_only=body.full_text_only,
                daily_cap=body.daily_cap,
                creators=[creator.model_dump() for creator in body.creators],
                license=body.license,
                keywords=body.keywords,
                confirm_production=body.confirm_production,
            )
        )

    @router.get("/items")
    def items(request: Request) -> dict:
        owner_hash, actor = publisher(request)
        return {"items": call(lambda: store.items(owner_hash, actor))}

    @router.post("/next")
    def next_step(request: Request) -> dict:
        owner_hash, actor = publisher(request, write=True)
        return call(lambda: store.next_step(owner_hash, actor))

    @router.post("/items/{item_id}/attach")
    def attach(item_id: str, body: Attach, request: Request) -> dict:
        owner_hash, actor = publisher(request, write=True)
        return call(
            lambda: store.attach(owner_hash, actor, _uuid(item_id), body.stage, _uuid(body.ref))
        )

    @router.post("/items/{item_id}/skip")
    def skip(item_id: str, request: Request) -> dict:
        owner_hash, actor = publisher(request, write=True)
        return call(lambda: store.skip(owner_hash, actor, _uuid(item_id)))

    return router
