"""Operator command to list and settle unknown budget holds from billing evidence.

An *unknown* hold means a paid call may or may not have been billed (a timeout,
a dropped connection, a process lost mid-call). Polymath never assumes such a
call was free; while any unknown hold exists, new research and activations stop.
This command is how the operator resolves one, from the Meta dashboard's real
figure. Run on the server:

    python -m polymath.budget_holds list
    python -m polymath.budget_holds settle <reservation-id> \\
        --actual-microdollars <amount> --evidence <short-key> --confirm

Settling never resends anything and never resumes the original call. Research
calls are settled through the dispatch journal, which also clears the run's
in-flight marker; Studio pictures are settled on the ledger directly.
"""

from __future__ import annotations

import argparse
import logging
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Engine, or_, select
from sqlalchemy.orm import Session

from polymath.budget import KEY_PATTERN, BudgetService
from polymath.budget_models import BudgetReservation
from polymath.dispatch import DispatchCoordinator, DispatchGrant
from polymath.models import ResearchExternalRequest, ResearchRun


class HoldError(Exception):
    """Static code; never echoes stored text."""


_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Hold:
    reservation_id: uuid.UUID
    account_id: str
    run_id: uuid.UUID
    task_kind: str
    kind: str  # "research" | "image" | "other"
    status: str
    estimated_microdollars: int
    reason: str | None
    created_at: str


def _kind(session: Session, row: BudgetReservation, task_kind: str) -> str:
    if task_kind == "render_image" and row.call_key.startswith("studio-image-"):
        return "image"
    bound = session.scalar(
        select(ResearchExternalRequest.request_id).where(
            ResearchExternalRequest.reservation_id == row.id
        )
    )
    return "research" if bound is not None else "other"


def list_holds(engine: Engine) -> list[Hold]:
    """Unknown holds, plus calls still marked in flight (possibly lost)."""
    with Session(engine) as session:
        rows = session.execute(
            select(BudgetReservation, ResearchRun.task_kind)
            .join(ResearchRun, ResearchRun.id == BudgetReservation.run_id)
            .where(
                or_(BudgetReservation.status == "unknown", BudgetReservation.status == "in_flight")
            )
            .order_by(BudgetReservation.created_at)
        ).all()
        return [
            Hold(
                reservation_id=row.id,
                account_id=row.account_id,
                run_id=row.run_id,
                task_kind=task_kind,
                kind=_kind(session, row, task_kind),
                status=row.status,
                estimated_microdollars=row.estimated_microdollars,
                reason=row.unknown_reason,
                created_at=row.created_at.isoformat(),
            )
            for row, task_kind in rows
        ]


def settle_hold(
    engine: Engine, reservation_id: uuid.UUID, *, actual_microdollars: int, evidence: str
) -> str:
    """Settle one unknown (or lost in-flight) hold at the amount Meta actually billed."""
    if type(actual_microdollars) is not int or actual_microdollars < 0:
        raise HoldError("invalid_amount")
    if not (1 <= len(evidence) <= 120 and KEY_PATTERN.fullmatch(evidence)):
        raise HoldError("invalid_evidence_key")
    evidence_key = f"operator-reconciled:{evidence}"[:200]
    with Session(engine) as session:
        row = session.get(BudgetReservation, reservation_id)
        if row is None:
            raise HoldError("hold_not_found")
        if row.status not in ("unknown", "in_flight"):
            raise HoldError("hold_not_unknown")
        task_kind = session.scalar(
            select(ResearchRun.task_kind).where(ResearchRun.id == row.run_id)
        )
        kind = _kind(session, row, task_kind)
        request = session.scalar(
            select(ResearchExternalRequest).where(ResearchExternalRequest.reservation_id == row.id)
        )
        grant = (
            DispatchGrant(
                run_id=request.run_id,
                request_id=request.request_id,
                account_id=row.account_id,
                reservation_id=row.id,
                lease_token=request.dispatch_lease_token,
                request_hash=request.request_hash,
            )
            if request is not None
            else None
        )
        observation = request.observation if request is not None else None
    if kind == "research":
        DispatchCoordinator(engine).reconcile(
            grant,
            observation=observation,
            actual_microdollars=actual_microdollars,
            evidence_key=evidence_key,
        )
        return "research"
    if kind == "image":
        budget = BudgetService(engine)
        with Session(engine) as session, session.begin():
            current = session.get(BudgetReservation, reservation_id, with_for_update=True)
            if current.status == "in_flight":
                budget.mark_unknown_in_session(session, current.id, reason="worker_lost")
            budget.settle_in_session(
                session,
                current.id,
                actual_microdollars=actual_microdollars,
                settlement_key=evidence_key,
                reconciliation=True,
            )
        return "image"
    raise HoldError("unsupported_hold_kind")


AUTO_SETTLE_EVIDENCE = "auto-settled-at-reserved-maximum"


def settle_unknown_holds_at_maximum(engine: Engine, *, older_than_seconds: int = 0) -> list[str]:
    """Settle every unknown hold at its full reserved maximum, without a person.

    A timed-out or unanswered call keeps its money counted either way; what an
    unsettled hold adds is a block on activation and, after a few of them, on
    every visitor's next investigation. During public use nobody reads billing
    dashboards, so the hold is closed at the most it could have cost. Settling
    at the maximum never spends less than the ledger already assumed, so the
    caps hold exactly as before; a person may still reconcile the journal later.
    """
    if type(older_than_seconds) is not int or older_than_seconds < 0:
        raise HoldError("invalid_amount")
    settled: list[str] = []
    for hold in list_holds(engine):
        if hold.status != "unknown":
            continue
        age = (datetime.now(UTC) - datetime.fromisoformat(hold.created_at)).total_seconds()
        if age < older_than_seconds:
            continue
        try:
            kind = settle_hold(
                engine,
                hold.reservation_id,
                actual_microdollars=hold.estimated_microdollars,
                evidence=AUTO_SETTLE_EVIDENCE,
            )
        except HoldError as error:
            _LOG.warning("unknown hold %s not auto-settled: %s", hold.reservation_id, error)
            continue
        _LOG.info(
            "unknown %s hold %s settled at its reserved maximum (%s)",
            kind,
            hold.reservation_id,
            _dollars(hold.estimated_microdollars),
        )
        settled.append(str(hold.reservation_id))
    return settled


def _dollars(microdollars: int) -> str:
    return f"${microdollars / 1_000_000:.6f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m polymath.budget_holds", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List unknown and in-flight holds")
    settle = commands.add_parser("settle", help="Settle one hold from billing evidence")
    settle.add_argument("reservation_id")
    settle.add_argument("--actual-microdollars", type=int, required=True)
    settle.add_argument(
        "--evidence",
        required=True,
        help="Short key for the evidence, e.g. meta-dashboard-2026-11-02",
    )
    settle.add_argument("--confirm", action="store_true", help="Required: this records real spend")
    arguments = parser.parse_args(argv)
    if arguments.command == "settle" and not arguments.confirm:
        print("Nothing changed: add --confirm to record this amount as real spend.")
        return 2

    from polymath.config import Settings
    from polymath.database import build_engine

    engine = build_engine(Settings())
    try:
        if arguments.command == "list":
            holds = list_holds(engine)
            if not holds:
                print("No unknown or in-flight holds.")
            for hold in holds:
                print(
                    f"{hold.reservation_id}  {hold.status:9}  {hold.kind:8}  "
                    f"hold {_dollars(hold.estimated_microdollars)}  "
                    f"reason {hold.reason or '-'}  since {hold.created_at}  run {hold.run_id}"
                )
            return 0
        try:
            identifier = uuid.UUID(arguments.reservation_id)
        except ValueError:
            print("Not a reservation id.")
            return 2
        try:
            kind = settle_hold(
                engine,
                identifier,
                actual_microdollars=arguments.actual_microdollars,
                evidence=arguments.evidence,
            )
        except HoldError as error:
            print(f"Nothing changed: {error}")
            return 1
        print(
            f"Settled {kind} hold {identifier} at {_dollars(arguments.actual_microdollars)}. "
            "Nothing was resent."
        )
        return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
