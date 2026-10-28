"""Explicit operator approval and private inspection of one-call verification.

The HTTP surface never constructs a model, dispatches a provider request,
activates research, or settles caller-supplied usage. Store methods reauthorize
the live operator inside the transactions that grant or revoke authority.
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

from polymath.meta_probe_contracts import (
    MetaProbeManifest,
    MetaProbePolicy,
    ProbeId,
    ProviderAccountAttestation,
)
from polymath.meta_readiness_contracts import ReadinessPolicy
from polymath.operator_auth import OperatorPolicy

HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}
ERRORS = {
    "meta_probe_unavailable": 503,
    "meta_probe_actor_forbidden": 403,
    "meta_probe_not_found": 404,
    "meta_probe_invalid_request": 422,
    "meta_probe_manifest_changed": 409,
    "meta_probe_account_conflict": 409,
    "meta_probe_prior_unresolved": 409,
    "meta_probe_authority_expired": 409,
    "meta_probe_outcome_unknown": 409,
    "meta_probe_storage_unavailable": 503,
    "readiness_role_unavailable": 503,
    "readiness_actor_forbidden": 403,
}


class _ProbeHTTPException(HTTPException):
    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code, detail, headers=dict(HEADERS))


class _ProbeRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                response = await original(request)
                response.headers.update(HEADERS)
                return response
            except RequestValidationError:
                raise _ProbeHTTPException(422, "meta_probe_invalid_request") from None
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **HEADERS}
                raise
            except Exception as error:
                from polymath.meta_probes import MetaProbeError
                from polymath.meta_readiness import ReadinessError

                code = str(error) if isinstance(error, (MetaProbeError, ReadinessError)) else ""
                if code in ERRORS:
                    raise _ProbeHTTPException(ERRORS[code], code) from None
                raise _ProbeHTTPException(503, "meta_probe_storage_unavailable") from None

        return handle

    async def handle(self, scope, receive, send) -> None:
        try:
            await super().handle(scope, receive, send)
        except HTTPException as error:
            error.headers = {**(error.headers or {}), **HEADERS}
            raise


class PreviewProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    readiness_id: ProbeId
    provider_account_attestation: ProviderAccountAttestation


class ArmProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    manifest: MetaProbeManifest
    expected_manifest_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")


class ProbeControl(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


def _uuid(value: str) -> uuid.UUID:
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise _ProbeHTTPException(422, "meta_probe_invalid_request") from None


def _response(value) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_response"):
        return value.to_response()
    return value.model_dump(mode="json")


def create_meta_probe_router(
    engine: Engine,
    *,
    probe_policy: MetaProbePolicy | None = None,
    readiness_policy: ReadinessPolicy | None = None,
    operator_policy: OperatorPolicy | None = None,
) -> APIRouter:
    if engine.dialect.name != "postgresql":
        raise ValueError("Native verification administration requires PostgreSQL")
    probe = probe_policy or MetaProbePolicy()
    readiness = readiness_policy or ReadinessPolicy()
    router = APIRouter(prefix="/api/v1/budget/verification", route_class=_ProbeRoute)

    def store():
        from polymath.meta_probes import MetaProbeStore

        return MetaProbeStore(
            engine,
            probe_policy=probe,
            readiness_policy=readiness,
            operator_policy=operator_policy,
        )

    @router.get("/capabilities")
    def capabilities() -> dict:
        role = bool(
            isinstance(operator_policy, OperatorPolicy)
            and operator_policy.configured
            and operator_policy.actor_id in readiness.allowed_actor_ids
        )
        available = bool(
            role and readiness.preparation_enabled and probe.paid_probe_enabled and probe.configured
        )
        return {
            "verification_available": available,
            "inspection_available": role,
            "cancel_available": role,
            "finalization_available": role,
            "research_execution_available": False,
            "reason": None if available else "meta_probe_unavailable",
        }

    @router.post("/preview")
    def preview(body: PreviewProbe, request: Request) -> dict:
        return _response(
            store().preview(request, _uuid(body.readiness_id), body.provider_account_attestation)
        )

    @router.post("/jobs", status_code=201)
    def arm(body: ArmProbe, request: Request) -> dict:
        return _response(
            store().arm_and_enqueue(request, body.manifest, body.expected_manifest_sha256)
        )

    @router.get("/jobs")
    def jobs(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        before: Annotated[str | None, Query(min_length=36, max_length=36)] = None,
    ) -> dict:
        rows = store().list_jobs(
            request, limit=limit + 1, before=_uuid(before) if before is not None else None
        )
        selected = [_response(row) for row in rows[:limit]]
        return {
            "jobs": selected,
            "next_cursor": selected[-1]["id"] if len(rows) > limit else None,
        }

    @router.get("/jobs/{run_id}")
    def job(run_id: Annotated[str, Path(min_length=36, max_length=36)], request: Request) -> dict:
        return _response(store().get_job(request, _uuid(run_id)))

    @router.post("/jobs/{run_id}/cancel")
    def cancel(
        run_id: Annotated[str, Path(min_length=36, max_length=36)],
        body: ProbeControl,
        request: Request,
    ) -> dict:
        return _response(store().cancel(request, _uuid(run_id)))

    @router.post("/jobs/{run_id}/finalize")
    def finalize(
        run_id: Annotated[str, Path(min_length=36, max_length=36)],
        body: ProbeControl,
        request: Request,
    ) -> dict:
        return _response(store().finalize(request, _uuid(run_id)))

    @router.get("/receipts/{receipt_id}")
    def receipt(
        receipt_id: Annotated[str, Path(min_length=36, max_length=36)], request: Request
    ) -> dict:
        return _response(store().get_receipt(request, _uuid(receipt_id)))

    return router
