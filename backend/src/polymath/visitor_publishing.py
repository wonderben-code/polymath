"""Publishing levels for a visitor's own Zenodo connection.

The visitor chooses once how much Polymath may do with their connection:

  1  ask me every time    nothing automatic; publish by hand from a finding
  2  draft for me         write it up and prepare the exact record; tap Publish
  3  publish to practice  as 2, then publish to their Zenodo sandbox
  4  publish for real     as 3, to Zenodo with a DOI, after a one-time confirmation

Only writing a finding up needs the visitor's open browser, which uses the
ordinary artifact route (with its free-use limits). Preparing and confirming a
record go through VisitorZenodoStore with every existing check, and publishing
itself is the visitor Zenodo worker's job. Switching a level on never sweeps up
work saved before that moment.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from pydantic import ValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polymath.access_models import ResearchRunAccess
from polymath.artifact_models import ArtifactRequest, GeneratedArtifact
from polymath.assessment_eligibility import current_supported_condition
from polymath.evidence_models import EvidenceResult
from polymath.models import ResearchRun
from polymath.publication_contracts import Creator, PublicationMetadata
from polymath.publishing import PublishingError, _full_text, _metadata, _title
from polymath.visitor_zenodo import VisitorZenodoError, VisitorZenodoStore, _browser, _now
from polymath.visitor_zenodo_models import (
    VisitorPublication,
    VisitorPublishingItem,
    VisitorPublishingPreference,
    ZenodoConnection,
)

LEVEL_DESTINATION = {3: "sandbox", 4: "production"}
WRITE_KINDS = ("write_summary", "write_paper")
ACTIVE = ("pending_write", "writing", "drafted", "ready", "queued")
TERMINAL_RUN = ("succeeded", "failed", "cancelled", "interrupted")
CANDIDATE_SCAN = 50


class VisitorPublishingError(Exception):
    """Sanitized code; never echoes stored or submitted text."""


def _view(row: VisitorPublishingPreference) -> dict:
    return {
        "level": row.level,
        "draft_destination": row.draft_destination,
        "write_kind": row.write_kind,
        "full_text_only": row.full_text_only,
        "daily_cap": row.daily_cap,
        "creators": list(row.creators),
        "license": row.license,
        "enabled_at": row.enabled_at.isoformat() if row.enabled_at else None,
        "production_confirmed": row.production_confirmed_at is not None,
    }


def _preference(session: Session, owner_hash: str, *, lock=False) -> VisitorPublishingPreference:
    session.execute(
        insert(VisitorPublishingPreference)
        .values(owner_session_hash=owner_hash)
        .on_conflict_do_nothing()
    )
    statement = select(VisitorPublishingPreference).where(
        VisitorPublishingPreference.owner_session_hash == owner_hash
    )
    return session.scalar(statement.with_for_update() if lock else statement)


class VisitorPublishing:
    def __init__(self, engine: Engine, store: VisitorZenodoStore):
        self.engine, self.store = engine, store

    def _connected(self, session: Session, owner_hash: str) -> set[str]:
        offered = set(self.store.policy.destinations)
        return {
            name
            for name in session.scalars(
                select(ZenodoConnection.destination).where(
                    ZenodoConnection.owner_session_hash == owner_hash
                )
            )
            if name in offered
        }

    # Preferences -------------------------------------------------------------

    def preferences(self, owner_hash: str) -> dict:
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            return _view(_preference(session, owner_hash))

    def set_preferences(
        self,
        owner_hash: str,
        *,
        level: int,
        draft_destination: str,
        write_kind: str,
        full_text_only: bool,
        daily_cap: int,
        creators: list[dict],
        license: str,
        confirm_production: bool = False,
    ) -> dict:
        if level not in (1, 2, 3, 4) or write_kind not in WRITE_KINDS:
            raise VisitorPublishingError("invalid_publishing_preferences")
        if draft_destination not in ("sandbox", "production") or not 1 <= daily_cap <= 10:
            raise VisitorPublishingError("invalid_publishing_preferences")
        try:
            checked = PublicationMetadata(
                title="Settings check",
                description="Settings check",
                creators=tuple(Creator(**creator) for creator in creators),
                license=license,
            )
        except (ValidationError, TypeError, ValueError):
            if level >= 2:
                raise VisitorPublishingError("publishing_name_required") from None
            checked = None
        needed = LEVEL_DESTINATION.get(level, draft_destination if level == 2 else None)
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            if needed is not None and needed not in self._connected(session, owner_hash):
                raise VisitorPublishingError("zenodo_not_connected")
            row = _preference(session, owner_hash, lock=True)
            now = _now(session)
            if level == 4 and row.production_confirmed_at is None and not confirm_production:
                raise VisitorPublishingError("production_confirmation_required")
            if level >= 2 and (row.level < 2 or row.enabled_at is None):
                row.enabled_at = now
            if level < 2:
                row.enabled_at = None
            if level == 4 and confirm_production:
                row.production_confirmed_at = now
            if level < 4:
                row.production_confirmed_at = None
            row.level, row.draft_destination, row.write_kind = level, draft_destination, write_kind
            row.full_text_only, row.daily_cap, row.license = full_text_only, daily_cap, license
            if checked is not None:
                row.creators = [creator.model_dump(mode="json") for creator in checked.creators]
            row.updated_at = now
            session.flush()
            return _view(row)

    # The ledger --------------------------------------------------------------

    def items(self, owner_hash: str) -> list[dict]:
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            self._absorb(session, owner_hash)
            rows = session.scalars(
                select(VisitorPublishingItem)
                .where(VisitorPublishingItem.owner_session_hash == owner_hash)
                .order_by(VisitorPublishingItem.created_at.desc(), VisitorPublishingItem.id)
                .limit(100)
            ).all()
            return [self._item_view(session, row) for row in rows]

    def next_step(self, owner_hash: str) -> dict:
        """Do every due server-side step, then say what the browser should do."""
        for _ in range(4):
            work = self._plan(owner_hash)
            if work["action"] == "prepare":
                self._prepare(owner_hash, work)
            elif work["action"] == "publish":
                if self._publish(owner_hash, work):
                    return {"action": "none", "reason": "daily_cap"}
            else:
                return work
        return {"action": "none", "reason": "working"}

    def attach_write(self, owner_hash: str, item_id: uuid.UUID, run_id: uuid.UUID) -> dict:
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            item = self._owned(session, owner_hash, item_id)
            if item.status != "pending_write":
                raise VisitorPublishingError("publishing_stage_not_due")
            request = session.scalar(
                select(ArtifactRequest)
                .join(ResearchRunAccess, ResearchRunAccess.run_id == ArtifactRequest.run_id)
                .where(
                    ArtifactRequest.run_id == run_id,
                    ResearchRunAccess.session_hash == owner_hash,
                )
            )
            if (
                request is None
                or request.source_result_id != item.result_id
                or request.kind != item.write_kind
            ):
                raise VisitorPublishingError("publishing_link_invalid")
            item.artifact_run_id, item.status, item.reason = run_id, "writing", None
            item.updated_at = func.clock_timestamp()
            session.flush()
            return self._item_view(session, item)

    def skip(self, owner_hash: str, item_id: uuid.UUID) -> dict:
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            item = self._owned(session, owner_hash, item_id)
            if item.status not in ("pending_write", "writing", "drafted", "ready"):
                raise VisitorPublishingError("publishing_stage_not_due")
            item.status, item.reason = "skipped", "skipped_by_you"
            item.updated_at = func.clock_timestamp()
            session.flush()
            return self._item_view(session, item)

    # Internal steps ----------------------------------------------------------

    def _owned(
        self, session: Session, owner_hash: str, item_id: uuid.UUID
    ) -> VisitorPublishingItem:
        item = session.scalar(
            select(VisitorPublishingItem)
            .where(
                VisitorPublishingItem.id == item_id,
                VisitorPublishingItem.owner_session_hash == owner_hash,
            )
            .with_for_update()
        )
        if item is None:
            raise VisitorPublishingError("publishing_item_not_found")
        return item

    def _open(self, session: Session, owner_hash: str) -> list[VisitorPublishingItem]:
        return list(
            session.scalars(
                select(VisitorPublishingItem)
                .where(
                    VisitorPublishingItem.owner_session_hash == owner_hash,
                    VisitorPublishingItem.status.in_(ACTIVE),
                )
                .order_by(VisitorPublishingItem.created_at, VisitorPublishingItem.id)
                .with_for_update()
            )
        )

    def _absorb(self, session: Session, owner_hash: str) -> None:
        for item in self._open(session, owner_hash):
            if item.status == "writing" and item.artifact_run_id is not None:
                run = session.get(ResearchRun, item.artifact_run_id)
                if run is None or run.status not in TERMINAL_RUN:
                    continue
                artifact = session.scalar(
                    select(GeneratedArtifact).where(GeneratedArtifact.run_id == run.id)
                )
                if (
                    run.status == "succeeded"
                    and artifact is not None
                    and (artifact.status == "ready")
                ):
                    item.artifact_id, item.status = artifact.id, "drafted"
                else:
                    item.status, item.reason = "failed", "write_up_not_ready"
            elif item.status in ("ready", "queued") and item.publication_id is not None:
                record = session.get(VisitorPublication, item.publication_id)
                if record is None:
                    item.status, item.reason = "failed", "publication_removed"
                elif record.status == "published":
                    item.status = "published"
                elif record.status in ("failed", "missing", "reconnect"):
                    item.status, item.reason = "failed", f"zenodo_{record.status}"
                elif record.status != "preview" and item.status == "ready":
                    item.status = "queued"
                else:
                    continue
            else:
                continue
            item.updated_at = func.clock_timestamp()
        session.flush()

    def _plan(self, owner_hash: str) -> dict:
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            preference = _preference(session, owner_hash, lock=True)
            if preference.level < 2:
                return {"action": "none", "reason": "manual"}
            self._absorb(session, owner_hash)
            connected = self._connected(session, owner_hash)
            for item in self._open(session, owner_hash):
                effective = min(item.level, preference.level)
                if item.status == "pending_write":
                    return {
                        "action": "write",
                        "item_id": str(item.id),
                        "result_id": str(item.result_id),
                        "kind": item.write_kind,
                    }
                if item.status == "writing":
                    return {
                        "action": "wait",
                        "item_id": str(item.id),
                        "run_id": str(item.artifact_run_id),
                    }
                if item.status == "drafted" and item.destination in connected:
                    artifact = session.get(GeneratedArtifact, item.artifact_id)
                    try:
                        metadata = _metadata(
                            SimpleNamespace(
                                creators=preference.creators,
                                keywords=(),
                                license=preference.license,
                            ),
                            artifact,
                        )
                    except PublishingError:
                        item.status, item.reason = "failed", "publishing_name_required"
                        continue
                    return {"action": "prepare", "item_id": str(item.id), "metadata": metadata}
                # Only the destination the current level allows: lowering the
                # level never lets an item prepared for production through.
                allowed = LEVEL_DESTINATION.get(effective) == item.destination and (
                    item.destination != "production"
                    or preference.production_confirmed_at is not None
                )
                if item.status == "ready" and allowed and item.destination in connected:
                    return {"action": "publish", "item_id": str(item.id)}
            return self._candidate(session, owner_hash, preference)

    def _candidate(
        self, session: Session, owner_hash: str, preference: VisitorPublishingPreference
    ) -> dict:
        today = session.scalar(
            select(func.count())
            .select_from(VisitorPublishingItem)
            .where(
                VisitorPublishingItem.owner_session_hash == owner_hash,
                VisitorPublishingItem.created_at >= func.date_trunc("day", _now(session)),
            )
        )
        if today >= preference.daily_cap:
            return {"action": "none", "reason": "daily_cap"}
        handled = select(VisitorPublishingItem.result_id).where(
            VisitorPublishingItem.owner_session_hash == owner_hash
        )
        rows = session.scalars(
            select(EvidenceResult)
            .join(ResearchRunAccess, ResearchRunAccess.run_id == EvidenceResult.run_id)
            .join(ResearchRun, ResearchRun.id == EvidenceResult.run_id)
            .where(
                ResearchRunAccess.session_hash == owner_hash,
                ResearchRun.task_kind == "research",
                EvidenceResult.status.in_(("bridge", "answer")),
                EvidenceResult.created_at >= preference.enabled_at,
                EvidenceResult.id.not_in(handled),
                current_supported_condition(),
            )
            .order_by(EvidenceResult.created_at, EvidenceResult.id)
            .limit(CANDIDATE_SCAN)
        ).all()
        chosen = next(
            (row for row in rows if not preference.full_text_only or _full_text(row)), None
        )
        if chosen is None:
            return {"action": "none", "reason": "nothing_new"}
        item = VisitorPublishingItem(
            id=uuid.uuid4(),
            owner_session_hash=owner_hash,
            result_id=chosen.id,
            level=preference.level,
            destination=LEVEL_DESTINATION.get(preference.level, preference.draft_destination),
            write_kind=preference.write_kind,
        )
        session.add(item)
        session.flush()
        return {
            "action": "write",
            "item_id": str(item.id),
            "result_id": str(item.result_id),
            "kind": item.write_kind,
        }

    def _settle(self, owner_hash: str, item_id: str, **values) -> None:
        with Session(self.engine) as session, session.begin():
            item = self._owned(session, owner_hash, uuid.UUID(item_id))
            for name, value in values.items():
                setattr(item, name, value)
            item.updated_at = func.clock_timestamp()

    def _prepare(self, owner_hash: str, work: dict) -> None:
        with Session(self.engine) as session:
            item = session.get(VisitorPublishingItem, uuid.UUID(work["item_id"]))
            artifact_id, destination = item.artifact_id, item.destination
        try:
            record = self.store.preview(
                owner_hash,
                artifact_id,
                PublicationMetadata.model_validate(work["metadata"]),
                destination,
            )
        except (VisitorZenodoError, ValidationError, ValueError) as error:
            code = str(error) if isinstance(error, VisitorZenodoError) else "publishing_invalid"
            self._settle(owner_hash, work["item_id"], status="failed", reason=code[:80])
            return
        self._settle(
            owner_hash,
            work["item_id"],
            status="ready",
            publication_id=uuid.UUID(record["id"]),
            reason=None,
        )

    def _publish(self, owner_hash: str, work: dict) -> bool:
        """Confirm a ready record; True when today's Zenodo limit holds it back."""
        with Session(self.engine) as session:
            item = session.get(VisitorPublishingItem, uuid.UUID(work["item_id"]))
            record = session.get(VisitorPublication, item.publication_id)
            publication_id, digest = record.id, record.manifest_sha256
            permanent = item.destination == "production"
        try:
            # The visitor confirmed permanent records once, when choosing level 4.
            self.store.confirm(owner_hash, publication_id, digest, confirm_permanent=permanent)
        except VisitorZenodoError as error:
            code = str(error)
            if code == "zenodo_daily_limit":
                # Waits for tomorrow; nothing is lost.
                self._settle(owner_hash, work["item_id"], reason=code)
                return True
            self._settle(owner_hash, work["item_id"], status="failed", reason=code[:80])
            return False
        self._settle(owner_hash, work["item_id"], status="queued", reason=None)
        return False

    def _item_view(self, session: Session, item: VisitorPublishingItem) -> dict:
        result = session.get(EvidenceResult, item.result_id)
        record = (
            session.get(VisitorPublication, item.publication_id) if item.publication_id else None
        )
        return {
            "id": str(item.id),
            "result_id": str(item.result_id),
            "title": _title(result) if result is not None else "A saved finding",
            "level": item.level,
            "destination": item.destination,
            "write_kind": item.write_kind,
            "status": item.status,
            "reason": item.reason,
            "artifact_id": str(item.artifact_id) if item.artifact_id else None,
            "publication_id": str(item.publication_id) if item.publication_id else None,
            "manifest_sha256": record.manifest_sha256 if record else None,
            "doi": record.doi if record else None,
            "record_url": record.record_url if record else None,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }
