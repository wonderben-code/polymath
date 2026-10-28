"""Real shared-ledger quota checks; all provider facts and funding are synthetic."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from test_memory import owner
from test_meta_readiness import readiness as readiness_fixture
from test_meta_research import active_research
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.budget_models import BudgetAccount, BudgetReservation, BudgetRunLimit
from polymath.meta_research_contracts import research_policy_sha256
from polymath.meta_research_models import (
    MetaResearchApproval,
    MetaResearchManifestRecord,
    NativeResearchRunBinding,
)
from polymath.models import ResearchRun
from polymath.operator_models import OperatorSession
from polymath.public_research_contracts import (
    PublicLimits,
    PublicResearchPolicy,
    build_public_research_manifest,
    public_research_manifest_sha256,
    public_sponsor_policy_sha256,
)
from polymath.public_research_models import PublicResearchDelegation, PublicResearchRunAccess
from polymath.public_research_quota import (
    PublicQuotaError,
    check_public_admission,
    check_public_dispatch,
    public_monetary_usage,
    public_session_usage,
    record_public_admission,
)

database = database_fixture
readiness = readiness_fixture
pytestmark = pytest.mark.integration
NETWORK = "a" * 64
ESTIMATE = 308192


def public_delegation(readiness, limits=None, *, active=None, deadline_seconds=300):
    """Actual saved readiness + settled synthetic canary, immutable public consent."""
    active = active or active_research(readiness)
    limits = limits or PublicLimits(
        public_total_cap_microdollars=10_000_000,
        public_daily_cap_microdollars=8_000_000,
        session_total_cap_microdollars=5_000_000,
        session_daily_cap_microdollars=4_000_000,
    )
    with Session(active.engine) as session, session.begin():
        account = session.get(BudgetAccount, active.proposal.account.account_id)
        operator = session.scalar(select(OperatorSession))
        now = session.scalar(select(func.clock_timestamp()))
        manifest = build_public_research_manifest(
            str(active.saved.id),
            active.proposal,
            research_policy=active.policy,
            public_policy=PublicResearchPolicy(enabled=True),
            sponsor_policy_sha256=public_sponsor_policy_sha256(
                operator_fingerprint=active.operator_policy.credential_fingerprint,
                actor_id=operator.actor_id,
                readiness_policy=active.readiness_policy,
            ),
            native_control_epoch=account.native_control_epoch,
            canary=active.preview.manifest.canary,
            review=active.preview.manifest.review,
            provider_account_attestation=active.preview.manifest.provider_account_attestation,
            public_limits=limits,
            valid_until=now + timedelta(seconds=deadline_seconds),
            now=now,
        )
        digest = public_research_manifest_sha256(manifest)
        record = MetaResearchManifestRecord(
            readiness_id=active.saved.id,
            canary_receipt_id=active.canary.id,
            account_id=account.id,
            manifest_sha256=digest,
            manifest=manifest.model_dump(mode="json"),
        )
        session.add(record)
        session.flush()
        approval = MetaResearchApproval(
            manifest_id=record.id,
            manifest_sha256=digest,
            operator_session_hash=operator.token_hash,
            browser_session_hash=operator.browser_session_hash,
            actor_id=operator.actor_id,
            research_policy_sha256=research_policy_sha256(active.policy),
            armed_epoch=account.native_control_epoch + 1,
            approved_at=now,
            expires_at=manifest.valid_until,
        )
        session.add(approval)
        session.flush()
        delegation = PublicResearchDelegation(
            approval_id=approval.id,
            account_id=account.id,
            manifest_sha256=digest,
            authorization_mode=manifest.authorization_mode,
            sponsor_operator_session_hash=operator.token_hash,
            sponsor_browser_session_hash=operator.browser_session_hash,
            sponsor_actor_id=operator.actor_id,
            sponsor_policy_sha256=manifest.sponsor_policy_sha256,
            public_policy_sha256=manifest.public_policy_sha256,
            valid_until=manifest.valid_until,
        )
        session.add(delegation)
        account.native_manifest_sha256 = digest
        account.native_control_epoch = approval.armed_epoch
        session.flush()
        return SimpleNamespace(
            engine=active.engine,
            active=active,
            manifest=manifest,
            delegation_id=approval.id,
            manifest_id=record.id,
            armed_epoch=approval.armed_epoch,
            account_id=account.id,
            limits=limits,
            now=now,
        )


def admit(public, requester_hash, *, network=NETWORK, task_kind="research", now=None):
    with Session(public.engine) as session, session.begin():
        delegation = session.get(PublicResearchDelegation, public.delegation_id)
        # Match production Account -> Run ordering before any account FK takes
        # a key-share lock; concurrent lock upgrades would otherwise deadlock.
        session.scalar(
            select(BudgetAccount).where(BudgetAccount.id == public.account_id).with_for_update()
        )
        run = ResearchRun(mode="explore", task_kind=task_kind, goal="Synthetic public research")
        session.add(run)
        session.flush()
        session.add(ResearchRunAccess(run_id=run.id, session_hash=requester_hash))
        profile = next(p for p in public.manifest.task_profiles if p.profile.task_kind == task_kind)
        binding = NativeResearchRunBinding(
            run_id=run.id,
            approval_id=delegation.approval_id,
            manifest_id=public.manifest_id,
            manifest_sha256=delegation.manifest_sha256,
            account_id=delegation.account_id,
            browser_session_hash=requester_hash,
            armed_epoch=public.armed_epoch,
            task_kind=task_kind,
            mode=run.mode,
            input_sha256="b" * 64,
            profile=profile.profile.model_dump(mode="json"),
            profile_sha256=profile.profile_sha256,
        )
        session.add(binding)
        session.add(
            BudgetRunLimit(
                account_id=delegation.account_id,
                run_id=run.id,
                cap_microdollars=public.manifest.proposal.account.run_cap_microdollars,
                max_calls=profile.profile.max_model_calls,
            )
        )
        session.flush()
        record_public_admission(
            session,
            run=run,
            binding=binding,
            delegation=delegation,
            requester_hash=requester_hash,
            network_bucket=network,
            now=now,
        )
        return run.id


def terminal(public, run_id, status="succeeded"):
    with Session(public.engine) as session, session.begin():
        session.get(ResearchRun, run_id).status = status


def reservation(public, run_id, amount, *, status="settled", now=None, reserved_day=None):
    now = now or datetime.now(UTC)
    with Session(public.engine) as session, session.begin():
        row = BudgetReservation(
            account_id=public.account_id,
            run_id=run_id,
            call_key=str(uuid.uuid4()),
            estimated_microdollars=amount,
            actual_microdollars=amount if status == "settled" else None,
            status=status,
            reserved_day=reserved_day or now.date(),
            settled_day=now.date() if status == "settled" else None,
            created_at=now,
            settled_at=now if status == "settled" else None,
            settlement_key=str(uuid.uuid4()) if status == "settled" else None,
        )
        session.add(row)
        session.flush()
        return row.id


def check(public, requester, *, amount=ESTIMATE, network=NETWORK, **kwargs):
    with Session(public.engine) as session, session.begin():
        return check_public_admission(
            session,
            delegation=session.get(PublicResearchDelegation, public.delegation_id),
            requester_hash=requester,
            network_bucket=network,
            estimated_microdollars=amount,
            **kwargs,
        )


def test_public_usage_counts_all_tasks_and_old_delegations_without_counting_private_probe(
    readiness,
):
    public = public_delegation(readiness)
    visitor = owner(public.engine, "public-visitor")
    another = owner(public.engine, "another-public-visitor")
    first = admit(public, visitor)
    terminal(public, first, "cancelled")
    reservation(public, first, 100)
    next_public = public_delegation(readiness, active=public.active)
    second = admit(next_public, visitor, task_kind="write_paper")
    terminal(public, second)
    reservation(public, second, 200, status="unknown")
    third = admit(next_public, another, task_kind="reassess_finding")
    reservation(public, third, 300, status="reserved")
    with Session(public.engine) as session:
        usage = public_monetary_usage(session, account_id=public.account_id, requester_hash=visitor)
        assert usage.public_total_microdollars == 600
        assert usage.session_total_microdollars == 300
        assert session.scalar(select(func.sum(BudgetReservation.actual_microdollars))) == 303
        # The existing 203-microdollar canary remains on the ONE shared account,
        # while only public admissions contribute to the public overlay.
        assert session.scalar(select(func.count()).select_from(BudgetAccount)) == 1
        assert (
            public_session_usage(
                session,
                delegation=session.get(PublicResearchDelegation, next_public.delegation_id),
                requester_hash=visitor,
            )["remaining_total_microdollars"]
            == 5_000_000 - 300
        )


def test_utc_day_counts_old_open_holds_and_cross_day_settlements_conservatively(readiness):
    public = public_delegation(readiness)
    visitor = owner(public.engine, "daily-public-visitor")
    run_id = admit(public, visitor)
    yesterday = public.now - timedelta(days=1)
    reservation(public, run_id, 100, now=yesterday)
    reservation(public, run_id, 200, now=public.now, reserved_day=yesterday.date())
    reservation(public, run_id, 300, status="in_flight", now=yesterday)
    reservation(public, run_id, 400, status="unknown", now=yesterday)
    with Session(public.engine) as session:
        usage = public_monetary_usage(
            session, account_id=public.account_id, requester_hash=visitor, now=public.now
        )
        assert usage.public_total_microdollars == 1000
        assert usage.public_daily_microdollars == 900
        assert usage.session_daily_microdollars == 900


@pytest.mark.parametrize(
    "field,code",
    [
        ("public_total_cap_microdollars", "public_research_total_budget_exceeded"),
        ("public_daily_cap_microdollars", "public_research_daily_budget_exceeded"),
        ("session_total_cap_microdollars", "public_session_total_budget_exceeded"),
        ("session_daily_cap_microdollars", "public_session_daily_budget_exceeded"),
    ],
)
def test_exact_monetary_boundary_and_each_public_session_cap(readiness, field, code):
    base = dict(
        public_total_cap_microdollars=2_000_000,
        public_daily_cap_microdollars=2_000_000,
        session_total_cap_microdollars=2_000_000,
        session_daily_cap_microdollars=2_000_000,
    )
    # Set all descendant caps equal so pure hierarchy remains valid. Error order
    # independently identifies the earliest reviewed cap that is exhausted.
    names = list(base)
    for key in names[names.index(field) :]:
        base[key] = 1_000_000
    public = public_delegation(readiness, PublicLimits(**base))
    visitor = owner(public.engine, "bounded-public-visitor")
    run_id = admit(public, visitor)
    terminal(public, run_id)
    reservation(public, run_id, 1_000_000 - ESTIMATE)
    assert check(public, visitor).session_total_microdollars == 1_000_000 - ESTIMATE
    with pytest.raises(PublicQuotaError, match=code) as rejected:
        check(public, visitor, amount=ESTIMATE + 1)
    assert rejected.value.status_code in (429, 503)
    if "daily" in code:
        assert 1 <= rejected.value.retry_after_seconds <= 86400


def test_post_reservation_counts_exact_current_estimate_once_and_rejects_foreign_exclusion(
    readiness,
):
    limits = PublicLimits(
        public_total_cap_microdollars=ESTIMATE,
        public_daily_cap_microdollars=ESTIMATE,
        session_total_cap_microdollars=ESTIMATE,
        session_daily_cap_microdollars=ESTIMATE,
    )
    public = public_delegation(readiness, limits)
    visitor = owner(public.engine, "postreserve-visitor")
    run_id = admit(public, visitor)
    hold = reservation(public, run_id, ESTIMATE, status="in_flight")
    with Session(public.engine) as session, session.begin():
        delegation = session.get(PublicResearchDelegation, public.delegation_id)
        access = session.get(PublicResearchRunAccess, run_id)
        usage = check_public_dispatch(
            session,
            access=access,
            delegation=delegation,
            estimated_microdollars=ESTIMATE,
            reservation_id=hold,
        )
        assert usage.session_total_microdollars == 0
    with (
        Session(public.engine) as session,
        session.begin(),
        pytest.raises(PublicQuotaError, match="public_research_binding_invalid"),
    ):
        check_public_dispatch(
            session,
            access=session.get(PublicResearchRunAccess, run_id),
            delegation=session.get(PublicResearchDelegation, public.delegation_id),
            estimated_microdollars=ESTIMATE,
            reservation_id=uuid.uuid4(),
        )
    with pytest.raises(PublicQuotaError, match="public_research_total_budget_exceeded"):
        check(public, visitor)


def test_cancelled_zero_spend_jobs_count_and_resume_does_not_create_new_admission(readiness):
    limits = PublicLimits(
        public_total_cap_microdollars=10_000_000,
        public_daily_cap_microdollars=8_000_000,
        session_total_cap_microdollars=5_000_000,
        session_daily_cap_microdollars=4_000_000,
        max_jobs_per_session_day=1,
    )
    public = public_delegation(readiness, limits)
    visitor = owner(public.engine, "one-job-visitor")
    run_id = admit(public, visitor)
    terminal(public, run_id, "interrupted")
    check(public, visitor, resume_run_id=run_id)
    terminal(public, run_id, "cancelled")
    with pytest.raises(PublicQuotaError, match="public_session_job_day_limit"):
        check(public, visitor)
    with Session(public.engine) as session, session.begin():
        binding = session.get(NativeResearchRunBinding, run_id)
        prior = record_public_admission(
            session,
            run=session.get(ResearchRun, run_id),
            binding=binding,
            delegation=session.get(PublicResearchDelegation, public.delegation_id),
            requester_hash=visitor,
            network_bucket=NETWORK,
        )
        assert prior.run_id == run_id
        assert session.scalar(select(func.count()).select_from(PublicResearchRunAccess)) == 1


def test_owner_active_includes_interrupted_private_and_free_task_kinds(readiness):
    public = public_delegation(readiness)
    visitor = owner(public.engine, "busy-visitor")
    with Session(public.engine) as session, session.begin():
        run = ResearchRun(
            mode="explore", task_kind="ingest_literature", status="interrupted", goal="Free task"
        )
        session.add(run)
        session.flush()
        session.add(ResearchRunAccess(run_id=run.id, session_hash=visitor))
    with pytest.raises(PublicQuotaError, match="active_run_limit"):
        check(public, visitor)


@pytest.mark.parametrize(
    "limits,status,same_network,code",
    [
        (
            {"max_active_jobs": 1, "max_queued_jobs": 1, "max_active_jobs_per_network": 1},
            "running",
            False,
            "public_research_active_limit",
        ),
        ({"max_queued_jobs": 1}, "queued", False, "public_research_queue_limit"),
        ({"max_active_jobs_per_network": 1}, "interrupted", True, "public_network_active_limit"),
        (
            {"max_jobs_per_day": 1, "max_jobs_per_session_day": 1},
            "cancelled",
            False,
            "public_research_job_day_limit",
        ),
    ],
)
def test_public_job_queue_and_network_caps_are_not_money_caps(
    readiness, limits, status, same_network, code
):
    public = public_delegation(
        readiness,
        PublicLimits(
            public_total_cap_microdollars=10_000_000,
            public_daily_cap_microdollars=8_000_000,
            session_total_cap_microdollars=5_000_000,
            session_daily_cap_microdollars=4_000_000,
            **limits,
        ),
    )
    first, second = owner(public.engine, "first-job"), owner(public.engine, "second-job")
    run_id = admit(public, first)
    terminal(public, run_id, status)
    with pytest.raises(PublicQuotaError, match=code):
        check(public, second, network=NETWORK if same_network else "c" * 64)


def test_account_lock_serializes_last_public_admission_slot(readiness):
    limits = PublicLimits(
        public_total_cap_microdollars=10_000_000,
        public_daily_cap_microdollars=8_000_000,
        session_total_cap_microdollars=5_000_000,
        session_daily_cap_microdollars=4_000_000,
        max_jobs_per_day=1,
        max_jobs_per_session_day=1,
    )
    public = public_delegation(readiness, limits)
    first, second = owner(public.engine, "race-first"), owner(public.engine, "race-second")
    started = Event()
    with Session(public.engine) as held, held.begin():
        held.scalar(
            select(BudgetAccount).where(BudgetAccount.id == public.account_id).with_for_update()
        )
        with ThreadPoolExecutor(max_workers=2) as pool:

            def attempt(visitor):
                started.set()
                try:
                    return admit(public, visitor)
                except PublicQuotaError as exc:
                    return exc.code

            futures = [pool.submit(attempt, first), pool.submit(attempt, second)]
            assert started.wait(5)
            held.commit()
            outcomes = [future.result(timeout=10) for future in futures]
    assert sum(isinstance(value, uuid.UUID) for value in outcomes) == 1
    assert outcomes.count("public_research_job_day_limit") == 1
    with Session(public.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicResearchRunAccess)) == 1


def test_same_account_serializes_the_last_public_monetary_slot(readiness):
    public = public_delegation(
        readiness,
        PublicLimits(
            public_total_cap_microdollars=ESTIMATE,
            public_daily_cap_microdollars=ESTIMATE,
            session_total_cap_microdollars=ESTIMATE,
            session_daily_cap_microdollars=ESTIMATE,
        ),
    )
    first = admit(public, owner(public.engine, "money-first"))
    second = admit(public, owner(public.engine, "money-second"))

    def checked_hold(run_id):
        try:
            with Session(public.engine) as session, session.begin():
                session.scalar(
                    select(BudgetAccount)
                    .where(BudgetAccount.id == public.account_id)
                    .with_for_update()
                )
                check_public_dispatch(
                    session,
                    access=session.get(PublicResearchRunAccess, run_id),
                    delegation=session.get(PublicResearchDelegation, public.delegation_id),
                    estimated_microdollars=ESTIMATE,
                )
                session.add(
                    BudgetReservation(
                        account_id=public.account_id,
                        run_id=run_id,
                        call_key=str(uuid.uuid4()),
                        estimated_microdollars=ESTIMATE,
                        status="reserved",
                        reserved_day=public.now.date(),
                        created_at=public.now,
                    )
                )
            return "reserved"
        except PublicQuotaError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(checked_hold, (first, second)))
    assert sorted(outcomes) == ["public_research_total_budget_exceeded", "reserved"]
    with Session(public.engine) as session:
        assert (
            public_monetary_usage(
                session,
                account_id=public.account_id,
                requester_hash=session.get(PublicResearchRunAccess, first).requester_session_hash,
            ).public_total_microdollars
            == ESTIMATE
        )


def test_account_wait_rechecks_requester_expiry_without_consuming_admission(readiness):
    public = public_delegation(readiness)
    visitor = owner(public.engine, "waiting-visitor")
    entering_lock = Event()

    def see_lock(conn, cursor, statement, parameters, context, executemany):
        if "budget_accounts" in statement and "FOR UPDATE" in statement:
            entering_lock.set()

    with Session(public.engine) as held, held.begin():
        held.scalar(
            select(BudgetAccount).where(BudgetAccount.id == public.account_id).with_for_update()
        )
        event.listen(public.engine, "before_cursor_execute", see_lock)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(check, public, visitor)
                assert entering_lock.wait(5)
                with Session(public.engine) as change, change.begin():
                    change.get(BrowserSession, visitor).expires_at = public.now - timedelta(
                        seconds=1
                    )
                held.commit()
                with pytest.raises(PublicQuotaError, match="public_research_requester_expired"):
                    future.result(timeout=10)
        finally:
            event.remove(public.engine, "before_cursor_execute", see_lock)
    with Session(public.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicResearchRunAccess)) == 0


def test_expired_requester_and_caller_inflated_limits_never_admit(readiness):
    public = public_delegation(readiness)
    visitor = owner(public.engine, "expiring-visitor")
    inflated = public.limits.model_copy(update={"session_total_cap_microdollars": 6_000_000})
    with pytest.raises(PublicQuotaError, match="public_research_binding_invalid"):
        check(public, visitor, limits=inflated)
    with Session(public.engine) as session, session.begin():
        session.get(BrowserSession, visitor).expires_at = public.now - timedelta(seconds=1)
    with pytest.raises(PublicQuotaError, match="public_research_requester_expired"):
        check(public, visitor)


def test_public_history_immutable_coherent_and_schema_matches(readiness):
    public = public_delegation(readiness)
    visitor = owner(public.engine, "schema-visitor")
    run_id = admit(public, visitor)
    for table, key in (
        (PublicResearchDelegation, public.delegation_id),
        (PublicResearchRunAccess, run_id),
    ):
        with Session(public.engine) as session, pytest.raises(DBAPIError):
            row = session.get(table, key)
            session.delete(row)
            session.commit()
    with Session(public.engine) as session, pytest.raises(IntegrityError):
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        foreign = owner(public.engine, "foreign-schema-visitor")
        session.add(
            PublicResearchRunAccess(
                run_id=uuid.uuid4(),
                approval_id=public.delegation_id,
                manifest_sha256=public.manifest
                and public_research_manifest_sha256(public.manifest),
                account_id=public.account_id,
                requester_session_hash=foreign,
                network_bucket=NETWORK,
                public_policy_sha256=public.manifest.public_policy_sha256,
            )
        )
        session.commit()
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    with public.engine.connect() as connection:
        config.attributes["connection"] = connection
        command.check(config)
        old_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
        connection.rollback()
        with pytest.raises(DBAPIError, match="Cannot discard public research"):
            command.downgrade(config, "0022_discovery_ladder")
        connection.rollback()
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == old_head
