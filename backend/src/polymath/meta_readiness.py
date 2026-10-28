"""Preparation and safe-disable administration; never creates execution authority.

This module has no model/provider factory, activation or reconciliation path.
Human access is rechecked inside Operator -> Account transactions. Existing
caps, settled usage, open holds, pricing flags and freeze state stay intact.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import Engine, case, func, select, text, tuple_
from sqlalchemy.orm import Session

from polymath.budget import OPEN_STATES, BudgetService, _key
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.evidence import EvidenceError, _canonical, _hash
from polymath.operator_auth import OperatorPolicy, require_operator
from polymath.operator_models import OperatorSession
from polymath.readiness_models import MetaReadinessAudit, MetaReadinessProposalRecord


class ReadinessError(Exception):
    """Static error codes; no secrets, raw evidence or privileged account details."""


@dataclass(frozen=True)
class ReadinessRecord:
    id: uuid.UUID
    account_id: str
    proposal_sha256: str
    proposal: dict
    quote: dict
    actor_id: str
    created_at: datetime
    temporal_status: str

    def to_response(self) -> dict:
        return {
            "id": str(self.id),
            "account_id": self.account_id,
            "proposal_sha256": self.proposal_sha256,
            "proposal": self.proposal,
            "quote": self.quote,
            "actor_id": self.actor_id,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
            "temporal_status": self.temporal_status,
            "native_status": "not_run",
            "execution_available": False,
        }


def _clock(session: Session):
    return session.scalar(select(func.clock_timestamp())).astimezone(UTC)


def _account_id(value):
    try:
        return _key(value, "account_id")
    except ValueError:
        raise ReadinessError("readiness_invalid_request") from None


def _uuid(value):
    try:
        parsed = uuid.UUID(str(value))
        if str(parsed) != str(value):
            raise ValueError
        return parsed
    except (ValueError, TypeError, AttributeError):
        raise ReadinessError("readiness_invalid_request") from None


def _account_lock(session: Session, account_id: str):
    # Serialize first creation as well as existing rows, after the operator lock.
    # Hash collisions only add serialization; they cannot bind different accounts.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:name, 728319046))"),
        {"name": "polymath-meta-readiness:" + account_id},
    )
    return session.scalar(
        select(BudgetAccount)
        .where(BudgetAccount.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _quote(proposal, policy, now=None):
    from polymath.meta_readiness_contracts import (
        MetaReadinessProposal,
        quote_proposal,
        validate_proposal_time,
    )

    try:
        if not isinstance(proposal, MetaReadinessProposal):
            proposal = MetaReadinessProposal.model_validate_json(json.dumps(proposal))
        else:
            proposal = MetaReadinessProposal.model_validate_json(proposal.model_dump_json())
        if now is not None:
            validate_proposal_time(proposal, now)
        return quote_proposal(proposal, policy=policy)
    except (ValueError, TypeError, AttributeError):
        raise ReadinessError("readiness_invalid_request") from None


def _stored(row: MetaReadinessProposalRecord, now: datetime) -> ReadinessRecord:
    from polymath.meta_readiness_contracts import (
        MetaReadinessProposal,
        proposal_sha256,
        quote_proposal,
    )

    try:
        proposal = MetaReadinessProposal.model_validate_json(json.dumps(row.proposal))
        # Historic inspection does not apply today's clock or new preparation caps.
        quote = quote_proposal(proposal).model_dump(mode="json")
        if (
            proposal_sha256(proposal) != row.proposal_sha256
            or _hash(_canonical(row.quote)) != row.quote_sha256
            or quote != row.quote
            or proposal.account.account_id != row.account_id
            or row.native_status != "not_run"
            or row.execution_available is not False
        ):
            raise ReadinessError("readiness_storage_unavailable")
        return ReadinessRecord(
            row.id,
            row.account_id,
            row.proposal_sha256,
            row.proposal,
            row.quote,
            row.actor_id,
            row.created_at,
            (
                "not_yet_current"
                if now < proposal.observed_at
                else "expired"
                if now >= proposal.valid_until
                else "current"
            ),
        )
    except (ValueError, TypeError, AttributeError, EvidenceError):
        raise ReadinessError("readiness_storage_unavailable") from None


def _audit(session: Session, account, operator, action, detail, proposal_id=None):
    session.add(
        MetaReadinessAudit(
            account_id=account.id,
            proposal_id=proposal_id,
            action=action,
            operator_session_hash=operator.token_hash,
            browser_session_hash=operator.browser_session_hash,
            actor_id=operator.actor_id,
            detail=detail,
            detail_sha256=_hash(_canonical(detail)),
        )
    )


def _inspection(session: Session, account: BudgetAccount) -> dict:
    usage = BudgetService._usage(session, account, _clock(session).astimezone(UTC).date())
    spent, held, unknown = session.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (
                            BudgetReservation.status == "settled",
                            BudgetReservation.actual_microdollars,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            BudgetReservation.status.in_(OPEN_STATES),
                            BudgetReservation.estimated_microdollars,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
            func.coalesce(
                func.sum(
                    case(
                        (
                            BudgetReservation.status == "unknown",
                            BudgetReservation.estimated_microdollars,
                        ),
                        else_=0,
                    )
                ),
                0,
            ),
        ).where(BudgetReservation.account_id == account.id)
    ).one()
    if int(spent) + int(held) != usage.committed_microdollars:
        raise ReadinessError("readiness_storage_unavailable")
    return {
        "account_id": account.id,
        "hard_cap_microdollars": account.hard_cap_microdollars,
        "daily_cap_microdollars": account.daily_cap_microdollars,
        "max_in_flight": account.max_in_flight,
        "execution_enabled": account.execution_enabled,
        "pricing_verified": account.pricing_verified,
        "pricing_reference": account.pricing_reference,
        "kill_switch": account.kill_switch,
        "frozen": account.frozen,
        "frozen_reason": account.frozen_reason,
        "created_at": account.created_at.astimezone(UTC).isoformat(),
        "usage": {
            "committed_microdollars": str(usage.committed_microdollars),
            "daily_committed_microdollars": str(usage.daily_committed_microdollars),
            "counted_spend_microdollars": str(int(spent)),
            "open_hold_microdollars": str(int(held)),
            "unknown_hold_microdollars": str(int(unknown)),
            "in_flight": usage.in_flight,
            "reservations": usage.reservations,
        },
        "native_status": "not_run",
        "execution_available": False,
    }


class MetaReadinessStore:
    def __init__(
        self, engine: Engine, readiness_policy=None, operator_policy: OperatorPolicy | None = None
    ):
        from polymath.meta_readiness_contracts import ReadinessPolicy

        if engine.dialect.name != "postgresql":
            raise ValueError("MetaReadinessStore requires PostgreSQL")
        self.engine = engine
        self.policy = readiness_policy or ReadinessPolicy()
        self.operator_policy = operator_policy

    def _authorize(self, session: Session, request: Request, *, write: bool):
        if not self.policy.allowed_actor_ids:
            raise ReadinessError("readiness_role_unavailable")
        operator = require_operator(session, request, write=write, policy=self.operator_policy)
        if not write:
            # Reads also keep the global lock order, without demanding write CSRF.
            session.scalar(
                select(OperatorSession)
                .where(OperatorSession.token_hash == operator.token_hash)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            operator = require_operator(session, request, write=False, policy=self.operator_policy)
        if operator.actor_id not in self.policy.allowed_actor_ids:
            raise ReadinessError("readiness_actor_forbidden")
        return operator

    def _preparation(self):
        if not self.policy.preparation_enabled:
            raise ReadinessError("readiness_preparation_disabled")

    def preview(self, request: Request, proposal):
        with Session(self.engine) as session, session.begin():
            self._authorize(session, request, write=True)
            self._preparation()
            quote = _quote(proposal, self.policy, _clock(session))
            account = _account_lock(session, quote.proposal.account.account_id)
            self._authorize(session, request, write=True)
            self._preparation()
            if account is not None:
                self._existing(account, quote.proposal.account)
            self._authorize(session, request, write=True)
            _quote(quote.proposal, self.policy, _clock(session))
            self._preparation()
            return quote

    @staticmethod
    def _existing(account, caps):
        if account.execution_enabled:
            raise ReadinessError("readiness_account_enabled")
        if (
            account.hard_cap_microdollars,
            account.daily_cap_microdollars,
            account.max_in_flight,
        ) != (
            caps.hard_cap_microdollars,
            caps.daily_cap_microdollars,
            caps.max_in_flight,
        ):
            raise ReadinessError("readiness_account_conflict")

    def save(self, request: Request, proposal, expected_proposal_sha256: str) -> ReadinessRecord:
        with Session(self.engine) as session, session.begin():
            operator = self._authorize(session, request, write=True)
            self._preparation()
            quote = _quote(proposal, None)
            if quote.proposal_sha256 != expected_proposal_sha256:
                raise ReadinessError("readiness_proposal_changed")
            caps = quote.proposal.account
            account = _account_lock(session, caps.account_id)
            operator = self._authorize(session, request, write=True)
            prior = session.scalar(
                select(MetaReadinessProposalRecord).where(
                    MetaReadinessProposalRecord.account_id == caps.account_id,
                    MetaReadinessProposalRecord.proposal_sha256 == quote.proposal_sha256,
                )
            )
            if prior is not None:
                result = _stored(prior, _clock(session))
                self._authorize(session, request, write=True)
                return result
            self._preparation()
            quote = _quote(quote.proposal, self.policy, _clock(session))
            if account is None:
                account = BudgetAccount(
                    id=caps.account_id,
                    hard_cap_microdollars=caps.hard_cap_microdollars,
                    daily_cap_microdollars=caps.daily_cap_microdollars,
                    max_in_flight=caps.max_in_flight,
                    created_at=_clock(session),
                    execution_enabled=False,
                    pricing_verified=False,
                    kill_switch=True,
                    frozen=False,
                )
                session.add(account)
                session.flush()
            else:
                self._existing(account, caps)
            payload, quoted = quote.proposal.model_dump(mode="json"), quote.model_dump(mode="json")
            row = MetaReadinessProposalRecord(
                account_id=account.id,
                proposal_sha256=quote.proposal_sha256,
                proposal=payload,
                quote=quoted,
                quote_sha256=_hash(_canonical(quoted)),
                operator_session_hash=operator.token_hash,
                browser_session_hash=operator.browser_session_hash,
                actor_id=operator.actor_id,
            )
            session.add(row)
            session.flush()
            _audit(
                session,
                account,
                operator,
                "proposal_prepared",
                {
                    "proposal_sha256": row.proposal_sha256,
                    "native_status": "not_run",
                    "execution_available": False,
                },
                row.id,
            )
            session.flush()
            self._authorize(session, request, write=True)
            _quote(quote.proposal, self.policy, _clock(session))
            self._preparation()
            return _stored(row, _clock(session))

    def get_proposal(self, request: Request, proposal_id) -> ReadinessRecord:
        with Session(self.engine) as session, session.begin():
            self._authorize(session, request, write=False)
            row = session.get(MetaReadinessProposalRecord, _uuid(proposal_id))
            if row is None:
                raise ReadinessError("readiness_proposal_not_found")
            result = _stored(row, _clock(session))
            self._authorize(session, request, write=False)
            return result

    def list_proposals(self, request: Request, *, account_id=None, limit=20, before=None):
        if type(limit) is not int or not 1 <= limit <= 21:
            raise ReadinessError("readiness_invalid_request")
        with Session(self.engine) as session, session.begin():
            self._authorize(session, request, write=False)
            query = select(MetaReadinessProposalRecord)
            if account_id is not None:
                query = query.where(
                    MetaReadinessProposalRecord.account_id == _account_id(account_id)
                )
            if before is not None:
                anchor = session.get(MetaReadinessProposalRecord, _uuid(before))
                if anchor is None:
                    raise ReadinessError("readiness_proposal_not_found")
                query = query.where(
                    tuple_(MetaReadinessProposalRecord.created_at, MetaReadinessProposalRecord.id)
                    < tuple_(anchor.created_at, anchor.id)
                )
            rows = session.scalars(
                query.order_by(
                    MetaReadinessProposalRecord.created_at.desc(),
                    MetaReadinessProposalRecord.id.desc(),
                ).limit(limit)
            ).all()
            result = tuple(_stored(row, _clock(session)) for row in rows)
            self._authorize(session, request, write=False)
            return result

    def inspect_account(self, request: Request, account_id: str):
        with Session(self.engine) as session, session.begin():
            self._authorize(session, request, write=False)
            account = _account_lock(session, _account_id(account_id))
            self._authorize(session, request, write=False)
            if account is None:
                raise ReadinessError("readiness_account_not_found")
            result = _inspection(session, account)
            self._authorize(session, request, write=False)
            return result

    def list_accounts(self, request: Request, *, limit=20, before=None):
        if type(limit) is not int or not 1 <= limit <= 21:
            raise ReadinessError("readiness_invalid_request")
        with Session(self.engine) as session, session.begin():
            self._authorize(session, request, write=False)
            query = select(BudgetAccount.id)
            if before is not None:
                query = query.where(BudgetAccount.id > _account_id(before))
            identifiers = session.scalars(query.order_by(BudgetAccount.id).limit(limit)).all()
            result = []
            for account_id in identifiers:
                account = _account_lock(session, account_id)
                self._authorize(session, request, write=False)
                result.append(_inspection(session, account))
            self._authorize(session, request, write=False)
            return tuple(result)

    def disable(self, request: Request, account_id: str):
        with Session(self.engine) as session, session.begin():
            operator = self._authorize(session, request, write=True)
            account = _account_lock(session, _account_id(account_id))
            operator = self._authorize(session, request, write=True)
            if account is None:
                raise ReadinessError("readiness_account_not_found")
            account.execution_enabled = False
            account.kill_switch = True
            session.flush()
            _audit(
                session,
                account,
                operator,
                "safe_disabled",
                {"execution_enabled": False, "kill_switch": True},
            )
            session.flush()
            result = _inspection(session, account)
            self._authorize(session, request, write=True)
            return result

    safe_disable = disable
