"""PostgreSQL monetary reservations; no model calls or public API integration.

All ledger mutations lock their account row first. A reservation is not dispatch
permission: claim_dispatch must succeed exactly once before sending a request.
Unknown outcomes retain their worst-case charge and in-flight slot until explicit
billing/usage evidence reconciles them. Monetary values are integer microdollars.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import Engine, case, func, or_, select
from sqlalchemy.orm import Session

from polymath.budget_models import BudgetAccount, BudgetReservation, BudgetRunLimit

MAX_MONEY = 9_000_000_000_000_000
KEY_PATTERN = re.compile(r"[A-Za-z0-9_.:/-]+")
OPEN_STATES = ("reserved", "in_flight", "unknown")


class BudgetError(Exception):
    """Static policy code; no secrets, prompts, or provider response bodies."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Reservation:
    id: uuid.UUID
    account_id: str
    run_id: uuid.UUID
    call_key: str
    estimated_microdollars: int
    actual_microdollars: int | None
    status: str
    reserved_day: date
    newly_reserved: bool = False


@dataclass(frozen=True)
class BudgetUsage:
    committed_microdollars: int
    daily_committed_microdollars: int
    in_flight: int
    reservations: int
    execution_enabled: bool
    pricing_verified: bool
    kill_switch: bool
    frozen: bool


def _integer(value: int, name: str, *, minimum: int = 0, maximum: int = MAX_MONEY) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer within the supported bounds")
    return value


def _key(value: str, name: str, *, maximum: int = 120) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or not KEY_PATTERN.fullmatch(value)
    ):
        raise ValueError(f"{name} must be a bounded identifier, not free text")
    return value


def _run_id(value: uuid.UUID) -> uuid.UUID:
    if not isinstance(value, uuid.UUID):
        raise ValueError("run_id must be a UUID")
    return value


def _reservation(row: BudgetReservation, *, newly_reserved: bool = False) -> Reservation:
    return Reservation(
        id=row.id,
        account_id=row.account_id,
        run_id=row.run_id,
        call_key=row.call_key,
        estimated_microdollars=row.estimated_microdollars,
        actual_microdollars=row.actual_microdollars,
        status=row.status,
        reserved_day=row.reserved_day,
        newly_reserved=newly_reserved,
    )


class BudgetService:
    def __init__(self, engine: Engine, *, clock: Callable[[], datetime] | None = None):
        if engine.dialect.name != "postgresql":
            raise ValueError("BudgetService requires PostgreSQL row-lock semantics")
        self.engine = engine
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise ValueError("Budget clock must provide timezone-aware datetimes")
        return now.astimezone(UTC)

    @staticmethod
    def _account(session: Session, account_id: str) -> BudgetAccount:
        row = session.scalar(
            select(BudgetAccount).where(BudgetAccount.id == account_id).with_for_update()
        )
        if row is None:
            raise BudgetError("account_not_configured")
        return row

    @staticmethod
    def _authorized(account: BudgetAccount) -> None:
        if account.kill_switch:
            raise BudgetError("operator_kill_switch")
        if account.frozen:
            raise BudgetError("account_frozen")
        if not account.execution_enabled or not account.pricing_verified:
            raise BudgetError("paid_execution_disabled")

    @staticmethod
    def _committed_expression():
        return case(
            (BudgetReservation.status == "settled", BudgetReservation.actual_microdollars),
            else_=BudgetReservation.estimated_microdollars,
        )

    @classmethod
    def _usage(cls, session: Session, account: BudgetAccount, day: date) -> BudgetUsage:
        committed = cls._committed_expression()
        row = session.execute(
            select(
                func.coalesce(func.sum(committed), 0),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                or_(
                                    BudgetReservation.reserved_day == day,
                                    BudgetReservation.settled_day == day,
                                    BudgetReservation.status.in_(OPEN_STATES),
                                ),
                                committed,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(
                        case(
                            (BudgetReservation.status.in_(OPEN_STATES), 1),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.count(BudgetReservation.id),
            ).where(BudgetReservation.account_id == account.id)
        ).one()
        return BudgetUsage(
            committed_microdollars=int(row[0]),
            daily_committed_microdollars=int(row[1]),
            in_flight=int(row[2]),
            reservations=int(row[3]),
            execution_enabled=account.execution_enabled,
            pricing_verified=account.pricing_verified,
            kill_switch=account.kill_switch,
            frozen=account.frozen,
        )

    @classmethod
    def _run_usage(cls, session: Session, account_id: str, run_id: uuid.UUID) -> tuple[int, int]:
        row = session.execute(
            select(
                func.coalesce(func.sum(cls._committed_expression()), 0),
                func.count(BudgetReservation.id),
            ).where(
                BudgetReservation.account_id == account_id,
                BudgetReservation.run_id == run_id,
            )
        ).one()
        return int(row[0]), int(row[1])

    def create_account(
        self,
        account_id: str,
        *,
        hard_cap_microdollars: int,
        daily_cap_microdollars: int,
        max_in_flight: int = 1,
    ) -> None:
        account_id = _key(account_id, "account_id")
        _integer(hard_cap_microdollars, "hard_cap_microdollars")
        _integer(daily_cap_microdollars, "daily_cap_microdollars")
        _integer(max_in_flight, "max_in_flight", minimum=1, maximum=100)
        with Session(self.engine) as session, session.begin():
            if session.get(BudgetAccount, account_id) is not None:
                raise BudgetError("account_already_configured")
            session.add(
                BudgetAccount(
                    id=account_id,
                    hard_cap_microdollars=hard_cap_microdollars,
                    daily_cap_microdollars=daily_cap_microdollars,
                    max_in_flight=max_in_flight,
                    created_at=self._now(),
                    execution_enabled=False,
                    pricing_verified=False,
                    kill_switch=False,
                    frozen=False,
                )
            )

    def authorize_execution(self, account_id: str, *, pricing_reference: str) -> None:
        """Operator-only integration point, after external pricing/cap verification.

        Recording a reference cannot itself verify real provider pricing. No
        default configuration invokes this method, and no call is dispatched here.
        """
        account_id = _key(account_id, "account_id")
        reference = _key(pricing_reference, "pricing_reference", maximum=200)
        with Session(self.engine) as session, session.begin():
            row = self._account(session, account_id)
            row.pricing_reference = reference
            row.pricing_verified = True
            row.execution_enabled = True

    def disable_execution(self, account_id: str) -> None:
        with Session(self.engine) as session, session.begin():
            self._account(session, _key(account_id, "account_id")).execution_enabled = False

    def set_kill_switch(self, account_id: str, *, active: bool) -> None:
        if type(active) is not bool:
            raise ValueError("active must be a bool")
        with Session(self.engine) as session, session.begin():
            self._account(session, _key(account_id, "account_id")).kill_switch = active

    def configure_run(
        self,
        account_id: str,
        run_id: uuid.UUID,
        *,
        cap_microdollars: int,
        max_calls: int,
    ) -> None:
        account_id = _key(account_id, "account_id")
        _run_id(run_id)
        _integer(cap_microdollars, "cap_microdollars")
        _integer(max_calls, "max_calls", minimum=1, maximum=1000)
        with Session(self.engine) as session, session.begin():
            self._account(session, account_id)
            prior = session.get(BudgetRunLimit, (account_id, run_id))
            if prior:
                if prior.cap_microdollars != cap_microdollars or prior.max_calls != max_calls:
                    raise BudgetError("run_configuration_conflict")
                return
            session.add(
                BudgetRunLimit(
                    account_id=account_id,
                    run_id=run_id,
                    cap_microdollars=cap_microdollars,
                    max_calls=max_calls,
                )
            )

    def reserve(
        self,
        account_id: str,
        run_id: uuid.UUID,
        *,
        call_key: str,
        estimated_microdollars: int,
    ) -> Reservation:
        account_id = _key(account_id, "account_id")
        _run_id(run_id)
        call_key = _key(call_key, "call_key")
        _integer(estimated_microdollars, "estimated_microdollars", minimum=1)
        with Session(self.engine) as session, session.begin():
            account = self._account(session, account_id)
            now = self._now()
            prior = session.scalar(
                select(BudgetReservation).where(
                    BudgetReservation.account_id == account_id,
                    BudgetReservation.call_key == call_key,
                )
            )
            if prior:
                if prior.run_id != run_id or prior.estimated_microdollars != estimated_microdollars:
                    raise BudgetError("reservation_idempotency_conflict")
                return _reservation(prior)
            self._authorized(account)
            limit = session.get(BudgetRunLimit, (account_id, run_id))
            if limit is None:
                raise BudgetError("run_budget_not_configured")
            usage = self._usage(session, account, now.date())
            run_committed, run_calls = self._run_usage(session, account_id, run_id)
            if (
                usage.committed_microdollars + estimated_microdollars
                > account.hard_cap_microdollars
            ):
                raise BudgetError("global_budget_exceeded")
            if (
                usage.daily_committed_microdollars + estimated_microdollars
                > account.daily_cap_microdollars
            ):
                raise BudgetError("daily_budget_exceeded")
            if run_committed + estimated_microdollars > limit.cap_microdollars:
                raise BudgetError("run_budget_exceeded")
            if run_calls >= limit.max_calls:
                raise BudgetError("run_call_limit")
            if usage.in_flight >= account.max_in_flight:
                raise BudgetError("concurrency_limit")
            row = BudgetReservation(
                account_id=account_id,
                run_id=run_id,
                call_key=call_key,
                estimated_microdollars=estimated_microdollars,
                status="reserved",
                reserved_day=now.date(),
                created_at=now,
            )
            session.add(row)
            session.flush()
            return _reservation(row, newly_reserved=True)

    @staticmethod
    def _locate(session: Session, reservation_id: uuid.UUID) -> BudgetReservation:
        if not isinstance(reservation_id, uuid.UUID):
            raise ValueError("reservation_id must be a UUID")
        row = session.get(BudgetReservation, reservation_id)
        if row is None:
            raise BudgetError("reservation_not_found")
        return row

    def claim_dispatch(self, reservation_id: uuid.UUID) -> bool:
        """True exactly once. An idempotent reserve retry never permits another call."""
        with Session(self.engine) as session, session.begin():
            row = self._locate(session, reservation_id)
            account = self._account(session, row.account_id)
            session.refresh(row)
            if row.status != "reserved":
                return False
            self._authorized(account)
            row.status = "in_flight"
            row.dispatched_at = self._now()
            # A queued reservation may cross midnight before its actual call.
            # Its eventual settled charge belongs to the dispatch day.
            row.reserved_day = row.dispatched_at.date()
            return True

    def mark_unknown(self, reservation_id: uuid.UUID, *, reason: str) -> Reservation:
        if reason not in {"timeout", "transport_error", "worker_lost", "usage_unavailable"}:
            raise ValueError("Unknown outcome requires a supported reason code")
        with Session(self.engine) as session, session.begin():
            row = self._locate(session, reservation_id)
            self._account(session, row.account_id)
            session.refresh(row)
            if row.status == "settled":
                raise BudgetError("reservation_already_settled")
            row.status = "unknown"
            row.unknown_reason = reason
            return _reservation(row)

    def settle(
        self,
        reservation_id: uuid.UUID,
        *,
        actual_microdollars: int,
        settlement_key: str,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> Reservation:
        """Record known response/billing evidence; unknown outcomes use reconcile."""
        return self._settle(
            reservation_id,
            actual_microdollars=actual_microdollars,
            settlement_key=settlement_key,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reconciliation=False,
        )

    def reconcile(
        self,
        reservation_id: uuid.UUID,
        *,
        actual_microdollars: int,
        evidence_key: str,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> Reservation:
        """Explicit billing/usage evidence may resolve an unknown outcome, even as zero.

        This is an internal operator integration point, not permission to infer
        zero cost from a timeout or missing usage.
        """
        return self._settle(
            reservation_id,
            actual_microdollars=actual_microdollars,
            settlement_key=evidence_key,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            reconciliation=True,
        )

    def _settle(
        self,
        reservation_id: uuid.UUID,
        *,
        actual_microdollars: int,
        settlement_key: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        reconciliation: bool,
    ) -> Reservation:
        _integer(actual_microdollars, "actual_microdollars")
        _key(settlement_key, "settlement_key", maximum=200)
        if (prompt_tokens is None) != (completion_tokens is None):
            raise ValueError("Both token counts must be known, or both unknown")
        if prompt_tokens is not None:
            _integer(prompt_tokens, "prompt_tokens", maximum=2**63 - 1)
            _integer(completion_tokens, "completion_tokens", maximum=2**63 - 1)
        with Session(self.engine) as session, session.begin():
            row = self._locate(session, reservation_id)
            account = self._account(session, row.account_id)
            session.refresh(row)
            now = self._now()
            if row.status == "settled":
                if (
                    row.actual_microdollars != actual_microdollars
                    or row.settlement_key != settlement_key
                    or row.prompt_tokens != prompt_tokens
                    or row.completion_tokens != completion_tokens
                ):
                    raise BudgetError("settlement_idempotency_conflict")
                return _reservation(row)
            if (reconciliation and row.status != "unknown") or (
                not reconciliation and row.status != "in_flight"
            ):
                raise BudgetError("invalid_settlement_state")
            row.actual_microdollars = actual_microdollars
            row.settlement_key = settlement_key
            row.prompt_tokens = prompt_tokens
            row.completion_tokens = completion_tokens
            row.status = "settled"
            row.settled_at = now
            row.settled_day = now.date()
            session.flush()
            usage = self._usage(session, account, now.date())
            original_day = self._usage(session, account, row.reserved_day)
            run_committed, _ = self._run_usage(session, row.account_id, row.run_id)
            run_limit = session.get(BudgetRunLimit, (row.account_id, row.run_id))
            if (
                usage.committed_microdollars > account.hard_cap_microdollars
                or usage.daily_committed_microdollars > account.daily_cap_microdollars
                or original_day.daily_committed_microdollars > account.daily_cap_microdollars
                or run_committed > run_limit.cap_microdollars
            ):
                account.frozen = True
                account.frozen_reason = "actual_cost_exceeded_cap"
            elif actual_microdollars > row.estimated_microdollars:
                account.frozen = True
                account.frozen_reason = "actual_cost_exceeded_reservation"
            return _reservation(row)

    def usage(self, account_id: str) -> BudgetUsage:
        with Session(self.engine) as session, session.begin():
            account = self._account(session, _key(account_id, "account_id"))
            return self._usage(session, account, self._now().date())
