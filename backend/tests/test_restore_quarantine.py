"""Restore quarantine against real PostgreSQL with populated synthetic authority."""

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_artifact_api import artifacts_api as artifacts_api_fixture
from test_publication import publication as publication_fixture
from test_publication_jobs import setup_job

from polymath import restore_quarantine
from polymath.access_models import BrowserSession
from polymath.budget import BudgetService
from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.evidence_models import EvidenceResult
from polymath.main import migration_heads
from polymath.models import ResearchRun
from polymath.operator_models import OperatorSession
from polymath.publication_job_models import PublicationJob, PublicationJobEvent

artifacts_api = artifacts_api_fixture
publication = publication_fixture


def arm(engine, account_id, *, frozen_reason=None):
    BudgetService(engine).create_account(
        account_id, hard_cap_microdollars=150_000_000, daily_cap_microdollars=50_000_000
    )
    with Session(engine) as session, session.begin():
        session.execute(
            update(BudgetAccount)
            .where(BudgetAccount.id == account_id)
            .values(
                execution_enabled=True,
                pricing_verified=True,
                pricing_reference="synthetic-fixture-pricing",
                native_scope="research",
                native_manifest_sha256="b" * 64,
                native_control_epoch=3,
                frozen=frozen_reason is not None,
                frozen_reason=frozen_reason,
            )
        )


def counts(engine):
    with Session(engine) as session:
        return tuple(
            session.scalar(select(func.count()).select_from(model))
            for model in (ResearchRun, EvidenceResult, BudgetReservation, PublicationJobEvent)
        )


@pytest.mark.integration
def test_quarantine_fences_every_restored_authority_and_keeps_history(publication):
    engine = publication.engine
    _, _, _, job = setup_job(publication)
    arm(engine, "native-account")
    arm(engine, "already-frozen", frozen_reason="actual_cost_exceeded_cap")
    runs, results, reservations, events = counts(engine)

    report = restore_quarantine.quarantine(engine, migration_heads())

    assert report.accounts_fenced == 2
    assert report.operator_sessions_revoked == 1
    assert report.browser_sessions_expired >= 1
    assert report.publication_jobs_frozen == 1
    with Session(engine) as session:
        now = session.scalar(select(func.now()))
        native = session.get(BudgetAccount, "native-account")
        assert not native.execution_enabled and native.kill_switch and native.frozen
        assert native.frozen_reason == "restore_quarantine"
        assert native.native_scope is None and native.native_manifest_sha256 is None
        assert native.native_control_epoch == 4
        assert native.pricing_verified and native.pricing_reference
        earlier = session.get(BudgetAccount, "already-frozen")
        assert earlier.frozen_reason == "actual_cost_exceeded_cap"
        assert earlier.native_control_epoch == 4
        assert (
            session.scalar(
                select(func.count())
                .select_from(OperatorSession)
                .where(OperatorSession.revoked_at.is_(None))
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(BrowserSession)
                .where(BrowserSession.expires_at > now)
            )
            == 0
        )
        frozen = session.get(PublicationJob, job.job_id)
        assert frozen.frozen and frozen.status == "interrupted"
        assert frozen.reason == "restore_quarantine" and frozen.lease_owner is None
    assert counts(engine) == (runs, results, reservations, events + 1)

    again = restore_quarantine.quarantine(engine, migration_heads())
    assert again.publication_jobs_frozen == 0
    assert again.operator_sessions_revoked == 0 and again.browser_sessions_expired == 0
    assert counts(engine) == (runs, results, reservations, events + 1)
    with Session(engine) as session:
        assert session.get(BudgetAccount, "native-account").native_control_epoch == 5


@pytest.mark.integration
def test_quarantine_refuses_an_unmigrated_schema_without_changes(publication):
    engine = publication.engine
    arm(engine, "native-account")
    with pytest.raises(restore_quarantine.QuarantineRefused, match="schema_not_at_head"):
        restore_quarantine.quarantine(engine, {"not-the-current-head"})
    with Session(engine) as session:
        account = session.get(BudgetAccount, "native-account")
        assert account.execution_enabled and not account.kill_switch
        assert account.native_control_epoch == 3
