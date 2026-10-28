"""Owned read-only Explore collections; reading never starts scientific work."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException

from polymath.discovery_api import HEADERS
from polymath.run_api import _authenticated


class _ExploreHTTPException(HTTPException):
    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code, detail, headers=dict(HEADERS))


class _ExploreRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                response = await original(request)
                response.headers.update(HEADERS)
                return response
            except RequestValidationError:
                raise _ExploreHTTPException(422, "invalid_explore_collection_request") from None
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **HEADERS}
                raise
            except Exception as error:
                from polymath.discovery_store import DiscoveryError

                if isinstance(error, DiscoveryError) and error.code in (
                    "discovery_not_found",
                    "explore_collection_not_found",
                ):
                    raise _ExploreHTTPException(404, "explore_collection_not_found") from None
                raise _ExploreHTTPException(503, "explore_collection_storage_unavailable") from None

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
        raise _ExploreHTTPException(422, "invalid_explore_collection_request") from None


def create_explore_collection_router(engine: Engine) -> APIRouter:
    if engine.dialect.name != "postgresql":
        raise ValueError("Explore collection inspection requires PostgreSQL")
    router = APIRouter(prefix="/api/v1/runs", route_class=_ExploreRoute)

    def owned(request: Request):
        with Session(engine) as session:
            return _authenticated(session, request).token_hash

    def store():
        from polymath.explore_collection_store import ExploreCollectionStore

        return ExploreCollectionStore(engine)

    @router.get("/{run_id}/explore-collection")
    def summary(run_id: Annotated[str, Path(min_length=36, max_length=36)], request: Request):
        return store().summary(owned(request), _uuid(run_id))

    @router.get("/{run_id}/bridges")
    def bridges(run_id: Annotated[str, Path(min_length=36, max_length=36)], request: Request):
        return store().list_stages(owned(request), _uuid(run_id))

    @router.get("/{run_id}/bridges/{stage_id}")
    def bridge(
        run_id: Annotated[str, Path(min_length=36, max_length=36)],
        stage_id: Annotated[str, Path(min_length=36, max_length=36)],
        request: Request,
    ):
        return store().get_stage(owned(request), _uuid(run_id), _uuid(stage_id))

    @router.get("/{run_id}/rankings")
    def rankings(run_id: Annotated[str, Path(min_length=36, max_length=36)], request: Request):
        return store().list_rankings(owned(request), _uuid(run_id))

    return router
