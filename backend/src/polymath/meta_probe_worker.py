"""One approved native protocol request; imports and disabled startup send nothing.

Fresh authority is minted by the dedicated dispatcher. Recorded observations can
be finalized without a current key, model, paid flag, operator, or network client.
This worker never invokes the research controller or repairs a model response.
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Engine

from polymath.config import Settings
from polymath.database import build_engine
from polymath.jobs import JobLease, JobRepository
from polymath.meta_probe_contracts import MetaObservation, MetaProbePolicy, MetaProbeRequest
from polymath.meta_probe_dispatch import MetaProbeDispatcher
from polymath.meta_probes import MetaProbeInput, MetaProbeStore
from polymath.meta_readiness_contracts import ReadinessPolicy
from polymath.operator_auth import OperatorPolicy
from polymath.research.meta import MetaModel


class _ObservingModel(Protocol):
    async def observe(self, request: MetaProbeRequest) -> MetaObservation: ...


class _FinalizeDatabaseSettings(BaseSettings):
    """Free journal recovery needs only the ledger, never live authority settings."""

    model_config = SettingsConfigDict(
        case_sensitive=False, extra="ignore", env_file=".env", hide_input_in_errors=True
    )
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://polymath:polymath@localhost:5432/polymath"
    )

    @field_validator("database_url")
    @classmethod
    def require_postgresql(cls, value: SecretStr) -> SecretStr:
        return Settings.require_postgresql(value)


@dataclass(frozen=True)
class MetaProbeOutcome:
    status: Literal["disabled", "idle", "finalized", "interrupted"]
    run_id: str | None = None
    receipt_id: str | None = None
    native_status: str | None = None


def configured_meta_probe_policies(
    settings: Settings,
) -> tuple[MetaProbePolicy, ReadinessPolicy, OperatorPolicy]:
    """Capture explicit server policy; no engine or model is constructed here."""
    probe = MetaProbePolicy(
        paid_probe_enabled=settings.paid_meta_probe_enabled,
        native_account_id=settings.native_budget_account_id,
        model_id=settings.meta_model,
        api_key=settings.model_api_key,
    )
    readiness = ReadinessPolicy(
        preparation_enabled=settings.budget_readiness_enabled,
        allowed_actor_ids=settings.budget_admin_actor_ids,
        max_hard_cap_microdollars=settings.budget_max_hard_cap_microdollars,
    )
    operator = OperatorPolicy(
        bootstrap_token=settings.operator_bootstrap_token,
        actor_id=settings.operator_actor_id,
        display_name=settings.operator_display_name,
    )
    return probe, readiness, operator


def _enabled(probe: MetaProbePolicy, readiness: ReadinessPolicy, operator: OperatorPolicy) -> bool:
    return bool(
        probe.paid_probe_enabled
        and probe.configured
        and readiness.preparation_enabled
        and operator.configured
        and operator.actor_id in readiness.allowed_actor_ids
    )


class MetaProbeWorker:
    def __init__(
        self,
        engine: Engine,
        *,
        probe_policy: MetaProbePolicy | None = None,
        readiness_policy: ReadinessPolicy | None = None,
        operator_policy: OperatorPolicy | None = None,
        model_factory: Callable[[MetaProbeInput], _ObservingModel] | None = None,
        lease_seconds: int = 60,
        heartbeat_seconds: float = 10,
    ):
        if type(lease_seconds) is not int or not 1 <= lease_seconds <= 3600:
            raise ValueError("invalid_probe_worker_lease")
        if (
            type(heartbeat_seconds) not in (int, float)
            or not 0 < heartbeat_seconds < lease_seconds / 2
        ):
            raise ValueError("invalid_probe_worker_heartbeat")
        self.engine = engine
        self.probe_policy = probe_policy or MetaProbePolicy()
        self.readiness_policy = readiness_policy or ReadinessPolicy()
        self.operator_policy = operator_policy or OperatorPolicy()
        self.lease_seconds = lease_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.jobs = JobRepository(engine)
        self.store = MetaProbeStore(
            engine, self.probe_policy, self.readiness_policy, self.operator_policy
        )
        self.dispatcher = MetaProbeDispatcher(
            engine, self.probe_policy, self.readiness_policy, self.operator_policy
        )
        # This frozen policy is the same credential snapshot checked by prepare.
        # Constructing the model is deferred until after a committed send grant.
        captured_policy = self.probe_policy

        def native_factory(value: MetaProbeInput) -> MetaModel:
            if captured_policy.api_key is None or captured_policy.model_id is None:
                raise ValueError("meta_probe_unavailable")
            return MetaModel(
                api_key=captured_policy.api_key,
                model=captured_policy.model_id,
                live_enabled=True,
                request_timeout_seconds=value.manifest.request_timeout_seconds,
            )

        self.model_factory = model_factory or native_factory

    async def finalize_known(self, run_id: uuid.UUID) -> MetaProbeOutcome:
        """Free recovery from a durable observation; never constructs a model."""
        record = await asyncio.to_thread(self.store.finalize_known, run_id)
        response = record.to_response()
        return MetaProbeOutcome("finalized", str(run_id), str(record.id), response["native_status"])

    async def _heartbeat(self, lease: JobLease) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                await asyncio.to_thread(
                    self.jobs.heartbeat, lease, lease_seconds=self.lease_seconds
                )
            except Exception:
                # Lost authority forbids future grants. It cannot discard an
                # actual response to the already committed one-send grant.
                return

    async def _interrupt(self, lease: JobLease) -> None:
        with suppress(Exception):
            await asyncio.to_thread(self.jobs.interrupt, lease)

    async def run_once(self, worker_id: str) -> MetaProbeOutcome:
        if not _enabled(self.probe_policy, self.readiness_policy, self.operator_policy):
            return MetaProbeOutcome("disabled")
        lease = await asyncio.to_thread(
            self.jobs.claim_next,
            worker_id,
            lease_seconds=self.lease_seconds,
            task_kinds=("verify_meta",),
        )
        if lease is None:
            return MetaProbeOutcome("idle")
        heartbeat = asyncio.create_task(self._heartbeat(lease))
        grant = None
        try:
            value = await asyncio.to_thread(self.store.load_input, lease)
            recorded = await asyncio.to_thread(self.dispatcher.get_recorded_observation, lease)
            if recorded is not None:
                return await self.finalize_known(lease.run_id)
            # A cancellation cannot stop an already running DB transaction.
            # If its grant commits after this await is cancelled, the durable
            # prepared request/hold remains, and recovery must never resend it.
            grant = await asyncio.to_thread(self.dispatcher.prepare, lease)
            if grant is None:
                # A concurrent winner can have committed known facts. A
                # duplicate/prepared grant is never permission to send again.
                recorded = await asyncio.to_thread(self.dispatcher.get_recorded_observation, lease)
                if recorded is not None:
                    return await self.finalize_known(lease.run_id)
                await self._interrupt(lease)
                return MetaProbeOutcome("interrupted", str(lease.run_id))
            try:
                model = self.model_factory(value)
                observation = await model.observe(value.manifest.request)
            except BaseException:
                await asyncio.shield(
                    asyncio.to_thread(
                        self.dispatcher.mark_unknown, grant, "meta_probe_response_unknown"
                    )
                )
                raise
            # Persist the transport observation and counted usage before any
            # receipt/protocol conclusion. Late accounting requires no lease.
            await asyncio.to_thread(self.dispatcher.complete, grant, observation)
            return await self.finalize_known(lease.run_id)
        except asyncio.CancelledError:
            await asyncio.shield(self._interrupt(lease))
            raise
        except Exception:
            await self._interrupt(lease)
            return MetaProbeOutcome("interrupted", str(lease.run_id))
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def run_until_stopped(self, worker_id: str, stop: asyncio.Event) -> None:
        while not stop.is_set():
            outcome = await self.run_once(worker_id)
            if outcome.status == "disabled":
                return
            if outcome.status == "idle":
                with suppress(TimeoutError):
                    await asyncio.wait_for(stop.wait(), timeout=1)


async def run(
    settings: Settings | _FinalizeDatabaseSettings,
    *,
    once: bool,
    worker_id: str,
    finalize_only: uuid.UUID | None = None,
) -> int:
    # This branch deliberately does not compose a key/model policy. The known
    # journal supplies all facts, and cannot mint any new send authority.
    if finalize_only is not None:
        engine = build_engine(settings)
        try:
            record = await asyncio.to_thread(MetaProbeStore(engine).finalize_known, finalize_only)
            print(f"Meta verification finalized: {record.to_response()['native_status']}")
            return 0
        except Exception:
            print("Meta verification finalization unavailable; no request sent.")
            return 2
        finally:
            engine.dispose()
    if not settings.paid_meta_probe_enabled:
        print("Meta verification worker disabled; no job claimed or request sent.")
        return 2
    probe, readiness, operator = configured_meta_probe_policies(settings)
    if not _enabled(probe, readiness, operator):
        print("Meta verification worker unavailable; no job claimed or request sent.")
        return 2
    engine = build_engine(settings)
    try:
        worker = MetaProbeWorker(
            engine,
            probe_policy=probe,
            readiness_policy=readiness,
            operator_policy=operator,
        )
        if once:
            result = await worker.run_once(worker_id)
            print(f"Meta verification worker result: {result.status}")
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
    parser = argparse.ArgumentParser(description="Run one explicitly approved Meta protocol probe")
    parser.add_argument("--once", action="store_true", help="Claim at most one queued probe")
    parser.add_argument("--worker-id", default="meta-probe-worker")
    parser.add_argument(
        "--finalize-only",
        type=uuid.UUID,
        metavar="RUN_ID",
        help="Finalize a known journal for free",
    )
    arguments = parser.parse_args()
    if (
        not 1 <= len(arguments.worker_id) <= 128
        or any(ord(character) < 33 or ord(character) == 127 for character in arguments.worker_id)
        or (arguments.finalize_only is not None and arguments.once)
    ):
        parser.error("invalid worker arguments")
    try:
        return asyncio.run(
            run(
                _FinalizeDatabaseSettings() if arguments.finalize_only is not None else Settings(),
                once=arguments.once,
                worker_id=arguments.worker_id,
                finalize_only=arguments.finalize_only,
            )
        )
    except Exception:
        print("Meta verification worker unavailable; inspect private service diagnostics.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
