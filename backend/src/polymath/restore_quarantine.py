"""Quarantine a restored database before any process can act on its old authority.

An old snapshot can rewind revocations, session expiry, funding epochs and
publication grants. This command fences every such authority in one transaction
and keeps all monetary, scientific and audit rows. It never contacts Meta,
Zenodo or a literature provider, and it never re-arms anything: re-enabling paid
or publication work afterwards needs reconciled external facts and fresh human
approval through the normal operator flows. See docs/DEPLOYMENT.md.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession
from polymath.budget_models import BudgetAccount
from polymath.config import Settings
from polymath.database import build_engine
from polymath.operator_models import OperatorSession
from polymath.publication_job_models import PublicationJob
from polymath.publication_jobs import _clear_lease, _event

QUARANTINE_REASON = "restore_quarantine"
ACTIVE_PUBLICATION_STATES = ("queued", "running", "interrupted", "failed")
EXECUTION_GATES = (
    "paid_meta_probe_enabled",
    "paid_meta_research_enabled",
    "public_meta_research_enabled",
    "publication_execution_enabled",
    "literature_ingestion_enabled",
)


class QuarantineRefused(Exception):
    """A precondition failed; nothing was changed."""


@dataclass(frozen=True)
class QuarantineReport:
    accounts_fenced: int
    operator_sessions_revoked: int
    browser_sessions_expired: int
    publication_jobs_frozen: int


def enabled_execution_gates(settings: Settings) -> tuple[str, ...]:
    return tuple(gate for gate in EXECUTION_GATES if getattr(settings, gate))


def quarantine(engine: Engine, migration_heads: set[str]) -> QuarantineReport:
    """Fence all restored authority atomically; idempotent and history-preserving."""
    with engine.connect() as connection:
        current = set(MigrationContext.configure(connection).get_current_heads())
    if current != migration_heads:
        raise QuarantineRefused("schema_not_at_head")
    with Session(engine) as session, session.begin():
        now = session.scalar(select(func.now()))
        # Lock accounts in a fixed order, as the dispatch paths do, so a stray
        # process cannot interleave a grant with the fence.
        accounts = session.scalars(
            select(BudgetAccount).order_by(BudgetAccount.id).with_for_update()
        ).all()
        for account in accounts:
            account.execution_enabled = False
            account.kill_switch = True
            if not account.frozen:
                account.frozen = True
                account.frozen_reason = QUARANTINE_REASON
            # Clearing the scope and advancing the epoch invalidates every
            # restored activation, probe approval and public delegation, which
            # are all bound to the epoch they were armed in.
            account.native_scope = None
            account.native_manifest_sha256 = None
            account.native_control_epoch += 1

        jobs = session.scalars(
            select(PublicationJob)
            .where(PublicationJob.status.in_(ACTIVE_PUBLICATION_STATES))
            .order_by(PublicationJob.id)
            .with_for_update()
        ).all()
        frozen_jobs = 0
        for job in jobs:
            if job.frozen:
                continue
            job.frozen = True
            job.status = "interrupted"
            job.reason = QUARANTINE_REASON
            _clear_lease(job)
            _event(
                session,
                job,
                f"{QUARANTINE_REASON}:{job.lease_token}:{job.event_sequence}",
                "interrupted",
                "Restored from a backup. Whether the remote deposit changed after the "
                "backup is unknown; manual reconciliation is required.",
            )
            frozen_jobs += 1

        operators = session.execute(
            update(OperatorSession)
            .where(OperatorSession.revoked_at.is_(None))
            .values(revoked_at=now)
        ).rowcount
        browsers = session.execute(
            update(BrowserSession).where(BrowserSession.expires_at > now).values(expires_at=now)
        ).rowcount
    return QuarantineReport(len(accounts), operators, browsers, frozen_jobs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fence all spending, publication and session authority in a restored "
        "database. Keeps every monetary, scientific and audit row."
    )
    parser.add_argument(
        "--confirm-restored-database",
        action="store_true",
        help="required: confirms DATABASE_URL points at the isolated restored copy",
    )
    arguments = parser.parse_args(argv)
    if not arguments.confirm_restored_database:
        parser.error("--confirm-restored-database is required")
    settings = Settings()
    enabled = enabled_execution_gates(settings)
    if enabled:
        print("Refused: turn these gates off before quarantine: " + ", ".join(enabled))
        return 2
    from polymath.main import migration_heads

    engine = build_engine(settings)
    try:
        report = quarantine(engine, migration_heads())
    except QuarantineRefused as error:
        print(f"Refused: {error}. Run migrations on the restored copy first; nothing changed.")
        return 2
    finally:
        engine.dispose()
    print(
        "Quarantine complete: "
        f"{report.accounts_fenced} budget account(s) disabled, killed, frozen and re-epoched; "
        f"{report.operator_sessions_revoked} operator session(s) revoked; "
        f"{report.browser_sessions_expired} browser session(s) expired; "
        f"{report.publication_jobs_frozen} publication job(s) frozen for reconciliation. "
        "Nothing was re-armed, and no external request was made."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
