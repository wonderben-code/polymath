"""HTTP application: honest health/capability checks and optional built web assets."""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from polymath import __version__
from polymath.artifact_api import create_artifact_router
from polymath.assessment_api import create_assessment_router
from polymath.budget_admin_api import create_budget_admin_router
from polymath.config import Settings, get_settings
from polymath.database import build_engine
from polymath.ingestion_api import create_ingestion_router
from polymath.knowledge import create_knowledge_router
from polymath.meta_probe_api import create_meta_probe_router
from polymath.meta_probe_contracts import MetaProbePolicy
from polymath.meta_readiness_contracts import ReadinessPolicy
from polymath.operator_auth import OperatorPolicy, create_operator_router
from polymath.publication import PublicationStore
from polymath.publication_api import create_publication_router
from polymath.publication_contracts import PublicationPolicy
from polymath.publication_job_api import create_publication_job_router
from polymath.publication_jobs import PublicationJobRepository
from polymath.publication_runtime import (
    configured_publication_policies,
    publication_execution_available,
)
from polymath.run_api import create_run_router
from polymath.sharing_api import create_sharing_router


def migration_heads() -> set[str]:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    return set(ScriptDirectory.from_config(config).get_heads())


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
) -> FastAPI:
    settings = settings or get_settings()
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
    application.include_router(
        create_run_router(application.state.engine, availability=research_availability)
    )
    application.include_router(create_knowledge_router(application.state.engine))
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
        create_sharing_router(application.state.engine, availability=sharing_availability)
    )
    application.include_router(
        create_ingestion_router(application.state.engine, availability=ingestion_availability)
    )
    application.include_router(
        create_artifact_router(application.state.engine, availability=research_availability)
    )
    application.include_router(
        create_assessment_router(application.state.engine, availability=research_availability)
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
            or request.url.path in ("/library", "/library/", "/shared", "/operator/budget")
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
            "reason": "available" if available else "research_not_implemented",
            "meta_configured": settings.meta_configured,
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

    return application


app = create_app()
