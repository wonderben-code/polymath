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
from polymath.config import Settings, get_settings
from polymath.database import build_engine
from polymath.knowledge import create_knowledge_router
from polymath.run_api import create_run_router


def migration_heads() -> set[str]:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    return set(ScriptDirectory.from_config(config).get_heads())


def create_app(
    settings: Settings | None = None,
    *,
    research_availability: Callable[[], bool] | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        yield
        application.state.engine.dispose()

    application = FastAPI(title="Polymath API", version=__version__, lifespan=lifespan)
    application.state.settings = settings
    application.state.engine = build_engine(settings)
    application.include_router(
        create_run_router(application.state.engine, availability=research_availability)
    )
    application.include_router(create_knowledge_router(application.state.engine))

    @application.middleware("http")
    async def private_api_responses(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/v1/"):
            response.headers["Cache-Control"] = "no-store"
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
