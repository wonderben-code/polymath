"""Bounded free literature collection, separate from every model execution path.

A tick commits at most one page per provider. Original readable snapshots are
saved before cursor advancement; failed or cancelled pages never advance their
cursor. Successful earlier page commits survive explicit worker resumption.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sqlalchemy import Engine

from polymath.evidence import EvidenceStore
from polymath.ingestion import IngestionError, IngestionInput, IngestionRepository
from polymath.jobs import JobLease, JobRepository, LeaseLost
from polymath.literature.errors import Provider, ProviderError
from polymath.literature.feed import FeedPage

PROVIDERS = ("openalex", "arxiv")
FAILURE_CODES = frozenset(
    {
        "access_denied",
        "rate_limited",
        "timeout",
        "transport_error",
        "invalid_response",
        "response_too_large",
        "source_unavailable",
    }
)


class IngestionTools(Protocol):
    async def fetch_page(
        self,
        provider: Provider,
        query: str,
        *,
        since: datetime,
        until: datetime,
        cursor: str | int | None = None,
        limit: int = 5,
    ) -> FeedPage: ...


@dataclass(frozen=True)
class IngestionWorkerPolicy:
    lease_seconds: int = 60
    heartbeat_seconds: float = 10.0
    provider_timeout_seconds: float = 20.0
    provider_wait_seconds: float = 15.0
    results_per_provider: int = 5

    def __post_init__(self):
        if type(self.lease_seconds) is not int or not 3 <= self.lease_seconds <= 3600:
            raise ValueError("Ingestion lease duration must be between3 and3600 seconds")
        if type(self.results_per_provider) is not int or not 1 <= self.results_per_provider <= 5:
            raise ValueError("Ingestion pages contain between1 and5 provider records")
        for value, low, high in (
            (self.heartbeat_seconds, 0.001, self.lease_seconds / 2),
            (self.provider_timeout_seconds, 0.001, 30),
            (self.provider_wait_seconds, 0, 15),
        ):
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or not low <= value <= high
            ):
                raise ValueError("Ingestion timing limits must be finite and bounded")
        if self.heartbeat_seconds >= self.lease_seconds / 2:
            raise ValueError("Ingestion heartbeat must be below half the lease duration")


@dataclass(frozen=True)
class IngestionWorkerOutcome:
    status: str
    run_id: str | None = None
    watch_id: str | None = None
    sources_added: int = 0
    ingestion_status: str | None = None


class _ProviderWaitExpired(Exception):
    pass


class IngestionWorker:
    def __init__(
        self,
        engine: Engine,
        *,
        tools_factory: Callable[[IngestionInput], IngestionTools],
        execution_enabled: bool = False,
        policy: IngestionWorkerPolicy | None = None,
    ):
        if type(execution_enabled) is not bool:
            raise ValueError("Ingestion execution gate must be an explicit bool")
        self.engine, self.tools_factory = engine, tools_factory
        self.execution_enabled = execution_enabled
        self.policy = policy or IngestionWorkerPolicy()
        self.jobs = JobRepository(engine)
        self.repository = IngestionRepository(engine)
        self.evidence = EvidenceStore(engine)

    async def _check_owner(self, lease: JobLease) -> IngestionInput:
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        return await asyncio.to_thread(self.repository.check_owner, lease)

    async def _permit(self, lease: JobLease, provider: Provider) -> IngestionInput:
        deadline = asyncio.get_running_loop().time() + self.policy.provider_wait_seconds
        while True:
            await self._check_owner(lease)
            delay = await asyncio.to_thread(self.repository.acquire_provider_slot, lease, provider)
            if type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0:
                raise IngestionError("invalid_provider_slot")
            if delay == 0:
                return await self._check_owner(lease)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise _ProviderWaitExpired
            # An asynchronous bounded wait lets heartbeats, cancellation, and
            # watch-pausing continue while respecting the provider's rate gate.
            await asyncio.sleep(min(float(delay), remaining, 3.0))

    async def _event(self, lease: JobLease, key: str, kind: str, summary: str):
        await asyncio.to_thread(
            self.jobs.append_event, lease, event_key=key, kind=kind, summary=summary
        )

    async def _failure(self, lease: JobLease, provider: Provider, cursor, code: str) -> dict:
        if code not in FAILURE_CODES:
            code = "transport_error"
        await self._check_owner(lease)
        await asyncio.to_thread(
            self.repository.record_provider_failure, lease, provider, cursor, code
        )
        await self._event(
            lease,
            f"ingestion:failed:{provider}:{lease.token}",
            "provider_failed",
            f"{provider} could not return a usable page ({code}); its cursor was retained.",
        )
        return {
            "provider": provider,
            "status": "failed",
            "error_code": code,
            "added_sources": 0,
            "sources_seen": 0,
        }

    async def _collect(self, lease: JobLease, initial: IngestionInput) -> dict:
        tools = self.tools_factory(initial)
        bounded = False
        results = {
            provider: {"provider": provider, **dict(result)}
            for provider, result in initial.provider_results.items()
            if provider in PROVIDERS
        }
        await self._event(
            lease,
            "ingestion:started",
            "literature_collection",
            "Checking new open-literature pages; this collection does not run a model.",
        )
        for provider in PROVIDERS:
            current = await self._check_owner(lease)
            if provider in current.completed_providers:
                if provider in current.provider_results:
                    results[provider] = {
                        "provider": provider,
                        **dict(current.provider_results[provider]),
                    }
                continue
            cursor = current.cursors[provider]
            if current.watch.remaining_source_capacity <= 0:
                bounded = True
                results[provider] = {
                    "provider": provider,
                    "status": "skipped",
                    "added_sources": 0,
                    "sources_seen": 0,
                    "error_code": "source_limit_reached",
                }
                await self._event(
                    lease,
                    f"ingestion:source-limit:{provider}",
                    "collection_bounded",
                    "The watch source limit was reached; remaining provider cursors were retained.",
                )
                continue
            if cursor.exhausted:
                results[provider] = {
                    "provider": provider,
                    "status": "skipped",
                    "added_sources": 0,
                    "sources_seen": 0,
                }
                continue
            try:
                current = await self._permit(lease, provider)
            except _ProviderWaitExpired:
                results[provider] = await self._failure(lease, provider, cursor, "rate_limited")
                continue
            # Recheck after waiting: an expired session or paused watch never
            # becomes authority merely because a pacing slot was acquired.
            await self._check_owner(lease)
            if current.cursors[provider] != cursor:
                raise IngestionError("watch_cursor_changed")
            page_limit = min(
                self.policy.results_per_provider, current.watch.remaining_source_capacity
            )
            if page_limit <= 0:
                bounded = True
                results[provider] = {
                    "provider": provider,
                    "status": "skipped",
                    "added_sources": 0,
                    "sources_seen": 0,
                    "error_code": "source_limit_reached",
                }
                continue
            await self._event(
                lease,
                f"ingestion:request:{provider}:{lease.token}",
                "provider_requested",
                f"Reading one bounded {provider} literature page.",
            )
            try:
                page = await asyncio.wait_for(
                    tools.fetch_page(
                        provider,
                        current.watch.query,
                        since=cursor.since,
                        until=cursor.until,
                        cursor=cursor.cursor,
                        limit=page_limit,
                    ),
                    timeout=self.policy.provider_timeout_seconds,
                )
                if (
                    not isinstance(page, FeedPage)
                    or page.provider != provider
                    or page.since != cursor.since
                    or page.until != cursor.until
                    or len(page.records) > page_limit
                ):
                    raise ValueError("invalid_feed_page")
            except ProviderError as error:
                code = error.code if error.provider == provider else "invalid_response"
                results[provider] = await self._failure(lease, provider, cursor, code)
                continue
            except TimeoutError:
                results[provider] = await self._failure(lease, provider, cursor, "timeout")
                continue
            except ValueError:
                results[provider] = await self._failure(lease, provider, cursor, "invalid_response")
                continue
            except Exception:
                results[provider] = await self._failure(lease, provider, cursor, "transport_error")
                continue
            await self._check_owner(lease)
            artifacts = []
            for record in page.records:
                if record.document is None:
                    continue
                await self._check_owner(lease)
                stored = await asyncio.to_thread(self.evidence.save_source, lease, record.document)
                artifacts.append(stored)
                title = " ".join(record.document.title.split())[:500]
                await self._event(
                    lease,
                    f"ingestion:source:{provider}:{stored.retrieval_id}",
                    "source_saved",
                    f"Saved {record.document.content_kind.replace('_', ' ')} snapshot: {title}",
                )
            await self._check_owner(lease)
            diagnostics = {
                "discovered": len(page.records),
                "metadata_only": page.metadata_only,
                "unreadable_source_ids": list(page.unreadable_source_ids),
                "window_precision": page.window_precision,
            }
            committed = await asyncio.to_thread(
                self.repository.commit_page,
                lease,
                provider,
                cursor,
                page.next_cursor,
                tuple(artifacts),
                diagnostics,
                exhausted=page.exhausted,
            )
            results[provider] = {
                "provider": provider,
                "status": "ok",
                "added_sources": committed.added_sources,
                "sources_seen": committed.sources_seen,
                "metadata_only": page.metadata_only,
            }
            await self._event(
                lease,
                f"ingestion:committed:{provider}",
                "provider_committed",
                f"Committed the {provider} page: {committed.added_sources} new source versions; "
                f"{page.metadata_only} records had no readable abstract.",
            )
        await self._check_owner(lease)
        ordered = [results[provider] for provider in PROVIDERS if provider in results]
        failures = sum(result["status"] == "failed" for result in ordered)
        status = (
            "failed"
            if failures == len(PROVIDERS)
            else "partial"
            if failures or bounded
            else "complete"
        )
        return {
            "ingestion_status": status,
            "watch_id": str(initial.watch.id),
            "sources_added": sum(int(result.get("added_sources", 0)) for result in ordered),
            "sources_seen": sum(int(result.get("sources_seen", 0)) for result in ordered),
            "provider_results": ordered,
            "error_code": (
                "all_providers_unavailable"
                if status == "failed"
                else "source_limit_reached"
                if bounded
                else None
            ),
            "model_calls": 0,
        }

    async def run_once(self, worker_id: str) -> IngestionWorkerOutcome:
        if not self.execution_enabled:
            return IngestionWorkerOutcome("disabled")
        # Reaping marks a lost lease interrupted; it never retries an external
        # model call. Disabled workers do not perform even this maintenance.
        await asyncio.to_thread(self.jobs.interrupt_expired)
        lease = await asyncio.to_thread(
            self.jobs.claim_next,
            worker_id,
            lease_seconds=self.policy.lease_seconds,
            task_kinds=("ingest_literature",),
        )
        if lease is None:
            return IngestionWorkerOutcome("idle")
        parent = asyncio.current_task()
        closing, lost = asyncio.Event(), asyncio.Event()

        async def heartbeat():
            while True:
                await asyncio.sleep(self.policy.heartbeat_seconds)
                try:
                    await asyncio.to_thread(
                        self.jobs.heartbeat, lease, lease_seconds=self.policy.lease_seconds
                    )
                except Exception:
                    if closing.is_set():
                        return
                    lost.set()
                    if parent is not None:
                        parent.cancel()
                    return

        pulse = asyncio.create_task(heartbeat())

        async def stop_pulse():
            closing.set()
            pulse.cancel()
            with suppress(asyncio.CancelledError):
                await pulse

        result, watch_id = None, None
        try:
            initial = await self._check_owner(lease)
            watch_id = str(initial.watch.id)
            result = await self._collect(lease, initial)
            await stop_pulse()
            await asyncio.to_thread(
                self.jobs.finish,
                lease,
                status="failed" if result["ingestion_status"] == "failed" else "succeeded",
                outcome=result,
            )
        except asyncio.CancelledError:
            await stop_pulse()
            await self._stop(lease)
            if not lost.is_set():
                raise
        except Exception:
            await stop_pulse()
            await self._stop(lease)
        finally:
            await stop_pulse()
        snapshot = await asyncio.to_thread(self.jobs.get, lease.run_id)
        return IngestionWorkerOutcome(
            snapshot.status,
            str(lease.run_id),
            watch_id,
            int(result["sources_added"]) if result and snapshot.status == "succeeded" else 0,
            result["ingestion_status"] if result else None,
        )

    async def _stop(self, lease: JobLease):
        snapshot = await asyncio.to_thread(self.jobs.get, lease.run_id)
        if snapshot.status == "cancel_requested":
            await asyncio.to_thread(self.jobs.finalize_cancellation, lease.run_id)
        elif snapshot.status == "running":
            try:
                await asyncio.to_thread(self.jobs.interrupt, lease)
            except LeaseLost:
                await asyncio.to_thread(self.jobs.interrupt_expired)

    async def run_until_stopped(
        self, worker_id: str, stop: asyncio.Event, *, poll_seconds: float = 1.0
    ):
        if type(poll_seconds) not in (int, float) or not 0 < poll_seconds <= 60:
            raise ValueError("Ingestion polling must be between0 and60 seconds")
        stopping = asyncio.create_task(stop.wait())
        work = None
        try:
            while not stop.is_set():
                work = asyncio.create_task(self.run_once(worker_id))
                done, _ = await asyncio.wait((work, stopping), return_when=asyncio.FIRST_COMPLETED)
                if stopping in done:
                    work.cancel()
                    with suppress(asyncio.CancelledError):
                        await work
                    return
                outcome = await work
                if outcome.status == "disabled":
                    return
                if outcome.status == "idle":
                    with suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), timeout=poll_seconds)
        finally:
            stopping.cancel()
            with suppress(asyncio.CancelledError):
                await stopping
            if work is not None and not work.done():
                work.cancel()
                with suppress(asyncio.CancelledError):
                    await work


if __name__ == "__main__":
    raise SystemExit(
        "Use the explicitly enabled literature ingestion runtime; collection never starts a model."
    )
