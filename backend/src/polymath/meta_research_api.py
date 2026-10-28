"""Exact operator/browser native research activation and private inspection.

The HTTP surface never constructs a model, dispatches a provider request,
or settles caller-supplied usage. Store methods reauthorize
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

from polymath.discovery_contracts import DiscoveryIntent
from polymath.meta_probe_contracts import ProbeId, ProviderAccountAttestation
from polymath.meta_readiness_contracts import ReadinessPolicy
from polymath.meta_research_contracts import (
    MetaResearchManifest,
    NativeResearchPolicy,
    NativeResearchReview,
)
from polymath.meta_research_errors import META_RESEARCH_ERROR_STATUSES
from polymath.operator_auth import OperatorPolicy

HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}
ERRORS = META_RESEARCH_ERROR_STATUSES


class _ResearchHTTPException(HTTPException):
    def __init__(self, status_code: int, detail: str):
        super().__init__(status_code, detail, headers=dict(HEADERS))


class _ResearchRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                response = await original(request)
                response.headers.update(HEADERS)
                return response
            except RequestValidationError:
                raise _ResearchHTTPException(422, "meta_research_invalid_request") from None
            except HTTPException as error:
                error.headers = {**(error.headers or {}), **HEADERS}
                raise
            except Exception as error:
                from polymath.meta_readiness import ReadinessError
                from polymath.meta_research_errors import MetaResearchError

                code = str(error) if isinstance(error, (MetaResearchError, ReadinessError)) else ""
                if code in ERRORS:
                    raise _ResearchHTTPException(ERRORS[code], code) from None
                raise _ResearchHTTPException(503, "meta_research_storage_unavailable") from None

        return handle

    async def handle(self, scope, receive, send) -> None:
        try:
            await super().handle(scope, receive, send)
        except HTTPException as error:
            error.headers = {**(error.headers or {}), **HEADERS}
            raise


class PreviewResearch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    readiness_id: ProbeId
    canary_receipt_id: ProbeId
    review: NativeResearchReview
    provider_account_attestation: ProviderAccountAttestation
    discovery: DiscoveryIntent | None = None


class ActivateResearch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    manifest: MetaResearchManifest
    expected_manifest_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")


class ResearchControl(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


def _uuid(value: str) -> uuid.UUID:
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise _ResearchHTTPException(422, "meta_research_invalid_request") from None


def _response(value) -> dict:
    if isinstance(value, dict):
        return value
    if hasattr(value, "to_response"):
        return value.to_response()
    return value.model_dump(mode="json")


def create_meta_research_router(
    engine: Engine,
    *,
    research_policy: NativeResearchPolicy | None = None,
    readiness_policy: ReadinessPolicy | None = None,
    operator_policy: OperatorPolicy | None = None,
) -> APIRouter:
    if engine.dialect.name != "postgresql":
        raise ValueError("Native research administration requires PostgreSQL")
    research = research_policy or NativeResearchPolicy()
    readiness = readiness_policy or ReadinessPolicy()
    router = APIRouter(prefix="/api/v1/budget/research", route_class=_ResearchRoute)

    def store():
        from polymath.meta_research import MetaResearchStore

        return MetaResearchStore(
            engine,
            research_policy=research,
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
            role
            and readiness.preparation_enabled
            and research.paid_research_enabled
            and research.configured
        )
        return {
            "activation_available": available,
            "inspection_available": role,
            "revoke_available": role,
            "audience": "operator_browser",
            "public_research_available": False,
            "reason": None if available else "meta_research_unavailable",
        }

    @router.post("/preview")
    def preview(body: PreviewResearch, request: Request) -> dict:
        return _response(
            store().preview(
                request,
                _uuid(body.readiness_id),
                _uuid(body.canary_receipt_id),
                body.review,
                body.provider_account_attestation,
                **({"discovery": body.discovery} if body.discovery is not None else {}),
            )
        )

    @router.post("/activations", status_code=201)
    def activate(body: ActivateResearch, request: Request) -> dict:
        return _response(store().activate(request, body.manifest, body.expected_manifest_sha256))

    @router.get("/current")
    def current(request: Request) -> dict:
        return _response(store().current(request))

    @router.get("/activations")
    def activations(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        before: Annotated[str | None, Query(min_length=36, max_length=36)] = None,
    ) -> dict:
        rows = store().list_activations(
            request, limit=limit + 1, before=_uuid(before) if before is not None else None
        )
        selected = [_response(row) for row in rows[:limit]]
        return {
            "activations": selected,
            "next_cursor": selected[-1]["id"] if len(rows) > limit else None,
        }

    @router.get("/activations/{activation_id}")
    def activation(
        activation_id: Annotated[str, Path(min_length=36, max_length=36)], request: Request
    ) -> dict:
        return _response(store().get_activation(request, _uuid(activation_id)))

    @router.post("/activations/{activation_id}/revoke")
    def revoke(
        activation_id: Annotated[str, Path(min_length=36, max_length=36)],
        body: ResearchControl,
        request: Request,
    ) -> dict:
        return _response(store().revoke(request, _uuid(activation_id)))

    return router
