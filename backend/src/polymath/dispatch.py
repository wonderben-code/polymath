"""Atomic job/budget dispatch boundary; this module never makes network requests.

A committed grant is the dispatch authorization point. Cancellation afterwards
cannot unsend that authorized operation, but prevents subsequent grants. Callers
must never send on a duplicate (None) result or retry an ambiguous request.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from polymath.budget import (
    BudgetService,
    Reservation,
    _reservation,
)
from polymath.budget import (
    _integer as _money_integer,
)
from polymath.budget import (
    _key as _budget_key,
)
from polymath.budget_models import BudgetReservation
from polymath.jobs import (
    ExternalRequestPending,
    JobLease,
    _bounded_text,
    _event,
    _json_object,
    _locked,
    _owned,
)
from polymath.models import ResearchExternalRequest, ResearchRun


class DispatchError(Exception):
    """Static control code; never includes provider content or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DispatchGrant:
    run_id: uuid.UUID
    request_id: str
    account_id: str
    reservation_id: uuid.UUID
    lease_token: int
    request_hash: str


def _hash(value: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("request_hash must be a lowercase SHA256 digest")
    return value


def _call_key(run_id: uuid.UUID, request_id: str) -> str:
    return "dispatch:" + hashlib.sha256(f"{run_id}:{request_id}".encode()).hexdigest()


class DispatchCoordinator:
    def __init__(self, engine: Engine, *, clock: Callable[[], datetime] | None = None):
        self.engine = engine
        self.budget = BudgetService(engine, clock=clock)

    def prepare_model_call(
        self,
        lease: JobLease,
        *,
        account_id: str,
        request_id: str,
        request_hash: str,
        estimated_microdollars: int,
    ) -> DispatchGrant | None:
        """Grant exactly once, committing lease/budget/pending-request together.

        Hash the complete immutable provider request, including model, endpoint,
        messages, and token limits. A duplicate matching request returns None;
        callers must retrieve a recorded response or stop, never send again.
        """
        _bounded_text(request_id, 128, "Request ID")
        _hash(request_hash)
        with Session(self.engine) as session, session.begin():
            # Always account -> job, including completion. Check lease after any
            # account-lock wait so an expired/cancelled worker cannot dispatch.
            self.budget._account(session, account_id)
            run = _owned(session, lease)
            prior = session.get(ResearchExternalRequest, (run.id, request_id))
            if prior is not None:
                reservation = session.get(BudgetReservation, prior.reservation_id)
                if (
                    prior.reservation_id is None
                    or prior.request_hash != request_hash
                    or reservation is None
                    or reservation.account_id != account_id
                    or reservation.estimated_microdollars != estimated_microdollars
                ):
                    raise DispatchError("dispatch_identity_conflict")
                return None
            if run.external_request_in_flight:
                raise ExternalRequestPending("An external request requires an observed outcome")
            reservation = self.budget.reserve_in_session(
                session,
                account_id,
                run.id,
                call_key=_call_key(run.id, request_id),
                estimated_microdollars=estimated_microdollars,
            )
            if not reservation.newly_reserved:
                raise DispatchError("dispatch_reservation_already_exists")
            if not self.budget.claim_dispatch_in_session(session, reservation.id):
                raise DispatchError("dispatch_already_claimed")
            session.add(
                ResearchExternalRequest(
                    run_id=run.id,
                    request_id=request_id,
                    status="prepared",
                    reservation_id=reservation.id,
                    dispatch_lease_token=lease.token,
                    request_hash=request_hash,
                )
            )
            run.external_request_id = request_id
            run.external_request_in_flight = True
            _event(
                session,
                run,
                f"external_started:{request_id}",
                "external_started",
                "Model operation authorized and recorded before dispatch; outcome pending",
            )
            return DispatchGrant(
                run.id, request_id, account_id, reservation.id, lease.token, request_hash
            )

    def _bound(
        self, session: Session, grant: DispatchGrant
    ) -> tuple[ResearchRun, ResearchExternalRequest, BudgetReservation]:
        self.budget._account(session, grant.account_id)
        run = _locked(session, grant.run_id)
        request = session.get(ResearchExternalRequest, (grant.run_id, grant.request_id))
        reservation = session.get(BudgetReservation, grant.reservation_id)
        if (
            request is None
            or reservation is None
            or request.reservation_id != grant.reservation_id
            or request.dispatch_lease_token != grant.lease_token
            or request.request_hash != grant.request_hash
            or reservation.run_id != grant.run_id
            or reservation.account_id != grant.account_id
            or reservation.call_key != _call_key(grant.run_id, grant.request_id)
        ):
            raise DispatchError("dispatch_binding_mismatch")
        return run, request, reservation

    def get_recorded_response(
        self, lease: JobLease, *, request_id: str, request_hash: str
    ) -> dict | None:
        """Replay only an observed identical request for the current job owner.

        A newly acquired lease may replay an earlier observed response rather
        than charging again. Prepared/unknown operations are not retryable.
        """
        _bounded_text(request_id, 128, "Request ID")
        _hash(request_hash)
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            request = session.get(ResearchExternalRequest, (run.id, request_id))
            if request is None:
                return None
            if request.reservation_id is None or request.request_hash != request_hash:
                raise DispatchError("dispatch_identity_conflict")
            if request.status == "prepared":
                raise DispatchError("dispatch_outcome_unknown")
            if request.observation is None:
                raise DispatchError("dispatch_response_unavailable")
            return _json_object(request.observation)

    def get_recorded_grant(self, run_id: uuid.UUID, *, request_id: str) -> DispatchGrant | None:
        """Internal recovery metadata only. This NEVER authorizes another send."""
        _bounded_text(request_id, 128, "Request ID")
        with Session(self.engine) as session, session.begin():
            run = _locked(session, run_id)
            request = session.get(ResearchExternalRequest, (run.id, request_id))
            if request is None or request.reservation_id is None:
                return None
            row = session.get(BudgetReservation, request.reservation_id)
            if row is None:
                raise DispatchError("dispatch_binding_mismatch")
            return DispatchGrant(
                run.id,
                request_id,
                row.account_id,
                row.id,
                request.dispatch_lease_token,
                request.request_hash,
            )

    def complete(
        self,
        grant: DispatchGrant,
        *,
        observation: dict,
        actual_microdollars: int | None,
        settlement_key: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> Reservation:
        """Persist a known response and its accounting, even after lost ownership.

        No checkpoint, outcome, or worker lease is changed. Missing cost is an
        unknown hold, never zero. A known late response may reconcile a prior
        timeout using explicit response/usage evidence.
        """
        data = _json_object(observation)
        with Session(self.engine) as session, session.begin():
            run, request, row = self._bound(session, grant)
            if request.status == "reconciled":
                self._audit_late_conflict(
                    session,
                    run,
                    request,
                    grant,
                    data,
                    actual_microdollars,
                    settlement_key,
                    prompt_tokens,
                    completion_tokens,
                )
            else:
                if request.observation is not None and request.observation != data:
                    raise DispatchError("dispatch_response_conflict")
                if actual_microdollars is None:
                    if (
                        settlement_key is not None
                        or prompt_tokens is not None
                        or completion_tokens is not None
                    ):
                        raise ValueError("Unknown cost cannot include settlement fields")
                    if row.status == "settled":
                        raise DispatchError("dispatch_accounting_conflict")
                    reservation = self.budget.mark_unknown_in_session(
                        session, row.id, reason="usage_unavailable"
                    )
                else:
                    if settlement_key is None:
                        raise ValueError("Known cost requires response/billing evidence")
                    reservation = self.budget.settle_in_session(
                        session,
                        row.id,
                        actual_microdollars=actual_microdollars,
                        settlement_key=settlement_key,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        reconciliation=row.status == "unknown",
                    )
                request.status = "observed"
                request.observation = data
                if run.external_request_id == grant.request_id:
                    run.external_request_in_flight = False
                _event(
                    session,
                    run,
                    f"external_observed:{grant.request_id}",
                    "external_observed",
                    "The model operation returned an observed outcome; accounting retained",
                )
                return reservation
        # Raise only after the audit/freeze transaction commits.
        raise DispatchError("dispatch_reconciliation_conflict")

    def _audit_late_conflict(
        self,
        session: Session,
        run: ResearchRun,
        request: ResearchExternalRequest,
        grant: DispatchGrant,
        observation: dict,
        actual_microdollars: int | None,
        settlement_key: str | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> None:
        if actual_microdollars is None:
            if (
                settlement_key is not None
                or prompt_tokens is not None
                or completion_tokens is not None
            ):
                raise ValueError("Unknown cost cannot include settlement fields")
        else:
            _money_integer(actual_microdollars, "actual_microdollars")
            _budget_key(settlement_key, "settlement_key", maximum=200)
            if (prompt_tokens is None) != (completion_tokens is None):
                raise ValueError("Both token counts must be known, or both unknown")
            if prompt_tokens is not None:
                _money_integer(prompt_tokens, "prompt_tokens", maximum=2**63 - 1)
                _money_integer(completion_tokens, "completion_tokens", maximum=2**63 - 1)
        usage = {
            "actual_microdollars": actual_microdollars,
            "settlement_key": settlement_key,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        }
        if request.late_observation is not None and (
            request.late_observation != observation or request.late_usage != usage
        ):
            raise DispatchError("dispatch_late_response_conflict")
        request.late_observation = observation
        request.late_usage = usage
        account = self.budget._account(session, grant.account_id)
        account.frozen = True
        account.frozen_reason = "dispatch_reconciliation_conflict"
        _event(
            session,
            run,
            f"external_late_conflict:{grant.request_id}",
            "accounting_conflict",
            "A late outcome conflicts with reconciled work; execution frozen for billing review",
        )

    def mark_unknown(self, grant: DispatchGrant, *, reason: str) -> Reservation:
        """Retain the hold and pending request. Never create a second dispatch."""
        with Session(self.engine) as session, session.begin():
            run, request, row = self._bound(session, grant)
            if request.status != "prepared" or row.status == "settled":
                # A racing completion wins over a late timeout notification.
                return _reservation(row)
            reservation = self.budget.mark_unknown_in_session(session, row.id, reason=reason)
            _event(
                session,
                run,
                f"external_unknown:{grant.request_id}",
                "external_unknown",
                "Model outcome is ambiguous; its full budget hold remains reserved",
            )
            return reservation

    def reconcile(
        self,
        grant: DispatchGrant,
        *,
        observation: dict | None,
        actual_microdollars: int,
        evidence_key: str,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> Reservation:
        """Internal operator seam: resolve a bound ambiguity from actual evidence.

        This method is not authentication or permission to infer a free call.
        It never resumes a run or permits redispatch of the historical request.
        """
        data = _json_object(observation) if observation is not None else None
        with Session(self.engine) as session, session.begin():
            run, request, row = self._bound(session, grant)
            if request.observation is not None and request.observation != data:
                raise DispatchError("dispatch_response_conflict")
            if row.status == "in_flight":
                # A hard crash can occur before the worker records unknown.
                # Explicit billing evidence can reconcile that same ambiguity;
                # retain the hold until settlement in this transaction commits.
                self.budget.mark_unknown_in_session(session, row.id, reason="worker_lost")
            reservation = self.budget.settle_in_session(
                session,
                row.id,
                actual_microdollars=actual_microdollars,
                settlement_key=evidence_key,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                reconciliation=True,
            )
            request.observation = data
            request.status = "reconciled"
            request.reconciliation = {"evidence_key": evidence_key}
            if run.external_request_id == grant.request_id:
                run.external_request_in_flight = False
            _event(
                session,
                run,
                f"external_reconciled:{grant.request_id}",
                "external_reconciled",
                "The model operation and its accounting were explicitly reconciled",
            )
            return reservation
