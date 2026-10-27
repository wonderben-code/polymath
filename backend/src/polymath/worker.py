"""Injected research worker with durable replay. No live model is constructed here.

Production paid execution remains disabled. Tests inject explicitly synthetic
models/tools and prices through the same ownership and accounting boundaries.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import Engine

from polymath.budget import BudgetError, BudgetService
from polymath.dispatch import DispatchCoordinator, DispatchError
from polymath.evidence import EvidenceStore
from polymath.jobs import JobLease, JobRepository, LeaseLost, RunSnapshot
from polymath.memory import MemoryError as ResearchMemoryError
from polymath.memory import ResearchMemory
from polymath.research.controller import LiteratureTools, ResearchController, ResearchLimits
from polymath.research.meta import META_COMPLETIONS_URL, ModelError, ModelReply, ResearchModel
from polymath.research.models import (
    MemoryFinding,
    MemorySearchResults,
    ResearchEvent,
    SearchResults,
    SourceDocument,
)


class ExecutionStopped(BaseException):
    """Control flow that must not become a model/tool observation or another call."""


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True)
class WorkerPolicy:
    account_id: str
    model_identity: str
    reservation_microdollars: int
    input_microdollars_per_million: int
    output_microdollars_per_million: int
    run_cap_microdollars: int
    lease_seconds: int = 60
    heartbeat_seconds: float = 10.0

    def __post_init__(self) -> None:
        for name in ("account_id", "model_identity"):
            value = getattr(self, name)
            if not isinstance(value, str) or not 1 <= len(value) <= 120:
                raise ValueError(f"{name} must be an explicit bounded identifier")
        for name in (
            "reservation_microdollars",
            "input_microdollars_per_million",
            "output_microdollars_per_million",
            "run_cap_microdollars",
            "lease_seconds",
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= 9_000_000_000_000_000:
                raise ValueError(f"{name} must be a positive bounded integer")
        if not 3 <= self.lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 3 and 3600")
        if (
            type(self.heartbeat_seconds) not in (int, float)
            or not 0 < self.heartbeat_seconds < self.lease_seconds / 2
        ):
            raise ValueError("heartbeat_seconds must be positive and below half the lease duration")
        if self.reservation_microdollars > self.run_cap_microdollars:
            raise ValueError("One call reservation cannot exceed the run cap")

    def actual_cost(self, reply: ModelReply) -> int | None:
        if reply.input_tokens is None or reply.output_tokens is None:
            return None
        if any(
            type(n) is not int or not 0 <= n <= 2**63 - 1
            for n in (
                reply.input_tokens,
                reply.output_tokens,
            )
        ):
            raise ValueError("Invalid reported usage")
        numerator = (
            reply.input_tokens * self.input_microdollars_per_million
            + reply.output_tokens * self.output_microdollars_per_million
        )
        return (numerator + 999_999) // 1_000_000


@dataclass(frozen=True)
class WorkerOutcome:
    status: str
    run_id: str | None = None
    result_id: str | None = None


class _RunContext:
    def __init__(self, engine: Engine, lease: JobLease, lease_seconds: int):
        self.jobs = JobRepository(engine)
        self.evidence = EvidenceStore(engine)
        self.lease = lease
        self.lease_seconds = lease_seconds

    async def check_owner(self) -> None:
        try:
            await asyncio.to_thread(
                self.jobs.heartbeat,
                self.lease,
                lease_seconds=self.lease_seconds,
            )
        except LeaseLost:
            raise ExecutionStopped("ownership_lost") from None

    async def checkpoint(self) -> RunSnapshot:
        await self.check_owner()
        return await asyncio.to_thread(self.jobs.get, self.lease.run_id)

    async def save_cache(self, key: str, value: dict) -> None:
        snapshot = await self.checkpoint()
        data = dict(snapshot.checkpoint)
        cache = dict(data.get("tool_observations", {}))
        if key in cache and cache[key] != value:
            raise ExecutionStopped("tool_replay_conflict")
        cache[key] = value
        data["tool_observations"] = cache
        try:
            await asyncio.to_thread(
                self.jobs.save_checkpoint,
                self.lease,
                expected_version=snapshot.checkpoint_version,
                checkpoint=data,
            )
        except (LeaseLost, ValueError):
            raise ExecutionStopped("checkpoint_not_saved") from None


class _RecordedModel:
    def __init__(
        self,
        context: _RunContext,
        coordinator: DispatchCoordinator,
        model: ResearchModel,
        policy: WorkerPolicy,
    ):
        self.context, self.coordinator, self.model, self.policy = (
            context,
            coordinator,
            model,
            policy,
        )
        self.step = 0

    async def complete(
        self, messages: list[dict[str, str]], *, max_output_tokens: int
    ) -> ModelReply:
        self.step += 1
        request_id = f"model-{self.step:04d}"
        request_hash = _digest(
            {
                "protocol": "polymath-worker-memory-v3",
                "model_identity": self.policy.model_identity,
                "endpoint": META_COMPLETIONS_URL,
                "stream": False,
                "messages": messages,
                "max_output_tokens": max_output_tokens,
            }
        )
        await self.context.check_owner()
        try:
            recorded = await asyncio.to_thread(
                self.coordinator.get_recorded_response,
                self.context.lease,
                request_id=request_id,
                request_hash=request_hash,
            )
            if recorded is not None:
                if recorded.get("failure_code") == "model_invalid_response":
                    raise ModelError("model_invalid_response")
                return ModelReply(**recorded)
            grant = await asyncio.to_thread(
                self.coordinator.prepare_model_call,
                self.context.lease,
                account_id=self.policy.account_id,
                request_id=request_id,
                request_hash=request_hash,
                estimated_microdollars=self.policy.reservation_microdollars,
            )
        except (DispatchError, BudgetError, LeaseLost):
            raise ExecutionStopped("dispatch_not_safe") from None
        if grant is None:
            raise ExecutionStopped("dispatch_already_authorized")
        try:
            reply = await self.model.complete(messages, max_output_tokens=max_output_tokens)
            if not isinstance(reply, ModelReply):
                raise ValueError("Invalid model reply")
            actual = self.policy.actual_cost(reply)
            invalid_content = not isinstance(reply.content, str)
            if not invalid_content:
                try:
                    reply.content.encode("utf-8")
                    invalid_content = (
                        not 1 <= len(reply.content) <= 16_000 or "\x00" in reply.content
                    )
                except UnicodeError:
                    invalid_content = True
            observation = (
                {"failure_code": "model_invalid_response"} if invalid_content else asdict(reply)
            )
            await asyncio.to_thread(
                self.coordinator.complete,
                grant,
                observation=observation,
                actual_microdollars=actual,
                settlement_key=f"response:{request_hash}" if actual is not None else None,
                prompt_tokens=reply.input_tokens if actual is not None else None,
                completion_tokens=reply.output_tokens if actual is not None else None,
            )
            await self.context.check_owner()
            if invalid_content:
                raise ModelError("model_invalid_response")
            return reply
        except BaseException:
            # A response or provider error may already be chargeable. Never infer
            # zero cost or automatically replay a request after an ambiguous exit.
            with suppress(Exception):
                await asyncio.shield(
                    asyncio.to_thread(
                        self.coordinator.mark_unknown,
                        grant,
                        reason="worker_lost",
                    )
                )
            raise


class _RecordedTools:
    def __init__(self, context: _RunContext, tools: LiteratureTools):
        self.context, self.tools = context, tools
        self.step = 0

    async def _cached(self, arguments: dict) -> tuple[str, str, dict | None]:
        self.step += 1
        key, digest = f"tool-{self.step:04d}", _digest(arguments)
        snapshot = await self.context.checkpoint()
        cached = snapshot.checkpoint.get("tool_observations", {}).get(key)
        if cached is not None and cached["hash"] != digest:
            raise ExecutionStopped("tool_replay_conflict")
        if cached is not None and cached.get("error"):
            raise RuntimeError("recorded_tool_failure")
        return key, digest, cached

    async def search(self, query: str, purpose: str) -> SearchResults:
        key, digest, cached = await self._cached(
            {"kind": "search", "query": query, "purpose": purpose}
        )
        if cached is not None:
            # Fresh provider objects need their discovery boundary restored for a
            # future, not-yet-recorded read. This is a free bounded refresh; model
            # observations remain the immutable saved snapshot.
            with suppress(Exception):
                await self.tools.search(query, purpose)
            return SearchResults.model_validate_json(json.dumps(cached["payload"]))
        try:
            result = await self.tools.search(query, purpose)
            if isinstance(result, list):
                result = SearchResults(sources=tuple(result))
            if not isinstance(result, SearchResults):
                raise ValueError("Invalid search observation")
        except Exception:
            await self.context.save_cache(key, {"hash": digest, "error": True})
            raise RuntimeError("literature_search_failed") from None
        await self.context.save_cache(
            key, {"hash": digest, "payload": result.model_dump(mode="json")}
        )
        return result

    async def read(self, source_id: str) -> SourceDocument:
        key, digest, cached = await self._cached({"kind": "read", "source_id": source_id})
        if cached is not None:
            return await asyncio.to_thread(
                self.context.evidence.get_document,
                self.context.lease.run_id,
                uuid.UUID(cached["retrieval_id"]),
            )
        try:
            document = await self.tools.read(source_id)
            if not isinstance(document, SourceDocument) or document.source_id != source_id:
                raise ValueError("Invalid source observation")
            stored = await asyncio.to_thread(
                self.context.evidence.save_source,
                self.context.lease,
                document,
            )
        except LeaseLost:
            raise ExecutionStopped("ownership_lost") from None
        except Exception:
            await self.context.save_cache(key, {"hash": digest, "error": True})
            raise RuntimeError("source_retrieval_failed") from None
        await self.context.save_cache(
            key, {"hash": digest, "retrieval_id": str(stored.retrieval_id)}
        )
        return document


class _RecordedMemory:
    """Replay immutable owned memory without repeating a changing database search.

    Finding bodies stay in immutable storage; checkpoints retain their digests.
    Original source snapshots are also linked durably to the current run before
    the controller can quote them. No provider refresh or external call occurs.
    """

    def __init__(self, context: _RunContext, memory: ResearchMemory):
        self.context, self.memory = context, memory
        self.step = 0

    async def _cached(self, arguments: dict) -> tuple[str, str, dict | None]:
        self.step += 1
        key, digest = f"memory-{self.step:04d}", _digest(arguments)
        snapshot = await self.context.checkpoint()
        cached = snapshot.checkpoint.get("tool_observations", {}).get(key)
        if cached is not None and cached["hash"] != digest:
            raise ExecutionStopped("memory_replay_conflict")
        if cached is not None and cached.get("error"):
            raise RuntimeError("recorded_memory_failure")
        return key, digest, cached

    async def search(self, query: str) -> MemorySearchResults:
        key, digest, cached = await self._cached({"kind": "search_memory", "query": query})
        scope = await asyncio.to_thread(self.memory.scope_fingerprint)
        if cached is not None:
            if cached["scope"] != scope:
                raise ExecutionStopped("memory_scope_lost")
            return MemorySearchResults.model_validate_json(json.dumps(cached["payload"]))
        try:
            found = await asyncio.to_thread(self.memory.search, query)
            if not isinstance(found, MemorySearchResults):
                raise ValueError("Invalid memory search")
        except LeaseLost:
            raise ExecutionStopped("ownership_lost") from None
        except Exception:
            await self.context.save_cache(key, {"hash": digest, "error": True})
            raise RuntimeError("memory_search_failed") from None
        await self.context.save_cache(
            key, {"hash": digest, "scope": scope, "payload": found.model_dump(mode="json")}
        )
        return found

    async def read_finding(self, result_id: str) -> MemoryFinding:
        async def record() -> MemoryFinding:
            key, digest, cached = await self._cached(
                {"kind": "read_memory_finding", "result_id": result_id}
            )
            try:
                found = await asyncio.to_thread(self.memory.read_finding, result_id)
                if not isinstance(found, MemoryFinding):
                    raise ValueError("Invalid memory finding")
                finding_digest = _digest(found.model_dump(mode="json"))
                if cached is not None and finding_digest != cached["finding_digest"]:
                    raise ExecutionStopped("memory_replay_conflict")
            except LeaseLost:
                raise ExecutionStopped("ownership_lost") from None
            except ResearchMemoryError:
                # These static failures leave the read transaction rolled back.
                # A known failed first read can be replayed as a tool failure.
                if cached is not None:
                    raise ExecutionStopped("memory_replay_unavailable") from None
                try:
                    await self.context.save_cache(key, {"hash": digest, "error": True})
                except Exception:
                    raise ExecutionStopped("memory_read_interrupted") from None
                raise RuntimeError("memory_finding_failed") from None
            except Exception:
                # The transaction may have committed its durable access receipt
                # before the caller lost the response. Never cache that as a
                # failed read: explicit resume retries this free, idempotent read.
                raise ExecutionStopped("memory_read_interrupted") from None
            if cached is None:
                try:
                    await self.context.save_cache(
                        key, {"hash": digest, "finding_digest": finding_digest}
                    )
                except Exception:
                    raise ExecutionStopped("memory_read_interrupted") from None
            return found

        try:
            return await record()
        except asyncio.CancelledError:
            # wait_for cancels this coroutine on a tool timeout. Its database
            # thread can still finish, so stop before any further model dispatch.
            raise ExecutionStopped("memory_read_interrupted") from None

    async def read_source(self, result_id: str, retrieval_id: str) -> SourceDocument:
        key, digest, cached = await self._cached(
            {"kind": "read_memory_source", "result_id": result_id, "retrieval_id": retrieval_id}
        )
        try:
            # Reauthorize even replayed observations: a saved checkpoint cannot
            # widen this run's current session scope or revive an expired one.
            document = await asyncio.to_thread(self.memory.read_source, result_id, retrieval_id)
            if not isinstance(document, SourceDocument):
                raise ValueError("Invalid memory source")
            if cached is not None:
                saved = await asyncio.to_thread(
                    self.context.evidence.get_document,
                    self.context.lease.run_id,
                    uuid.UUID(cached["retrieval_id"]),
                )
                if document != saved:
                    raise ExecutionStopped("memory_replay_conflict")
                return saved
            stored = await asyncio.to_thread(
                self.context.evidence.save_source, self.context.lease, document
            )
        except LeaseLost:
            raise ExecutionStopped("ownership_lost") from None
        except Exception:
            if cached is not None:
                raise ExecutionStopped("memory_replay_unavailable") from None
            await self.context.save_cache(key, {"hash": digest, "error": True})
            raise RuntimeError("memory_source_failed") from None
        await self.context.save_cache(
            key, {"hash": digest, "retrieval_id": str(stored.retrieval_id)}
        )
        return document


class ResearchWorker:
    def __init__(
        self,
        engine: Engine,
        *,
        model_factory: Callable[[RunSnapshot], ResearchModel],
        tools_factory: Callable[[RunSnapshot], LiteratureTools],
        policy: WorkerPolicy,
        limits: ResearchLimits | None = None,
        execution_enabled: bool = False,
    ):
        if type(execution_enabled) is not bool:
            raise ValueError("execution_enabled must be a bool")
        self.engine, self.policy = engine, policy
        self.model_factory, self.tools_factory = model_factory, tools_factory
        self.limits = limits or ResearchLimits()
        self.execution_enabled = execution_enabled
        self.jobs, self.budget = JobRepository(engine), BudgetService(engine)

    async def run_until_stopped(
        self, worker_id: str, stop: asyncio.Event, *, poll_seconds: float = 1.0
    ) -> None:
        """Poll real jobs; shutdown interrupts/checkpoints the current investigation."""
        if type(poll_seconds) not in (int, float) or not 0 < poll_seconds <= 60:
            raise ValueError("poll_seconds must be between zero and sixty")
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
            if work is not None and not work.done():
                work.cancel()
                with suppress(asyncio.CancelledError):
                    await work
            stopping.cancel()
            with suppress(asyncio.CancelledError):
                await stopping

    async def run_once(self, worker_id: str) -> WorkerOutcome:
        if not self.execution_enabled:
            return WorkerOutcome("disabled")
        lease = await asyncio.to_thread(
            self.jobs.claim_next,
            worker_id,
            lease_seconds=self.policy.lease_seconds,
        )
        if lease is None:
            return WorkerOutcome("idle")
        context = _RunContext(self.engine, lease, self.policy.lease_seconds)
        parent = asyncio.current_task()
        lost = asyncio.Event()
        closing = asyncio.Event()

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(self.policy.heartbeat_seconds)
                try:
                    await asyncio.to_thread(
                        self.jobs.heartbeat,
                        lease,
                        lease_seconds=self.policy.lease_seconds,
                    )
                except Exception:
                    if closing.is_set():
                        return
                    lost.set()
                    if parent is not None:
                        parent.cancel()
                    return

        async def public_event(item: ResearchEvent) -> None:
            try:
                await asyncio.to_thread(
                    self.jobs.append_event,
                    lease,
                    event_key=f"controller:v3:{item.sequence}",
                    kind=item.kind,
                    summary=item.summary,
                )
            except LeaseLost:
                raise ExecutionStopped("ownership_lost") from None

        pulse = asyncio.create_task(heartbeat())

        async def stop_heartbeat() -> None:
            closing.set()
            pulse.cancel()
            with suppress(asyncio.CancelledError):
                await pulse

        try:
            snapshot = await context.checkpoint()
            fingerprint = _digest(
                {
                    "protocol": "polymath-worker-memory-v3",
                    "mode": snapshot.mode,
                    "policy": asdict(self.policy),
                    "limits": asdict(self.limits),
                }
            )
            existing = snapshot.checkpoint.get("worker_protocol")
            if existing is not None and existing != fingerprint:
                raise ExecutionStopped("worker_protocol_changed")
            if existing is None:
                await asyncio.to_thread(
                    self.jobs.save_checkpoint,
                    lease,
                    expected_version=snapshot.checkpoint_version,
                    checkpoint={**snapshot.checkpoint, "worker_protocol": fingerprint},
                )
            await asyncio.to_thread(
                self.budget.configure_run,
                self.policy.account_id,
                lease.run_id,
                cap_microdollars=self.policy.run_cap_microdollars,
                max_calls=self.limits.max_model_calls,
            )
            controller = ResearchController(
                _RecordedModel(
                    context,
                    DispatchCoordinator(self.engine),
                    self.model_factory(snapshot),
                    self.policy,
                ),
                _RecordedTools(context, self.tools_factory(snapshot)),
                self.limits,
                on_event=public_event,
                memory=_RecordedMemory(context, ResearchMemory(self.engine, lease)),
            )
            result = await controller.investigate(snapshot.goal, mode=snapshot.mode)
            await context.check_owner()
            current = await asyncio.to_thread(self.jobs.get, lease.run_id)
            if current.external_request_in_flight:
                raise ExecutionStopped("external_outcome_unknown")
            saved = await asyncio.to_thread(context.evidence.save_result, lease, result)
            status = "failed" if result.status == "failed" else "succeeded"
            await stop_heartbeat()
            await asyncio.to_thread(
                self.jobs.finish,
                lease,
                status=status,
                outcome={
                    "result_id": str(saved.id),
                    "research_status": result.status,
                    "error_code": result.error_code,
                },
                evidence_refs=[saved.artifact_ref],
            )
            return WorkerOutcome(status, str(lease.run_id), str(saved.id))
        except (ExecutionStopped, asyncio.CancelledError):
            await stop_heartbeat()
            await self._stop(lease)
            if not lost.is_set() and asyncio.current_task().cancelling():
                raise
            return WorkerOutcome(
                (await asyncio.to_thread(self.jobs.get, lease.run_id)).status, str(lease.run_id)
            )
        except Exception:
            await stop_heartbeat()
            await self._stop(lease)
            return WorkerOutcome(
                (await asyncio.to_thread(self.jobs.get, lease.run_id)).status, str(lease.run_id)
            )
        finally:
            await stop_heartbeat()

    async def _stop(self, lease: JobLease) -> None:
        snapshot = await asyncio.to_thread(self.jobs.get, lease.run_id)
        if snapshot.status == "cancel_requested":
            await asyncio.to_thread(self.jobs.finalize_cancellation, lease.run_id)
        elif snapshot.status == "running":
            try:
                await asyncio.to_thread(self.jobs.interrupt, lease)
            except LeaseLost:
                await asyncio.to_thread(self.jobs.interrupt_expired)


if __name__ == "__main__":
    raise SystemExit(
        "Paid research worker is disabled: Meta access, pricing and account limits "
        "must be verified and the owner must permit paid calls first."
    )
