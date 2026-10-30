"""Publishing approval levels and the publishing autopilot's ledger.

The publisher chooses once how much Polymath may do on its own:

  1  ask every time       nothing automatic
  2  draft for me         write and prepare; the publisher taps Publish
  3  publish to sandbox   write, prepare, approve and queue for Zenodo sandbox
  4  publish for real     as 3, to Zenodo production, after a one-time
                          confirmation, within an evidence bar and a daily cap

This module never writes, prepares, approves, queues or contacts Zenodo. It
chooses the next step for the publisher's open browser, which performs it
through the ordinary artifact, publication and job routes (with every existing
check), and then records the exact linked object here. Each item therefore
explains why it was written and published.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import ValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from polymath.access_models import ResearchRunAccess
from polymath.artifact_models import ArtifactRequest, GeneratedArtifact
from polymath.assessment_eligibility import current_supported_condition
from polymath.evidence_models import EvidenceResult
from polymath.models import ResearchRun
from polymath.publication_contracts import Creator, PublicationMetadata
from polymath.publication_job_models import PublicationJob
from polymath.publication_models import PublicationApproval, PublicationManifestRecord
from polymath.publishing_models import PublishingItem, PublishingPreference

TERMINAL = ("succeeded", "failed", "cancelled", "interrupted")
LEVEL_DESTINATION = {3: "sandbox", 4: "production"}
WRITE_KINDS = ("write_summary", "write_paper")
ACTIVE = ("pending_write", "writing", "drafted", "ready", "approved", "queued")
CANDIDATE_SCAN = 50


class PublishingError(Exception):
    """Sanitized code; never echoes stored or submitted text."""


def _now(session: Session) -> datetime:
    return session.scalar(select(func.clock_timestamp()))


def _preference(session: Session, actor_id: str, *, lock=False) -> PublishingPreference:
    statement = select(PublishingPreference).where(PublishingPreference.actor_id == actor_id)
    row = session.scalar(statement.with_for_update() if lock else statement)
    if row is None:
        row = PublishingPreference(actor_id=actor_id)
        session.add(row)
        session.flush()
        if lock:
            row = session.scalar(statement.with_for_update())
    return row


def _preference_view(row: PublishingPreference) -> dict:
    return {
        "level": row.level,
        "draft_destination": row.draft_destination,
        "write_kind": row.write_kind,
        "full_text_only": row.full_text_only,
        "daily_cap": row.daily_cap,
        "creators": list(row.creators),
        "license": row.license,
        "keywords": list(row.keywords),
        "enabled_at": row.enabled_at.isoformat() if row.enabled_at else None,
        "production_confirmed": row.production_confirmed_at is not None,
    }


def _title(result: EvidenceResult) -> str:
    try:
        finding = result.payload["result"]["finding"]
        value = finding.get("title") or finding.get("question") or ""
    except (KeyError, TypeError, AttributeError):
        value = ""
    value = " ".join(str(value).split())
    return value[:300] or "A saved finding"


def _full_text(result: EvidenceResult) -> bool:
    try:
        quotes = result.payload["result"]["quotes"]
    except (KeyError, TypeError):
        return False
    return any(
        isinstance(quote, dict) and quote.get("content_kind") == "full_text" for quote in quotes
    )


def _metadata(preference: PublishingPreference, artifact: GeneratedArtifact) -> dict:
    """Default Zenodo metadata from the publisher's settings and the write-up itself."""
    draft = (artifact.payload or {}).get("draft") or {}
    title = " ".join(str(draft.get("title") or "").split())[:300]
    abstract = " ".join(
        " ".join(str(part.get("text", "")).split())
        for part in draft.get("abstract") or ()
        if isinstance(part, dict)
    )[:4800]
    description = (
        f"{abstract} Written by Polymath from saved, cited evidence; claims carry the model's "
        "support labels and are not independent scientific verification."
    )[:5000]
    try:
        return PublicationMetadata(
            title=title,
            description=description,
            creators=tuple(Creator(**creator) for creator in preference.creators),
            keywords=tuple(preference.keywords),
            license=preference.license,
        ).model_dump(mode="json")
    except (ValidationError, TypeError, ValueError):
        raise PublishingError("publishing_metadata_invalid") from None


class PublishingStore:
    def __init__(self, engine: Engine, *, destinations: tuple[str, ...] = ()):
        if engine.dialect.name != "postgresql":
            raise ValueError("Publishing requires PostgreSQL")
        self.engine = engine
        self.destinations = destinations

    # Preferences -------------------------------------------------------------

    def preferences(self, actor_id: str) -> dict:
        with Session(self.engine) as session, session.begin():
            return _preference_view(_preference(session, actor_id))

    def set_preferences(
        self,
        actor_id: str,
        *,
        level: int,
        draft_destination: str,
        write_kind: str,
        full_text_only: bool,
        daily_cap: int,
        creators: list[dict],
        license: str,
        keywords: list[str],
        confirm_production: bool = False,
    ) -> dict:
        if level not in (1, 2, 3, 4) or write_kind not in WRITE_KINDS:
            raise PublishingError("invalid_publishing_preferences")
        if draft_destination not in ("sandbox", "production") or not 1 <= daily_cap <= 10:
            raise PublishingError("invalid_publishing_preferences")
        needed = LEVEL_DESTINATION.get(level, draft_destination if level == 2 else None)
        if needed is not None and needed not in self.destinations:
            raise PublishingError("publishing_destination_unavailable")
        try:
            metadata = PublicationMetadata(
                title="Settings check",
                description="Settings check",
                creators=tuple(Creator(**creator) for creator in creators),
                keywords=tuple(keywords),
                license=license,
            )
        except (ValidationError, TypeError, ValueError):
            if level >= 2:
                raise PublishingError("publishing_metadata_invalid") from None
            metadata = None
        with Session(self.engine) as session, session.begin():
            row = _preference(session, actor_id, lock=True)
            now = _now(session)
            if level == 4 and row.production_confirmed_at is None and not confirm_production:
                raise PublishingError("production_confirmation_required")
            if level >= 2 and (row.level < 2 or row.enabled_at is None):
                # Switching on never sweeps up work saved before this moment.
                row.enabled_at = now
            if level < 2:
                row.enabled_at = None
            if level == 4 and confirm_production:
                row.production_confirmed_at = now
            if level < 4:
                row.production_confirmed_at = None
            row.level, row.draft_destination, row.write_kind = level, draft_destination, write_kind
            row.full_text_only, row.daily_cap, row.license = full_text_only, daily_cap, license
            if metadata is not None:
                row.creators = [creator.model_dump(mode="json") for creator in metadata.creators]
                row.keywords = list(metadata.keywords)
            row.updated_at = now
            session.flush()
            return _preference_view(row)

    # The ledger ----------------------------------------------------------------

    def items(self, owner_hash: str, actor_id: str) -> list[dict]:
        with Session(self.engine) as session, session.begin():
            self._absorb(session, owner_hash, actor_id)
            rows = session.scalars(
                select(PublishingItem)
                .where(
                    PublishingItem.owner_session_hash == owner_hash,
                    PublishingItem.actor_id == actor_id,
                )
                .order_by(PublishingItem.created_at.desc(), PublishingItem.id)
                .limit(100)
            ).all()
            return [self._item_view(session, row) for row in rows]

    def next_step(self, owner_hash: str, actor_id: str) -> dict:
        """The next step for the publisher's browser, or why nothing is due."""
        with Session(self.engine) as session, session.begin():
            preference = _preference(session, actor_id, lock=True)
            if preference.level < 2:
                return {"action": "none", "reason": "manual"}
            self._absorb(session, owner_hash, actor_id)
            for item in self._open(session, owner_hash, actor_id):
                step = self._step(session, preference, item)
                if step is not None:
                    return step
            return self._candidate(session, owner_hash, preference)

    def attach(
        self, owner_hash: str, actor_id: str, item_id: uuid.UUID, stage: str, ref: uuid.UUID
    ) -> dict:
        """Record the exact object the browser just created for an item's due stage."""
        with Session(self.engine) as session, session.begin():
            item = session.scalar(
                select(PublishingItem)
                .where(
                    PublishingItem.id == item_id,
                    PublishingItem.owner_session_hash == owner_hash,
                    PublishingItem.actor_id == actor_id,
                )
                .with_for_update()
            )
            if item is None:
                raise PublishingError("publishing_item_not_found")
            if stage == "write" and item.status == "pending_write":
                request = session.scalar(
                    select(ArtifactRequest)
                    .join(ResearchRunAccess, ResearchRunAccess.run_id == ArtifactRequest.run_id)
                    .where(
                        ArtifactRequest.run_id == ref,
                        ResearchRunAccess.session_hash == owner_hash,
                    )
                )
                if (
                    request is None
                    or request.source_result_id != item.result_id
                    or request.kind != item.write_kind
                ):
                    raise PublishingError("publishing_link_invalid")
                item.artifact_run_id, item.status = ref, "writing"
            elif stage == "prepare" and item.status == "drafted":
                manifest = session.get(PublicationManifestRecord, ref)
                if (
                    manifest is None
                    or manifest.owner_session_hash != owner_hash
                    or manifest.artifact_id != item.artifact_id
                    or manifest.manifest.get("destination") != item.destination
                ):
                    raise PublishingError("publishing_link_invalid")
                item.manifest_id, item.status = ref, "ready"
            elif stage == "approve" and item.status == "ready":
                approval = session.get(PublicationApproval, ref)
                if approval is None or approval.manifest_id != item.manifest_id:
                    raise PublishingError("publishing_link_invalid")
                item.approval_id, item.status = ref, "approved"
            elif stage == "queue" and item.status == "approved":
                job = session.get(PublicationJob, ref)
                if (
                    job is None
                    or job.owner_session_hash != owner_hash
                    or job.approval_id != item.approval_id
                    or job.manifest_id != item.manifest_id
                ):
                    raise PublishingError("publishing_link_invalid")
                item.job_id, item.status = ref, "queued"
            else:
                raise PublishingError("publishing_stage_not_due")
            item.reason = None
            item.updated_at = func.clock_timestamp()
            session.flush()
            return self._item_view(session, item)

    def skip(self, owner_hash: str, actor_id: str, item_id: uuid.UUID) -> dict:
        """Stop an item before it is queued; anything already queued keeps its own controls."""
        with Session(self.engine) as session, session.begin():
            item = session.scalar(
                select(PublishingItem)
                .where(
                    PublishingItem.id == item_id,
                    PublishingItem.owner_session_hash == owner_hash,
                    PublishingItem.actor_id == actor_id,
                )
                .with_for_update()
            )
            if item is None:
                raise PublishingError("publishing_item_not_found")
            if item.status not in ("pending_write", "writing", "drafted", "ready", "approved"):
                raise PublishingError("publishing_stage_not_due")
            item.status, item.reason = "skipped", "skipped_by_publisher"
            item.updated_at = func.clock_timestamp()
            session.flush()
            return self._item_view(session, item)

    # Internal steps -----------------------------------------------------------

    def _open(self, session: Session, owner_hash: str, actor_id: str) -> list[PublishingItem]:
        return list(
            session.scalars(
                select(PublishingItem)
                .where(
                    PublishingItem.owner_session_hash == owner_hash,
                    PublishingItem.actor_id == actor_id,
                    PublishingItem.status.in_(ACTIVE),
                )
                .order_by(PublishingItem.created_at, PublishingItem.id)
            )
        )

    def _absorb(self, session: Session, owner_hash: str, actor_id: str) -> None:
        for item in self._open(session, owner_hash, actor_id):
            if item.status == "writing" and item.artifact_run_id is not None:
                run = session.get(ResearchRun, item.artifact_run_id)
                if run is None or run.status not in TERMINAL:
                    continue
                artifact = session.scalar(
                    select(GeneratedArtifact).where(GeneratedArtifact.run_id == run.id)
                )
                if (
                    run.status == "succeeded"
                    and artifact is not None
                    and artifact.status == "ready"
                ):
                    item.artifact_id, item.status = artifact.id, "drafted"
                else:
                    item.status, item.reason = "failed", "write_up_not_ready"
                item.updated_at = func.clock_timestamp()
            elif item.status == "queued" and item.job_id is not None:
                job = session.get(PublicationJob, item.job_id)
                if job is not None and job.status == "published":
                    item.status = "published"
                elif job is not None and job.status in ("failed", "cancelled"):
                    item.status, item.reason = "failed", f"publication_{job.status}"
                else:
                    continue
                item.updated_at = func.clock_timestamp()
        session.flush()

    def _step(
        self, session: Session, preference: PublishingPreference, item: PublishingItem
    ) -> dict | None:
        effective = min(item.level, preference.level)
        if item.status == "pending_write":
            return {
                "action": "write",
                "item_id": str(item.id),
                "result_id": str(item.result_id),
                "kind": item.write_kind,
            }
        if item.status == "writing":
            return {"action": "wait", "item_id": str(item.id), "run_id": str(item.artifact_run_id)}
        if item.status == "drafted":
            artifact = session.get(GeneratedArtifact, item.artifact_id)
            try:
                metadata = _metadata(preference, artifact)
            except PublishingError:
                item.status, item.reason = "failed", "publishing_metadata_invalid"
                return None
            return {
                "action": "prepare",
                "item_id": str(item.id),
                "artifact_id": str(item.artifact_id),
                "destination": item.destination,
                "metadata": metadata,
            }
        # Only the destination the current level allows: lowering the level
        # from 4 to 3 never lets an item prepared for production through.
        allowed = LEVEL_DESTINATION.get(effective) == item.destination and (
            item.destination != "production" or preference.production_confirmed_at is not None
        )
        if item.status == "ready" and allowed:
            manifest = session.get(PublicationManifestRecord, item.manifest_id)
            return {
                "action": "approve",
                "item_id": str(item.id),
                "manifest_id": str(item.manifest_id),
                "manifest_sha256": manifest.manifest_sha256,
            }
        if item.status == "approved" and allowed:
            manifest = session.get(PublicationManifestRecord, item.manifest_id)
            return {
                "action": "queue",
                "item_id": str(item.id),
                "manifest_id": str(item.manifest_id),
                "approval_id": str(item.approval_id),
                "manifest_sha256": manifest.manifest_sha256,
            }
        # Ready items at level 2 wait for the publisher's tap; queued items wait
        # for the worker. Neither blocks the next finding.
        return None

    def _candidate(
        self, session: Session, owner_hash: str, preference: PublishingPreference
    ) -> dict:
        now = _now(session)
        today = session.scalar(
            select(func.count())
            .select_from(PublishingItem)
            .where(
                PublishingItem.actor_id == preference.actor_id,
                PublishingItem.created_at >= func.date_trunc("day", now),
            )
        )
        if today >= preference.daily_cap:
            return {"action": "none", "reason": "daily_cap"}
        handled = select(PublishingItem.result_id).where(
            PublishingItem.owner_session_hash == owner_hash
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
        destination = LEVEL_DESTINATION.get(preference.level, preference.draft_destination)
        item = PublishingItem(
            id=uuid.uuid4(),
            actor_id=preference.actor_id,
            owner_session_hash=owner_hash,
            result_id=chosen.id,
            level=preference.level,
            destination=destination,
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

    def _item_view(self, session: Session, item: PublishingItem) -> dict:
        result = session.get(EvidenceResult, item.result_id)
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
            "manifest_id": str(item.manifest_id) if item.manifest_id else None,
            "approval_id": str(item.approval_id) if item.approval_id else None,
            "job_id": str(item.job_id) if item.job_id else None,
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        }
