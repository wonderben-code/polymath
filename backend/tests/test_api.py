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


def test_credentials_alone_never_enable_research_submission() -> None:
    application = create_app(
        Settings(
            MODEL_API_KEY="synthetic-test-placeholder", META_MODEL="fixture-model", _env_file=None
        )
    )
    with TestClient(application) as client:
        capabilities = client.get("/api/v1/capabilities").json()
        assert client.get("/api/v1/assessments/capabilities").json()["execution_available"] is False
        assert (
            client.get("/api/v1/knowledge/watches/capabilities").json()["execution_available"]
            is False
        )
    assert capabilities["meta_configured"] is True
    assert capabilities["research_available"] is False


def test_free_collection_setting_enables_no_model_or_publication_authority() -> None:
    application = create_app(Settings(literature_ingestion_enabled=True, _env_file=None))
    with TestClient(application) as client:
        capabilities = client.get("/api/v1/knowledge/watches/capabilities")
        assert capabilities.status_code == 200
        assert capabilities.json()["execution_available"] is True
        assert capabilities.json()["model_calls"] is False
        assert capabilities.headers["cache-control"] == "no-store"
        assert client.get("/api/v1/capabilities").json()["research_available"] is False
        assert (
            client.get("/api/v1/publication-jobs/capabilities").json()["execution_available"]
            is False
        )


@pytest.mark.parametrize("enabled", [False, True])
def test_public_sharing_configuration_never_enables_paid_authority(enabled) -> None:
    application = create_app(Settings(public_sharing_enabled=enabled, _env_file=None))
    with TestClient(application) as client:
        response = client.get("/api/v1/shares/capabilities")
        assert response.status_code == 200
        assert response.json()["sharing_available"] is enabled
        assert client.get("/api/v1/capabilities").json()["research_available"] is False
        assert (
            client.get("/api/v1/publication-jobs/capabilities").json()["execution_available"]
            is False
        )
        assert "set-cookie" not in response.headers
        if not enabled:
            assert client.get("/api/v1/public/library").status_code == 404


def test_default_publication_routes_are_mounted_without_privileged_authority() -> None:
    application = create_app(Settings(_env_file=None))
    with TestClient(application) as client:
        publication = client.get("/api/v1/publications/capabilities")
        assert publication.status_code == 200
        assert publication.json() == {
            "configured": False,
            "publisher": None,
            "destinations": [],
            "can_prepare": False,
        }
        operator = client.get("/api/v1/operator/session")
        assert operator.status_code == 200
        assert operator.json()["configured"] is False
        assert operator.json()["authenticated"] is False
        assert operator.json()["csrf_token"] is None
        assert publication.headers["cache-control"] == "no-store"


def test_explicit_approval_configuration_does_not_enable_model_execution() -> None:
    application = create_app(
        Settings(
            operator_bootstrap_token="Synthetic_Operator_0123456789abcdef-ABCDEF",
            publication_approval_enabled=True,
            publication_publisher_id="synthetic-publisher",
            publication_publisher_name="Synthetic Publisher",
            publication_destinations=("sandbox",),
            _env_file=None,
        )
    )
    with TestClient(application) as client:
        publication = client.get("/api/v1/publications/capabilities").json()
        assert publication["configured"] is True
        assert publication["destinations"] == ["sandbox"]
        assert publication["publisher"]["identifier"] == "synthetic-publisher"
        assert client.get("/api/v1/capabilities").json()["research_available"] is False
        assert (
            client.get("/api/v1/publication-jobs/capabilities").json()["execution_available"]
            is False
        )


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
        assert client.get("/api/v1/nonexistent").headers["Cache-Control"] == "no-store"
        assert client.get("/api").status_code == 404
        assert client.get("/api/v1/health").json()["status"] == "ok"


@pytest.mark.parametrize("path", ["/library", "/shared", "/shared/" + "a" * 43])
def test_public_html_has_privacy_headers_without_creating_owner_session(tmp_path, path) -> None:
    (tmp_path / "index.html").write_text("<html>Public finding reader</html>")
    application = create_app(Settings(STATIC_DIR=tmp_path, _env_file=None))
    with TestClient(application) as client:
        response = client.get(path)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, nofollow"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "set-cookie" not in response.headers


def test_invalid_static_directory_fails_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="built index.html"):
        create_app(Settings(STATIC_DIR=tmp_path, _env_file=None))
