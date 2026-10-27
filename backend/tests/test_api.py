from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import StaticPool

from polymath.config import Settings
from polymath.main import create_app, migration_heads


def test_health_does_not_require_database_or_model() -> None:
    application = create_app(Settings(_env_file=None))
    with TestClient(application) as client:
        assert client.get("/api/v1/health").json() == {
            "status": "ok",
            "service": "polymath",
            "version": "0.1.0",
        }
        assert client.get("/api/v1/capabilities").json() == {
            "research_available": False,
            "reason": "research_not_implemented",
            "meta_configured": False,
        }
        assert client.get("/api/v1/research").status_code == 404


def test_unavailable_database_has_sanitized_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    application = create_app(Settings(_env_file=None))

    def unavailable() -> None:
        raise OperationalError("credentials-must-not-leak", {}, Exception("private-database-host"))

    monkeypatch.setattr(application.state.engine, "connect", unavailable)
    with TestClient(application) as client:
        response = client.get("/api/v1/readiness")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {"database": "unavailable", "migrations": "not_checked"},
    }
    assert "credentials" not in response.text
    assert "private-database-host" not in response.text


@pytest.mark.parametrize("revision", [None, "old_revision", "current"])
def test_readiness_requires_current_migrations(revision: str | None) -> None:
    # SQLite exercises the readiness state logic only. PostgreSQL migrations have a separate
    # integration test and are never considered validated by this test.
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    if revision == "current":
        revision = next(iter(migration_heads()))
    if revision:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
            connection.execute(
                text("INSERT INTO alembic_version VALUES (:revision)"), {"revision": revision}
            )
    application = create_app(Settings(_env_file=None))
    application.state.engine.dispose()
    application.state.engine = engine
    with TestClient(application) as client:
        response = client.get("/api/v1/readiness")
    expected_ready = revision in migration_heads()
    assert response.status_code == (200 if expected_ready else 503)
    assert response.json() == {
        "status": "ready" if expected_ready else "not_ready",
        "checks": {"database": "ok", "migrations": "ok" if expected_ready else "pending"},
    }


def test_built_frontend_serves_assets_and_never_masks_unknown_api(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>Polymath application</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log('built asset')")
    application = create_app(Settings(STATIC_DIR=tmp_path, _env_file=None))
    with TestClient(application) as client:
        assert "Polymath application" in client.get("/").text
        assert "Polymath application" in client.get("/investigations/example").text
        assert "built asset" in client.get("/assets/app.js").text
        assert client.get("/api/v1/nonexistent").status_code == 404
        assert client.get("/api").status_code == 404
        assert client.get("/api/v1/health").json()["status"] == "ok"


def test_invalid_static_directory_fails_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="built index.html"):
        create_app(Settings(STATIC_DIR=tmp_path, _env_file=None))
