"""Explicit durable public sponsorship, without model or provider construction.

Issuance needs a live operator. The reviewed delegation deadline is independent
of that login's lifetime; historical validation never consults current settings.
"""

from __future__ import annotations

import re
from datetime import UTC

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from polymath.budget_models import BudgetAccount, BudgetReservation
from polymath.meta_readiness import _account_lock, _clock, _stored
from polymath.meta_research_errors import MetaResearchError
from polymath.meta_research_models import (
    MetaResearchApproval,
    MetaResearchManifestRecord,
    MetaResearchRevocation,
)
from polymath.operator_models import OperatorSession
from polymath.public_research_contracts import (
    PUBLIC_RESEARCH_PROTOCOL,
    PublicLimits,
    PublicResearchManifest,
    build_public_research_manifest,
    public_research_manifest_sha256,
    public_sponsor_policy_sha256,
    validate_current_public_research_manifest,
)
from polymath.public_research_models import PublicResearchDelegation
from polymath.readiness_models import MetaReadinessProposalRecord


def validate_public_delegation(session, approval, row, manifest):
    """Validate immutable protocol/sidecar coherence, including after logout.

    Private historical approvals must have no public sidecar. A public protocol
    always requires the exact immutable sidecar; row presence is not authority.
    """
    sidecar = session.get(PublicResearchDelegation, approval.id)
    if not isinstance(manifest, PublicResearchManifest):
        if sidecar is not None:
            raise MetaResearchError("meta_research_storage_unavailable")
        return None
    operator = session.get(OperatorSession, approval.operator_session_hash)
    if (
        sidecar is None
        or operator is None
        or manifest.protocol != PUBLIC_RESEARCH_PROTOCOL
        or manifest.authorization_mode != "durable_owner_delegation"
        or manifest.continues_after_operator_logout is not True
        or approval.manifest_id != row.id
        or row.manifest_sha256 != public_research_manifest_sha256(manifest)
        or approval.manifest_sha256 != row.manifest_sha256
        or row.account_id != manifest.account_id
        or str(row.readiness_id) != manifest.readiness_id
        or str(row.canary_receipt_id) != manifest.canary.receipt_id
        or approval.armed_epoch != manifest.native_control_epoch + 1
        or approval.approved_at >= approval.expires_at
        or approval.expires_at != manifest.valid_until
        or re.fullmatch(r"[a-f0-9]{64}", approval.research_policy_sha256) is None
        or (
            sidecar.approval_id,
            sidecar.account_id,
            sidecar.manifest_sha256,
            sidecar.authorization_mode,
            sidecar.sponsor_operator_session_hash,
            sidecar.sponsor_browser_session_hash,
            sidecar.sponsor_actor_id,
            sidecar.sponsor_policy_sha256,
            sidecar.public_policy_sha256,
            sidecar.valid_until,
        )
        != (
            approval.id,
            manifest.account_id,
            row.manifest_sha256,
            manifest.authorization_mode,
            approval.operator_session_hash,
            approval.browser_session_hash,
            approval.actor_id,
            manifest.sponsor_policy_sha256,
            manifest.public_policy_sha256,
            manifest.valid_until,
        )
        or operator.browser_session_hash != approval.browser_session_hash
        or operator.actor_id != approval.actor_id
    ):
        raise MetaResearchError("meta_research_storage_unavailable")
    return sidecar


class PublicDelegationStore:
    def __init__(self, parent):
        self.parent, self.engine = parent, parent.engine

    def _sponsor_hash(self, operator):
        try:
            return public_sponsor_policy_sha256(
                operator_fingerprint=self.parent.operator_policy.credential_fingerprint,
                actor_id=operator.actor_id,
                readiness_policy=self.parent.readiness_policy,
            )
        except (ValueError, AttributeError):
            raise MetaResearchError("meta_research_authority_expired") from None

    def _fresh(self, session, manifest, account, operator):
        try:
            validate_current_public_research_manifest(
                manifest,
                research_policy=self.parent.research_policy,
                public_policy=self.parent.public_policy,
                sponsor_policy_sha256=self._sponsor_hash(operator),
                now=_clock(session),
            )
        except ValueError:
            raise MetaResearchError("meta_research_authority_expired") from None
        self.parent._current_facts(session, account, manifest, manifest.proposal)

    def preview(
        self,
        request,
        readiness_id,
        canary_receipt_id,
        review,
        provider_account_attestation,
        public_limits,
        valid_until,
        *,
        discovery=None,
        scientific_context=None,
    ):
        from polymath.meta_probe_contracts import ProviderAccountAttestation
        from polymath.meta_readiness_contracts import MetaReadinessProposal, _utc
        from polymath.meta_research import MetaResearchPreview, _canary, _dto, _id
        from polymath.meta_research_contracts import NativeResearchReview

        with Session(self.engine) as session, session.begin():
            operator = self.parent._authorize(session, request, write=True)
            self.parent._enabled()
            if not self.parent.public_policy.enabled:
                raise MetaResearchError("meta_research_unavailable")
            row = session.get(MetaReadinessProposalRecord, _id(readiness_id))
            if row is None:
                raise MetaResearchError("meta_research_not_found")
            account = _account_lock(session, row.account_id)
            operator = self.parent._authorize(session, request, write=True)
            proposal = _dto(MetaReadinessProposal, _stored(row, _clock(session)).proposal)
            try:
                manifest = build_public_research_manifest(
                    str(row.id),
                    proposal,
                    research_policy=self.parent.research_policy,
                    public_policy=self.parent.public_policy,
                    sponsor_policy_sha256=self._sponsor_hash(operator),
                    native_control_epoch=account.native_control_epoch,
                    canary=_canary(session, canary_receipt_id),
                    review=_dto(NativeResearchReview, review),
                    provider_account_attestation=_dto(
                        ProviderAccountAttestation, provider_account_attestation
                    ),
                    public_limits=_dto(PublicLimits, public_limits),
                    valid_until=_utc(valid_until),
                    now=_clock(session),
                    discovery=discovery,
                    scientific_context=scientific_context,
                )
            except ValueError:
                raise MetaResearchError("meta_research_invalid_request") from None
            self._fresh(session, manifest, account, operator)
            self.parent._authorize(session, request, write=True)
            return MetaResearchPreview(manifest, public_research_manifest_sha256(manifest))

    def activate(self, request, manifest, expected_sha):
        from polymath.meta_research import _canary, _dto, _id
        from polymath.meta_research_contracts import research_policy_sha256

        manifest = _dto(PublicResearchManifest, manifest)
        digest = public_research_manifest_sha256(manifest)
        if digest != expected_sha:
            raise MetaResearchError("meta_research_manifest_changed")
        with Session(self.engine) as session, session.begin():
            operator = self.parent._authorize(session, request, write=True)
            account = _account_lock(session, manifest.account_id)
            operator = self.parent._authorize(session, request, write=True)
            existing = session.scalar(
                select(MetaResearchManifestRecord).where(
                    MetaResearchManifestRecord.manifest_sha256 == digest
                )
            )
            if existing is not None:
                result = self.record(session, existing, operator)
                self.parent._authorize(session, request, write=True)
                return result
            self._fresh(session, manifest, account, operator)
            ready = session.get(MetaReadinessProposalRecord, _id(manifest.readiness_id))
            if ready is None or manifest.canary != _canary(session, manifest.canary.receipt_id):
                raise MetaResearchError("meta_research_account_conflict")
            if ready.proposal_sha256 != manifest.proposal_sha256 or _stored(
                ready, _clock(session)
            ).proposal != manifest.proposal.model_dump(mode="json"):
                raise MetaResearchError("meta_research_manifest_changed")
            if account.native_control_epoch != manifest.native_control_epoch:
                raise MetaResearchError("meta_research_manifest_changed")
            if (
                session.scalar(
                    select(BudgetReservation.id)
                    .where(
                        BudgetReservation.account_id == account.id,
                        BudgetReservation.status.in_(("reserved", "in_flight", "unknown")),
                    )
                    .limit(1)
                )
                is not None
            ):
                raise MetaResearchError("meta_research_prior_unresolved")
            row = MetaResearchManifestRecord(
                readiness_id=ready.id,
                canary_receipt_id=_id(manifest.canary.receipt_id),
                account_id=account.id,
                manifest_sha256=digest,
                manifest=manifest.model_dump(mode="json"),
            )
            session.add(row)
            session.flush()
            approval = MetaResearchApproval(
                manifest_id=row.id,
                manifest_sha256=digest,
                operator_session_hash=operator.token_hash,
                browser_session_hash=operator.browser_session_hash,
                actor_id=operator.actor_id,
                research_policy_sha256=research_policy_sha256(self.parent.research_policy),
                armed_epoch=account.native_control_epoch + 1,
                approved_at=_clock(session),
                expires_at=manifest.valid_until,
            )
            session.add(approval)
            session.flush()
            session.add(
                PublicResearchDelegation(
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
            )
            operator = self.parent._authorize(session, request, write=True)
            self._fresh(session, manifest, account, operator)
            account.native_scope = "research"
            account.native_manifest_sha256 = digest
            account.native_control_epoch = approval.armed_epoch
            account.kill_switch = False
            session.flush()
            operator = self.parent._authorize(session, request, write=True)
            self.parent._approval_current(
                session,
                approval,
                manifest,
                manifest.proposal,
                account,
                operator=operator,
                requester_hash=None,
            )
            result = self.record(session, row, operator)
            self.parent._authorize(session, request, write=True)
            return result

    def record(self, session, row, viewer):
        from polymath.meta_research import MetaResearchRecord, _activation

        approval = session.scalar(
            select(MetaResearchApproval).where(MetaResearchApproval.manifest_id == row.id)
        )
        if approval is None:
            raise MetaResearchError("meta_research_storage_unavailable")
        approval, row, manifest, proposal = _activation(session, approval.id)
        if not isinstance(manifest, PublicResearchManifest):
            raise MetaResearchError("meta_research_not_found")
        validate_public_delegation(session, approval, row, manifest)
        operator = session.get(
            OperatorSession, approval.operator_session_hash, populate_existing=True
        )
        account = session.get(BudgetAccount, row.account_id, populate_existing=True)
        revoked = session.get(MetaResearchRevocation, approval.id)
        try:
            self.parent._approval_current(
                session,
                approval,
                manifest,
                proposal,
                account,
                operator=operator,
                requester_hash=None,
            )
            valid = True
        except MetaResearchError:
            valid = False
        return MetaResearchRecord(
            row.id,
            {
                "manifest_sha256": row.manifest_sha256,
                "manifest": row.manifest,
                "audience": "public_sessions",
                "authorization_mode": manifest.authorization_mode,
                "continues_after_operator_logout": True,
                "approval": {
                    "id": str(approval.id),
                    "actor_id": approval.actor_id,
                    "approved_at": approval.approved_at.astimezone(UTC).isoformat(),
                    "expires_at": approval.expires_at.astimezone(UTC).isoformat(),
                    "revoked_at": revoked.revoked_at.astimezone(UTC).isoformat()
                    if revoked
                    else None,
                    "valid": valid,
                },
                "delegation_current": valid,
                "created_at": row.created_at.astimezone(UTC).isoformat(),
            },
        )

    def list_activations(self, request, *, limit=20, before=None):
        from polymath.meta_research import _id

        if type(limit) is not int or not 1 <= limit <= 21:
            raise MetaResearchError("meta_research_invalid_request")
        with Session(self.engine) as session, session.begin():
            operator = self.parent._authorize(session, request, write=False)
            query = (
                select(MetaResearchManifestRecord)
                .join(MetaResearchApproval)
                .join(PublicResearchDelegation)
            )
            if before is not None:
                anchor = session.get(MetaResearchManifestRecord, _id(before))
                if anchor is None or anchor.manifest.get("protocol") != PUBLIC_RESEARCH_PROTOCOL:
                    raise MetaResearchError("meta_research_not_found")
                query = query.where(
                    tuple_(MetaResearchManifestRecord.created_at, MetaResearchManifestRecord.id)
                    < tuple_(anchor.created_at, anchor.id)
                )
            rows = session.scalars(
                query.order_by(
                    MetaResearchManifestRecord.created_at.desc(),
                    MetaResearchManifestRecord.id.desc(),
                ).limit(limit)
            ).all()
            result = tuple(self.record(session, row, operator) for row in rows)
            self.parent._authorize(session, request, write=False)
            return result

    def get_activation(self, request, activation_id):
        from polymath.meta_research import _id

        with Session(self.engine) as session, session.begin():
            operator = self.parent._authorize(session, request, write=False)
            row = session.get(MetaResearchManifestRecord, _id(activation_id))
            if row is None or row.manifest.get("protocol") != PUBLIC_RESEARCH_PROTOCOL:
                raise MetaResearchError("meta_research_not_found")
            result = self.record(session, row, operator)
            self.parent._authorize(session, request, write=False)
            return result

    def current(self, request):
        with Session(self.engine) as session, session.begin():
            operator = self.parent._authorize(session, request, write=False)
            account_id = self.parent.research_policy.native_account_id
            account = session.get(BudgetAccount, account_id) if account_id else None
            row = (
                session.scalar(
                    select(MetaResearchManifestRecord).where(
                        MetaResearchManifestRecord.manifest_sha256 == account.native_manifest_sha256
                    )
                )
                if account is not None and account.native_scope == "research"
                else None
            )
            if row is None or row.manifest.get("protocol") != PUBLIC_RESEARCH_PROTOCOL:
                return {"activation": None, "reason": "meta_research_activation_required"}
            record = self.record(session, row, operator).to_response()
            self.parent._authorize(session, request, write=False)
            return {
                "activation": record,
                "reason": None
                if record["delegation_current"]
                else "meta_research_authority_expired",
            }

    def revoke(self, request, activation_id):
        from polymath.meta_research import _activation, _id

        with Session(self.engine) as session, session.begin():
            operator = self.parent._authorize(session, request, write=True)
            row = session.get(MetaResearchManifestRecord, _id(activation_id))
            if row is None or row.manifest.get("protocol") != PUBLIC_RESEARCH_PROTOCOL:
                raise MetaResearchError("meta_research_not_found")
            account = _account_lock(session, row.account_id)
            operator = self.parent._authorize(session, request, write=True)
            approval = session.scalar(
                select(MetaResearchApproval).where(MetaResearchApproval.manifest_id == row.id)
            )
            if approval is None:
                raise MetaResearchError("meta_research_storage_unavailable")
            _activation(session, approval.id)
            if session.get(MetaResearchRevocation, approval.id) is None:
                session.add(
                    MetaResearchRevocation(approval_id=approval.id, actor_id=operator.actor_id)
                )
            account.native_control_epoch += 1
            account.kill_switch = True
            session.flush()
            result = self.record(session, row, operator)
            self.parent._authorize(session, request, write=True)
            return result
