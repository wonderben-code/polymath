"""Owned, read-only discovery stages and whole-run observations.

These routes read committed evidence only. They never enqueue work, construct a
model, fetch a paper, select a final stage, or grant scientific authority.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from polymath.run_api import _authenticated

HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}


class _DiscoveryHTTPException(HTTPException):
    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code, detail, headers=dict(HEADERS))


class _DiscoveryRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                response = await original(request)
                response.headers.update(HEADERS)
                return response
            except RequestValidationError:
                raise _DiscoveryHTTPException(422, "invalid_discovery_request") from None
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **HEADERS}
                raise
            except Exception as error:
                from polymath.discovery_store import DiscoveryError

                if isinstance(error, DiscoveryError) and error.code == "discovery_not_found":
                    raise _DiscoveryHTTPException(404, "discovery_not_found") from None
                raise _DiscoveryHTTPException(503, "discovery_storage_unavailable") from None

        return handle

    async def handle(self, scope, receive, send) -> None:
        try:
            await super().handle(scope, receive, send)
        except HTTPException as error:
            error.headers = {**(error.headers or {}), **HEADERS}
            raise


def _uuid(value: str) -> uuid.UUID:
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise _DiscoveryHTTPException(422, "invalid_discovery_request") from None


def create_discovery_router(engine: Engine) -> APIRouter:
    if engine.dialect.name != "postgresql":
        raise ValueError("Discovery evidence inspection requires PostgreSQL")
    router = APIRouter(prefix="/api/v1/runs", route_class=_DiscoveryRoute)

    def owned(request: Request):
        with Session(engine) as session:
            return _authenticated(session, request).token_hash

    def store():
        from polymath.discovery_store import DiscoveryStore

        return DiscoveryStore(engine)

    @router.get("/{run_id}/discovery")
    def summary(run_id: Annotated[str, Path(min_length=36, max_length=36)], request: Request):
        return store().summary(owned(request), _uuid(run_id))

    @router.get("/{run_id}/discovery/stages")
    def stages(run_id: Annotated[str, Path(min_length=36, max_length=36)], request: Request):
        return store().list_stages(owned(request), _uuid(run_id))

    @router.get("/{run_id}/discovery/stages/{stage_id}")
    def stage(
        run_id: Annotated[str, Path(min_length=36, max_length=36)],
        stage_id: Annotated[str, Path(min_length=36, max_length=36)],
        request: Request,
    ):
        return store().get_stage(owned(request), _uuid(run_id), _uuid(stage_id))

    return router
