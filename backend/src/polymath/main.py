"""HTTP application: honest health/capability checks and optional built web assets."""

import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath import __version__
from polymath.artifact_api import create_artifact_router
from polymath.assessment_api import create_assessment_router
from polymath.budget_admin_api import create_budget_admin_router
from polymath.budget_models import BudgetAccount
from polymath.config import Settings, get_settings
from polymath.converge_api import create_converge_router
from polymath.database import build_engine
from polymath.discovery_api import create_discovery_router
from polymath.explore_collection_api import create_explore_collection_router
from polymath.frontier_api import create_frontier_router
from polymath.ingestion_api import create_ingestion_router
from polymath.knowledge import create_knowledge_router
from polymath.meta_probe_api import create_meta_probe_router
from polymath.meta_probe_contracts import MetaProbePolicy
from polymath.meta_readiness_contracts import ReadinessPolicy
from polymath.meta_research import MetaResearchStore
from polymath.meta_research_api import create_meta_research_router
from polymath.meta_research_contracts import NativeResearchPolicy
from polymath.meta_research_models import MetaResearchManifestRecord
from polymath.operator_auth import OperatorPolicy, create_operator_router
from polymath.public_research_api import create_public_research_router
from polymath.public_research_contracts import (
    MAX_PUBLIC_MANIFEST_BYTES,
    PUBLIC_RESEARCH_PROTOCOL,
    PublicResearchManifest,
    PublicResearchPolicy,
    PublicResourceLimits,
)
from polymath.public_resource_controls import (
    PublicResourceControls,
    PublicResourcePolicy,
    derive_network_key,
)
from polymath.public_resource_middleware import PublicResourceMiddleware
from polymath.publication import PublicationStore
from polymath.publication_api import create_publication_router
from polymath.publication_contracts import PublicationPolicy
from polymath.publication_job_api import create_publication_job_router
from polymath.publication_jobs import PublicationJobRepository
from polymath.publication_runtime import (
    configured_publication_policies,
    publication_execution_available,
)
from polymath.publishing_api import create_publishing_router
from polymath.run_api import create_run_router
from polymath.sharing_api import create_sharing_router
from polymath.visitor_zenodo import VisitorZenodoPolicy
from polymath.visitor_zenodo_api import create_visitor_zenodo_router


def migration_heads() -> set[str]:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    return set(ScriptDirectory.from_config(config).get_heads())


def _reviewed_public_resources(
    session: Session, account_id: str | None
) -> PublicResourceLimits | None:
    """Read historical resource bounds, without evaluating spending authority.

    Expiry, logout, key removal and a changed scientific prompt cannot prevent
    reading saved work or stopping it. Invalid/unavailable manifests leave the
    independent server resource ceilings in place; financial grants remain the
    responsibility of the native store. This helper obtains no locks or grants.
    """
    if account_id is None:
        return None
    row = session.scalar(
        select(MetaResearchManifestRecord)
        .join(
            BudgetAccount,
            BudgetAccount.native_manifest_sha256 == MetaResearchManifestRecord.manifest_sha256,
        )
        .where(
            BudgetAccount.id == account_id,
            MetaResearchManifestRecord.account_id == account_id,
        )
    )
    if row is None:
        return None
    try:
        if (
            not isinstance(row.manifest, dict)
            or row.manifest.get("protocol") != PUBLIC_RESEARCH_PROTOCOL
        ):
            return None
        raw = json.dumps(
            row.manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode("utf-8")
        if (
            len(raw) > MAX_PUBLIC_MANIFEST_BYTES
            or hashlib.sha256(raw).hexdigest() != row.manifest_sha256
        ):
            return None
        historical = PublicResearchManifest.model_validate_json(raw)
        if historical.account_id != account_id:
            return None
        return historical.public_limits.resources
    except (ValueError, TypeError, AttributeError):
        return None


def create_app(
    settings: Settings | None = None,
    *,
    research_availability: Callable[[], bool] | None = None,
    operator_policy: OperatorPolicy | None = None,
    publication_policy: PublicationPolicy | None = None,
    publication_availability: Callable[[], bool] | None = None,
    ingestion_availability: Callable[[], bool] | None = None,
    sharing_availability: Callable[[], bool] | None = None,
    readiness_policy: ReadinessPolicy | None = None,
    probe_policy: MetaProbePolicy | None = None,
    research_policy: NativeResearchPolicy | None = None,
    public_policy: PublicResearchPolicy | None = None,
    resource_controls: PublicResourceControls | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    native_required = research_policy is not None or research_availability is None
    configured_operator, configured_publication = configured_publication_policies(settings)
    if operator_policy is None:
        operator_policy = configured_operator
    if publication_policy is None:
        publication_policy = configured_publication
    if readiness_policy is None:
        readiness_policy = ReadinessPolicy(
            preparation_enabled=settings.budget_readiness_enabled,
            allowed_actor_ids=settings.budget_admin_actor_ids,
            max_hard_cap_microdollars=settings.budget_max_hard_cap_microdollars,
        )
    if probe_policy is None:
        probe_policy = MetaProbePolicy(
            paid_probe_enabled=settings.paid_meta_probe_enabled,
            native_account_id=settings.native_budget_account_id,
            model_id=settings.meta_model,
            api_key=settings.model_api_key,
        )
    if research_policy is None:
        research_policy = NativeResearchPolicy(
            paid_research_enabled=settings.paid_meta_research_enabled,
            native_account_id=settings.native_budget_account_id,
            model_id=settings.meta_model,
            api_key=settings.model_api_key,
        )
    if public_policy is None:
        public_policy = PublicResearchPolicy(
            enabled=settings.public_meta_research_enabled,
            max_delegation_seconds=settings.public_max_delegation_seconds,
        )
    if research_availability is None:

        def research_availability() -> bool:
            return bool(
                research_policy.paid_research_enabled
                and research_policy.configured
                and readiness_policy.preparation_enabled
                and operator_policy.configured
                and operator_policy.actor_id in readiness_policy.allowed_actor_ids
            )

    if publication_availability is None:

        def publication_availability() -> bool:
            return publication_execution_available(settings)

    if ingestion_availability is None:

        def ingestion_availability() -> bool:
            return settings.literature_ingestion_enabled

    if sharing_availability is None:

        def sharing_availability() -> bool:
            return settings.public_sharing_enabled

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        yield
        application.state.engine.dispose()

    application = FastAPI(title="Polymath API", version=__version__, lifespan=lifespan)
    application.state.settings = settings
    application.state.operator_policy = operator_policy
    application.state.publication_policy = publication_policy
    application.state.engine = build_engine(settings)
    application.state.public_policy = public_policy

    def reviewed_resources(session):
        return _reviewed_public_resources(session, research_policy.native_account_id)

    if resource_controls is None:
        resource_controls = PublicResourceControls(
            application.state.engine,
            PublicResourcePolicy(
                enabled=settings.public_resource_limits_enabled,
                network_key=derive_network_key(settings.operator_bootstrap_token)
                if settings.public_resource_limits_enabled
                else None,
                limits=public_policy.resources,
            ),
            limits_resolver=reviewed_resources,
        )
    else:
        original_resolver = resource_controls.limits_resolver

        def combined_resources(session):
            authoritative = reviewed_resources(session)
            custom = original_resolver(session) if original_resolver is not None else None
            if authoritative is None:
                return custom
            if custom is None:
                return authoritative
            if not isinstance(custom, PublicResourceLimits):
                raise ValueError("invalid_public_resource_limits")
            return PublicResourceLimits.model_validate(
                {
                    key: min(value, getattr(custom, key))
                    for key, value in authoritative.model_dump(mode="json").items()
                }
            )

        # Keep the caller's independent resolver intact when several apps share
        # one injected control configuration. All assembled services below use
        # this same wrapper and the same persistent database counters.
        resource_policy = resource_controls.policy
        if resource_policy.enabled:
            resource_policy = replace(
                resource_policy,
                limits=PublicResourceLimits.model_validate(
                    {
                        key: min(value, getattr(public_policy.resources, key))
                        for key, value in resource_policy.limits.model_dump(mode="json").items()
                    }
                ),
            )
        resource_controls = PublicResourceControls(
            resource_controls.engine,
            resource_policy,
            limits_resolver=combined_resources,
        )
    application.state.public_resource_controls = resource_controls
    native_store = (
        MetaResearchStore(
            application.state.engine,
            research_policy=research_policy,
            readiness_policy=readiness_policy,
            operator_policy=operator_policy,
            public_policy=public_policy,
            resource_controls=resource_controls,
        )
        if native_required
        else None
    )
    application.state.native_research_store = native_store
    application.include_router(
        create_run_router(
            application.state.engine,
            availability=research_availability,
            native_store=native_store,
        )
    )
    application.include_router(create_discovery_router(application.state.engine))
    application.include_router(create_explore_collection_router(application.state.engine))
    application.include_router(
        create_public_research_router(
            application.state.engine,
            native_store=native_store,
            research_policy=research_policy,
            readiness_policy=readiness_policy,
            operator_policy=operator_policy,
            public_policy=public_policy,
            resource_controls=resource_controls,
        )
    )
    application.include_router(create_knowledge_router(application.state.engine))
    application.include_router(
        create_frontier_router(
            application.state.engine, availability=lambda: settings.frontier_enabled
        )
    )
    application.include_router(
        create_converge_router(
            application.state.engine, availability=lambda: settings.converge_enabled
        )
    )
    application.include_router(
        create_visitor_zenodo_router(
            application.state.engine, VisitorZenodoPolicy.from_settings(settings)
        )
    )
    application.include_router(
        create_budget_admin_router(
            application.state.engine,
            readiness_policy=readiness_policy,
            operator_policy=operator_policy,
        )
    )
    application.include_router(
        create_meta_probe_router(
            application.state.engine,
            probe_policy=probe_policy,
            readiness_policy=readiness_policy,
            operator_policy=operator_policy,
        )
    )
    application.include_router(
        create_meta_research_router(
            application.state.engine,
            research_policy=research_policy,
            readiness_policy=readiness_policy,
            operator_policy=operator_policy,
        )
    )
    application.include_router(
        create_sharing_router(application.state.engine, availability=sharing_availability)
    )
    application.include_router(
        create_ingestion_router(application.state.engine, availability=ingestion_availability)
    )
    application.include_router(
        create_artifact_router(
            application.state.engine,
            availability=research_availability,
            native_store=native_store,
        )
    )
    application.include_router(
        create_assessment_router(
            application.state.engine,
            availability=research_availability,
            native_store=native_store,
        )
    )
    application.include_router(
        create_operator_router(application.state.engine, policy=operator_policy)
    )
    application.include_router(
        create_publication_router(
            application.state.engine,
            policy=publication_policy,
            operator_policy=operator_policy,
            status_resolver=PublicationJobRepository(
                application.state.engine,
                PublicationStore(
                    application.state.engine, publication_policy, operator_policy=operator_policy
                ),
            ).status_for_manifest,
        )
    )
    application.include_router(
        create_publication_job_router(
            application.state.engine,
            policy=publication_policy,
            operator_policy=operator_policy,
            availability=publication_availability,
        )
    )
    application.include_router(
        create_publishing_router(
            application.state.engine,
            publication_policy=publication_policy,
            operator_policy=operator_policy,
            execution_available=publication_availability,
        )
    )

    @application.middleware("http")
    async def private_api_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/"):
            response.headers["Cache-Control"] = "no-store"
        if (
            request.url.path.startswith("/api/v1/public/")
            or request.url.path.startswith("/api/v1/shares")
            or request.url.path.startswith("/shared/")
            or request.url.path.startswith("/api/v1/budget")
            or request.url.path
            in ("/library", "/library/", "/shared", "/operator/budget", "/zenodo/callback")
        ):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response

    @application.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "service": "polymath", "version": __version__}

    @application.get("/api/v1/readiness", response_model=None)
    def readiness() -> JSONResponse:
        checks = {"database": "unavailable", "migrations": "not_checked"}
        try:
            with application.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                checks["database"] = "ok"
                current = set(MigrationContext.configure(connection).get_current_heads())
                checks["migrations"] = "ok" if current == migration_heads() else "pending"
        except SQLAlchemyError:
            # No exception string, connection URL, credentials, or SQL enters the response.
            checks["database"] = "unavailable"
            checks["migrations"] = "not_checked"
        ready = checks == {"database": "ok", "migrations": "ok"}
        return JSONResponse(
            {"status": "ready" if ready else "not_ready", "checks": checks},
            status_code=200 if ready else 503,
        )

    @application.get("/api/v1/capabilities")
    def capabilities() -> dict[str, str | bool]:
        # This policy is injected by trusted application assembly, never inferred
        # from a key or a client request. The exported production app defaults off.
        try:
            available = research_availability is not None and research_availability() is True
        except Exception:
            available = False
        return {
            "research_available": available,
            "reason": "available"
            if available
            else ("native_research_disabled" if native_required else "research_not_implemented"),
            "meta_configured": settings.meta_configured,
            "native_research_required": native_required,
        }

    frontend = settings.frontend_dist
    if frontend is not None:
        frontend = frontend.resolve()
        if not (frontend / "index.html").is_file():
            raise ValueError("FRONTEND_DIST must contain the built index.html")
        if (frontend / "assets").is_dir():
            application.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

        @application.get("/{path:path}", include_in_schema=False)
        def frontend_route(path: str) -> FileResponse:
            if path == "api" or path.startswith("api/"):
                raise HTTPException(status_code=404, detail="Not Found")
            candidate = (frontend / path).resolve()
            if candidate.is_relative_to(frontend) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(frontend / "index.html")

    # The ASGI resource gate is outermost and bounds bodies before FastAPI can
    # parse them. Disabled construction performs no database or model action.
    application.add_middleware(PublicResourceMiddleware, controls=resource_controls)
    return application


app = create_app()
