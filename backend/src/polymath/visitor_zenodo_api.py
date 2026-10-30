"""Visitor-owned routes for Connect Zenodo (docs/CONNECT_ZENODO.md).

Every route belongs to the caller's own browser session; writes need its CSRF
token. No route returns a token, and none can reach the owner's publisher path.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.publication_contracts import PublicationMetadata
from polymath.run_api import (
    _authenticated,
    _authorize_write,
    _private_response,
    _PrivateHTTPException,
    _PrivateRoute,
)
from polymath.visitor_zenodo import VisitorZenodoError, VisitorZenodoPolicy, VisitorZenodoStore

ERROR_STATUS = {
    "zenodo_session_required": 401,
    "zenodo_publication_not_found": 404,
    "zenodo_destination_unavailable": 409,
    "zenodo_not_connected": 409,
    "zenodo_publication_changed": 409,
    "zenodo_finding_not_current": 409,
    "zenodo_permanent_confirmation_required": 409,
    "zenodo_state_invalid": 400,
    "zenodo_connection_refused": 400,
    "zenodo_permission_missing": 400,
    "zenodo_connection_failed": 502,
    "zenodo_reconnect_required": 409,
    "zenodo_daily_limit": 429,
    "zenodo_publication_invalid": 422,
}

Destination = Literal["sandbox", "production"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class Connect(Strict):
    destination: Destination


class Complete(Strict):
    code: str = Field(min_length=1, max_length=512)
    state: str = Field(min_length=20, max_length=200)


class Preview(Strict):
    artifact_id: str = Field(min_length=36, max_length=36)
    destination: Destination
    metadata: dict[str, Any]


class Publish(Strict):
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirm_permanent: bool = False


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError, AttributeError):
        raise _PrivateHTTPException(404, "zenodo_publication_not_found") from None


def create_visitor_zenodo_router(
    engine: Engine,
    policy: VisitorZenodoPolicy | None = None,
    *,
    token_transport=None,
) -> APIRouter:
    store = VisitorZenodoStore(engine, policy)
    router = APIRouter(
        prefix="/api/v1/zenodo",
        route_class=_PrivateRoute,
        dependencies=[Depends(_private_response)],
    )

    def owner(request: Request, *, write=False) -> str:
        if write:
            _authorize_write(request)
        if not store.policy.destinations:
            raise _PrivateHTTPException(503, "zenodo_unavailable")
        with Session(engine) as session:
            return _authenticated(session, request).token_hash

    def failure(error: Exception):
        if isinstance(error, VisitorZenodoError):
            code = str(error)
            return _PrivateHTTPException(ERROR_STATUS.get(code, 422), code)
        return _PrivateHTTPException(503, "zenodo_storage_unavailable")

    def call(operation):
        try:
            return operation()
        except (VisitorZenodoError, SQLAlchemyError) as error:
            raise failure(error) from None

    @router.get("/status")
    def status(request: Request) -> dict:
        if not store.policy.destinations:
            return {"available": False, "destinations": [], "daily_publications": 0}
        owner_hash = owner(request)
        return call(lambda: store.status(owner_hash))

    @router.post("/connect")
    def connect(body: Connect, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        return {"authorize_url": call(lambda: store.begin(owner_hash, body.destination))}

    @router.post("/complete")
    async def complete(body: Complete, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        try:
            destination = await store.complete(
                owner_hash, body.code, body.state, transport=token_transport
            )
        except (VisitorZenodoError, SQLAlchemyError) as error:
            raise failure(error) from None
        return {"destination": destination, "connected": True}

    @router.delete("/connections/{destination}")
    def disconnect(destination: Destination, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        call(lambda: store.disconnect(owner_hash, destination))
        return call(lambda: store.status(owner_hash))

    @router.post("/publications", status_code=201)
    def preview(body: Preview, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        try:
            metadata = PublicationMetadata.model_validate(body.metadata)
        except (ValidationError, ValueError, TypeError):
            raise _PrivateHTTPException(422, "zenodo_publication_invalid") from None
        return call(
            lambda: store.preview(owner_hash, _uuid(body.artifact_id), metadata, body.destination)
        )

    @router.get("/publications")
    def publications(
        request: Request, artifact_id: str | None = Query(default=None, max_length=36)
    ) -> dict:
        owner_hash = owner(request)
        artifact = _uuid(artifact_id) if artifact_id else None
        return call(lambda: store.list(owner_hash, artifact))

    @router.get("/publications/{publication_id}")
    def publication(publication_id: str, request: Request) -> dict:
        owner_hash = owner(request)
        return call(lambda: store.get(owner_hash, _uuid(publication_id)))

    @router.post("/publications/{publication_id}/publish")
    def publish(publication_id: str, body: Publish, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        return call(
            lambda: store.confirm(
                owner_hash,
                _uuid(publication_id),
                body.manifest_sha256,
                confirm_permanent=body.confirm_permanent,
            )
        )

    @router.post("/publications/{publication_id}/retry")
    def retry(publication_id: str, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        return call(lambda: store.retry(owner_hash, _uuid(publication_id)))

    return router
