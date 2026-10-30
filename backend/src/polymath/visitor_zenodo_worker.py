"""Publish visitors' confirmed records to their own Zenodo (docs/CONNECT_ZENODO.md).

Each record goes through create draft → upload exact bytes → verify → publish →
verify again. The moment before creating a draft is recorded first. After an
unknown creation outcome the worker only searches the visitor's own deposits
for the exact marker; it never creates a second draft by itself.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import signal
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from pydantic import SecretStr
from sqlalchemy import Engine, or_, select
from sqlalchemy.orm import Session

from polymath.config import Settings
from polymath.database import build_engine
from polymath.publication import PublicationError, _artifact
from polymath.visitor_zenodo import (
    LEASE_SECONDS,
    VisitorZenodoError,
    VisitorZenodoPolicy,
    VisitorZenodoStore,
    _now,
    stored_manifest,
)
from polymath.visitor_zenodo_models import VisitorPublication
from polymath.zenodo import ZenodoClient, ZenodoDeposit, ZenodoError, metadata_matches

ClientFactory = Callable[[str, SecretStr], ZenodoClient]


class _Stop(Exception):
    def __init__(self, status: str, code: str | None = None):
        super().__init__(code or status)
        self.status, self.code = status, code


@dataclass(frozen=True)
class _Claim:
    id: uuid.UUID
    owner_hash: str
    destination: str
    check_first: bool


def live_client(destination: str, token: SecretStr) -> ZenodoClient:
    return ZenodoClient(destination, token, live_enabled=True)


class VisitorZenodoWorker:
    def __init__(
        self,
        engine: Engine,
        store: VisitorZenodoStore,
        client_factory: ClientFactory = live_client,
        token_transport=None,
    ):
        self.engine, self.store = engine, store
        self.client_factory, self.token_transport = client_factory, token_transport

    def claim(self, worker_id: str) -> _Claim | None:
        with Session(self.engine) as session, session.begin():
            now = _now(session)
            row = session.scalar(
                select(VisitorPublication)
                .where(
                    or_(
                        VisitorPublication.status.in_(("queued", "checking")),
                        (VisitorPublication.status == "running")
                        & (VisitorPublication.lease_expires_at < now),
                    )
                )
                .order_by(VisitorPublication.updated_at, VisitorPublication.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            # A lease that ended mid-creation is an unknown outcome: search only.
            check_first = row.status == "checking" or (
                row.create_started_at is not None and row.deposit_id is None
            )
            row.status, row.lease_owner = "running", worker_id
            row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
            row.updated_at = now
            return _Claim(row.id, row.owner_session_hash, row.destination, check_first)

    def _update(self, claim: _Claim, worker_id: str, **values) -> VisitorPublication:
        with Session(self.engine) as session, session.begin():
            row = session.get(VisitorPublication, claim.id, with_for_update=True)
            if row is None or row.status != "running" or row.lease_owner != worker_id:
                raise _Stop("lease_lost")
            for name, value in values.items():
                setattr(row, name, value)
            row.updated_at = _now(session)
            session.flush()
            session.expunge(row)
            return row

    def _finish(self, claim: _Claim, worker_id: str, status: str, code: str | None) -> None:
        try:
            self._update(
                claim,
                worker_id,
                status=status,
                error_code=code,
                lease_owner=None,
                lease_expires_at=None,
            )
        except _Stop:
            pass

    def _content(self, claim: _Claim, row: VisitorPublication, markdown_sha256: str) -> bytes:
        with Session(self.engine) as session:
            try:
                artifact = _artifact(session, claim.owner_hash, row.artifact_id)
            except PublicationError:
                raise _Stop("failed", "zenodo_publication_not_found") from None
        content = artifact.markdown.encode("utf-8")
        if hashlib.sha256(content).hexdigest() != markdown_sha256:
            raise _Stop("failed", "zenodo_publication_changed")
        return content

    async def _verify(self, client: ZenodoClient, deposit, manifest, content: bytes) -> None:
        if not metadata_matches(deposit, manifest):
            raise _Stop("failed", "zenodo_metadata_mismatch")
        if (
            len(deposit.files) != 1
            or deposit.files[0].filename != manifest.file_name
            or deposit.files[0].size != len(content)
        ):
            raise _Stop("failed", "zenodo_file_mismatch")
        remote = await client.get_file_bytes(
            deposit, manifest.file_name, maxbytes=manifest.markdown_bytes
        )
        if hashlib.sha256(remote).hexdigest() != manifest.markdown_sha256:
            raise _Stop("failed", "zenodo_file_mismatch")

    async def _publish(self, claim: _Claim, worker_id: str) -> None:
        row = self._update(claim, worker_id)
        manifest = stored_manifest(row)
        content = self._content(claim, row, manifest.markdown_sha256)
        try:
            token = await self.store.access_token(
                claim.owner_hash, claim.destination, self.token_transport
            )
        except VisitorZenodoError:
            raise _Stop("reconnect", "zenodo_reconnect_required") from None
        client = self.client_factory(claim.destination, token)
        deposit_id = row.deposit_id
        if deposit_id is None and claim.check_first:
            matches = [
                found
                for found in await client.find_deposits(manifest.remote_marker)
                if metadata_matches(found, manifest)
            ]
            if not matches:
                raise _Stop("missing", "zenodo_nothing_found")
            if len(matches) > 1:
                raise _Stop("failed", "zenodo_reconciliation_inconclusive")
            deposit_id = matches[0].id
            self._update(claim, worker_id, deposit_id=deposit_id)
        if deposit_id is None:
            self._update(claim, worker_id, create_started_at=_db_now(self.engine))
            try:
                created = await client.create_draft(manifest)
            except ZenodoError as error:
                if error.outcome_unknown:
                    raise _Stop("checking", "zenodo_outcome_unknown") from None
                # A definite refusal created nothing, so a later try may create.
                self._update(claim, worker_id, create_started_at=None)
                raise
            deposit_id = created.id
            self._update(claim, worker_id, deposit_id=deposit_id)
        deposit = await client.get_deposit(deposit_id)
        if not deposit.published:
            if deposit.submitted or deposit.state not in ("unsubmitted", "inprogress"):
                raise _Stop("failed", "zenodo_unexpected_state")
            if not deposit.files:
                await client.upload_file(
                    deposit, manifest.file_name, content, manifest.markdown_sha256
                )
                deposit = await client.get_deposit(deposit_id)
            await self._verify(client, deposit, manifest, content)
            try:
                await client.publish(deposit_id)
            except ZenodoError as error:
                # Publishing one draft twice cannot create a second record, so an
                # unknown outcome is settled by reading the deposit below.
                if not error.outcome_unknown:
                    raise
            deposit = await client.get_deposit(deposit_id)
        if not deposit.published:
            raise _Stop("failed", "zenodo_not_yet_published")
        await self._verify(client, deposit, manifest, content)
        self._published(claim, worker_id, deposit)

    def _published(self, claim: _Claim, worker_id: str, deposit: ZenodoDeposit) -> None:
        self._update(
            claim,
            worker_id,
            status="published",
            deposit_id=deposit.id,
            doi=deposit.doi,
            record_url=deposit.record_url,
            published_at=_db_now(self.engine),
            error_code=None,
            lease_owner=None,
            lease_expires_at=None,
        )

    async def run_once(self, worker_id: str) -> str:
        claim = await asyncio.to_thread(self.claim, worker_id)
        if claim is None:
            return "idle"
        try:
            await self._publish(claim, worker_id)
            return "published"
        except _Stop as stop:
            if stop.status != "lease_lost":
                self._finish(claim, worker_id, stop.status, stop.code)
            return stop.status
        except ZenodoError as error:
            code = str(error)
            status = "reconnect" if code == "zenodo_access_denied" else "failed"
            self._finish(claim, worker_id, status, code)
            return status
        except VisitorZenodoError as error:
            self._finish(claim, worker_id, "failed", str(error))
            return "failed"
        except Exception:
            # Never let one record stop the worker. Mid-creation, only a search
            # may follow; otherwise the visitor can choose to try again.
            return self._unexpected(claim, worker_id)

    def _unexpected(self, claim: _Claim, worker_id: str) -> str:
        with Session(self.engine) as session:
            row = session.get(VisitorPublication, claim.id)
            unknown = (
                row is not None and row.create_started_at is not None and (row.deposit_id is None)
            )
        status = "checking" if unknown else "failed"
        self._finish(claim, worker_id, status, "zenodo_unexpected_error")
        return status

    async def run_until_stopped(self, worker_id: str, stop: asyncio.Event) -> None:
        while not stop.is_set():
            if await self.run_once(worker_id) == "idle":
                try:
                    await asyncio.wait_for(stop.wait(), timeout=3)
                except TimeoutError:
                    pass


def _db_now(engine: Engine):
    with Session(engine) as session:
        return _now(session)


async def run(settings: Settings, *, once: bool, worker_id: str) -> int:
    policy = VisitorZenodoPolicy.from_settings(settings)
    if not policy.destinations:
        print("Visitor Zenodo publishing is off; nothing claimed or sent.")
        return 2
    engine = build_engine(settings)
    try:
        worker = VisitorZenodoWorker(engine, VisitorZenodoStore(engine, policy))
        if once:
            print(f"Visitor Zenodo worker result: {await worker.run_once(worker_id)}")
            return 0
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
        await worker.run_until_stopped(worker_id, stop)
        return 0
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish visitors' confirmed Zenodo records")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id", default="visitor-zenodo-worker")
    arguments = parser.parse_args()
    if not 1 <= len(arguments.worker_id) <= 128:
        parser.error("worker-id must contain 1–128 characters")
    return asyncio.run(run(Settings(), once=arguments.once, worker_id=arguments.worker_id))


if __name__ == "__main__":
    raise SystemExit(main())
