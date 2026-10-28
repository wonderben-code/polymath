"""Explicit durable visitor sponsorship and an owned, read-only access projection.

The ordinary access endpoint never creates a browser session or operator grant.
Administration delegates live role and both-CSRF authorization to the store.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Engine
from starlette.exceptions import HTTPException

from polymath.discovery_contracts import DiscoveryIntent
from polymath.explore_collection_contracts import ExploreCollectionIntent
from polymath.meta_probe_contracts import ProbeId, ProviderAccountAttestation
from polymath.meta_readiness_contracts import ReadinessPolicy, _utc
from polymath.meta_research_api import HEADERS, _response
from polymath.meta_research_contracts import (
    NativeResearchPolicy,
    NativeResearchReview,
    ScientificContextIntent,
)
from polymath.meta_research_errors import META_RESEARCH_ERROR_STATUSES, MetaResearchError
from polymath.operator_auth import OperatorPolicy
from polymath.public_research_contracts import (
    PublicLimits,
    PublicResearchManifest,
    PublicResearchPolicy,
)
from polymath.public_research_quota import PUBLIC_QUOTA_ERROR_STATUSES, PublicQuotaError
from polymath.public_resource_controls import PublicResourceError

PREFIX = "/api/v1/budget/research/public"
RESOURCE_ERRORS = {
    "public_resource_controls_unavailable": 503,
    "public_request_rate_limited": 429,
    "public_stream_limit": 429,
    "public_stream_expired": 429,
    "public_request_body_too_large": 413,
    "public_request_body_timeout": 408,
    "invalid_public_request": 400,
    "research_session_required": 401,
}


def _error(status: int, detail: str, retry=None):
    headers = dict(HEADERS)
    if type(retry) is int and 0 <= retry <= 86400:
        headers["Retry-After"] = str(retry)
    return HTTPException(status, detail, headers=headers)


class _PublicResearchRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handle(request: Request):
            try:
                response = await original(request)
                response.headers.update(HEADERS)
                return response
            except RequestValidationError:
                raise _error(422, "meta_research_invalid_request") from None
            except HTTPException as caught:
                caught.headers = {**(caught.headers or {}), **HEADERS}
                raise
            except PublicQuotaError as caught:
                code = caught.code
                if code in PUBLIC_QUOTA_ERROR_STATUSES:
                    raise _error(
                        PUBLIC_QUOTA_ERROR_STATUSES[code], code, caught.retry_after_seconds
                    ) from None
                raise _error(503, "meta_research_storage_unavailable") from None
            except PublicResourceError as caught:
                if caught.code in RESOURCE_ERRORS:
                    raise _error(
                        RESOURCE_ERRORS[caught.code], caught.code, caught.retry_after_seconds
                    ) from None
                raise _error(503, "public_resource_controls_unavailable") from None
            except Exception as caught:
                from polymath.meta_readiness import ReadinessError

                code = (
                    caught.code
                    if isinstance(caught, MetaResearchError)
                    else (str(caught) if isinstance(caught, ReadinessError) else "")
                )
                if code in META_RESEARCH_ERROR_STATUSES:
                    raise _error(META_RESEARCH_ERROR_STATUSES[code], code) from None
                raise _error(503, "meta_research_storage_unavailable") from None

        return handle

    async def handle(self, scope, receive, send):
        try:
            await super().handle(scope, receive, send)
        except HTTPException as caught:
            caught.headers = {**(caught.headers or {}), **HEADERS}
            raise


class PreviewPublicResearch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    readiness_id: ProbeId
    canary_receipt_id: ProbeId
    review: NativeResearchReview
    provider_account_attestation: ProviderAccountAttestation
    public_limits: PublicLimits
    valid_until: datetime
    discovery: DiscoveryIntent | None = None
    scientific_context: ScientificContextIntent | None = None
    explore_collection: ExploreCollectionIntent | None = None

    @field_validator("explore_collection", mode="before")
    @classmethod
    def explicit_explore_marker_is_not_null(cls, value):
        if value is None:
            raise ValueError("explore_collection_intent_invalid")
        return value

    @field_validator("valid_until", mode="before")
    @classmethod
    def utc_deadline(cls, value):
        return _utc(value)


class ActivatePublicResearch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    manifest: PublicResearchManifest
    expected_manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$", min_length=64, max_length=64)


class PublicResearchControl(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


def _uuid(value):
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise _error(422, "meta_research_invalid_request") from None


def create_public_research_router(
    engine: Engine,
    *,
    native_store=None,
    research_policy: NativeResearchPolicy | None = None,
    readiness_policy: ReadinessPolicy | None = None,
    operator_policy: OperatorPolicy | None = None,
    public_policy: PublicResearchPolicy | None = None,
    resource_controls=None,
) -> APIRouter:
    if engine.dialect.name != "postgresql":
        raise ValueError("Public research administration requires PostgreSQL")
    research = research_policy or NativeResearchPolicy()
    readiness = readiness_policy or ReadinessPolicy()
    public = public_policy or PublicResearchPolicy()
    router = APIRouter(route_class=_PublicResearchRoute)

    def parent():
        if native_store is not None:
            return native_store
        from polymath.meta_research import MetaResearchStore

        return MetaResearchStore(
            engine,
            research_policy=research,
            readiness_policy=readiness,
            operator_policy=operator_policy,
            public_policy=public,
            resource_controls=resource_controls,
        )

    @router.get("/api/v1/research/access")
    def access(request: Request):
        return _response(parent().public_service.access(request))

    @router.get("/api/v1/research/protocols")
    def protocols(request: Request):
        return _response(parent().public_service.protocols(request))

    @router.get("/api/v1/research/explore-protocols")
    def explore_protocols(request: Request):
        return _response(parent().public_service.explore_protocols(request))

    @router.get(PREFIX + "/capabilities")
    def capabilities():
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
            and public.enabled
        )
        return {
            "activation_available": available,
            "inspection_available": role,
            "revoke_available": role,
            "audience": "public_sessions",
            "reason": None if available else "public_research_unavailable",
        }

    @router.post(PREFIX + "/preview")
    def preview(body: PreviewPublicResearch, request: Request):
        return _response(
            parent().public_preview(
                request,
                _uuid(body.readiness_id),
                _uuid(body.canary_receipt_id),
                body.review,
                body.provider_account_attestation,
                body.public_limits,
                body.valid_until,
                **({"discovery": body.discovery} if body.discovery is not None else {}),
                **(
                    {"scientific_context": body.scientific_context}
                    if body.scientific_context is not None
                    else {}
                ),
                **(
                    {"explore_collection": body.explore_collection}
                    if body.explore_collection is not None
                    else {}
                ),
            )
        )

    @router.post(PREFIX + "/activate", status_code=201)
    def activate(body: ActivatePublicResearch, request: Request):
        return _response(
            parent().public_activate(request, body.manifest, body.expected_manifest_sha256)
        )

    @router.get(PREFIX + "/current")
    def current(request: Request):
        return _response(parent().public_delegations.current(request))

    @router.get(PREFIX + "/activations")
    def activations(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=20)] = 20,
        before: Annotated[str | None, Query(min_length=36, max_length=36)] = None,
    ):
        rows = parent().public_delegations.list_activations(
            request, limit=limit + 1, before=_uuid(before) if before is not None else None
        )
        selected = [_response(row) for row in rows[:limit]]
        return {
            "activations": selected,
            "next_cursor": selected[-1]["id"] if len(rows) > limit else None,
        }

    @router.get(PREFIX + "/activations/{activation_id}")
    def activation(
        activation_id: Annotated[str, Path(min_length=36, max_length=36)], request: Request
    ):
        return _response(parent().public_delegations.get_activation(request, _uuid(activation_id)))

    @router.post(PREFIX + "/activations/{activation_id}/revoke")
    def revoke(
        activation_id: Annotated[str, Path(min_length=36, max_length=36)],
        body: PublicResearchControl,
        request: Request,
    ):
        return _response(parent().public_delegations.revoke(request, _uuid(activation_id)))

    return router
