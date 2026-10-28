"""Owned, bounded literature watches; HTTP never runs a model or a provider.

Watch creation, resumption, and explicit checks need an injected collection
policy. Reading or pausing existing watches remains possible when collection is
disabled. Every mutation also needs the browser's own CSRF and origin checks.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.ingestion import IngestionError, IngestionRepository
from polymath.run_api import _authenticated, _authorize_write, _PrivateHTTPException

MIN_INTERVAL_SECONDS = 21_600
MAX_INTERVAL_SECONDS = 604_800


class _IngestionHTTPException(_PrivateHTTPException):
    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code, detail)
        self.headers["X-Content-Type-Options"] = "nosniff"


class _IngestionRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                raise _IngestionHTTPException(422, "invalid_watch_request") from None
            except HTTPException as error:
                error.headers = {
                    **(error.headers or {}),
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                }
                raise

        return handle


class CreateWatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    label: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=500)
    interval_seconds: int = Field(default=86_400, ge=MIN_INTERVAL_SECONDS, le=MAX_INTERVAL_SECONDS)
    source_result_id: str | None = Field(default=None, min_length=36, max_length=36)

    @model_validator(mode="after")
    def bounded_watch(self) -> CreateWatch:
        self.label, self.query = self.label.strip(), self.query.strip()
        for value in (self.label, self.query):
            try:
                value.encode("utf-8")
            except UnicodeError:
                raise ValueError("Watch input must be valid public text") from None
            if not value or any(
                ord(character) < 32 or ord(character) == 127 for character in value
            ):
                raise ValueError("Watch input must be bounded plain text")
        if self.source_result_id is not None and str(uuid.UUID(self.source_result_id)) != (
            self.source_result_id
        ):
            raise ValueError("An exact owned finding version is required")
        return self


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"


def _storage_error() -> _IngestionHTTPException:
    return _IngestionHTTPException(503, "ingestion_storage_unavailable")


def _ingestion_error(error: IngestionError) -> _IngestionHTTPException:
    code = str(error)
    if code == "watch_not_found":
        return _IngestionHTTPException(404, "watch_not_found")
    if code in (
        "ingestion_watch_limit",
        "ingestion_queue_full",
        "ingestion_active_run_limit",
        "ingestion_watch_source_limit",
        "watch_check_throttled",
    ):
        return _IngestionHTTPException(429, code)
    if code in ("watch_paused", "watch_expired"):
        return _IngestionHTTPException(409, code)
    if code == "invalid_watch_request":
        return _IngestionHTTPException(422, code)
    return _storage_error()


def create_ingestion_router(
    engine: Engine, availability: Callable[[], bool] | None = None
) -> APIRouter:
    if engine.dialect.name != "postgresql":
        raise ValueError("Literature watches require PostgreSQL")
    repository = IngestionRepository(engine)
    policy = availability or (lambda: False)
    router = APIRouter(
        prefix="/api/v1/knowledge/watches",
        route_class=_IngestionRoute,
        dependencies=[Depends(_private)],
    )

    def capabilities() -> dict:
        try:
            available = policy() is True
        except Exception:
            available = False
        return {
            "execution_available": available,
            "reason": None if available else "ingestion_execution_disabled",
            "model_calls": False,
            "min_interval_seconds": MIN_INTERVAL_SECONDS,
            "max_interval_seconds": MAX_INTERVAL_SECONDS,
            "watch_limit": 10,
            "source_limit_per_watch": 2000,
            "coverage": "bounded_provider_windows",
        }

    def scope(request: Request, *, write=False, collect=False) -> str:
        if write:
            _authorize_write(request)
        with Session(engine) as session:
            owner_hash = _authenticated(session, request).token_hash
        if collect and not capabilities()["execution_available"]:
            raise _IngestionHTTPException(503, "ingestion_execution_disabled")
        return owner_hash

    @router.get("/capabilities")
    def get_capabilities() -> dict:
        return capabilities()

    @router.post("", status_code=201)
    def create_watch(body: CreateWatch, request: Request) -> dict:
        try:
            owner_hash = scope(request, write=True, collect=True)
            return repository.create(
                owner_hash,
                label=body.label,
                query=body.query,
                interval_seconds=body.interval_seconds,
                source_result_id=uuid.UUID(body.source_result_id)
                if body.source_result_id
                else None,
            ).to_response()
        except IngestionError as error:
            raise _ingestion_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("")
    def list_watches(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        before: uuid.UUID | None = None,
    ) -> dict:
        try:
            records = repository.list(scope(request), limit=limit + 1, before=before)
            return {
                "watches": [record.to_response() for record in records[:limit]],
                "next_cursor": str(records[limit - 1].id) if len(records) > limit else None,
            }
        except IngestionError as error:
            raise _ingestion_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/{watch_id}")
    def get_watch(watch_id: uuid.UUID, request: Request) -> dict:
        try:
            return repository.get(scope(request), watch_id).to_response()
        except IngestionError as error:
            raise _ingestion_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.post("/{watch_id}/pause")
    def pause_watch(watch_id: uuid.UUID, request: Request) -> dict:
        try:
            return repository.pause(scope(request, write=True), watch_id).to_response()
        except IngestionError as error:
            raise _ingestion_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.post("/{watch_id}/resume")
    def resume_watch(watch_id: uuid.UUID, request: Request) -> dict:
        try:
            return repository.resume(
                scope(request, write=True, collect=True), watch_id
            ).to_response()
        except IngestionError as error:
            raise _ingestion_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.post("/{watch_id}/check", status_code=201)
    def check_watch(watch_id: uuid.UUID, request: Request) -> dict:
        try:
            return repository.run_now(
                scope(request, write=True, collect=True), watch_id
            ).to_response()
        except IngestionError as error:
            raise _ingestion_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/{watch_id}/sources")
    def list_sources(
        watch_id: uuid.UUID,
        request: Request,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        before: uuid.UUID | None = None,
    ) -> dict:
        try:
            records = repository.list_sources(
                scope(request), watch_id, limit=limit + 1, before=before
            )
            return {
                "sources": [record.to_response() for record in records[:limit]],
                "next_cursor": str(records[limit - 1].id) if len(records) > limit else None,
            }
        except IngestionError as error:
            raise _ingestion_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/{watch_id}/ticks")
    def list_ticks(
        watch_id: uuid.UUID,
        request: Request,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        before: uuid.UUID | None = None,
    ) -> dict:
        try:
            records = repository.list_ticks(
                scope(request), watch_id, limit=limit + 1, before=before
            )
            return {
                "ticks": [record.to_response() for record in records[:limit]],
                "next_cursor": str(records[limit - 1].id) if len(records) > limit else None,
            }
        except IngestionError as error:
            raise _ingestion_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/{watch_id}/sources/{source_id}")
    def source_document(watch_id: uuid.UUID, source_id: uuid.UUID, request: Request) -> dict:
        try:
            return repository.get_source_document(scope(request), watch_id, source_id)
        except IngestionError as error:
            raise _ingestion_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    return router
