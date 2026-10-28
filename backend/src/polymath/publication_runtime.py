"""Explicit server composition for publication; importing this module sends nothing.

Keys alone never enable execution. Every mutation also requires a current,
exact human approval and a durable dispatch grant. Native sandbox verification
is still an operational prerequisite, separate from the mock transport suite.
"""

from __future__ import annotations

import argparse
import asyncio
import signal

from sqlalchemy import Engine

from polymath.config import Settings
from polymath.database import build_engine
from polymath.operator_auth import OperatorPolicy
from polymath.publication_contracts import PublicationPolicy, PublisherIdentity
from polymath.publication_worker import PublicationWorker
from polymath.zenodo import ZenodoClient


def configured_publication_policies(settings: Settings) -> tuple[OperatorPolicy, PublicationPolicy]:
    operator = OperatorPolicy(
        bootstrap_token=settings.operator_bootstrap_token,
        actor_id=settings.operator_actor_id,
        display_name=settings.operator_display_name,
    )
    publication = (
        PublicationPolicy(
            publisher=PublisherIdentity(
                identifier=settings.publication_publisher_id,
                display_name=settings.publication_publisher_name,
            ),
            allowed_destinations=settings.publication_destinations,
        )
        if settings.publication_approval_enabled
        else PublicationPolicy()
    )
    return operator, publication


def publication_execution_available(settings: Settings) -> bool:
    return bool(
        settings.publication_execution_enabled
        and settings.publication_approval_enabled
        and settings.operator_bootstrap_token
        and settings.publication_destinations
        and all(
            settings.zenodo_sandbox_token
            if destination == "sandbox"
            else settings.zenodo_production_token
            for destination in settings.publication_destinations
        )
    )


def build_publication_worker(engine: Engine, settings: Settings) -> PublicationWorker:
    operator, publication = configured_publication_policies(settings)
    enabled = publication_execution_available(settings)

    def client_factory(snapshot):
        if not enabled or snapshot.destination not in settings.publication_destinations:
            raise ValueError("Publication execution is disabled for this destination")
        token = (
            settings.zenodo_sandbox_token
            if snapshot.destination == "sandbox"
            else settings.zenodo_production_token
        )
        if token is None:
            raise ValueError("Publication credentials are unavailable")
        return ZenodoClient(snapshot.destination, token, live_enabled=True)

    return PublicationWorker(
        engine,
        publication_policy=publication,
        operator_policy=operator,
        client_factory=client_factory,
        execution_enabled=enabled,
    )


async def run(settings: Settings, *, once: bool, worker_id: str) -> int:
    if not publication_execution_available(settings):
        print("Publication worker disabled; no job claimed or remote request made.")
        return 2
    engine = build_engine(settings)
    try:
        worker = build_publication_worker(engine, settings)
        if once:
            result = await worker.run_once(worker_id)
            print(f"Publication worker result: {result.status}")
            return 0
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
        await worker.run_until_stopped(worker_id, stop)
        return 0
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run explicitly authorized publication jobs")
    parser.add_argument("--once", action="store_true", help="Claim at most one already queued job")
    parser.add_argument("--worker-id", default="publication-worker")
    arguments = parser.parse_args()
    if not 1 <= len(arguments.worker_id) <= 128:
        parser.error("worker-id must contain 1–128 characters")
    return asyncio.run(run(Settings(), once=arguments.once, worker_id=arguments.worker_id))


if __name__ == "__main__":
    raise SystemExit(main())
