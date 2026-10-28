"""Account-scoped native scheduling uses short DB locks, never network authority."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_evidence import document, result_for
from test_memory import owner
from test_meta_readiness import readiness as readiness_fixture
from test_public_research_quota import admit, public_delegation, reservation, terminal
from test_worker import database as database_fixture

from polymath.budget_models import BudgetReservation
from polymath.evidence import EvidenceStore
from polymath.evidence_models import EvidenceQuote, EvidenceResult, SourceRetrieval
from polymath.jobs import JobRepository
from polymath.models import ResearchExternalRequest, ResearchRun
from polymath.public_research_models import PublicResearchDelegation
from polymath.public_research_quota import check_public_admission
from polymath.run_api import COOKIE_NAME, create_run_router

pytestmark = pytest.mark.integration
database = database_fixture
readiness = readiness_fixture


def queued_pair(readiness):
    public = public_delegation(readiness)
    first = admit(public, owner(public.engine, "first-claim"))
    second = admit(public, owner(public.engine, "second-claim"), network="b" * 64)
    return public, JobRepository(public.engine), first, second


def scoped_claim(repository, public, worker="native-worker"):
    return repository.claim_next(worker, native_only=True, native_account_id=public.account_id)


def test_concurrent_workers_claim_only_one_job_for_same_native_account(readiness):
    public, jobs, first, second = queued_pair(readiness)
    third = admit(public, owner(public.engine, "third-claim"), network="c" * 64)
    fourth = admit(public, owner(public.engine, "fourth-claim"), network="d" * 64)
    start = Barrier(4)

    def claim(index):
        start.wait(timeout=5)
        return scoped_claim(jobs, public, f"native-{index}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(claim, range(4)))
    (winner,) = [value for value in claims if value is not None]
    assert winner.run_id == first
    assert jobs.get(first).status == "running"
    for run_id in (second, third, fourth):
        assert jobs.get(run_id).status == "queued"
        assert jobs.get(run_id).lease_token == 0
    # Scheduling creates no new hold or dispatch journal.
    with Session(public.engine) as session:
        assert session.scalar(select(func.count()).select_from(BudgetReservation)) == 1
        assert session.scalar(select(func.count()).select_from(ResearchExternalRequest)) == 1


def test_scoped_candidate_filter_and_legacy_defaults_remain_separate(readiness):
    public, jobs, first, second = queued_pair(readiness)
    legacy = jobs.create(mode="ask", goal="Synthetic legacy task")
    assert (
        jobs.claim_next("wrong-account", native_only=True, native_account_id="different-ledger")
        is None
    )
    assert jobs.claim_next("legacy").run_id == legacy.id
    claim = scoped_claim(jobs, public)
    assert claim.run_id == first
    # The old explicit native-only seam retains its original behavior when no
    # account scope was supplied (fixtures/internal compatibility).
    assert jobs.claim_next("old-explicit-native", native_only=True).run_id == second


@pytest.mark.parametrize("native_only,account", [(False, "ledger"), (True, ""), (True, 1)])
def test_invalid_account_scope_cannot_claim_or_mutate(readiness, native_only, account):
    public, jobs, first, second = queued_pair(readiness)
    with pytest.raises(ValueError):
        jobs.claim_next("invalid", native_only=native_only, native_account_id=account)
    assert jobs.get(first).status == jobs.get(second).status == "queued"


@pytest.mark.parametrize("status", ["interrupted", "failed", "cancelled", "succeeded"])
def test_known_stopped_job_does_not_pause_other_visitors(readiness, status):
    public, jobs, first, second = queued_pair(readiness)
    terminal(public, first, status)
    assert scoped_claim(jobs, public).run_id == second
    assert jobs.get(first).status == status


@pytest.mark.parametrize("status", ["cancel_requested", "waiting_retry", "interrupted"])
def test_cancellation_and_waiting_inflight_block_other_native_claims(readiness, status):
    public, jobs, first, second = queued_pair(readiness)
    with Session(public.engine) as session, session.begin():
        run = session.get(ResearchRun, first)
        run.status = status
        run.external_request_in_flight = True
        run.external_request_id = "synthetic-inflight"
    assert scoped_claim(jobs, public) is None
    assert jobs.get(second).status == "queued"


@pytest.mark.parametrize("prepared", [False, True])
def test_cancel_api_then_worker_loss_finishes_local_cancel_without_losing_remote_facts(
    readiness, prepared
):
    public = public_delegation(readiness)
    app = FastAPI()
    app.include_router(create_run_router(public.engine))
    with TestClient(app) as client:
        response = client.get("/api/v1/session")
        assert response.status_code == 200
        requester = hashlib.sha256(client.cookies[COOKIE_NAME].encode()).hexdigest()
        first = admit(public, requester)
        second = admit(public, owner(public.engine, "waiting-after-cancel"), network="b" * 64)
        jobs = JobRepository(public.engine)
        lease = scoped_claim(jobs, public)
        assert lease.run_id == first
        if prepared:
            hold_id = reservation(public, first, 100, status="in_flight")
            with Session(public.engine) as session, session.begin():
                run = session.get(ResearchRun, first)
                run.external_request_in_flight = True
                run.external_request_id = "model-0001"
                session.add(
                    ResearchExternalRequest(
                        run_id=first,
                        request_id="model-0001",
                        status="prepared",
                        reservation_id=hold_id,
                        dispatch_lease_token=lease.token,
                        request_hash="c" * 64,
                    )
                )
        cancelled = client.post(
            f"/api/v1/runs/{first}/cancel",
            headers={
                "X-CSRF-Token": response.json()["csrf_token"],
                "Origin": "http://testserver",
            },
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancel_requested"
        # The former process vanishes without executing Worker._stop.
        new_claim = scoped_claim(jobs, public, "replacement-process")
        assert jobs.get(first).status == "cancelled"
        assert jobs.get(first).lease_token > lease.token
        assert jobs.get(first).external_request_in_flight is prepared
        if prepared:
            assert new_claim is None and jobs.get(second).status == "queued"
            with Session(public.engine) as session:
                assert session.get(BudgetReservation, hold_id).status == "in_flight"
                assert (
                    session.get(ResearchExternalRequest, (first, "model-0001")).status == "prepared"
                )
        else:
            assert new_claim.run_id == second
        assert [event.kind for event in jobs.events_after(first)].count("cancelled") == 1
        jobs.finalize_cancellation(first)
        assert [event.kind for event in jobs.events_after(first)].count("cancelled") == 1


@pytest.mark.parametrize("ambiguous", ["prepared", "unknown", "probe_unknown"])
def test_expiry_exposes_ambiguity_and_never_claims_waiting_work(readiness, ambiguous):
    public, jobs, first, second = queued_pair(readiness)
    lease = scoped_claim(jobs, public)
    assert lease.run_id == first
    if ambiguous == "probe_unknown":
        # Probe accounting shares the exact account despite having no native
        # research RunBinding. An unknown probe hold must pause public claims.
        with Session(public.engine) as session, session.begin():
            probe = session.scalar(select(BudgetReservation))
            probe.status = "unknown"
            probe.actual_microdollars = None
            probe.settlement_key = None
            probe.settled_day = None
            probe.settled_at = None
    else:
        hold = reservation(
            public, first, 100, status="unknown" if ambiguous == "unknown" else "in_flight"
        )
        if ambiguous == "prepared":
            with Session(public.engine) as session, session.begin():
                run = session.get(ResearchRun, first)
                run.external_request_in_flight = True
                run.external_request_id = "model-0001"
                session.add(
                    ResearchExternalRequest(
                        run_id=first,
                        request_id="model-0001",
                        status="prepared",
                        reservation_id=hold,
                        dispatch_lease_token=lease.token,
                        request_hash="c" * 64,
                    )
                )
    with Session(public.engine) as session, session.begin():
        session.get(ResearchRun, first).lease_expires_at = public.now - timedelta(seconds=1)
    assert first in jobs.interrupt_expired()
    assert jobs.get(first).status == "interrupted"
    assert scoped_claim(jobs, public) is None
    assert jobs.get(second).status == "queued" and jobs.get(second).lease_token == 0


def test_expired_known_run_stays_interrupted_while_next_visitor_runs(readiness):
    public, jobs, first, second = queued_pair(readiness)
    assert scoped_claim(jobs, public).run_id == first
    with Session(public.engine) as session, session.begin():
        session.get(ResearchRun, first).lease_expires_at = public.now - timedelta(seconds=1)
    assert first in jobs.interrupt_expired()
    assert scoped_claim(jobs, public).run_id == second
    assert jobs.get(first).status == "interrupted"


@pytest.mark.parametrize(
    "reason,ambiguous,expected",
    [
        (None, False, "worker_shutdown"),
        ("public_session_daily_budget_exceeded", False, "public_session_daily_budget_exceeded"),
        ("public_research_authority_expired", True, "external_outcome_unknown"),
    ],
)
def test_public_stop_reason_is_static_and_never_hides_ambiguous_external_work(
    readiness, reason, ambiguous, expected
):
    public, jobs, first, _ = queued_pair(readiness)
    lease = scoped_claim(jobs, public)
    if ambiguous:
        with Session(public.engine) as session, session.begin():
            session.get(ResearchRun, first).external_request_in_flight = True
    result = jobs.interrupt(lease, reason=reason)
    assert result.status == "interrupted" and result.interruption_reason == expected


def test_interruption_rejects_arbitrary_text_before_mutating_lease(readiness):
    public, jobs, first, _ = queued_pair(readiness)
    lease = scoped_claim(jobs, public)
    with pytest.raises(ValueError, match="Unsupported public interruption reason"):
        jobs.interrupt(lease, reason="secret provider response text")
    assert jobs.get(first).status == "running" and jobs.get(first).lease_token == lease.token


def wait_until_expired(public):
    # Bounded real-clock wait; no stored approval/deadline/hash is edited.
    with Session(public.engine) as session:
        remaining = (
            public.manifest.valid_until - session.scalar(select(func.clock_timestamp()))
        ).total_seconds()
    if remaining > 0:
        assert remaining < 4
        Event().wait(remaining + 0.03)


def test_expired_public_queue_is_fenced_and_frees_owner_for_another_delegation(readiness):
    expired = public_delegation(readiness, deadline_seconds=2)
    visitor = owner(expired.engine, "expired-queue-visitor")
    first = admit(expired, visitor)
    current = public_delegation(readiness, active=expired.active)
    other = admit(current, owner(current.engine, "current-queue-visitor"), network="b" * 64)
    jobs = JobRepository(expired.engine)
    assert jobs.claim_next("wrong-scope", native_only=True, native_account_id="other") is None
    wait_until_expired(expired)
    assert scoped_claim(jobs, current).run_id == other
    old = jobs.get(first)
    assert old.status == "cancelled" and old.lease_token == 1
    assert old.interruption_reason == "public_research_authority_expired"
    assert old.lease_owner is None and old.lease_expires_at is None
    with Session(current.engine) as session, session.begin():
        # Cancellation removes only active-owner pressure; the immutable
        # admission still counts toward daily and historical job caps.
        check_public_admission(
            session,
            delegation=session.get(PublicResearchDelegation, current.delegation_id),
            requester_hash=visitor,
            network_bucket="c" * 64,
            estimated_microdollars=308192,
        )
    assert [event.kind for event in jobs.events_after(first)] == ["cancelled"]


def test_expired_resumed_queue_preserves_saved_evidence_known_cost_and_unknown_hold(readiness):
    public = public_delegation(readiness, deadline_seconds=3)
    first = admit(public, owner(public.engine, "resumed-expiring-visitor"))
    jobs = JobRepository(public.engine)
    lease = scoped_claim(jobs, public)
    assert lease.run_id == first
    left, right = document("fixture:left"), document("fixture:right")
    store = EvidenceStore(public.engine)
    store.save_source(lease, left)
    store.save_source(lease, right)
    saved = store.save_result(lease, result_for(left, right))
    hold_id = reservation(public, first, 400, status="unknown")
    known_id = reservation(public, first, 125)
    jobs.save_checkpoint(
        lease,
        expected_version=0,
        checkpoint={"saved_result": str(saved.id), "observed_model_calls": ["model-0001"]},
        evidence_refs=[str(saved.id)],
    )
    interrupted = jobs.interrupt(lease)
    # This is the exact already-admitted run returning to queued state after
    # explicit recovery; it creates no new PublicResearchRunAccess record.
    with Session(public.engine) as session, session.begin():
        session.get(ResearchRun, first).status = "queued"
        before = {
            "quotes": session.scalar(select(func.count()).select_from(EvidenceQuote)),
            "retrievals": session.scalar(select(func.count()).select_from(SourceRetrieval)),
            "result_sha256": session.get(EvidenceResult, saved.id).content_sha256,
        }
    wait_until_expired(public)
    assert scoped_claim(jobs, public) is None
    cancelled = jobs.get(first)
    assert cancelled.status == "cancelled" and cancelled.lease_token > interrupted.lease_token
    assert cancelled.checkpoint == interrupted.checkpoint
    assert cancelled.evidence_refs == [str(saved.id)]
    with Session(public.engine) as session:
        assert session.get(EvidenceResult, saved.id).content_sha256 == before["result_sha256"]
        assert session.scalar(select(func.count()).select_from(EvidenceQuote)) == before["quotes"]
        assert (
            session.scalar(select(func.count()).select_from(SourceRetrieval))
            == before["retrievals"]
        )
        assert session.get(BudgetReservation, known_id).actual_microdollars == 125
        assert session.get(BudgetReservation, hold_id).status == "unknown"
        assert session.get(BudgetReservation, hold_id).estimated_microdollars == 400
