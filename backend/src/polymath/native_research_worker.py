"""Existing scientific controllers with one scoped, journalled native Meta model.

No import, configuration check, disabled runtime, or cached observation sends a
request. Fresh requests require a dedicated committed grant against the original
run activation. This module offers no model, endpoint, key, or budget overrides.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import re
import signal
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import asdict, dataclass
from typing import Protocol

from pydantic import SecretStr
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.artifact_controller import ArtifactLimits
from polymath.assessment_controller import AssessmentLimits
from polymath.config import Settings
from polymath.database import build_engine
from polymath.jobs import PUBLIC_INTERRUPTION_REASONS, JobLease, RunSnapshot, _owned
from polymath.literature import OpenLiteratureTools
from polymath.meta_probe_contracts import MetaObservation
from polymath.meta_probe_worker import _FinalizeDatabaseSettings
from polymath.meta_readiness_contracts import ReadinessPolicy, quote_proposal
from polymath.meta_research import MetaResearchStore, NativeResearchInput
from polymath.meta_research_contracts import (
    MetaResearchRequest,
    NativeDiscoveryProfile,
    NativeResearchPolicy,
    assess_meta_usage,
    build_meta_research_request,
    model_reply_from_observation,
    research_step_sha256,
)
from polymath.meta_research_dispatch import MetaResearchDispatcher, NativeResearchGrant
from polymath.meta_research_errors import META_RESEARCH_ERROR_STATUSES, MetaResearchError
from polymath.operator_auth import OperatorPolicy
from polymath.provider_pacing import DatabaseProviderPacer
from polymath.public_research_contracts import PublicResearchPolicy
from polymath.research.controller import ResearchLimits
from polymath.research.meta import MetaModel, ModelReply
from polymath.worker import (
    ExecutionStopped,
    ResearchWorker,
    WorkerPolicy,
    WorkerRuntimeProfile,
    _RunContext,
)

_REQUEST_ID = re.compile(r"model-(?!0000)[0-9]{4}")


def _dispatch_stop(error: MetaResearchError, *, public: bool = False) -> ExecutionStopped:
    code = error.code
    if public:
        code = {
            "meta_research_authority_expired": "public_research_authority_expired",
            "meta_research_prior_unresolved": "public_research_prior_unresolved",
            "meta_research_unavailable": "public_research_disabled",
        }.get(code, code)
    return ExecutionStopped(
        code
        if code in META_RESEARCH_ERROR_STATUSES or code in PUBLIC_INTERRUPTION_REASONS
        else "native_dispatch_not_safe"
    )


def _authorize_provider(session: Session, lease: JobLease) -> None:
    """Fence each free source request without carrying DB locks into HTTP."""
    try:
        _owned(session, lease)
        owner_alive = session.scalar(
            select(BrowserSession.expires_at > func.clock_timestamp())
            .join(ResearchRunAccess, ResearchRunAccess.session_hash == BrowserSession.token_hash)
            .where(ResearchRunAccess.run_id == lease.run_id)
        )
        if owner_alive is not True:
            from polymath.public_research_models import PublicResearchRunAccess

            raise ExecutionStopped(
                "public_research_requester_expired"
                if session.get(PublicResearchRunAccess, lease.run_id) is not None
                else "ownership_lost"
            )
    except ExecutionStopped:
        raise
    except Exception:
        # Ordinary provider exceptions become failed-search observations. Losing
        # ownership must instead stop the whole controller before another call.
        raise ExecutionStopped("native_source_authority_unavailable") from None


def _validate_native_limits(value, parent) -> None:
    timeout = value.model_timeout_seconds
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 30 < timeout <= 90:
        raise ValueError("invalid_native_orchestration_deadline")
    # Validate every historical controller constraint unchanged. Only the local
    # native orchestration deadline extends beyond the legacy 60-second cap.
    parent(**(asdict(value) | {"model_timeout_seconds": min(timeout, 60)}))


@dataclass(frozen=True)
class NativeResearchLimits(ResearchLimits):
    def __post_init__(self):
        _validate_native_limits(self, ResearchLimits)


@dataclass(frozen=True)
class NativeArtifactLimits(ArtifactLimits):
    def __post_init__(self):
        _validate_native_limits(self, ArtifactLimits)


@dataclass(frozen=True)
class NativeAssessmentLimits(AssessmentLimits):
    def __post_init__(self):
        _validate_native_limits(self, AssessmentLimits)


class _NativeModel(Protocol):
    async def observe_research(self, request: MetaResearchRequest) -> MetaObservation: ...


def configured_native_research_policies(
    settings: Settings,
) -> tuple[NativeResearchPolicy, ReadinessPolicy, OperatorPolicy]:
    """Capture explicit server configuration without constructing DB/model clients."""
    return (
        NativeResearchPolicy(
            paid_research_enabled=settings.paid_meta_research_enabled,
            native_account_id=settings.native_budget_account_id,
            model_id=settings.meta_model,
            api_key=settings.model_api_key,
        ),
        ReadinessPolicy(
            preparation_enabled=settings.budget_readiness_enabled,
            allowed_actor_ids=settings.budget_admin_actor_ids,
            max_hard_cap_microdollars=settings.budget_max_hard_cap_microdollars,
        ),
        OperatorPolicy(
            bootstrap_token=settings.operator_bootstrap_token,
            actor_id=settings.operator_actor_id,
            display_name=settings.operator_display_name,
        ),
    )


def native_research_configured(
    research: NativeResearchPolicy, readiness: ReadinessPolicy, operator: OperatorPolicy
) -> bool:
    return bool(
        research.paid_research_enabled
        and research.configured
        and readiness.preparation_enabled
        and operator.configured
        and operator.actor_id in readiness.allowed_actor_ids
    )


def original_runtime_profile(value: NativeResearchInput) -> WorkerRuntimeProfile:
    """Reconstruct original quantitative policy, never the latest activation."""
    proposal, profile = value.proposal, value.profile
    quote = quote_proposal(proposal)
    policy = WorkerPolicy(
        account_id=value.account_id,
        model_identity=proposal.model_id,
        reservation_microdollars=quote.per_call_reservation_microdollars,
        input_microdollars_per_million=proposal.pricing.input_microdollars_per_million,
        output_microdollars_per_million=proposal.pricing.output_microdollars_per_million,
        run_cap_microdollars=proposal.account.run_cap_microdollars,
    )
    common = {
        "max_model_calls": profile.max_model_calls,
        "max_actions": profile.max_actions,
        "max_repairs": profile.max_repairs,
        "max_output_tokens": profile.max_output_tokens,
        "max_context_characters": profile.max_context_characters,
        "model_timeout_seconds": profile.model_timeout_seconds,
        "tool_timeout_seconds": profile.tool_timeout_seconds,
    }
    # Only the selected task's limits are used by its controller. The complete
    # effective profile is pinned in the native fingerprint below; the other
    # dataclasses retain stable, validated values without inventing rate facts.
    if isinstance(profile, NativeDiscoveryProfile):
        from polymath.research.discovery import DiscoveryLimits

        research_limits = DiscoveryLimits(**common)
    else:
        research_limits = NativeResearchLimits(**common)
    artifact_limits = NativeArtifactLimits(
        **(
            common
            | {
                "max_model_calls": min(12, profile.max_model_calls),
                "max_actions": min(12, profile.max_actions),
            }
        )
    )
    assessment_limits = NativeAssessmentLimits(
        **(
            common
            | {
                "max_model_calls": min(12, profile.max_model_calls),
                "max_actions": min(12, profile.max_actions),
                "max_sources": profile.max_sources,
            }
        )
    )
    return WorkerRuntimeProfile(
        policy=policy,
        limits=research_limits,
        artifact_limits=artifact_limits,
        assessment_limits=assessment_limits,
        protocol_binding={
            "activation_sha256": value.activation_sha256,
            "profile_sha256": value.profile_sha256,
            "input_sha256": value.input_sha256,
        },
    )


class NativeRecordedModel:
    """The existing ResearchModel interface with native authority/accounting."""

    def __init__(
        self,
        context: _RunContext,
        store: MetaResearchStore,
        dispatcher: MetaResearchDispatcher,
        profile: WorkerRuntimeProfile,
        model_factory: Callable[[NativeResearchInput, NativeResearchGrant], _NativeModel],
    ):
        self.context = context
        self.store = store
        self.dispatcher = dispatcher
        self.profile = profile
        self.model_factory = model_factory
        self.step = 0
        self._input: NativeResearchInput | None = None

    async def complete(
        self, messages: list[dict[str, str]], *, max_output_tokens: int
    ) -> ModelReply:
        self.step += 1
        if not 1 <= self.step <= 9999:
            raise ExecutionStopped("native_request_limit")
        request_id = f"model-{self.step:04d}"
        await self.context.check_owner()
        if self._input is None:
            try:
                self._input = await asyncio.to_thread(
                    self.store.load_original_input, self.context.lease
                )
            except MetaResearchError as error:
                raise _dispatch_stop(error) from None
        value = self._input
        if self.profile.protocol_binding != {
            "activation_sha256": value.activation_sha256,
            "profile_sha256": value.profile_sha256,
            "input_sha256": value.input_sha256,
        }:
            raise ExecutionStopped("native_profile_conflict")
        request = build_meta_research_request(
            value.proposal, messages, max_output_tokens=max_output_tokens
        )
        request_hash = research_step_sha256(
            request,
            activation_sha256=value.activation_sha256,
            profile_sha256=value.profile_sha256,
            request_id=request_id,
        )
        try:
            observation = await asyncio.to_thread(
                self.dispatcher.get_recorded_observation,
                self.context.lease,
                request_id,
                request_hash,
            )
            if observation is None:
                grant = await asyncio.to_thread(
                    self.dispatcher.prepare,
                    self.context.lease,
                    request_id,
                    request,
                    request_hash,
                )
                if grant is None:
                    observation = await asyncio.to_thread(
                        self.dispatcher.get_recorded_observation,
                        self.context.lease,
                        request_id,
                        request_hash,
                    )
                    if observation is None:
                        raise ExecutionStopped("native_dispatch_already_authorized")
                else:
                    try:
                        model = self.model_factory(value, grant)
                        observation = await model.observe_research(request)
                        await asyncio.to_thread(self.dispatcher.complete, grant, observation)
                    except BaseException:
                        with suppress(Exception):
                            await asyncio.shield(
                                asyncio.to_thread(
                                    self.dispatcher.mark_unknown, grant, "worker_lost"
                                )
                            )
                        raise
        except asyncio.CancelledError:
            raise
        except MetaResearchError as error:
            raise _dispatch_stop(
                error, public=value.manifest.audience == "public_sessions"
            ) from None
        except Exception:
            raise ExecutionStopped("native_dispatch_not_safe") from None
        # No finish/action parsing before the exact bounded transport facts and
        # separately rounded billing have committed. Unknown is never a repair.
        usage = assess_meta_usage(value.proposal, request, observation)
        if usage.status != "known" or usage.usage_within_bounds is not True:
            raise ExecutionStopped("native_billing_not_safe")
        await self.context.check_owner()
        return model_reply_from_observation(value.proposal, request, observation)


def build_native_research_worker(
    engine: Engine,
    *,
    research_policy: NativeResearchPolicy,
    readiness_policy: ReadinessPolicy,
    operator_policy: OperatorPolicy,
    public_policy: PublicResearchPolicy | None = None,
    model_factory: Callable[[NativeResearchInput, NativeResearchGrant], _NativeModel] | None = None,
    tools_factory=None,
    openalex_api_key: SecretStr | None = None,
) -> ResearchWorker:
    """Compose the existing worker; the native adapter stays lazy until its grant."""
    store = MetaResearchStore(
        engine, research_policy, readiness_policy, operator_policy, public_policy=public_policy
    )
    dispatcher = MetaResearchDispatcher(
        engine, research_policy, readiness_policy, operator_policy, public_policy=public_policy
    )

    def native_factory(value: NativeResearchInput, grant: NativeResearchGrant) -> MetaModel:
        # Use the exact immutable secret snapshot checked at the grant boundary,
        # never a newer current policy or the worker's startup configuration.
        return MetaModel(
            api_key=grant.api_key,
            model=grant.model_id,
            live_enabled=True,
            request_timeout_seconds=grant.request_timeout_seconds,
        )

    def resolve(lease: JobLease, snapshot: RunSnapshot) -> WorkerRuntimeProfile:
        try:
            value = store.load_original_input(lease)
        except MetaResearchError as error:
            raise _dispatch_stop(error) from None
        if value.profile.task_kind != snapshot.task_kind:
            raise ExecutionStopped("native_task_conflict")
        return original_runtime_profile(value)

    def recorded(context, snapshot, profile):
        return NativeRecordedModel(
            context, store, dispatcher, profile, model_factory or native_factory
        )

    def forbidden_legacy_model(snapshot):
        raise ExecutionStopped("native_legacy_factory_forbidden")

    def source_tools(context: _RunContext, snapshot: RunSnapshot):
        return OpenLiteratureTools(
            openalex_api_key=openalex_api_key,
            provider_slot=DatabaseProviderPacer(
                engine,
                authorize=lambda session: _authorize_provider(session, context.lease),
                max_wait_seconds=10,
            ),
        )

    return ResearchWorker(
        engine,
        model_factory=forbidden_legacy_model,
        tools_factory=tools_factory
        or (lambda _: OpenLiteratureTools(openalex_api_key=openalex_api_key)),
        tools_context_factory=source_tools if tools_factory is None else None,
        policy=None,
        runtime_profile_resolver=resolve,
        recorded_model_factory=recorded,
        native_only=True,
        native_claim_account_id=research_policy.native_account_id,
        execution_enabled=native_research_configured(
            research_policy, readiness_policy, operator_policy
        ),
    )


async def run(
    settings: Settings | _FinalizeDatabaseSettings,
    *,
    once: bool,
    worker_id: str,
    finalize_only: uuid.UUID | None = None,
    request_id: str = "model-0001",
) -> int:
    if finalize_only is not None:
        engine = build_engine(settings)
        try:
            record = await asyncio.to_thread(
                MetaResearchStore(engine).finalize_known_call, finalize_only, request_id
            )
            if record.to_response()["usage"]["status"] != "known":
                print("Native research outcome remains unknown; hold retained and no request sent.")
                return 2
            print(
                "Native research billing finalized; no scientific completion or new send implied."
            )
            return 0
        except Exception:
            print("Native research billing finalization unavailable; no request sent.")
            return 2
        finally:
            engine.dispose()
    if not settings.paid_meta_research_enabled:
        print("Native research worker disabled; no job claimed or request sent.")
        return 2
    research, readiness, operator = configured_native_research_policies(settings)
    if not native_research_configured(research, readiness, operator):
        print("Native research worker unavailable; no job claimed or request sent.")
        return 2
    engine = build_engine(settings)
    try:
        worker = build_native_research_worker(
            engine,
            research_policy=research,
            readiness_policy=readiness,
            operator_policy=operator,
            public_policy=PublicResearchPolicy(enabled=settings.public_meta_research_enabled),
            openalex_api_key=settings.openalex_api_key,
        )
        if once:
            result = await worker.run_once(worker_id)
            print(f"Native research worker result: {result.status}")
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
    parser = argparse.ArgumentParser(description="Run explicitly activated native Meta research")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id", default="native-research-worker")
    parser.add_argument("--finalize-only", type=uuid.UUID, metavar="RUN_ID")
    parser.add_argument("--request-id", default=None, metavar="MODEL_STEP")
    arguments = parser.parse_args()
    if (
        not 1 <= len(arguments.worker_id) <= 128
        or any(ord(character) < 33 or ord(character) == 127 for character in arguments.worker_id)
        or (arguments.finalize_only is not None and arguments.once)
        or (arguments.request_id is not None and arguments.finalize_only is None)
        or (
            arguments.request_id is not None and _REQUEST_ID.fullmatch(arguments.request_id) is None
        )
    ):
        parser.error("invalid native worker arguments")
    try:
        return asyncio.run(
            run(
                _FinalizeDatabaseSettings() if arguments.finalize_only is not None else Settings(),
                once=arguments.once,
                worker_id=arguments.worker_id,
                finalize_only=arguments.finalize_only,
                request_id=arguments.request_id or "model-0001",
            )
        )
    except Exception:
        print("Native research worker unavailable; inspect private service diagnostics.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
