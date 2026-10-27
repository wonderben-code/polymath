"""Real migration/ORM checks, isolated in an owned disposable PostgreSQL schema."""

import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from polymath.config import Settings
from polymath.main import create_app
from polymath.models import ResearchRun


@pytest.mark.integration
def test_postgresql_migrations_repeatability_constraints_and_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run real PostgreSQL integration checks")

    url = make_url(database_url)
    assert url.drivername == "postgresql+psycopg", "TEST_DATABASE_URL must use psycopg"
    schema = "polymath_test_" + uuid.uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", scoped_url.render_as_string(hide_password=False))
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    engine = create_engine(scoped_url, hide_parameters=True)
    try:
        application = create_app(Settings(_env_file=None))
        with TestClient(application) as client:
            assert client.get("/api/v1/readiness").status_code == 503
            command.upgrade(config, "head")
            command.upgrade(config, "head")
            command.check(config)
            assert client.get("/api/v1/readiness").json() == {
                "status": "ready",
                "checks": {"database": "ok", "migrations": "ok"},
            }
        with Session(engine) as session:
            run = ResearchRun(mode="explore", goal="Test migration persistence")
            session.add(run)
            session.commit()
            identity = run.id
        with Session(engine) as session:
            restored = session.get(ResearchRun, identity)
            assert restored is not None
            assert restored.status == "queued"
            assert restored.created_at.tzinfo is not None
            session.add(ResearchRun(mode="invalid", goal="Invalid mode must be rejected"))
            with pytest.raises(IntegrityError):
                session.commit()
        command.downgrade(config, "base")
        assert "research_runs" not in inspect(engine).get_table_names()
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
