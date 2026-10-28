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
from typing import TYPE_CHECKING, Any

from sqlalchemy import Engine

from polymath.artifact_contracts import ArtifactBasis
from polymath.artifact_controller import ArtifactController, ArtifactLimits
from polymath.artifacts import ArtifactStore
from polymath.assessment_contracts import AssessmentBasis, AssessmentSourceSnapshot
from polymath.assessments import AssessmentStore
from polymath.budget import BudgetError, BudgetService
from polymath.dispatch import DispatchCoordinator, DispatchError
from polymath.evidence import EvidenceStore
from polymath.full_text import FullTextError, FullTextStore
from polymath.full_text_contracts import ExtractedFullText, FullTextIndex
from polymath.jobs import JobLease, JobRepository, LeaseLost, RunSnapshot
from polymath.lineage import get_parent_refs
from polymath.math_checks import (
    FiniteCheckResult,
    FiniteConjecture,
    check_conjecture,
    validate_check,
)
from polymath.memory import MemoryError as ResearchMemoryError
from polymath.memory import ResearchMemory
from polymath.research.controller import LiteratureTools, ResearchController, ResearchLimits
from polymath.research.meta import META_COMPLETIONS_URL, ModelError, ModelReply, ResearchModel
from polymath.research.models import (
    ArxivFullTextLocator,
    MemoryFinding,
    MemorySearchResults,
    ResearchEvent,
    SearchResults,
    SourceDocument,
    SourceReference,
)

if TYPE_CHECKING:
    from polymath.assessment_controller import AssessmentLimits


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
class WorkerRuntimeProfile:
    """Original immutable per-run policy; credentials never belong here."""

    policy: WorkerPolicy
    limits: ResearchLimits
    artifact_limits: ArtifactLimits
    assessment_limits: AssessmentLimits | None = None
    protocol_binding: dict | None = None

    def __post_init__(self):
        if (
            not isinstance(self.policy, WorkerPolicy)
            or not isinstance(self.limits, ResearchLimits)
            or not isinstance(self.artifact_limits, ArtifactLimits)
        ):
            raise ValueError("Invalid original worker policy or controller limits")
        if self.assessment_limits is not None:
            from polymath.assessment_controller import AssessmentLimits

            if not isinstance(self.assessment_limits, AssessmentLimits):
                raise ValueError("Invalid original assessment limits")
        if self.protocol_binding is not None:
            if type(self.protocol_binding) is not dict or not self.protocol_binding:
                raise ValueError("Invalid native run binding")
            _digest(self.protocol_binding)


@dataclass(frozen=True)
class WorkerOutcome:
    status: str
    run_id: str | None = None
    result_id: str | None = None
    artifact_id: str | None = None
    assessment_id: str | None = None


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
                "protocol": "polymath-worker-full-text-v8",
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
        self.full_text = FullTextStore(context.evidence.engine)
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
            saved = SearchResults.model_validate_json(json.dumps(cached["payload"]))
            with suppress(Exception):
                await self.tools.search(query, purpose)
            restore = getattr(self.tools, "restore_discovery", None)
            if callable(restore):
                try:
                    restore(saved)
                except Exception:
                    raise ExecutionStopped("discovery_replay_unavailable") from None
            return saved
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

    async def identity_context(self) -> tuple[SourceReference, ...]:
        try:
            return await asyncio.to_thread(
                self.full_text.owned_identity_context, self.context.lease
            )
        except (Exception, asyncio.CancelledError):
            raise ExecutionStopped("full_text_identity_context_unavailable") from None

    async def open_full_text(self, source_id: str) -> FullTextIndex:
        # Durable pin first: reopening or resuming never refetches/reextracts an
        # already observed body, even if the provider now serves another version.
        try:
            pinned = await asyncio.to_thread(
                self.full_text.get_index, self.context.lease, source_id
            )
        except (Exception, asyncio.CancelledError):
            raise ExecutionStopped("full_text_index_read_interrupted") from None
        key = f"fulltext-index:{source_id}"
        if pinned is not None:
            try:
                await self.context.save_cache(
                    key,
                    {"hash": _digest({"source_id": source_id}), "snapshot_id": pinned.snapshot_id},
                )
            except (Exception, asyncio.CancelledError):
                raise ExecutionStopped("full_text_index_receipt_interrupted") from None
            return pinned
        snapshot = await self.context.checkpoint()
        prior = snapshot.checkpoint.get("tool_observations", {}).get(key)
        if prior is not None:
            if prior.get("error"):
                raise RuntimeError("recorded_full_text_unavailable")
            raise ExecutionStopped("full_text_index_replay_unavailable")
        try:
            reference = self.tools.get_discovered_reference(source_id)
            availability = self.tools.get_full_text_availability(source_id)
            if (
                not isinstance(reference, SourceReference)
                or reference.source_id != source_id
                or availability is None
                or availability.status != "candidate"
                or availability.locator is None
            ):
                raise ValueError("Full-text candidate is unavailable")
            locator_key = f"fulltext-locator:{source_id}"
            binding = {
                "source": reference.model_dump(mode="json"),
                "availability": availability.model_dump(mode="json"),
            }
            locator_hash = _digest(binding)
            cached_locator = snapshot.checkpoint.get("tool_observations", {}).get(locator_key)
            if cached_locator is not None:
                if cached_locator.get("hash") != locator_hash:
                    raise ExecutionStopped("full_text_locator_replay_conflict")
                locator = ArxivFullTextLocator.model_validate_json(
                    json.dumps(cached_locator["locator"])
                )
            else:
                locator = await self.tools.resolve_full_text_locator(source_id)
            if (
                not isinstance(locator, ArxivFullTextLocator)
                or locator.arxiv_version is None
                or locator.arxiv_id != availability.locator.arxiv_id
                or availability.locator.arxiv_version not in (None, locator.arxiv_version)
            ):
                raise ValueError("Full-text locator does not match frozen discovery")
        except ExecutionStopped:
            raise
        except asyncio.CancelledError:
            raise ExecutionStopped("full_text_locator_resolution_interrupted") from None
        except Exception:
            await self.context.save_cache(
                key, {"hash": _digest({"source_id": source_id}), "error": True}
            )
            raise RuntimeError("full_text_unavailable") from None
        # The resolved exact version is committed before any HTML request. A
        # lost metadata/cache response stops rather than choosing a new latest.
        if cached_locator is None:
            try:
                await self.context.save_cache(
                    locator_key,
                    {"hash": locator_hash, **binding, "locator": locator.model_dump(mode="json")},
                )
            except (Exception, asyncio.CancelledError):
                raise ExecutionStopped("full_text_locator_receipt_interrupted") from None
        try:
            fetched = await self.tools.fetch_full_text(source_id, locator=locator)
            if (
                not isinstance(fetched, ExtractedFullText)
                or fetched.arxiv_id != locator.arxiv_id
                or fetched.arxiv_version != locator.arxiv_version
            ):
                raise ValueError("Full-text response does not match its resolved version")
        except asyncio.CancelledError:
            raise ExecutionStopped("full_text_fetch_interrupted") from None
        except Exception:
            await self.context.save_cache(
                key, {"hash": _digest({"source_id": source_id}), "error": True}
            )
            raise RuntimeError("full_text_unavailable") from None
        try:
            index = await asyncio.to_thread(
                self.full_text.save_snapshot, self.context.lease, reference, fetched
            )
            await self.context.save_cache(
                key, {"hash": _digest({"source_id": source_id}), "snapshot_id": index.snapshot_id}
            )
            return index
        except FullTextError as error:
            if str(error) == "full_text_snapshot_already_indexed":
                await self.context.save_cache(
                    key, {"hash": _digest({"source_id": source_id}), "error": True}
                )
                raise RuntimeError("full_text_alias_already_indexed") from None
            raise ExecutionStopped("full_text_index_commit_interrupted") from None
        except (Exception, asyncio.CancelledError):
            raise ExecutionStopped("full_text_index_commit_interrupted") from None

    async def read_full_text_window(self, snapshot_id: str, start: int, end: int) -> SourceDocument:
        key = f"fulltext-window:{snapshot_id}:{start}:{end}"
        digest = _digest({"snapshot_id": snapshot_id, "start": start, "end": end})
        try:
            snapshot = await self.context.checkpoint()
            cached = snapshot.checkpoint.get("tool_observations", {}).get(key)
            if cached is not None and cached.get("hash") != digest:
                raise ExecutionStopped("full_text_window_replay_conflict")
            document = await asyncio.to_thread(
                self.full_text.read_window, self.context.lease, snapshot_id, start, end
            )
            stored = await asyncio.to_thread(
                self.context.evidence.save_source, self.context.lease, document
            )
            if cached is not None and cached.get("retrieval_id") != str(stored.retrieval_id):
                raise ExecutionStopped("full_text_window_replay_conflict")
            await self.context.save_cache(
                key,
                {"hash": digest, "retrieval_id": str(stored.retrieval_id)},
            )
            return document
        except (Exception, asyncio.CancelledError):
            raise ExecutionStopped("full_text_window_read_interrupted") from None


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


class _RecordedAssessmentTools:
    """Deliver exact version handles and durable searches to the assessment model.

    A committed read with a lost response is interrupted rather than cached as
    failure. Resume repeats that free immutable read without another model call.
    """

    def __init__(self, context: _RunContext, store: AssessmentStore, tools: LiteratureTools):
        self.context, self.store = context, store
        self.literature = _RecordedTools(context, tools)

    async def identity_context(self) -> tuple[SourceReference, ...]:
        return await self.literature.identity_context()

    async def open_full_text(self, source_id: str) -> FullTextIndex:
        return await self.literature.open_full_text(source_id)

    async def read_full_text_window(
        self, snapshot_id: str, start: int, end: int
    ) -> AssessmentSourceSnapshot:
        document = await self.literature.read_full_text_window(snapshot_id, start, end)
        try:
            stored = await asyncio.to_thread(
                self.context.evidence.save_source, self.context.lease, document
            )
            return AssessmentSourceSnapshot(
                retrieval_id=str(stored.retrieval_id),
                version_id=str(stored.version_id),
                document=document,
            )
        except (Exception, asyncio.CancelledError):
            raise ExecutionStopped("assessment_full_text_window_interrupted") from None

    async def read_source_by_retrieval(self, retrieval_id: str) -> SourceDocument:
        await self.context.check_owner()
        key = f"assessment-source:{retrieval_id}"
        digest = _digest({"kind": "assessment_source", "retrieval_id": retrieval_id})
        snapshot = await self.context.checkpoint()
        cached = snapshot.checkpoint.get("tool_observations", {}).get(key)
        if cached is not None and cached.get("hash") != digest:
            raise ExecutionStopped("assessment_source_replay_conflict")
        try:
            document = await asyncio.to_thread(
                self.store.read_source, self.context.lease, retrieval_id
            )
            if not isinstance(document, SourceDocument):
                raise ValueError("Invalid pinned source observation")
            if cached is None:
                await self.context.save_cache(key, {"hash": digest, "retrieval_id": retrieval_id})
            return document
        except (Exception, asyncio.CancelledError):
            raise ExecutionStopped("assessment_source_read_interrupted") from None

    async def search(self, query: str, purpose: str) -> SearchResults:
        try:
            result = await self.literature.search(query, purpose)
        except asyncio.CancelledError:
            raise ExecutionStopped("assessment_search_receipt_interrupted") from None
        # The successful tool receipt is saved before recording its purpose.
        # Both writes are replayable; neither can authorize a second model call.
        try:
            snapshot = await self.context.checkpoint()
            purposes = set(snapshot.checkpoint.get("assessment_search_purposes", []))
            if purpose not in purposes:
                await asyncio.to_thread(
                    self.context.jobs.save_checkpoint,
                    self.context.lease,
                    expected_version=snapshot.checkpoint_version,
                    checkpoint={
                        **snapshot.checkpoint,
                        "assessment_search_purposes": sorted((*purposes, purpose)),
                    },
                )
        except (Exception, asyncio.CancelledError):
            raise ExecutionStopped("assessment_search_receipt_interrupted") from None
        return result

    async def read_literature_source(self, source_id: str) -> AssessmentSourceSnapshot:
        key, digest, cached = await self.literature._cached(
            {"kind": "assessment_literature_source", "source_id": source_id}
        )
        if cached is not None:
            try:
                document = await asyncio.to_thread(
                    self.context.evidence.get_document,
                    self.context.lease.run_id,
                    uuid.UUID(cached["retrieval_id"]),
                )
                return AssessmentSourceSnapshot(
                    retrieval_id=cached["retrieval_id"],
                    version_id=cached["version_id"],
                    document=document,
                )
            except (Exception, asyncio.CancelledError):
                raise ExecutionStopped("assessment_source_replay_unavailable") from None
        try:
            document = await self.literature.tools.read(source_id)
            if not isinstance(document, SourceDocument) or document.source_id != source_id:
                raise ValueError("Invalid literature source observation")
        except Exception:
            await self.context.save_cache(key, {"hash": digest, "error": True})
            raise RuntimeError("source_retrieval_failed") from None
        try:
            stored = await asyncio.to_thread(
                self.context.evidence.save_source, self.context.lease, document
            )
            await self.context.save_cache(
                key,
                {
                    "hash": digest,
                    "retrieval_id": str(stored.retrieval_id),
                    "version_id": str(stored.version_id),
                },
            )
            return AssessmentSourceSnapshot(
                retrieval_id=str(stored.retrieval_id),
                version_id=str(stored.version_id),
                document=document,
            )
        except (Exception, asyncio.CancelledError):
            raise ExecutionStopped("assessment_source_read_interrupted") from None


class _RecordedChecks:
    """Only the bounded integer checker is callable; there is no execution tool."""

    def __init__(self, context: _RunContext):
        self.context = context
        self.step = 0

    async def check(self, conjecture: FiniteConjecture) -> FiniteCheckResult:
        self.step += 1
        key = f"math-{self.step:04d}"
        digest = _digest(conjecture.model_dump(mode="json"))
        snapshot = await self.context.checkpoint()
        cached = snapshot.checkpoint.get("tool_observations", {}).get(key)
        if cached is not None:
            if cached["hash"] != digest:
                raise ExecutionStopped("math_replay_conflict")
            result = FiniteCheckResult.model_validate_json(json.dumps(cached["payload"]))
            validate_check(result, conjecture)
            return result
        result = await asyncio.to_thread(check_conjecture, conjecture)
        await self.context.save_cache(
            key, {"hash": digest, "payload": result.model_dump(mode="json")}
        )
        return result


class ResearchWorker:
    def __init__(
        self,
        engine: Engine,
        *,
        model_factory: Callable[[RunSnapshot], ResearchModel],
        tools_factory: Callable[[RunSnapshot], LiteratureTools],
        policy: WorkerPolicy | None,
        limits: ResearchLimits | None = None,
        artifact_limits: ArtifactLimits | None = None,
        assessment_limits: AssessmentLimits | None = None,
        execution_enabled: bool = False,
        runtime_profile_resolver: Callable[[JobLease, RunSnapshot], WorkerRuntimeProfile]
        | None = None,
        recorded_model_factory: Callable[
            [_RunContext, RunSnapshot, WorkerRuntimeProfile], ResearchModel
        ]
        | None = None,
        native_only: bool = False,
    ):
        if type(execution_enabled) is not bool:
            raise ValueError("execution_enabled must be a bool")
        if type(native_only) is not bool:
            raise ValueError("native_only must be a bool")
        if policy is None and not (native_only and runtime_profile_resolver is not None):
            raise ValueError("A worker requires an explicit policy or a native run policy resolver")
        if native_only and (runtime_profile_resolver is None or recorded_model_factory is None):
            raise ValueError("Native work requires its original profile and recorded model")
        self.engine, self.policy = engine, policy
        self.model_factory, self.tools_factory = model_factory, tools_factory
        self.limits = limits or ResearchLimits()
        self.artifact_limits = artifact_limits or ArtifactLimits()
        self.assessment_limits = assessment_limits
        self.execution_enabled = execution_enabled
        self.runtime_profile_resolver = runtime_profile_resolver
        self.recorded_model_factory = recorded_model_factory
        self.native_only = native_only
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
        await asyncio.to_thread(self.jobs.interrupt_expired)
        lease = await asyncio.to_thread(
            self.jobs.claim_next,
            worker_id,
            task_kinds=(
                "research",
                "formalise",
                "write_summary",
                "write_paper",
                "reassess_finding",
            ),
            lease_seconds=self.policy.lease_seconds if self.policy is not None else 60,
            native_only=self.native_only,
        )
        if lease is None:
            return WorkerOutcome("idle")
        try:
            if self.runtime_profile_resolver is not None:
                claimed = await asyncio.to_thread(self.jobs.get, lease.run_id)
                profile = await asyncio.to_thread(self.runtime_profile_resolver, lease, claimed)
                if not isinstance(profile, WorkerRuntimeProfile):
                    raise ValueError("Invalid per-run worker profile")
                if self.native_only and profile.protocol_binding is None:
                    raise ValueError("Native work requires an immutable run binding")
            else:
                profile = WorkerRuntimeProfile(
                    self.policy, self.limits, self.artifact_limits, self.assessment_limits
                )
        except asyncio.CancelledError:
            await asyncio.shield(self._stop(lease))
            raise
        except (ExecutionStopped, Exception):
            await self._stop(lease)
            return WorkerOutcome(
                (await asyncio.to_thread(self.jobs.get, lease.run_id)).status, str(lease.run_id)
            )
        policy = profile.policy
        limits = profile.limits
        artifact_limits = profile.artifact_limits
        context = _RunContext(self.engine, lease, policy.lease_seconds)
        parent = asyncio.current_task()
        lost = asyncio.Event()
        closing = asyncio.Event()

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(policy.heartbeat_seconds)
                try:
                    await asyncio.to_thread(
                        self.jobs.heartbeat,
                        lease,
                        lease_seconds=policy.lease_seconds,
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
                    event_key=f"controller:v7:{item.sequence}",
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
            parents = await asyncio.to_thread(get_parent_refs, self.engine, lease)
            basis: ArtifactBasis | None = None
            assessment_basis: AssessmentBasis | None = None
            assessment_limits = None
            if snapshot.task_kind in ("formalise", "write_summary", "write_paper"):
                basis = await asyncio.to_thread(ArtifactStore(self.engine).load_basis, lease)
            elif snapshot.task_kind == "reassess_finding":
                from polymath.assessment_controller import AssessmentController, AssessmentLimits

                assessment_basis = await asyncio.to_thread(
                    AssessmentStore(self.engine).load_basis, lease
                )
                assessment_limits = profile.assessment_limits or AssessmentLimits()
            fingerprint_input = {
                "protocol": "polymath-worker-full-text-v8",
                "mode": snapshot.mode,
                "task_kind": snapshot.task_kind,
                "parents": [parent.model_dump(mode="json") for parent in parents],
                "artifact_basis": (
                    _digest(basis.model_dump(mode="json")) if basis is not None else None
                ),
                "assessment_basis": (
                    _digest(assessment_basis.model_dump(mode="json"))
                    if assessment_basis is not None
                    else None
                ),
                "policy": asdict(policy),
                "limits": asdict(limits),
                "artifact_limits": asdict(artifact_limits),
                "assessment_limits": (
                    asdict(assessment_limits) if assessment_limits is not None else None
                ),
            }
            if profile.protocol_binding is not None:
                fingerprint_input["native_run_binding"] = profile.protocol_binding
            fingerprint = _digest(fingerprint_input)
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
                policy.account_id,
                lease.run_id,
                cap_microdollars=policy.run_cap_microdollars,
                max_calls=(
                    artifact_limits.max_model_calls
                    if profile.protocol_binding is not None and basis is not None
                    else min(
                        limits.max_model_calls,
                        assessment_limits.max_model_calls
                        if assessment_limits
                        else limits.max_model_calls,
                    )
                ),
            )
            model = (
                self.recorded_model_factory(context, snapshot, profile)
                if (self.recorded_model_factory is not None)
                else _RecordedModel(
                    context,
                    DispatchCoordinator(self.engine),
                    self.model_factory(snapshot),
                    policy,
                )
            )
            if assessment_basis is not None:
                store = AssessmentStore(self.engine)
                controller = AssessmentController(
                    model,
                    _RecordedAssessmentTools(context, store, self.tools_factory(snapshot)),
                    assessment_limits,
                    on_event=public_event,
                )
                assessment = await controller.reassess(assessment_basis)
                await context.check_owner()
                current = await asyncio.to_thread(self.jobs.get, lease.run_id)
                if current.external_request_in_flight:
                    raise ExecutionStopped("external_outcome_unknown")
                await stop_heartbeat()
                saved_assessment = await asyncio.to_thread(store.save, lease, assessment)
                # Final assessment, current head and terminal job commit together
                # in the store. A second finish would split that boundary.
                finished = await asyncio.to_thread(self.jobs.get, lease.run_id)
                return WorkerOutcome(
                    finished.status,
                    str(lease.run_id),
                    result_id=(
                        str(saved_assessment.candidate_result_id)
                        if saved_assessment.applied and saved_assessment.candidate_result_id
                        else None
                    ),
                    assessment_id=str(saved_assessment.id),
                )
            if basis is not None:
                controller = ArtifactController(
                    model,
                    artifact_limits,
                    on_event=public_event,
                    check_tool=_RecordedChecks(context),
                )
                artifact = await controller.generate(snapshot.task_kind, basis)
                await context.check_owner()
                current = await asyncio.to_thread(self.jobs.get, lease.run_id)
                if current.external_request_in_flight:
                    raise ExecutionStopped("external_outcome_unknown")
                saved_artifact = await asyncio.to_thread(
                    ArtifactStore(self.engine).save, lease, artifact
                )
                status = "failed" if artifact.status == "failed" else "succeeded"
                await stop_heartbeat()
                await asyncio.to_thread(
                    self.jobs.finish,
                    lease,
                    status=status,
                    outcome={
                        "artifact_id": str(saved_artifact.id),
                        "artifact_status": artifact.status,
                        "error_code": artifact.error_code,
                    },
                    evidence_refs=[saved_artifact.artifact_ref],
                )
                return WorkerOutcome(status, str(lease.run_id), artifact_id=str(saved_artifact.id))
            controller = ResearchController(
                model,
                _RecordedTools(context, self.tools_factory(snapshot)),
                limits,
                on_event=public_event,
                memory=_RecordedMemory(context, ResearchMemory(self.engine, lease)),
            )
            result = await controller.investigate(
                snapshot.goal,
                mode=snapshot.mode,
                parent_result_ids=tuple(parent.result_id for parent in parents),
            )
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
                raise asyncio.CancelledError from None
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
