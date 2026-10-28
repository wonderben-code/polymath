"""Explicit free-provider collection process, separate from all model execution."""

from __future__ import annotations

import argparse
import asyncio
import signal

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from polymath.config import Settings
from polymath.database import build_engine
from polymath.ingestion import IngestionRepository
from polymath.ingestion_worker import IngestionWorker
from polymath.literature.feed import OpenLiteratureFeed


def build_ingestion_worker(engine: Engine, settings: Settings) -> IngestionWorker:
    return IngestionWorker(
        engine,
        tools_factory=lambda _: OpenLiteratureFeed(),
        execution_enabled=settings.literature_ingestion_enabled,
    )


async def run(settings: Settings, *, once: bool, worker_id: str) -> int:
    if not settings.literature_ingestion_enabled:
        print(
            "Literature collection disabled; no job claimed, provider request or model call made."
        )
        return 2
    engine = build_engine(settings)
    try:
        repository = IngestionRepository(engine)
        worker = build_ingestion_worker(engine, settings)
        if once:
            await asyncio.to_thread(repository.schedule_due, limit=5)
            outcome = await worker.run_once(worker_id)
            print(f"Literature collection result: {outcome.status}; model calls: 0.")
            return 0
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)

        async def schedule():
            while not stop.is_set():
                await asyncio.to_thread(repository.schedule_due, limit=5)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=1.0)
                except TimeoutError:
                    pass

        tasks = (
            asyncio.create_task(schedule()),
            asyncio.create_task(worker.run_until_stopped(worker_id, stop)),
        )
        try:
            await asyncio.gather(*tasks)
        finally:
            stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return 0
    except SQLAlchemyError:
        print("Literature collection storage unavailable; review the service configuration.")
        return 1
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect bounded open-literature provider windows")
    parser.add_argument(
        "--once", action="store_true", help="Schedule due watches and run at most one"
    )
    parser.add_argument("--worker-id", default="literature-worker")
    arguments = parser.parse_args()
    if not 1 <= len(arguments.worker_id) <= 128:
        parser.error("worker-id must contain 1–128 characters")
    return asyncio.run(run(Settings(), once=arguments.once, worker_id=arguments.worker_id))


if __name__ == "__main__":
    raise SystemExit(main())
