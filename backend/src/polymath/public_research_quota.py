"""Non-resetting public projections of the existing monetary/admission ledger.

These helpers never approve, reserve, send, settle or release money. Strict
checks serialize on the existing BudgetAccount; read projections are advisory.
The caller retains the Sponsor -> queue -> Account -> Run authority lock order.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import case, func, or_, select

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.budget import MAX_MONEY, OPEN_STATES, BudgetService
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.evidence import _canonical, _hash
from polymath.meta_research_errors import MetaResearchError
from polymath.meta_research_models import (
    MetaResearchApproval,
    MetaResearchManifestRecord,
    NativeResearchRunBinding,
)
from polymath.models import ResearchRun
from polymath.public_research_models import PublicResearchDelegation, PublicResearchRunAccess

ACTIVE_STATES = ("queued", "running", "waiting_retry", "cancel_requested", "interrupted")
QUEUED_STATES = ("queued", "waiting_retry")
PUBLIC_QUOTA_ERROR_STATUSES = {
    "public_research_total_budget_exceeded": 503,
    "public_research_daily_budget_exceeded": 429,
    "public_session_total_budget_exceeded": 429,
    "public_session_daily_budget_exceeded": 429,
    "public_session_job_day_limit": 429,
    "public_research_job_day_limit": 429,
    "public_research_active_limit": 429,
    "public_research_queue_limit": 429,
    "public_network_active_limit": 429,
    "active_run_limit": 429,
    "public_research_binding_invalid": 503,
    "public_research_delegation_expired": 503,
    "public_research_requester_expired": 401,
}


class PublicQuotaError(MetaResearchError):
    def __init__(self, code, retry_after_seconds=None):
        super().__init__(code)
        self.status_code = PUBLIC_QUOTA_ERROR_STATUSES.get(code, 503)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class PublicMonetaryUsage:
    public_total_microdollars: int
    public_daily_microdollars: int
    session_total_microdollars: int
    session_daily_microdollars: int


def _now(session, now):
    value = now if now is not None else session.scalar(select(func.clock_timestamp()))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PublicQuotaError("public_research_binding_invalid")
    return value.astimezone(UTC)


def _hash_id(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise PublicQuotaError("public_research_binding_invalid")
    return value


def _amount(value):
    if type(value) is not int or not 1 <= value <= MAX_MONEY:
        raise PublicQuotaError("public_research_binding_invalid")
    return value


def _approved(session, delegation, supplied_limits=None):
    from polymath.public_research_contracts import PublicLimits, PublicResearchManifest

    if not isinstance(delegation, PublicResearchDelegation):
        raise PublicQuotaError("public_research_binding_invalid")
    approval = session.get(MetaResearchApproval, delegation.approval_id)
    row = session.get(MetaResearchManifestRecord, approval.manifest_id) if approval else None
    try:
        manifest = PublicResearchManifest.model_validate_json(json.dumps(row.manifest))
        if (
            row.account_id != delegation.account_id
            or manifest.account_id != delegation.account_id
            or row.manifest_sha256 != delegation.manifest_sha256
            or _hash(_canonical(row.manifest)) != row.manifest_sha256
            or approval.manifest_sha256 != row.manifest_sha256
            or delegation.authorization_mode != "durable_owner_delegation"
            or manifest.authorization_mode != delegation.authorization_mode
            or manifest.public_policy_sha256 != delegation.public_policy_sha256
            or manifest.sponsor_policy_sha256 != delegation.sponsor_policy_sha256
            or approval.operator_session_hash != delegation.sponsor_operator_session_hash
            or approval.browser_session_hash != delegation.sponsor_browser_session_hash
            or approval.actor_id != delegation.sponsor_actor_id
            or approval.expires_at != delegation.valid_until
            or manifest.valid_until != delegation.valid_until
        ):
            raise ValueError
        if supplied_limits is not None:
            limits = PublicLimits.model_validate_json(
                supplied_limits.model_dump_json()
                if isinstance(supplied_limits, PublicLimits)
                else json.dumps(supplied_limits)
            )
            if limits != manifest.public_limits:
                raise ValueError
        return manifest, manifest.public_limits
    except (ValueError, TypeError, AttributeError):
        raise PublicQuotaError("public_research_binding_invalid") from None


def _account(session, account_id):
    account = session.scalar(
        select(BudgetAccount)
        .where(BudgetAccount.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise PublicQuotaError("public_research_binding_invalid")
    return account


def _live_requester(session, requester_hash, now):
    owner = session.get(BrowserSession, _hash_id(requester_hash), populate_existing=True)
    if owner is None or owner.expires_at <= now:
        raise PublicQuotaError("public_research_requester_expired")


def _identity(access, delegation):
    if (
        access.approval_id != delegation.approval_id
        or access.account_id != delegation.account_id
        or access.manifest_sha256 != delegation.manifest_sha256
        or access.public_policy_sha256 != delegation.public_policy_sha256
    ):
        raise PublicQuotaError("public_research_binding_invalid")


def public_monetary_usage(
    session, *, account_id, requester_hash, now=None, exclude_reservation_id=None
):
    """Read only: all public delegations/tasks; conservative UTC-day/hold rules."""
    requester_hash = _hash_id(requester_hash)
    now = _now(session, now)
    committed = BudgetService._committed_expression()
    daily = or_(
        BudgetReservation.reserved_day == now.date(),
        BudgetReservation.settled_day == now.date(),
        BudgetReservation.status.in_(OPEN_STATES),
    )
    own = PublicResearchRunAccess.requester_session_hash == requester_hash
    query = select(
        func.coalesce(func.sum(committed), 0),
        func.coalesce(func.sum(case((daily, committed), else_=0)), 0),
        func.coalesce(func.sum(case((own, committed), else_=0)), 0),
        func.coalesce(func.sum(case((own & daily, committed), else_=0)), 0),
    )
    query = (
        query.select_from(BudgetReservation)
        .join(
            PublicResearchRunAccess,
            (PublicResearchRunAccess.run_id == BudgetReservation.run_id)
            & (PublicResearchRunAccess.account_id == BudgetReservation.account_id),
        )
        .where(BudgetReservation.account_id == account_id)
    )
    if exclude_reservation_id is not None:
        query = query.where(BudgetReservation.id != exclude_reservation_id)
    values = session.execute(query).one()
    return PublicMonetaryUsage(*(int(value) for value in values))


def _job_usage(session, *, account_id, requester_hash, network_bucket, now, exclude_run_id=None):
    start = datetime.combine(now.date(), time.min, UTC)
    day = (PublicResearchRunAccess.admitted_at >= start) & (
        PublicResearchRunAccess.admitted_at < start + timedelta(days=1)
    )
    own = PublicResearchRunAccess.requester_session_hash == requester_hash
    active = ResearchRun.status.in_(ACTIVE_STATES)
    queued = ResearchRun.status.in_(QUEUED_STATES)
    query = (
        select(
            func.coalesce(func.sum(case((day, 1), else_=0)), 0),
            func.coalesce(func.sum(case((day & own, 1), else_=0)), 0),
            func.coalesce(func.sum(case((active, 1), else_=0)), 0),
            func.coalesce(func.sum(case((queued, 1), else_=0)), 0),
            func.coalesce(
                func.sum(
                    case(
                        (active & (PublicResearchRunAccess.network_bucket == network_bucket), 1),
                        else_=0,
                    )
                ),
                0,
            ),
        )
        .select_from(PublicResearchRunAccess)
        .join(ResearchRun, ResearchRun.id == PublicResearchRunAccess.run_id)
        .where(PublicResearchRunAccess.account_id == account_id)
    )
    if exclude_run_id is not None:
        query = query.where(PublicResearchRunAccess.run_id != exclude_run_id)
    public_day, session_day, public_active, public_queued, network_active = session.execute(
        query
    ).one()
    owned = (
        select(func.count())
        .select_from(ResearchRun)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == ResearchRun.id)
        .where(
            ResearchRunAccess.session_hash == requester_hash, ResearchRun.status.in_(ACTIVE_STATES)
        )
    )
    if exclude_run_id is not None:
        owned = owned.where(ResearchRun.id != exclude_run_id)
    return {
        "public_jobs_today": int(public_day),
        "session_jobs_today": int(session_day),
        "public_active_jobs": int(public_active),
        "public_queued_jobs": int(public_queued),
        "network_active_jobs": int(network_active),
        "session_active_jobs": int(session.scalar(owned)),
    }


def _midnight(now):
    return max(
        1,
        min(
            86400,
            int(
                (
                    datetime.combine(now.date() + timedelta(days=1), time.min, UTC) - now
                ).total_seconds()
            )
            + 1,
        ),
    )


def _money_checks(
    session, delegation, requester_hash, limits, estimate, now, reservation_id=None, run_id=None
):
    account = _account(session, delegation.account_id)
    if (
        limits.public_total_cap_microdollars > account.hard_cap_microdollars
        or limits.public_daily_cap_microdollars > account.daily_cap_microdollars
    ):
        raise PublicQuotaError("public_research_binding_invalid")
    if reservation_id is not None:
        reservation = session.get(BudgetReservation, reservation_id, populate_existing=True)
        access = session.get(PublicResearchRunAccess, run_id)
        if (
            reservation is None
            or access is None
            or reservation.run_id != run_id
            or reservation.account_id != account.id
            or access.requester_session_hash != requester_hash
            or reservation.status not in ("reserved", "in_flight")
            or reservation.estimated_microdollars != estimate
        ):
            raise PublicQuotaError("public_research_binding_invalid")
        _identity(access, delegation)
    usage = public_monetary_usage(
        session,
        account_id=account.id,
        requester_hash=requester_hash,
        now=now,
        exclude_reservation_id=reservation_id,
    )
    checks = (
        (
            usage.public_total_microdollars,
            limits.public_total_cap_microdollars,
            "public_research_total_budget_exceeded",
            None,
        ),
        (
            usage.public_daily_microdollars,
            limits.public_daily_cap_microdollars,
            "public_research_daily_budget_exceeded",
            _midnight(now),
        ),
        (
            usage.session_total_microdollars,
            limits.session_total_cap_microdollars,
            "public_session_total_budget_exceeded",
            None,
        ),
        (
            usage.session_daily_microdollars,
            limits.session_daily_cap_microdollars,
            "public_session_daily_budget_exceeded",
            _midnight(now),
        ),
    )
    for committed, cap, code, retry in checks:
        if committed + estimate > cap:
            raise PublicQuotaError(code, retry)
    return usage


def _job_checks(
    session,
    delegation,
    requester_hash,
    network_bucket,
    limits,
    now,
    *,
    exclude_run_id=None,
    new_admission=True,
):
    usage = _job_usage(
        session,
        account_id=delegation.account_id,
        requester_hash=requester_hash,
        network_bucket=network_bucket,
        now=now,
        exclude_run_id=exclude_run_id,
    )
    if usage["session_active_jobs"] >= limits.max_active_jobs_per_session:
        raise PublicQuotaError("active_run_limit", 1)
    if new_admission and usage["session_jobs_today"] >= limits.max_jobs_per_session_day:
        raise PublicQuotaError("public_session_job_day_limit", _midnight(now))
    if new_admission and usage["public_jobs_today"] >= limits.max_jobs_per_day:
        raise PublicQuotaError("public_research_job_day_limit", _midnight(now))
    for field, cap, code in (
        ("public_active_jobs", limits.max_active_jobs, "public_research_active_limit"),
        ("public_queued_jobs", limits.max_queued_jobs, "public_research_queue_limit"),
        ("network_active_jobs", limits.max_active_jobs_per_network, "public_network_active_limit"),
    ):
        if usage[field] >= cap:
            raise PublicQuotaError(code, 1)
    return usage


def check_public_admission(
    session,
    *,
    delegation,
    requester_hash,
    network_bucket,
    estimated_microdollars,
    now=None,
    limits=None,
    resume_run_id=None,
):
    """Caller holds sponsor+queue; lock account and check a new job or original resume."""
    _, approved = _approved(session, delegation, limits)
    _account(session, delegation.account_id)
    now = _now(session, now)
    requester_hash, network_bucket = _hash_id(requester_hash), _hash_id(network_bucket)
    _live_requester(session, requester_hash, now)
    if delegation.valid_until <= now:
        raise PublicQuotaError("public_research_delegation_expired")
    if resume_run_id is not None:
        access = session.get(PublicResearchRunAccess, resume_run_id)
        if (
            access is None
            or access.requester_session_hash != requester_hash
            or access.network_bucket != network_bucket
        ):
            raise PublicQuotaError("public_research_binding_invalid")
        _identity(access, delegation)
    usage = _money_checks(
        session, delegation, requester_hash, approved, _amount(estimated_microdollars), now
    )
    _job_checks(
        session,
        delegation,
        requester_hash,
        network_bucket,
        approved,
        now,
        exclude_run_id=resume_run_id,
        new_admission=resume_run_id is None,
    )
    return usage


def record_public_admission(
    session, *, run, binding, delegation, requester_hash, network_bucket, now=None
):
    """Final exact once insertion; cancelled admissions remain counted forever."""
    from polymath.meta_readiness_contracts import quote_proposal

    clock_override = now
    manifest, limits = _approved(session, delegation)
    _account(session, delegation.account_id)
    now = _now(session, now)
    requester_hash, network_bucket = _hash_id(requester_hash), _hash_id(network_bucket)
    if (
        not isinstance(binding, NativeResearchRunBinding)
        or binding.run_id != run.id
        or binding.approval_id != delegation.approval_id
        or binding.manifest_sha256 != delegation.manifest_sha256
        or binding.account_id != delegation.account_id
        or binding.browser_session_hash != requester_hash
    ):
        raise PublicQuotaError("public_research_binding_invalid")
    owner = session.get(ResearchRunAccess, run.id)
    if owner is None or owner.session_hash != requester_hash:
        raise PublicQuotaError("public_research_binding_invalid")
    prior = session.get(PublicResearchRunAccess, run.id)
    if prior is not None:
        _identity(prior, delegation)
        if prior.requester_session_hash != requester_hash or prior.network_bucket != network_bucket:
            raise PublicQuotaError("public_research_binding_invalid")
        return prior
    if run.status != "queued" or run.lease_token != 0:
        raise PublicQuotaError("public_research_binding_invalid")
    _live_requester(session, requester_hash, now)
    if delegation.valid_until <= now:
        raise PublicQuotaError("public_research_delegation_expired")
    estimate = quote_proposal(manifest.proposal).per_call_reservation_microdollars
    _money_checks(session, delegation, requester_hash, limits, estimate, now)
    _job_checks(
        session, delegation, requester_hash, network_bucket, limits, now, exclude_run_id=run.id
    )
    row = PublicResearchRunAccess(
        run_id=run.id,
        approval_id=delegation.approval_id,
        manifest_sha256=delegation.manifest_sha256,
        account_id=delegation.account_id,
        requester_session_hash=requester_hash,
        network_bucket=network_bucket,
        public_policy_sha256=delegation.public_policy_sha256,
        admitted_at=now,
    )
    session.add(row)
    session.flush()
    now = _now(session, clock_override)
    _live_requester(session, requester_hash, now)
    if delegation.valid_until <= now:
        raise PublicQuotaError("public_research_delegation_expired")
    _money_checks(session, delegation, requester_hash, limits, estimate, now)
    _job_checks(
        session, delegation, requester_hash, network_bucket, limits, now, exclude_run_id=run.id
    )
    return row


def check_public_dispatch(
    session,
    *,
    access,
    delegation,
    estimated_microdollars,
    now=None,
    limits=None,
    reservation_id=None,
):
    """No job increment. After reserving, exclude only THIS matching hold and add once."""
    _, approved = _approved(session, delegation, limits)
    _account(session, delegation.account_id)
    now = _now(session, now)
    _identity(access, delegation)
    _live_requester(session, access.requester_session_hash, now)
    if delegation.valid_until <= now:
        raise PublicQuotaError("public_research_delegation_expired")
    return _money_checks(
        session,
        delegation,
        access.requester_session_hash,
        approved,
        _amount(estimated_microdollars),
        now,
        reservation_id=reservation_id,
        run_id=access.run_id,
    )


def public_session_usage(session, *, delegation, requester_hash, now=None):
    """Advisory read, without account locks. It never promises a later admission."""
    _, limits = _approved(session, delegation)
    now = _now(session, now)
    usage = public_monetary_usage(
        session, account_id=delegation.account_id, requester_hash=requester_hash, now=now
    )
    jobs = _job_usage(
        session,
        account_id=delegation.account_id,
        requester_hash=requester_hash,
        network_bucket=None,
        now=now,
    )
    total = max(0, limits.session_total_cap_microdollars - usage.session_total_microdollars)
    daily = max(0, limits.session_daily_cap_microdollars - usage.session_daily_microdollars)
    public_total = max(0, limits.public_total_cap_microdollars - usage.public_total_microdollars)
    public_daily = max(0, limits.public_daily_cap_microdollars - usage.public_daily_microdollars)
    return {
        "remaining_total_microdollars": total,
        "remaining_daily_microdollars": daily,
        "remaining_public_total_microdollars": public_total,
        "remaining_public_daily_microdollars": public_daily,
        "remaining_session_allowance_microdollars": min(total, daily, public_total, public_daily),
        "remaining_jobs_today": max(
            0, limits.max_jobs_per_session_day - jobs["session_jobs_today"]
        ),
        "remaining_public_jobs_today": max(0, limits.max_jobs_per_day - jobs["public_jobs_today"]),
        **jobs,
    }
