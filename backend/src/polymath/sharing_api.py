"""Explicit owner grants and cookie-free public reading of immutable snapshots.

Handlers never create research runs, browser sessions, provider requests or model
calls. Public reads use the saved public projection, not private result routes.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from polymath.run_api import _authenticated, _authorize_write
from polymath.sharing import SharingError, SharingPolicy, SharingStore

HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}
PUBLIC_ID = re.compile(r"[A-Za-z0-9_-]{43}")


class _SharingHTTPException(HTTPException):
    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code, detail, headers=dict(HEADERS))


class _SharingRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                response = await original(request)
                response.headers.update(HEADERS)
                return response
            except RequestValidationError:
                raise _SharingHTTPException(422, "sharing_invalid_request") from None
            except SharingError as error:
                raise _sharing_error(error) from None
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **HEADERS}
                raise
            except Exception:
                raise _SharingHTTPException(503, "sharing_storage_unavailable") from None

        return handle

    async def handle(self, scope, receive, send) -> None:
        try:
            await super().handle(scope, receive, send)
        except HTTPException as error:
            # Method errors are raised before the request handler is entered.
            error.headers = {**(error.headers or {}), **HEADERS}
            raise


def _uuid(value: str) -> uuid.UUID:
    try:
        identifier = uuid.UUID(value)
        if str(identifier) != value:
            raise ValueError
        return identifier
    except (ValueError, TypeError, AttributeError):
        raise _SharingHTTPException(422, "sharing_invalid_request") from None


def _public_id(value: str | None) -> str | None:
    if value is not None and PUBLIC_ID.fullmatch(value) is None:
        raise _SharingHTTPException(404, "sharing_not_found")
    return value


def _query(value: str) -> str:
    value = value.strip()
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _SharingHTTPException(422, "sharing_invalid_request") from None
    if len(value) > 200 or any(ord(character) < 32 for character in value):
        raise _SharingHTTPException(422, "sharing_invalid_request")
    return value


def _sharing_error(error: SharingError) -> _SharingHTTPException:
    code = str(error)
    status = {
        "sharing_not_found": 404,
        "sharing_source_not_current": 409,
        "sharing_projection_changed": 409,
        "sharing_idempotency_conflict": 409,
        "sharing_active_limit": 429,
        "sharing_global_limit": 429,
        "sharing_invalid_request": 422,
        "sharing_unavailable": 503,
    }.get(code)
    return (
        _SharingHTTPException(status, code)
        if status
        else _SharingHTTPException(503, "sharing_storage_unavailable")
    )


class CreateShare(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    source_result_id: str = Field(min_length=36, max_length=36)
    expected_projection_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")
    idempotency_key: str = Field(min_length=36, max_length=36)

    @model_validator(mode="after")
    def canonical_identifiers(self) -> CreateShare:
        for value in (self.source_result_id, self.idempotency_key):
            if str(uuid.UUID(value)) != value:
                raise ValueError("Canonical saved identifiers are required")
        return self


def create_sharing_router(
    engine: Engine, availability: Callable[[], bool] | None = None
) -> APIRouter:
    if engine.dialect.name != "postgresql":
        raise ValueError("Public sharing requires PostgreSQL")
    policy = availability or (lambda: False)
    router = APIRouter(route_class=_SharingRoute)

    def enabled() -> bool:
        try:
            return policy() is True
        except Exception:
            return False

    def store() -> SharingStore:
        return SharingStore(engine, policy=SharingPolicy(enabled=enabled()), availability=enabled)

    def owner(request: Request, *, write: bool = False) -> str:
        with Session(engine) as session:
            browser = _authenticated(session, request)
            if write:
                _authorize_write(request)
            return browser.token_hash

    def private_available() -> None:
        if not enabled():
            raise _SharingHTTPException(503, "sharing_unavailable")

    def public_available() -> None:
        if not enabled():
            raise _SharingHTTPException(404, "sharing_not_found")

    @router.get("/api/v1/shares/capabilities")
    def capabilities() -> dict:
        active = enabled()
        return {"sharing_available": active, "reason": None if active else "sharing_unavailable"}

    @router.get("/api/v1/shares/preview")
    def preview(
        request: Request,
        source_result_id: Annotated[str, Query(min_length=36, max_length=36)],
    ) -> dict:
        owner_hash = owner(request)
        private_available()
        response = store().preview(owner_hash, _uuid(source_result_id)).to_response()
        private_available()
        return response

    @router.post("/api/v1/shares", status_code=201)
    def share(body: CreateShare, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        private_available()
        response = (
            store()
            .share(
                owner_hash,
                _uuid(body.source_result_id),
                body.expected_projection_sha256,
                _uuid(body.idempotency_key),
            )
            .to_response()
        )
        return response

    @router.get("/api/v1/shares")
    def list_owned(
        request: Request,
        source_result_id: Annotated[str | None, Query(min_length=36, max_length=36)] = None,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        before: Annotated[str | None, Query(max_length=43)] = None,
    ) -> dict:
        owner_hash = owner(request)
        rows = store().list_owned(
            owner_hash,
            source_result_id=_uuid(source_result_id) if source_result_id else None,
            limit=limit + 1,
            before=_public_id(before),
        )
        return {
            "shares": [row.to_response() for row in rows[:limit]],
            "next_cursor": rows[limit - 1].public_id if len(rows) > limit else None,
        }

    @router.post("/api/v1/shares/{public_id}/revoke")
    def revoke(public_id: str, request: Request) -> dict:
        owner_hash = owner(request, write=True)
        return store().revoke(owner_hash, _public_id(public_id)).to_response()

    @router.get("/api/v1/public/findings/{public_id}", dependencies=[Depends(public_available)])
    def public_finding(public_id: str) -> dict:
        public_available()
        response = store().get_public(_public_id(public_id))
        public_available()
        return response

    @router.get("/api/v1/public/library", dependencies=[Depends(public_available)])
    def library(
        q: Annotated[str, Query(max_length=200)] = "",
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        before: Annotated[str | None, Query(max_length=43)] = None,
    ) -> dict:
        public_available()
        response = store().list_public(query=_query(q), limit=limit, before=_public_id(before))
        public_available()
        return response

    @router.get("/api/v1/public/map", dependencies=[Depends(public_available)])
    def public_map(q: Annotated[str, Query(max_length=200)] = "") -> dict:
        public_available()
        response = store().public_map(query=_query(q))
        public_available()
        return response

    return router
