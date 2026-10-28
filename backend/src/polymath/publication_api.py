"""Exact document previews and human approval receipts; no publishing transport.

Preparing a manifest performs local database work only. Approving or revoking
requires both the owning browser session and a separate operator session.
Neither operation queues a deposit, calls Zenodo, or grants a model new tools.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.operator_auth import OperatorPolicy, require_operator
from polymath.publication import PublicationError, PublicationStore
from polymath.publication_contracts import PublicationMetadata, PublicationPolicy
from polymath.run_api import _authenticated, _authorize_write, _PrivateHTTPException


class _PublicationHTTPException(_PrivateHTTPException):
    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code, detail)
        self.headers["X-Content-Type-Options"] = "nosniff"


class _PublicationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                return await original(request)
            except RequestValidationError:
                raise _PublicationHTTPException(422, "invalid_publication_request") from None
            except HTTPException as error:
                error.headers = {
                    **(error.headers or {}),
                    "Cache-Control": "no-store",
                    "X-Content-Type-Options": "nosniff",
                }
                raise

        return handle


class PreparePublication(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    artifact_id: str = Field(min_length=36, max_length=36)
    metadata: PublicationMetadata
    destination: Literal["sandbox", "production"]

    @model_validator(mode="after")
    def exact_artifact(self) -> PreparePublication:
        if str(uuid.UUID(self.artifact_id)) != self.artifact_id:
            raise ValueError("An exact saved document is required")
        return self


class ApprovePublication(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    expected_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


def _private(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"


def _storage_error() -> _PrivateHTTPException:
    return _PublicationHTTPException(503, "publication_storage_unavailable")


def _publication_error(error: PublicationError) -> _PrivateHTTPException:
    code = str(error)
    if code == "publication_unavailable":
        return _PublicationHTTPException(503, code)
    if code in ("publication_not_found", "publication_approval_not_found"):
        return _PublicationHTTPException(404, "publication_not_found")
    if code in (
        "invalid_publication_manifest",
        "publication_manifest_changed",
        "publication_source_not_current",
    ):
        return _PublicationHTTPException(409, code)
    if code == "operator_session_required":
        return _PublicationHTTPException(401, code)
    if code == "publication_approval_limit":
        return _PublicationHTTPException(429, code)
    return _storage_error()


def create_publication_router(
    engine: Engine,
    *,
    policy: PublicationPolicy | None = None,
    operator_policy: OperatorPolicy | None = None,
    status_resolver: Callable[[str, uuid.UUID], str] | None = None,
) -> APIRouter:
    if engine.dialect.name != "postgresql":
        raise ValueError("Publication routes require PostgreSQL")
    store = PublicationStore(engine, policy, operator_policy=operator_policy)
    router = APIRouter(
        prefix="/api/v1/publications",
        route_class=_PublicationRoute,
        dependencies=[Depends(_private)],
    )

    def public_record(owner_hash: str, record) -> dict:
        response = record.to_response()
        if status_resolver is not None:
            try:
                status = status_resolver(owner_hash, record.id)
            except HTTPException:
                raise
            except Exception:
                raise _storage_error() from None
            if status not in ("not_published", "unknown", "published"):
                raise _storage_error()
            response["publication_status"] = status
        return response

    @router.get("/capabilities")
    def capabilities() -> dict:
        return store.capabilities()

    @router.get("")
    def list_manifests(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        before: uuid.UUID | None = None,
        artifact_id: uuid.UUID | None = None,
    ) -> dict:
        try:
            with Session(engine) as session:
                owner_hash = _authenticated(session, request).token_hash
            records = store.list(
                owner_hash, limit=limit + 1, before=before, artifact_id=artifact_id
            )
            return {
                "manifests": [public_record(owner_hash, record) for record in records[:limit]],
                "next_cursor": str(records[limit - 1].id) if len(records) > limit else None,
            }
        except PublicationError as error:
            raise _publication_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.post("", status_code=201)
    def prepare(body: PreparePublication, request: Request) -> dict:
        _authorize_write(request)
        try:
            with Session(engine) as session:
                owner_hash = _authenticated(session, request).token_hash
            record = store.prepare(
                owner_hash, uuid.UUID(body.artifact_id), body.metadata, body.destination
            )
            return public_record(owner_hash, record)
        except PublicationError as error:
            raise _publication_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.get("/{manifest_id}")
    def get_manifest(manifest_id: uuid.UUID, request: Request) -> dict:
        try:
            with Session(engine) as session:
                owner_hash = _authenticated(session, request).token_hash
            return public_record(owner_hash, store.get(owner_hash, manifest_id))
        except PublicationError as error:
            raise _publication_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.post("/{manifest_id}/approve")
    def approve(manifest_id: uuid.UUID, body: ApprovePublication, request: Request) -> dict:
        _authorize_write(request)
        try:
            with Session(engine) as session:
                owner_hash = _authenticated(session, request).token_hash
                operator = require_operator(session, request, write=True, policy=operator_policy)
            # Release the HTTP authentication lock before the store opens its
            # own transaction and independently locks/revalidates the session.
            store.approve(owner_hash, manifest_id, body.expected_manifest_sha256, operator)
            return public_record(owner_hash, store.get(owner_hash, manifest_id))
        except PublicationError as error:
            raise _publication_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    @router.post("/approvals/{approval_id}/revoke")
    def revoke(approval_id: uuid.UUID, request: Request) -> dict:
        _authorize_write(request)
        try:
            with Session(engine) as session:
                owner_hash = _authenticated(session, request).token_hash
                operator = require_operator(session, request, write=True, policy=operator_policy)
            receipt = store.revoke(owner_hash, approval_id, operator)
            return public_record(owner_hash, store.get(owner_hash, receipt.manifest_id))
        except PublicationError as error:
            raise _publication_error(error) from None
        except SQLAlchemyError:
            raise _storage_error() from None

    return router
