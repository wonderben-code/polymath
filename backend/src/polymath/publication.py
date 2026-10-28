"""Exact, local publication authorization records. Nothing here contacts a publisher.

An approval binds immutable exported bytes, reviewed metadata, publisher, and
destination together. A receipt never constitutes publication or a remote grant.
Models cannot create operator sessions or bypass these human authorization checks.
"""

from __future__ import annotations

import hmac
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError
from sqlalchemy import Engine, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.artifact_models import GeneratedArtifact
from polymath.artifacts import ArtifactError, StoredArtifact
from polymath.artifacts import _stored as _stored_artifact
from polymath.assessment_eligibility import (
    EligibilityError,
    lock_current_source,
    require_current_supported,
)
from polymath.evidence import EvidenceError, _canonical, _hash
from polymath.models import ResearchRun
from polymath.operator_auth import OperatorPolicy
from polymath.operator_models import OperatorSession
from polymath.publication_contracts import (
    PublicationManifest,
    PublicationMetadata,
    PublicationPolicy,
    publication_marker,
)
from polymath.publication_models import (
    PublicationApproval,
    PublicationManifestRecord,
    PublicationRevocation,
)

MAX_APPROVALS_PER_MANIFEST_DAY = 10


class PublicationError(Exception):
    """Static private error code, without source, metadata, or credential values."""


@dataclass(frozen=True)
class ApprovalRecord:
    id: uuid.UUID
    manifest_id: uuid.UUID
    manifest_sha256: str
    actor_id: str
    approved_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    valid: bool

    def to_response(self) -> dict:
        return {
            "id": str(self.id),
            "manifest_id": str(self.manifest_id),
            "manifest_sha256": self.manifest_sha256,
            "actor_id": self.actor_id,
            "approved_at": self.approved_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "valid": self.valid,
        }


@dataclass(frozen=True)
class ManifestRecord:
    id: uuid.UUID
    manifest_sha256: str
    manifest: dict
    created_at: datetime
    approval: ApprovalRecord | None

    def to_response(self) -> dict:
        return {
            "id": str(self.id),
            "manifest_sha256": self.manifest_sha256,
            "manifest": self.manifest,
            "created_at": self.created_at.isoformat(),
            "approval": self.approval.to_response() if self.approval else None,
            "publication_status": "not_published",
            "artifact_export_url": f"/api/v1/artifacts/{self.manifest['artifact_id']}/export",
        }


def _now(session: Session) -> datetime:
    return session.scalar(select(func.clock_timestamp()))


def _browser(session: Session, owner_hash: str) -> BrowserSession:
    row = session.get(BrowserSession, owner_hash, populate_existing=True)
    if row is None or row.expires_at <= _now(session):
        raise PublicationError("publication_not_found")
    return row


def hash_manifest(manifest: PublicationManifest) -> str:
    try:
        verified = PublicationManifest.model_validate_json(manifest.model_dump_json())
        return _hash(_canonical(verified.model_dump(mode="json")))
    except (ValueError, TypeError, AttributeError, EvidenceError):
        raise PublicationError("invalid_publication_manifest") from None


def _manifest(row: PublicationManifestRecord) -> PublicationManifest:
    try:
        manifest = PublicationManifest.model_validate_json(_canonical(row.manifest))
        if hash_manifest(manifest) != row.manifest_sha256 or manifest.artifact_id != str(
            row.artifact_id
        ):
            raise ValueError
        return manifest
    except (ValueError, TypeError, AttributeError, EvidenceError):
        raise PublicationError("invalid_publication_manifest") from None


def _artifact(session: Session, owner_hash: str, artifact_id: uuid.UUID) -> StoredArtifact:
    row = session.scalar(
        select(GeneratedArtifact)
        .join(ResearchRun, ResearchRun.id == GeneratedArtifact.run_id)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == GeneratedArtifact.run_id)
        .where(
            GeneratedArtifact.id == artifact_id,
            GeneratedArtifact.status == "ready",
            GeneratedArtifact.kind.in_(("write_summary", "write_paper")),
            ResearchRun.status == "succeeded",
            ResearchRunAccess.session_hash == owner_hash,
        )
    )
    if row is None:
        raise PublicationError("publication_not_found")
    try:
        return _stored_artifact(row)
    except ArtifactError:
        raise PublicationError("invalid_publication_manifest") from None


def _current_artifact_source(
    session: Session, owner_hash: str, artifact: StoredArtifact, *, lock: bool = False
) -> None:
    """Check new publication authority without changing historical export/readback."""
    try:
        source_id = uuid.UUID(artifact.basis["reference"]["result_id"])
        check = lock_current_source if lock else require_current_supported
        check(session, owner_hash, source_id)
    except (EligibilityError, ValueError, KeyError, TypeError):
        raise PublicationError("publication_source_not_current") from None


def _build_manifest(
    artifact: StoredArtifact,
    metadata: PublicationMetadata,
    policy: PublicationPolicy,
    destination: str,
) -> PublicationManifest:
    if not policy.configured or destination not in policy.allowed_destinations:
        raise PublicationError("publication_unavailable")
    try:
        fields = {
            "protocol": "polymath-publication-v1",
            "artifact_id": str(artifact.id),
            "artifact_run_id": str(artifact.run_id),
            "markdown_sha256": artifact.markdown_sha256,
            "markdown_bytes": len(artifact.markdown.encode("utf-8")),
            "payload_sha256": artifact.payload_sha256,
            "basis_sha256": artifact.basis_sha256,
            "file_name": f"polymath-{artifact.id}.md",
            "media_type": "text/markdown; charset=utf-8",
            "metadata": metadata.model_dump(mode="json"),
            "publisher": policy.publisher.model_dump(mode="json"),
            "destination": destination,
        }
        return PublicationManifest.model_validate_json(
            _canonical({**fields, "remote_marker": publication_marker(fields)})
        )
    except (ValueError, TypeError, ValidationError):
        raise PublicationError("invalid_publication_manifest") from None


def _owned_manifest(
    session: Session, owner_hash: str, manifest_id: uuid.UUID, *, lock: bool = False
) -> PublicationManifestRecord:
    statement = select(PublicationManifestRecord).where(
        PublicationManifestRecord.id == manifest_id,
        PublicationManifestRecord.owner_session_hash == owner_hash,
    )
    if lock:
        statement = statement.with_for_update()
    row = session.scalar(statement)
    if row is None:
        raise PublicationError("publication_not_found")
    return row


class PublicationStore:
    def __init__(
        self,
        engine: Engine,
        policy: PublicationPolicy | None = None,
        *,
        operator_policy: OperatorPolicy | None = None,
    ):
        if engine.dialect.name != "postgresql":
            raise ValueError("PublicationStore requires PostgreSQL transaction semantics")
        self.engine = engine
        self.policy = policy or PublicationPolicy()
        self.operator_policy = operator_policy or OperatorPolicy()

    def capabilities(self) -> dict:
        return {
            "configured": self.policy.configured,
            "publisher": self.policy.publisher.model_dump(mode="json")
            if self.policy.publisher
            else None,
            "destinations": list(self.policy.allowed_destinations),
            "can_prepare": self.policy.configured,
        }

    def _operator_valid(
        self, operator: OperatorSession | None, owner_hash: str, now: datetime
    ) -> bool:
        return bool(
            self.operator_policy.configured
            and operator is not None
            and operator.browser_session_hash == owner_hash
            and operator.revoked_at is None
            and operator.expires_at > now
            and operator.actor_id == self.operator_policy.actor_id
            and operator.display_name == self.operator_policy.display_name
            and hmac.compare_digest(
                operator.credential_fingerprint, self.operator_policy.credential_fingerprint
            )
        )

    def _operator(
        self, session: Session, owner_hash: str, supplied: OperatorSession
    ) -> OperatorSession:
        token_hash = getattr(supplied, "token_hash", None)
        if not isinstance(token_hash, str) or re.fullmatch(r"[a-f0-9]{64}", token_hash) is None:
            raise PublicationError("operator_session_required")
        row = session.scalar(
            select(OperatorSession)
            .where(OperatorSession.token_hash == token_hash)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if not self._operator_valid(row, owner_hash, _now(session)):
            raise PublicationError("operator_session_required")
        return row

    def _revalidate(
        self, session: Session, owner_hash: str, row: PublicationManifestRecord
    ) -> PublicationManifest:
        manifest = _manifest(row)
        artifact = _artifact(session, owner_hash, row.artifact_id)
        _current_artifact_source(session, owner_hash, artifact)
        expected = _build_manifest(artifact, manifest.metadata, self.policy, manifest.destination)
        if expected != manifest or hash_manifest(expected) != row.manifest_sha256:
            raise PublicationError("publication_manifest_changed")
        return manifest

    def _approval_record(
        self,
        session: Session,
        owner_hash: str,
        manifest_row: PublicationManifestRecord,
        approval: PublicationApproval,
    ) -> ApprovalRecord:
        _browser(session, owner_hash)
        revoked = session.get(PublicationRevocation, approval.id)
        operator = session.get(OperatorSession, approval.operator_session_hash)
        now = _now(session)
        valid = (
            approval.manifest_id == manifest_row.id
            and approval.manifest_sha256 == manifest_row.manifest_sha256
            and approval.approved_at <= now < approval.expires_at
            and revoked is None
            and self._operator_valid(operator, owner_hash, now)
            and operator.actor_id == approval.actor_id
        )
        if valid:
            try:
                self._revalidate(session, owner_hash, manifest_row)
            except PublicationError:
                valid = False
        return ApprovalRecord(
            id=approval.id,
            manifest_id=approval.manifest_id,
            manifest_sha256=approval.manifest_sha256,
            actor_id=approval.actor_id,
            approved_at=approval.approved_at,
            expires_at=approval.expires_at,
            revoked_at=revoked.revoked_at if revoked else None,
            valid=valid,
        )

    def _record(
        self, session: Session, owner_hash: str, row: PublicationManifestRecord
    ) -> ManifestRecord:
        _browser(session, owner_hash)
        manifest = _manifest(row)
        approval = session.scalar(
            select(PublicationApproval)
            .where(PublicationApproval.manifest_id == row.id)
            .order_by(PublicationApproval.approved_at.desc(), PublicationApproval.id.desc())
            .limit(1)
        )
        return ManifestRecord(
            id=row.id,
            manifest_sha256=row.manifest_sha256,
            manifest=manifest.model_dump(mode="json"),
            created_at=row.created_at,
            approval=self._approval_record(session, owner_hash, row, approval)
            if approval
            else None,
        )

    def prepare(
        self,
        owner_hash: str,
        artifact_id: uuid.UUID,
        metadata: PublicationMetadata,
        destination: str,
    ) -> ManifestRecord:
        try:
            metadata = PublicationMetadata.model_validate_json(metadata.model_dump_json())
        except (ValueError, TypeError, AttributeError):
            raise PublicationError("invalid_publication_manifest") from None
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            artifact = _artifact(session, owner_hash, artifact_id)
            _current_artifact_source(session, owner_hash, artifact)
            manifest = _build_manifest(artifact, metadata, self.policy, destination)
            digest = hash_manifest(manifest)
            session.execute(
                insert(PublicationManifestRecord)
                .values(
                    id=uuid.uuid4(),
                    owner_session_hash=owner_hash,
                    artifact_id=artifact_id,
                    manifest=manifest.model_dump(mode="json"),
                    manifest_sha256=digest,
                )
                .on_conflict_do_nothing(constraint="uq_publication_manifest")
            )
            row = session.scalar(
                select(PublicationManifestRecord).where(
                    PublicationManifestRecord.owner_session_hash == owner_hash,
                    PublicationManifestRecord.manifest_sha256 == digest,
                )
            )
            if _manifest(row) != manifest:
                raise PublicationError("invalid_publication_manifest")
            return self._record(session, owner_hash, row)

    def list(
        self,
        owner_hash: str,
        *,
        limit: int = 20,
        before: uuid.UUID | None = None,
        artifact_id: uuid.UUID | None = None,
    ) -> tuple[ManifestRecord, ...]:
        # The public API caps20 and asks for one extra row to report a real cursor.
        if type(limit) is not int or not 1 <= limit <= 21:
            raise ValueError("Publication history limit must be between1 and21")
        with Session(self.engine) as session:
            _browser(session, owner_hash)
            statement = select(PublicationManifestRecord).where(
                PublicationManifestRecord.owner_session_hash == owner_hash
            )
            if artifact_id is not None:
                statement = statement.where(PublicationManifestRecord.artifact_id == artifact_id)
            if before is not None:
                anchor = _owned_manifest(session, owner_hash, before)
                statement = statement.where(
                    tuple_(PublicationManifestRecord.created_at, PublicationManifestRecord.id)
                    < tuple_(anchor.created_at, anchor.id)
                )
            rows = session.scalars(
                statement.order_by(
                    PublicationManifestRecord.created_at.desc(), PublicationManifestRecord.id.desc()
                ).limit(limit)
            ).all()
            return tuple(self._record(session, owner_hash, row) for row in rows)

    def get(self, owner_hash: str, manifest_id: uuid.UUID) -> ManifestRecord:
        with Session(self.engine) as session:
            _browser(session, owner_hash)
            return self._record(
                session, owner_hash, _owned_manifest(session, owner_hash, manifest_id)
            )

    def approve(
        self,
        owner_hash: str,
        manifest_id: uuid.UUID,
        expected_manifest_sha256: str,
        operator_session: OperatorSession,
    ) -> ApprovalRecord:
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            operator = self._operator(session, owner_hash, operator_session)
            manifest_row = _owned_manifest(session, owner_hash, manifest_id, lock=True)
            _browser(session, owner_hash)
            if expected_manifest_sha256 != manifest_row.manifest_sha256:
                raise PublicationError("publication_manifest_changed")
            self._revalidate(session, owner_hash, manifest_row)
            now = _now(session)
            day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            approvals = session.scalars(
                select(PublicationApproval)
                .where(
                    PublicationApproval.manifest_id == manifest_id,
                    or_(
                        PublicationApproval.approved_at >= day,
                        PublicationApproval.expires_at > now,
                    ),
                )
                .order_by(PublicationApproval.approved_at.desc(), PublicationApproval.id.desc())
                .limit(MAX_APPROVALS_PER_MANIFEST_DAY * 2 + 1)
            ).all()
            if len(approvals) > MAX_APPROVALS_PER_MANIFEST_DAY * 2:
                raise PublicationError("publication_approval_limit")
            for prior in approvals:
                record = self._approval_record(session, owner_hash, manifest_row, prior)
                if record.valid and record.actor_id == operator.actor_id:
                    return record
            now = _now(session)
            browser = _browser(session, owner_hash)
            day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            if sum(approval.approved_at >= day for approval in approvals) >= (
                MAX_APPROVALS_PER_MANIFEST_DAY
            ):
                raise PublicationError("publication_approval_limit")
            expiry = min(
                now + timedelta(seconds=self.policy.approval_seconds),
                operator.expires_at,
                browser.expires_at,
            )
            if expiry <= now:
                raise PublicationError("operator_session_required")
            approval = PublicationApproval(
                manifest_id=manifest_id,
                manifest_sha256=manifest_row.manifest_sha256,
                operator_session_hash=operator.token_hash,
                actor_id=operator.actor_id,
                approved_at=now,
                expires_at=expiry,
            )
            session.add(approval)
            session.flush()
            return self._approval_record(session, owner_hash, manifest_row, approval)

    def revoke(
        self,
        owner_hash: str,
        approval_id: uuid.UUID,
        operator_session: OperatorSession,
    ) -> ApprovalRecord:
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            operator = self._operator(session, owner_hash, operator_session)
            approval = session.get(PublicationApproval, approval_id)
            if approval is None:
                raise PublicationError("publication_approval_not_found")
            manifest_row = _owned_manifest(session, owner_hash, approval.manifest_id, lock=True)
            _browser(session, owner_hash)
            if not self._operator_valid(operator, owner_hash, _now(session)):
                raise PublicationError("operator_session_required")
            if session.get(PublicationRevocation, approval_id) is None:
                session.add(
                    PublicationRevocation(approval_id=approval_id, actor_id=operator.actor_id)
                )
                session.flush()
            return self._approval_record(session, owner_hash, manifest_row, approval)

    def validate_approval(
        self,
        owner_hash: str,
        approval_id: uuid.UUID,
        *,
        manifest_id: uuid.UUID | None = None,
        expected_manifest_sha256: str | None = None,
        session: Session | None = None,
    ) -> ApprovalRecord:
        """Revalidate an exact receipt. This never grants or sends a remote request.

        A future dispatcher must pass its transaction's session and persist its
        grant before sending; a separate read is not external-send permission.
        """
        if session is None:
            with Session(self.engine) as owned_session, owned_session.begin():
                return self.validate_approval(
                    owner_hash,
                    approval_id,
                    manifest_id=manifest_id,
                    expected_manifest_sha256=expected_manifest_sha256,
                    session=owned_session,
                )
        _browser(session, owner_hash)
        approval = session.get(PublicationApproval, approval_id)
        if approval is None:
            raise PublicationError("publication_approval_not_found")
        # Check the immutable ownership pointer before inspecting the receipt's
        # operator. Foreign and missing receipts must remain indistinguishable.
        # This read takes no row lock; authority locks still use operator first.
        _owned_manifest(session, owner_hash, approval.manifest_id)
        # Use the same lock order as approval/revocation and operator logout:
        # operator first, exact manifest second. A caller's eventual dispatch
        # transaction can retain both locks through its durable grant boundary.
        operator = session.get(OperatorSession, approval.operator_session_hash)
        try:
            self._operator(session, owner_hash, operator)
        except PublicationError:
            raise PublicationError("publication_manifest_changed") from None
        row = _owned_manifest(session, owner_hash, approval.manifest_id, lock=True)
        # Authority order is operator -> manifest -> finding head -> publication
        # job. The head lock survives through the dispatch grant transaction.
        # Readback/late remote facts never acquire this authority lock.
        _current_artifact_source(
            session, owner_hash, _artifact(session, owner_hash, row.artifact_id), lock=True
        )
        _browser(session, owner_hash)
        record = self._approval_record(session, owner_hash, row, approval)
        if (
            not record.valid
            or (manifest_id is not None and manifest_id != record.manifest_id)
            or (
                expected_manifest_sha256 is not None
                and expected_manifest_sha256 != record.manifest_sha256
            )
        ):
            raise PublicationError("publication_manifest_changed")
        return record
