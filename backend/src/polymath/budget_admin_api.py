"""Operator-only readiness preparation, ledger inspection, and safe disable.

This surface cannot activate execution, verify pricing, probe Meta, construct a
model client, or reconcile/reset a reservation. Preparation is not authority to
spend. The store reauthorizes the live human role inside its own transactions.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from starlette.exceptions import HTTPException

from polymath.meta_readiness import MetaReadinessStore, ReadinessError
from polymath.meta_readiness_contracts import MetaReadinessProposal, ReadinessPolicy
from polymath.operator_auth import OperatorPolicy

HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}


class _BudgetAdminHTTPException(HTTPException):
    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code, detail, headers=dict(HEADERS))


def _readiness_error(error: ReadinessError) -> _BudgetAdminHTTPException:
    code = str(error)
    status = {
        "readiness_role_unavailable": 503,
        "readiness_actor_forbidden": 403,
        "readiness_preparation_disabled": 503,
        "readiness_account_not_found": 404,
        "readiness_proposal_not_found": 404,
        "readiness_account_enabled": 409,
        "readiness_account_conflict": 409,
        "readiness_proposal_changed": 409,
        "readiness_invalid_request": 422,
        "readiness_storage_unavailable": 503,
    }.get(code)
    return (
        _BudgetAdminHTTPException(status, code)
        if status
        else _BudgetAdminHTTPException(503, "readiness_storage_unavailable")
    )


class _BudgetAdminRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                response = await original(request)
                response.headers.update(HEADERS)
                return response
            except RequestValidationError:
                raise _BudgetAdminHTTPException(422, "readiness_invalid_request") from None
            except ReadinessError as error:
                raise _readiness_error(error) from None
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **HEADERS}
                raise
            except Exception:
                raise _BudgetAdminHTTPException(503, "readiness_storage_unavailable") from None

        return handle

    async def handle(self, scope, receive, send) -> None:
        try:
            await super().handle(scope, receive, send)
        except HTTPException as error:
            error.headers = {**(error.headers or {}), **HEADERS}
            raise


class PreviewReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    proposal: MetaReadinessProposal


class SaveReadiness(PreviewReadiness):
    expected_proposal_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")


class DisableAccount(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


def _uuid(value: str) -> uuid.UUID:
    try:
        identifier = uuid.UUID(value)
        if str(identifier) != value:
            raise ValueError
        return identifier
    except (ValueError, TypeError, AttributeError):
        raise _BudgetAdminHTTPException(422, "readiness_invalid_request") from None


def _account_id(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _BudgetAdminHTTPException(422, "readiness_invalid_request") from None
    if not 1 <= len(value) <= 120 or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise _BudgetAdminHTTPException(422, "readiness_invalid_request")
    return value


def _response(value) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_response"):
        return value.to_response()
    return value.model_dump(mode="json")


def create_budget_admin_router(
    engine: Engine,
    *,
    readiness_policy: ReadinessPolicy | None = None,
    operator_policy: OperatorPolicy | None = None,
) -> APIRouter:
    if engine.dialect.name != "postgresql":
        raise ValueError("Budget administration requires PostgreSQL")
    policy = readiness_policy or ReadinessPolicy()
    store = MetaReadinessStore(engine, readiness_policy=policy, operator_policy=operator_policy)
    router = APIRouter(prefix="/api/v1/budget", route_class=_BudgetAdminRoute)

    @router.get("/capabilities")
    def capabilities() -> dict:
        authorized = bool(
            isinstance(operator_policy, OperatorPolicy)
            and operator_policy.configured
            and operator_policy.actor_id in policy.allowed_actor_ids
        )
        preparation = bool(authorized and policy.preparation_enabled)
        return {
            "preparation_available": preparation,
            "inspection_available": authorized,
            "disable_available": authorized,
            "native_status": "not_run",
            "execution_available": False,
            "reason": None
            if preparation
            else ("preparation_disabled" if authorized else "readiness_unavailable"),
        }

    @router.post("/readiness/preview")
    def preview(body: PreviewReadiness, request: Request) -> dict:
        return _response(store.preview(request, body.proposal))

    @router.post("/readiness", status_code=201)
    def save(body: SaveReadiness, request: Request) -> dict:
        return _response(store.save(request, body.proposal, body.expected_proposal_sha256))

    @router.get("/readiness")
    def list_proposals(
        request: Request,
        account_id: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        before: Annotated[str | None, Query(min_length=36, max_length=36)] = None,
    ) -> dict:
        rows = store.list_proposals(
            request,
            account_id=_account_id(account_id) if account_id is not None else None,
            limit=limit + 1,
            before=_uuid(before) if before is not None else None,
        )
        selected = [_response(row) for row in rows[:limit]]
        return {
            "proposals": selected,
            "next_cursor": selected[-1]["id"] if len(rows) > limit else None,
        }

    @router.get("/readiness/{proposal_id}")
    def get_proposal(
        proposal_id: Annotated[str, Path(min_length=36, max_length=36)], request: Request
    ) -> dict:
        return _response(store.get_proposal(request, _uuid(proposal_id)))

    @router.get("/accounts")
    def accounts(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        before: Annotated[str | None, Query(min_length=1, max_length=120)] = None,
    ) -> dict:
        rows = store.list_accounts(
            request, limit=limit + 1, before=_account_id(before) if before is not None else None
        )
        return {
            "accounts": [_response(row) for row in rows[:limit]],
            "next_cursor": _response(rows[limit - 1])["account_id"] if len(rows) > limit else None,
        }

    @router.get("/accounts/{account_id:path}")
    def inspect_account(
        account_id: Annotated[str, Path(min_length=1, max_length=120)], request: Request
    ) -> dict:
        return _response(store.inspect_account(request, _account_id(account_id)))

    @router.post("/accounts/{account_id:path}/disable")
    def disable(
        account_id: Annotated[str, Path(min_length=1, max_length=120)],
        body: DisableAccount,
        request: Request,
    ) -> dict:
        return _response(store.safe_disable(request, _account_id(account_id)))

    return router
