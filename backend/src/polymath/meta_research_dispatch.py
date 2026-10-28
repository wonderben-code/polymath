"""One native scientific send per journal entry, with original-price late accounting."""

import re
from dataclasses import dataclass, field

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from polymath.budget import BudgetService, NativeResearchPermit
from polymath.budget_models import BudgetReservation, BudgetRunLimit
from polymath.dispatch import DispatchCoordinator, DispatchGrant, _call_key
from polymath.evidence import _canonical, _hash
from polymath.jobs import _event, _locked, _owned
from polymath.meta_probe_contracts import MetaObservation, observation_sha256
from polymath.meta_readiness import _account_lock
from polymath.meta_research import (
    MetaResearchError,
    MetaResearchStore,
    _activation,
    _dto,
    _fresh_locked_run,
    _original_input,
)
from polymath.meta_research_models import NativeResearchCallReceipt, NativeResearchCallRequest
from polymath.models import ResearchExternalRequest
from polymath.operator_models import OperatorSession


@dataclass(frozen=True)
class NativeResearchGrant(DispatchGrant):
    api_key: SecretStr = field(repr=False)
    model_id: str
    request_timeout_seconds: float


@dataclass(frozen=True)
class NativeResearchCallRecord:
    payload: dict

    def to_response(self):
        return self.payload


def _request_id(value):
    if (
        not isinstance(value, str)
        or re.fullmatch(r"model-[0-9]{4}", value) is None
        or int(value[-4:]) < 1
    ):
        raise MetaResearchError("meta_research_invalid_request")
    return value


def _call_input(session, value, request_id):
    from polymath.meta_research_contracts import (
        MetaResearchRequest,
        meta_research_request_sha256,
        research_step_sha256,
    )

    row = session.get(NativeResearchCallRequest, (value.run_id, _request_id(request_id)))
    if row is None:
        raise MetaResearchError("meta_research_outcome_unknown")
    request = _dto(MetaResearchRequest, row.request)
    if row.request_sha256 != meta_research_request_sha256(
        request
    ) or row.step_sha256 != research_step_sha256(
        request,
        activation_sha256=value.activation_sha256,
        profile_sha256=value.profile_sha256,
        request_id=request_id,
    ):
        raise MetaResearchError("meta_research_storage_unavailable")
    return row, request


def _known_payload(session, value, request_id):
    from polymath.meta_readiness_contracts import quote_proposal
    from polymath.meta_research_contracts import assess_meta_usage

    call, request = _call_input(session, value, request_id)
    journal = session.get(ResearchExternalRequest, (value.run_id, request_id))
    if journal is None or journal.status != "observed" or journal.observation is None:
        raise MetaResearchError("meta_research_outcome_unknown")
    observation = _dto(MetaObservation, journal.observation)
    usage = assess_meta_usage(value.proposal, request, observation)
    reservation = session.get(BudgetReservation, call.reservation_id)
    known = usage.actual_microdollars is not None
    if (
        journal.reservation_id != call.reservation_id
        or journal.request_hash != call.step_sha256
        or reservation is None
        or reservation.run_id != value.run_id
        or reservation.account_id != value.account_id
        or reservation.call_key != _call_key(value.run_id, request_id)
        or reservation.estimated_microdollars
        != quote_proposal(value.proposal).per_call_reservation_microdollars
        or journal.dispatch_lease_token is None
        or journal.dispatch_lease_token < 1
        or reservation.actual_microdollars != usage.actual_microdollars
        or reservation.status != ("settled" if known else "unknown")
        or (
            known
            and (
                reservation.settlement_key != "native-research:" + observation_sha256(observation)
                or reservation.prompt_tokens != usage.input_tokens
                or reservation.completion_tokens != usage.output_tokens
            )
        )
    ):
        raise MetaResearchError("meta_research_storage_unavailable")
    data = usage.model_dump(mode="json")
    for name in ("input_tokens", "output_tokens"):
        data[name] = str(data[name]) if data[name] is not None else None
    return {
        "run_id": str(value.run_id),
        "request_id": request_id,
        "activation_sha256": value.activation_sha256,
        "profile_sha256": value.profile_sha256,
        "request_sha256": call.request_sha256,
        "step_sha256": call.step_sha256,
        "observation_sha256": observation_sha256(observation),
        "reservation_id": str(reservation.id),
        "usage": data,
        "scientific_completion_implied": False,
    }


def finalize_known_call(engine, run_id, request_id):
    """Original immutable usage only; no live key/role/flag, client, send or repair."""
    with Session(engine) as session, session.begin():
        value = _original_input(session, run_id)
        _account_lock(session, value.account_id)
        _locked(session, run_id)
        payload = _known_payload(session, value, _request_id(request_id))
        row = session.get(NativeResearchCallReceipt, (run_id, request_id))
        if row is not None:
            if row.payload != payload or row.payload_sha256 != _hash(_canonical(payload)):
                raise MetaResearchError("meta_research_storage_unavailable")
            return NativeResearchCallRecord(row.payload)
        session.add(
            NativeResearchCallReceipt(
                run_id=run_id,
                request_id=request_id,
                reservation_id=payload["reservation_id"],
                manifest_sha256=value.activation_sha256,
                request_sha256=payload["request_sha256"],
                observation_sha256=payload["observation_sha256"],
                payload=payload,
                payload_sha256=_hash(_canonical(payload)),
            )
        )
        return NativeResearchCallRecord(payload)


class MetaResearchDispatcher:
    def __init__(
        self,
        engine,
        research_policy=None,
        readiness_policy=None,
        operator_policy=None,
        *,
        public_policy=None,
    ):
        self.engine = engine
        self.store = MetaResearchStore(
            engine, research_policy, readiness_policy, operator_policy, public_policy=public_policy
        )
        self.coordinator = DispatchCoordinator(engine)
        self.budget = BudgetService(engine)

    def get_recorded_observation(self, lease, request_id, request_hash):
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            value = _original_input(session, run.id, check_input=True)
            journal = session.get(ResearchExternalRequest, (run.id, _request_id(request_id)))
            if journal is None:
                return None
            call, _ = _call_input(session, value, request_id)
            if journal.request_hash != request_hash or call.step_sha256 != request_hash:
                raise MetaResearchError("meta_research_input_changed")
            if journal.status != "observed" or journal.observation is None:
                raise MetaResearchError("meta_research_outcome_unknown")
            _known_payload(session, value, request_id)
            return _dto(MetaObservation, journal.observation)

    def prepare(self, lease, request_id, request, request_hash):
        from polymath.meta_readiness_contracts import quote_proposal
        from polymath.meta_research_contracts import (
            MetaResearchRequest,
            meta_research_request_sha256,
            research_policy_sha256,
            research_step_sha256,
        )

        request_id = _request_id(request_id)
        request = _dto(MetaResearchRequest, request)
        with Session(self.engine) as session, session.begin():
            value = _original_input(session, lease.run_id)
            approval, _, manifest, proposal = _activation(session, value.approval_id)
            operator = session.scalar(
                select(OperatorSession)
                .where(OperatorSession.token_hash == approval.operator_session_hash)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            account = _account_lock(session, value.account_id)
            _fresh_locked_run(session, lease.run_id)
            run = _owned(session, lease)
            value = _original_input(session, run.id, check_input=True)
            expected = research_step_sha256(
                request,
                activation_sha256=value.activation_sha256,
                profile_sha256=value.profile_sha256,
                request_id=request_id,
            )
            if (
                expected != request_hash
                or request.payload.model != proposal.model_id
                or request.payload.max_tokens != value.profile.max_output_tokens
                or sum(len(m.content) for m in request.payload.messages)
                > value.profile.max_context_characters
                or int(request_id[-4:]) > value.profile.max_model_calls
            ):
                raise MetaResearchError("meta_research_invalid_request")
            prior = session.get(ResearchExternalRequest, (run.id, request_id))
            if prior is not None:
                call, _ = _call_input(session, value, request_id)
                if prior.request_hash != expected or call.step_sha256 != expected:
                    raise MetaResearchError("meta_research_input_changed")
                return None
            self.store._approval_current(
                session, approval, manifest, proposal, account, operator=operator, run_id=run.id
            )
            previous = session.scalars(
                select(NativeResearchCallRequest).where(NativeResearchCallRequest.run_id == run.id)
            ).all()
            if len(previous) + 1 != int(request_id[-4:]) or run.external_request_in_flight:
                raise MetaResearchError("meta_research_outcome_unknown")
            limit = session.get(BudgetRunLimit, (account.id, run.id))
            if (
                limit is None
                or limit.max_calls != value.profile.max_model_calls
                or limit.cap_microdollars != proposal.account.run_cap_microdollars
            ):
                raise MetaResearchError("meta_research_account_conflict")
            estimate = quote_proposal(proposal).per_call_reservation_microdollars
            permit = NativeResearchPermit(
                account.id, run.id, value.activation_sha256, value.armed_epoch, expected
            )
            reservation = self.budget.reserve_in_session(
                session,
                account.id,
                run.id,
                call_key=_call_key(run.id, request_id),
                estimated_microdollars=estimate,
                native_permit=permit,
            )
            if not reservation.newly_reserved or not self.budget.claim_dispatch_in_session(
                session, reservation.id, native_permit=permit
            ):
                raise MetaResearchError("meta_research_outcome_unknown")
            session.add(
                NativeResearchCallRequest(
                    run_id=run.id,
                    request_id=request_id,
                    reservation_id=reservation.id,
                    request=request.model_dump(mode="json"),
                    request_sha256=meta_research_request_sha256(request),
                    step_sha256=expected,
                )
            )
            session.add(
                ResearchExternalRequest(
                    run_id=run.id,
                    request_id=request_id,
                    status="prepared",
                    reservation_id=reservation.id,
                    dispatch_lease_token=lease.token,
                    request_hash=expected,
                )
            )
            run.external_request_id = request_id
            run.external_request_in_flight = True
            _event(
                session,
                run,
                "native_grant:" + request_id,
                "external_started",
                "Native model call reserved and authorized before its one send",
            )
            session.flush()
            self.store._approval_current(
                session,
                approval,
                manifest,
                proposal,
                account,
                operator=operator,
                reserve=False,
                pending_request=(run.id, request_id),
                run_id=run.id,
            )
            _owned(session, lease)
            self.budget._scope_authorized(session, account, run.id, permit)
            self.budget._authorized(account, native_permit=permit)
            captured = self.store.research_policy
            if research_policy_sha256(captured) != approval.research_policy_sha256:
                raise MetaResearchError("meta_research_authority_expired")
            return NativeResearchGrant(
                run.id,
                request_id,
                account.id,
                reservation.id,
                lease.token,
                expected,
                captured.api_key,
                captured.model_id,
                value.profile.request_timeout_seconds,
            )

    def complete(self, grant, observation):
        from polymath.meta_research_contracts import assess_meta_usage

        observation = _dto(MetaObservation, observation)
        with Session(self.engine) as session:
            value = _original_input(session, grant.run_id)
            call, request = _call_input(session, value, grant.request_id)
            if (
                call.step_sha256 != grant.request_hash
                or call.reservation_id != grant.reservation_id
                or grant.account_id != value.account_id
            ):
                raise MetaResearchError("meta_research_storage_unavailable")
            usage = assess_meta_usage(value.proposal, request, observation)
        known = usage.actual_microdollars is not None
        return self.coordinator.complete(
            grant,
            observation=observation.model_dump(mode="json"),
            actual_microdollars=usage.actual_microdollars,
            settlement_key="native-research:" + observation_sha256(observation) if known else None,
            prompt_tokens=usage.input_tokens if known else None,
            completion_tokens=usage.output_tokens if known else None,
            freeze_reason="native_research_" + usage.reason_code if usage.freeze_required else None,
        )

    def mark_unknown(self, grant, reason="transport_error"):
        if reason not in {"timeout", "transport_error", "worker_lost", "usage_unavailable"}:
            reason = "transport_error"
        return self.coordinator.mark_unknown(grant, reason=reason)

    def finalize_known_call(self, run_id, request_id):
        return finalize_known_call(self.engine, run_id, request_id)
