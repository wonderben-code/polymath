"""Free collection is an explicit process; credentials never enable another engine."""

import asyncio
import signal
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from polymath import ingestion_runtime
from polymath.config import Settings


class Engine:
    disposed = False

    def dispose(self):
        self.disposed = True


@pytest.mark.asyncio
async def test_disabled_runtime_returns_before_database_or_network(monkeypatch, capsys):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Disabled collection must not construct storage or a provider")

    monkeypatch.setattr(ingestion_runtime, "build_engine", forbidden)
    settings = Settings(
        model_api_key="synthetic-unusable-model-key",
        meta_model="synthetic-model-name",
        zenodo_sandbox_token="synthetic-unusable-publisher-key",
        _env_file=None,
    )
    assert not settings.literature_ingestion_enabled
    assert await ingestion_runtime.run(settings, once=True, worker_id="disabled") == 2
    assert "disabled" in capsys.readouterr().out
    assert not settings.publication_execution_enabled


def test_explicit_collection_factory_constructs_no_provider_until_claim(monkeypatch):
    calls = []

    def forbidden():
        pytest.fail("Constructing a worker must not construct a provider")

    monkeypatch.setattr(ingestion_runtime, "OpenLiteratureFeed", forbidden)
    monkeypatch.setattr(
        ingestion_runtime,
        "IngestionWorker",
        lambda engine, **kwargs: calls.append((engine, kwargs)),
    )
    engine = Engine()
    settings = Settings(literature_ingestion_enabled=True, _env_file=None)
    ingestion_runtime.build_ingestion_worker(engine, settings)
    assert calls[0][0] is engine
    assert calls[0][1]["execution_enabled"] is True
    assert callable(calls[0][1]["tools_factory"])
    assert not settings.meta_configured
    assert not settings.publication_execution_enabled


@pytest.mark.asyncio
async def test_once_schedules_bounded_watches_and_runs_at_most_one_job(monkeypatch, capsys):
    engine, events = Engine(), []

    class Repository:
        def __init__(self, supplied):
            assert supplied is engine

        def schedule_due(self, *, limit):
            events.append(("schedule", limit))

    class Worker:
        async def run_once(self, worker_id):
            events.append(("claim", worker_id))
            return SimpleNamespace(status="idle")

    monkeypatch.setattr(ingestion_runtime, "build_engine", lambda _: engine)
    monkeypatch.setattr(ingestion_runtime, "IngestionRepository", Repository)
    monkeypatch.setattr(ingestion_runtime, "build_ingestion_worker", lambda *_: Worker())
    assert (
        await ingestion_runtime.run(
            Settings(literature_ingestion_enabled=True, _env_file=None),
            once=True,
            worker_id="fixture",
        )
        == 0
    )
    assert events == [("schedule", 5), ("claim", "fixture")]
    assert engine.disposed
    assert "model calls: 0" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_long_running_runtime_passes_shutdown_to_worker_and_cleans_storage(monkeypatch):
    engine, entered = Engine(), asyncio.Event()
    handlers, scopes = {}, []

    class Repository:
        def __init__(self, _):
            pass

        def schedule_due(self, *, limit):
            assert limit == 5

    class Worker:
        async def run_until_stopped(self, worker_id, stop):
            assert worker_id == "fixture"
            scopes.append(stop)
            entered.set()
            await stop.wait()

    monkeypatch.setattr(ingestion_runtime, "build_engine", lambda _: engine)
    monkeypatch.setattr(ingestion_runtime, "IngestionRepository", Repository)
    monkeypatch.setattr(ingestion_runtime, "build_ingestion_worker", lambda *_: Worker())
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "add_signal_handler",
        lambda sig, callback: handlers.update({sig: callback}),
    )
    task = asyncio.create_task(
        ingestion_runtime.run(
            Settings(literature_ingestion_enabled=True, _env_file=None),
            once=False,
            worker_id="fixture",
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=5)
    handlers[signal.SIGTERM]()
    assert await asyncio.wait_for(task, timeout=5) == 0
    assert scopes[0].is_set()
    assert engine.disposed


@pytest.mark.asyncio
async def test_storage_errors_are_sanitized_and_engine_is_disposed(monkeypatch, capsys):
    engine = Engine()

    class Repository:
        def __init__(self, _):
            pass

        def schedule_due(self, **_):
            raise OperationalError("private-query-fixture", {}, Exception("private-db-fixture"))

    monkeypatch.setattr(ingestion_runtime, "build_engine", lambda _: engine)
    monkeypatch.setattr(ingestion_runtime, "IngestionRepository", Repository)
    monkeypatch.setattr(ingestion_runtime, "build_ingestion_worker", lambda *_: object())
    assert (
        await ingestion_runtime.run(
            Settings(literature_ingestion_enabled=True, _env_file=None),
            once=True,
            worker_id="fixture",
        )
        == 1
    )
    output = capsys.readouterr().out
    assert "storage unavailable" in output
    assert "private-query-fixture" not in output
    assert "private-db-fixture" not in output
    assert engine.disposed
