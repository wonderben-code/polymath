"""One reviewed native canary: immutable consent, scoped arming and free receipts.

There is no model factory or network operation here. Fresh authority uses
Operator -> Account -> Run; late known facts use only Account -> Run.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, timedelta

from sqlalchemy import Engine, func, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.budget import BudgetService
from polymath.budget_models import BudgetAccount, BudgetReservation, BudgetRunLimit
from polymath.dispatch import _call_key
from polymath.evidence import _canonical, _hash
from polymath.jobs import _clear_lease, _event, _locked, _owned
from polymath.meta_probe_contracts import (
    MetaObservation,
    MetaProbeManifest,
    MetaProbePolicy,
    ProviderAccountAttestation,
    assess_probe_observation,
    build_probe_manifest,
    probe_manifest_sha256,
    probe_policy_sha256,
    validate_probe_manifest,
)
from polymath.meta_probe_models import (
    MetaNativeIdentityBinding,
    MetaProbeApproval,
    MetaProbeManifestRecord,
    MetaProbeReceipt,
    MetaProbeRequest,
    MetaProbeRevocation,
)
from polymath.meta_readiness import _account_lock, _clock, _stored
from polymath.meta_readiness_contracts import ReadinessPolicy, validate_proposal_time
from polymath.models import ResearchExternalRequest, ResearchRun
from polymath.operator_auth import require_operator
from polymath.operator_models import OperatorSession
from polymath.readiness_models import MetaReadinessProposalRecord
from polymath.run_api import ACTIVE_STATES, QUEUE_LOCK_KEY, _PrivateHTTPException

REQUEST_ID = "model-0001"


class MetaProbeError(Exception):
    """A bounded public control code, never raw provider or credential data."""


def _uuid(value):
    try:
        result = uuid.UUID(str(value))
        if str(result) != str(value):
            raise ValueError
        return result
    except (ValueError, TypeError, AttributeError):
        raise MetaProbeError("meta_probe_invalid_request") from None


def _parse(cls, value):
    try:
        return cls.model_validate_json(
            value.model_dump_json() if isinstance(value, cls) else json.dumps(value)
        )
    except (ValueError, TypeError, AttributeError):
        raise MetaProbeError("meta_probe_invalid_request") from None


@dataclass(frozen=True)
class MetaProbeInput:
    run_id: uuid.UUID
    account_id: str
    manifest: MetaProbeManifest
    proposal: object
    approval_id: uuid.UUID
    armed_epoch: int
    request_hash: str


@dataclass(frozen=True)
class MetaProbePreview:
    manifest: MetaProbeManifest
    manifest_sha256: str

    def to_response(self):
        return {
            "manifest": self.manifest.model_dump(mode="json"),
            "manifest_sha256": self.manifest_sha256,
            "research_execution_available": False,
        }


@dataclass(frozen=True)
class MetaProbeReceiptRecord:
    id: uuid.UUID
    payload: dict
    created_at: object

    def to_response(self):
        return {
            "id": str(self.id),
            **self.payload,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
        }


@dataclass(frozen=True)
class MetaProbeJobRecord:
    id: uuid.UUID
    payload: dict

    def to_response(self):
        return {"id": str(self.id), **self.payload}


def _input(session, run_id):
    binding = session.get(MetaProbeRequest, run_id)
    if binding is None:
        raise MetaProbeError("meta_probe_not_found")
    row = session.get(MetaProbeManifestRecord, binding.manifest_id)
    approval = session.get(MetaProbeApproval, binding.approval_id)
    ready = session.get(MetaReadinessProposalRecord, row.readiness_id)
    run = session.get(ResearchRun, run_id)
    limit = session.get(BudgetRunLimit, (binding.account_id, run_id))
    try:
        from polymath.meta_readiness_contracts import MetaReadinessProposal

        proposal = MetaReadinessProposal.model_validate_json(
            json.dumps(_stored(ready, _clock(session)).proposal)
        )
        manifest = MetaProbeManifest.model_validate_json(json.dumps(row.manifest))
        validate_probe_manifest(manifest, proposal)
        if (
            probe_manifest_sha256(manifest) != binding.manifest_sha256
            or row.manifest_sha256 != binding.manifest_sha256
            or manifest.readiness_id != str(ready.id)
            or row.account_id != binding.account_id
            or manifest.account_id != binding.account_id
            or binding.request_sha256 != manifest.request_sha256
            or approval.manifest_id != row.id
            or approval.manifest_sha256 != row.manifest_sha256
            or approval.armed_epoch != binding.armed_epoch
            or approval.armed_epoch != manifest.native_control_epoch + 1
            or run.task_kind != "verify_meta"
            or limit is None
            or limit.max_calls != 1
            or limit.cap_microdollars != manifest.reservation_microdollars
        ):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise MetaProbeError("meta_probe_storage_unavailable") from None
    return MetaProbeInput(
        run_id,
        binding.account_id,
        manifest,
        proposal,
        approval.id,
        binding.armed_epoch,
        binding.request_sha256,
    )


def _known_receipt_payload(session, value):
    request = session.get(ResearchExternalRequest, (value.run_id, REQUEST_ID))
    if request is None or request.status != "observed" or request.observation is None:
        raise MetaProbeError("meta_probe_outcome_unknown")
    observation = _parse(MetaObservation, request.observation)
    assessment = assess_probe_observation(value.manifest, value.proposal, observation)
    reservation = session.get(BudgetReservation, request.reservation_id)
    if (
        request.request_hash != value.request_hash
        or reservation is None
        or reservation.account_id != value.account_id
        or reservation.run_id != value.run_id
        or reservation.actual_microdollars != assessment.actual_microdollars
        or reservation.estimated_microdollars != value.manifest.reservation_microdollars
        or reservation.call_key != _call_key(value.run_id, REQUEST_ID)
        or request.dispatch_lease_token is None
        or request.dispatch_lease_token < 1
        or (
            assessment.actual_microdollars is not None
            and (
                reservation.settlement_key != "native-probe:" + assessment.observation_sha256
                or reservation.prompt_tokens != assessment.input_tokens
                or reservation.completion_tokens != assessment.output_tokens
            )
        )
        or reservation.status
        != ("unknown" if assessment.actual_microdollars is None else "settled")
    ):
        raise MetaProbeError("meta_probe_storage_unavailable")
    payload = {
        "run_id": str(value.run_id),
        "manifest_sha256": probe_manifest_sha256(value.manifest),
        "observation_sha256": assessment.observation_sha256,
        "bindings": {
            "proposal_sha256": value.manifest.proposal_sha256,
            "request_sha256": value.request_hash,
            "credential_binding_sha256": value.manifest.credential_binding_sha256,
            "reservation_id": str(reservation.id),
            "approval_id": str(value.approval_id),
            "armed_epoch": value.armed_epoch,
        },
        "native_status": assessment.status,
        "observation": {
            name: getattr(observation, name)
            for name in (
                "requested_model_id",
                "observed_model_id",
                "http_status",
                "finish_reason",
                "body_complete",
            )
        }
        | {
            "started_at": observation.started_at.isoformat(),
            "finished_at": observation.finished_at.isoformat(),
        },
        "assessment": assessment.model_dump(mode="json"),
        "research_execution_available": False,
    }
    for name in ("input_tokens", "output_tokens"):
        count = payload["assessment"][name]
        payload["assessment"][name] = str(count) if count is not None else None
    return payload


def _receipt(session, row):
    if (
        _hash(_canonical(row.payload)) != row.payload_sha256
        or row.payload.get("research_execution_available") is not False
        or row.manifest_sha256 != row.payload.get("manifest_sha256")
        or row.observation_sha256 != row.payload.get("observation_sha256")
        or row.payload != _known_receipt_payload(session, _input(session, row.run_id))
    ):
        raise MetaProbeError("meta_probe_storage_unavailable")
    return MetaProbeReceiptRecord(row.id, row.payload, row.created_at)


def _job(session, run_id, store):
    value = _input(session, run_id)
    run = session.get(ResearchRun, run_id)
    approval = session.get(MetaProbeApproval, value.approval_id)
    revoked = session.get(MetaProbeRevocation, approval.id)
    receipt = session.scalar(select(MetaProbeReceipt).where(MetaProbeReceipt.run_id == run_id))
    operator = session.get(OperatorSession, approval.operator_session_hash)
    browser = session.get(BrowserSession, approval.browser_session_hash)
    account = session.get(BudgetAccount, value.account_id)
    now = _clock(session)
    valid = (
        revoked is None
        and now < approval.expires_at
        and operator is not None
        and operator.revoked_at is None
        and now < operator.expires_at
        and browser is not None
        and now < browser.expires_at
        and account.native_control_epoch == value.armed_epoch
        and not account.kill_switch
        and not account.frozen
        and store.readiness_policy.preparation_enabled
        and operator.actor_id in store.readiness_policy.allowed_actor_ids
        and store.operator_policy is not None
        and store.operator_policy.configured
        and operator.credential_fingerprint == store.operator_policy.credential_fingerprint
        and operator.actor_id == store.operator_policy.actor_id
        and operator.display_name == store.operator_policy.display_name
        and store.probe_policy.paid_probe_enabled
        and store.probe_policy.configured
        and approval.probe_policy_sha256 == probe_policy_sha256(store.probe_policy)
        and store.probe_policy.credential_binding_sha256 == value.manifest.credential_binding_sha256
        and store.probe_policy.model_id == value.proposal.model_id
        and store.probe_policy.native_account_id == value.account_id
    )
    request = session.get(ResearchExternalRequest, (run_id, REQUEST_ID))
    reservation = session.get(BudgetReservation, request.reservation_id) if request else None
    unknown = request is not None and (
        request.status == "prepared" or reservation.status == "unknown"
    )
    return MetaProbeJobRecord(
        run_id,
        {
            "task_kind": "verify_meta",
            "status": run.status,
            "manifest_sha256": probe_manifest_sha256(value.manifest),
            "manifest": value.manifest.model_dump(mode="json"),
            "approval": {
                "id": str(approval.id),
                "actor_id": approval.actor_id,
                "approved_at": approval.approved_at.astimezone(UTC).isoformat(),
                "expires_at": approval.expires_at.astimezone(UTC).isoformat(),
                "revoked_at": revoked.revoked_at.astimezone(UTC).isoformat() if revoked else None,
                "valid": bool(valid),
            },
            "receipt": _receipt(session, receipt).to_response() if receipt else None,
            "can_cancel": run.status not in {"succeeded", "failed", "cancelled"},
            "can_finalize": receipt is None
            and request is not None
            and request.status == "observed"
            and request.observation is not None,
            "external_outcome_unknown": unknown,
            "reason": run.interruption_reason,
            "created_at": run.created_at.astimezone(UTC).isoformat(),
            "research_execution_available": False,
        },
    )


class MetaProbeStore:
    def __init__(
        self, engine: Engine, probe_policy=None, readiness_policy=None, operator_policy=None
    ):
        if engine.dialect.name != "postgresql":
            raise ValueError("MetaProbeStore requires PostgreSQL")
        self.engine = engine
        self.probe_policy = probe_policy or MetaProbePolicy()
        self.readiness_policy = readiness_policy or ReadinessPolicy()
        self.operator_policy = operator_policy

    @staticmethod
    def _identity_keys(manifest):
        return (
            ("credential", manifest.credential_binding_sha256),
            (
                "provider_account",
                _hash(
                    "polymath-meta-provider-account-v1\0"
                    + manifest.provider_account_attestation.provider_billing_account_id
                ),
            ),
        )

    def _require_identity_bindings(self, session, manifest):
        for kind, identity in self._identity_keys(manifest):
            self._require_identity_bindings_partial(session, kind, identity, manifest.account_id)

    @staticmethod
    def _require_identity_bindings_partial(session, kind, identity, account_id):
        row = session.get(MetaNativeIdentityBinding, (kind, identity), populate_existing=True)
        if row is None or row.account_id != account_id:
            raise MetaProbeError("meta_probe_account_conflict")

    def _authorize(self, session, request, *, write):
        if not self.readiness_policy.allowed_actor_ids:
            raise MetaProbeError("readiness_role_unavailable")
        operator = require_operator(session, request, write=write, policy=self.operator_policy)
        if not write:
            session.refresh(operator, with_for_update=True)
            operator = require_operator(session, request, write=False, policy=self.operator_policy)
        if operator.actor_id not in self.readiness_policy.allowed_actor_ids:
            raise MetaProbeError("meta_probe_actor_forbidden")
        return operator

    def _enabled(self):
        if (
            not self.readiness_policy.preparation_enabled
            or not self.probe_policy.paid_probe_enabled
            or not self.probe_policy.configured
        ):
            raise MetaProbeError("meta_probe_unavailable")

    def _current(self, session, account, manifest, proposal, *, additional_reservation=True):
        self._enabled()
        now = _clock(session)
        try:
            validate_probe_manifest(manifest, proposal)
            validate_proposal_time(proposal, now)
        except ValueError:
            raise MetaProbeError("meta_probe_authority_expired") from None
        attestation = manifest.provider_account_attestation
        if (
            now >= manifest.valid_until
            or now < attestation.observed_at
            or now >= attestation.valid_until
            or self.probe_policy.model_id != proposal.model_id
            or self.probe_policy.native_account_id != account.id
            or manifest.credential_binding_sha256 != self.probe_policy.credential_binding_sha256
        ):
            raise MetaProbeError("meta_probe_authority_expired")
        caps = proposal.account
        if (
            (account.hard_cap_microdollars, account.daily_cap_microdollars, account.max_in_flight)
            != (caps.hard_cap_microdollars, caps.daily_cap_microdollars, caps.max_in_flight)
            or account.hard_cap_microdollars > self.readiness_policy.max_hard_cap_microdollars
            or account.frozen
            or account.execution_enabled
        ):
            raise MetaProbeError("meta_probe_account_conflict")
        usage = BudgetService._usage(session, account, now.date())
        if usage.committed_microdollars + (
            manifest.reservation_microdollars if additional_reservation else 0
        ) > min(
            attestation.available_credit_microdollars,
            attestation.remaining_provider_allowance_microdollars,
        ):
            raise MetaProbeError("meta_probe_account_conflict")

    def preview(self, request, readiness_id, attestation):
        with Session(self.engine) as session, session.begin():
            operator = self._authorize(session, request, write=True)
            self._enabled()
            row = session.get(MetaReadinessProposalRecord, _uuid(readiness_id))
            if row is None:
                raise MetaProbeError("meta_probe_not_found")
            account = _account_lock(session, row.account_id)
            operator = self._authorize(session, request, write=True)
            from polymath.meta_readiness_contracts import MetaReadinessProposal

            proposal = MetaReadinessProposal.model_validate_json(
                json.dumps(_stored(row, _clock(session)).proposal)
            )
            attestation = _parse(ProviderAccountAttestation, attestation)
            browser = session.get(BrowserSession, operator.browser_session_hash)
            expiry = min(
                proposal.valid_until,
                attestation.valid_until,
                operator.expires_at,
                browser.expires_at,
                _clock(session) + timedelta(seconds=self.probe_policy.consent_seconds),
            )
            try:
                manifest = build_probe_manifest(
                    str(row.id),
                    proposal,
                    policy=self.probe_policy,
                    native_control_epoch=account.native_control_epoch,
                    provider_account_attestation=attestation,
                    valid_until=expiry,
                )
            except ValueError:
                raise MetaProbeError("meta_probe_invalid_request") from None
            self._current(session, account, manifest, proposal)
            self._authorize(session, request, write=True)
            return MetaProbePreview(manifest, probe_manifest_sha256(manifest))

    def arm_and_enqueue(self, request, manifest, expected_manifest_sha256):
        manifest = _parse(MetaProbeManifest, manifest)
        digest = probe_manifest_sha256(manifest)
        if digest != expected_manifest_sha256:
            raise MetaProbeError("meta_probe_manifest_changed")
        with Session(self.engine) as session, session.begin():
            operator = self._authorize(session, request, write=True)
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUEUE_LOCK_KEY})
            account = _account_lock(session, manifest.account_id)
            operator = self._authorize(session, request, write=True)
            prior = session.scalar(
                select(MetaProbeManifestRecord).where(
                    MetaProbeManifestRecord.manifest_sha256 == digest
                )
            )
            if prior is not None:
                binding = session.scalar(
                    select(MetaProbeRequest).where(MetaProbeRequest.manifest_id == prior.id)
                )
                return _job(session, binding.run_id, self)
            row = session.get(MetaReadinessProposalRecord, _uuid(manifest.readiness_id))
            if row is None or account is None:
                raise MetaProbeError("meta_probe_not_found")
            from polymath.meta_readiness_contracts import MetaReadinessProposal

            proposal = MetaReadinessProposal.model_validate_json(
                json.dumps(_stored(row, _clock(session)).proposal)
            )
            self._current(session, account, manifest, proposal)
            if account.native_control_epoch != manifest.native_control_epoch:
                raise MetaProbeError("meta_probe_manifest_changed")
            unresolved = session.scalar(
                select(ResearchRun.id)
                .join(MetaProbeRequest, MetaProbeRequest.run_id == ResearchRun.id)
                .where(
                    MetaProbeRequest.account_id == account.id,
                    ResearchRun.status.in_(
                        ("queued", "running", "waiting_retry", "cancel_requested")
                    ),
                )
                .limit(1)
            )
            open_probe = session.scalar(
                select(BudgetReservation.id)
                .join(MetaProbeRequest, MetaProbeRequest.run_id == BudgetReservation.run_id)
                .where(
                    BudgetReservation.account_id == account.id,
                    BudgetReservation.status.in_(("reserved", "in_flight", "unknown")),
                )
                .limit(1)
            )
            if unresolved or open_probe:
                raise MetaProbeError("meta_probe_prior_unresolved")
            own = session.scalar(
                select(func.count())
                .select_from(ResearchRun)
                .join(ResearchRunAccess)
                .where(
                    ResearchRunAccess.session_hash == operator.browser_session_hash,
                    ResearchRun.status.in_(ACTIVE_STATES),
                )
            )
            total = session.scalar(
                select(func.count())
                .select_from(ResearchRun)
                .where(ResearchRun.status.in_(ACTIVE_STATES))
            )
            if own >= 1 or total >= 100:
                raise _PrivateHTTPException(429, "active_run_limit" if own else "queue_capacity")
            now = _clock(session)
            browser = session.get(BrowserSession, operator.browser_session_hash)
            expires = min(
                manifest.valid_until,
                operator.expires_at,
                browser.expires_at,
                now + timedelta(seconds=self.probe_policy.consent_seconds),
            )
            identities = self._identity_keys(manifest)
            for kind, identity in identities:
                prior_identity = session.get(MetaNativeIdentityBinding, (kind, identity))
                if prior_identity is not None and prior_identity.account_id != account.id:
                    raise MetaProbeError("meta_probe_account_conflict")
                if prior_identity is None:
                    session.execute(
                        insert(MetaNativeIdentityBinding)
                        .values(
                            kind=kind,
                            binding_sha256=identity,
                            account_id=account.id,
                            actor_id=operator.actor_id,
                        )
                        .on_conflict_do_nothing()
                    )
                    self._require_identity_bindings_partial(session, kind, identity, account.id)
            saved = MetaProbeManifestRecord(
                readiness_id=row.id,
                account_id=account.id,
                manifest_sha256=digest,
                manifest=manifest.model_dump(mode="json"),
            )
            session.add(saved)
            session.flush()
            approval = MetaProbeApproval(
                manifest_id=saved.id,
                manifest_sha256=digest,
                operator_session_hash=operator.token_hash,
                browser_session_hash=operator.browser_session_hash,
                actor_id=operator.actor_id,
                probe_policy_sha256=probe_policy_sha256(self.probe_policy),
                armed_epoch=account.native_control_epoch + 1,
                approved_at=now,
                expires_at=expires,
            )
            session.add(approval)
            run = ResearchRun(
                mode="explore",
                task_kind="verify_meta",
                goal="Isolated Meta protocol and reported-usage check",
            )
            session.add(run)
            session.flush()
            session.add(
                ResearchRunAccess(run_id=run.id, session_hash=operator.browser_session_hash)
            )
            session.add(
                BudgetRunLimit(
                    account_id=account.id,
                    run_id=run.id,
                    cap_microdollars=manifest.reservation_microdollars,
                    max_calls=1,
                )
            )
            session.add(
                MetaProbeRequest(
                    run_id=run.id,
                    manifest_id=saved.id,
                    approval_id=approval.id,
                    account_id=account.id,
                    manifest_sha256=digest,
                    request_sha256=manifest.request_sha256,
                    armed_epoch=approval.armed_epoch,
                )
            )
            self._authorize(session, request, write=True)
            self._current(session, account, manifest, proposal)
            account.native_scope = "probe_only"
            account.native_manifest_sha256 = digest
            account.native_control_epoch = approval.armed_epoch
            account.kill_switch = False
            _event(
                session,
                run,
                "probe_armed",
                "queued",
                "One exact Meta protocol check approved; research execution remains unavailable",
            )
            session.flush()
            self._authorize(session, request, write=True)
            self._current(session, account, manifest, proposal)
            if _clock(session) >= expires:
                raise MetaProbeError("meta_probe_authority_expired")
            return _job(session, run.id, self)

    def load_input(self, lease):
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            if run.task_kind != "verify_meta":
                raise MetaProbeError("meta_probe_not_found")
            return _input(session, run.id)

    def get_job(self, request, run_id):
        with Session(self.engine) as session, session.begin():
            self._authorize(session, request, write=False)
            result = _job(session, _uuid(run_id), self)
            self._authorize(session, request, write=False)
            return result

    def list_jobs(self, request, *, limit=20, before=None):
        if type(limit) is not int or not 1 <= limit <= 21:
            raise MetaProbeError("meta_probe_invalid_request")
        with Session(self.engine) as session, session.begin():
            self._authorize(session, request, write=False)
            query = select(ResearchRun).join(MetaProbeRequest)
            if before is not None:
                anchor = session.get(ResearchRun, _uuid(before))
                if anchor is None or anchor.task_kind != "verify_meta":
                    raise MetaProbeError("meta_probe_not_found")
                query = query.where(
                    tuple_(ResearchRun.created_at, ResearchRun.id)
                    < tuple_(anchor.created_at, anchor.id)
                )
            rows = session.scalars(
                query.order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc()).limit(limit)
            ).all()
            result = tuple(_job(session, row.id, self) for row in rows)
            self._authorize(session, request, write=False)
            return result

    def cancel(self, request, run_id):
        with Session(self.engine) as session, session.begin():
            operator = self._authorize(session, request, write=True)
            value = _input(session, _uuid(run_id))
            account = _account_lock(session, value.account_id)
            run = _locked(session, value.run_id)
            operator = self._authorize(session, request, write=True)
            if session.get(MetaProbeRevocation, value.approval_id) is None:
                session.add(
                    MetaProbeRevocation(approval_id=value.approval_id, actor_id=operator.actor_id)
                )
            account.native_control_epoch += 1
            account.kill_switch = True
            if run.status not in {"succeeded", "failed", "cancelled"}:
                run.status = "cancelled"
                run.lease_token += 1
                run.finished_at = _clock(session)
                _clear_lease(run)
                _event(
                    session,
                    run,
                    "probe_cancelled",
                    "cancelled",
                    "Further probe dispatch stopped; existing billing facts and holds retained",
                )
            session.flush()
            self._authorize(session, request, write=True)
            return _job(session, run.id, self)

    def finalize_known(self, run_id):
        """Free deterministic recovery from the retained response; no live authority."""
        with Session(self.engine) as session, session.begin():
            value = _input(session, _uuid(run_id))
            _account_lock(session, value.account_id)
            run = _locked(session, value.run_id)
            prior = session.scalar(
                select(MetaProbeReceipt).where(MetaProbeReceipt.run_id == run.id)
            )
            if prior is not None:
                return _receipt(session, prior)
            payload = _known_receipt_payload(session, value)
            row = MetaProbeReceipt(
                run_id=run.id,
                manifest_sha256=payload["manifest_sha256"],
                observation_sha256=payload["observation_sha256"],
                payload=payload,
                payload_sha256=_hash(_canonical(payload)),
            )
            session.add(row)
            session.flush()
            if run.status != "cancelled":
                run.status = "succeeded" if payload["native_status"] == "passed" else "failed"
                run.lease_token += 1
                run.finished_at = _clock(session)
                _clear_lease(run)
            run.outcome = {
                "meta_probe_receipt_id": str(row.id),
                "native_status": payload["native_status"],
                "research_execution_available": False,
            }
            _event(
                session,
                run,
                "probe_finalized",
                "completed",
                "One-call protocol receipt saved; no research execution authority granted",
            )
            return _receipt(session, row)

    def finalize(self, request, run_id):
        with Session(self.engine) as session, session.begin():
            self._authorize(session, request, write=True)
        self.finalize_known(run_id)
        return self.get_job(request, run_id)

    def get_receipt(self, request, receipt_id):
        with Session(self.engine) as session, session.begin():
            self._authorize(session, request, write=False)
            row = session.get(MetaProbeReceipt, _uuid(receipt_id))
            if row is None:
                raise MetaProbeError("meta_probe_not_found")
            result = _receipt(session, row)
            self._authorize(session, request, write=False)
            return result
