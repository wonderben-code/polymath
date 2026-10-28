"""Fresh native grants share the ordinary ledger; late facts need no new consent."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.budget import BudgetService, NativeProbePermit
from polymath.dispatch import DispatchCoordinator, DispatchGrant, _call_key
from polymath.jobs import _event, _owned
from polymath.meta_probe_contracts import (
    MetaObservation,
    assess_probe_usage,
    probe_policy_sha256,
)
from polymath.meta_probe_models import MetaProbeApproval, MetaProbeRevocation
from polymath.meta_probes import REQUEST_ID, MetaProbeError, MetaProbeStore, _input, _parse
from polymath.meta_readiness import _account_lock, _clock
from polymath.models import ResearchExternalRequest
from polymath.operator_models import OperatorSession


class MetaProbeDispatcher:
    def __init__(self, engine, probe_policy=None, readiness_policy=None, operator_policy=None):
        self.engine = engine
        self.store = MetaProbeStore(engine, probe_policy, readiness_policy, operator_policy)
        self.coordinator = DispatchCoordinator(engine)
        self.budget = BudgetService(engine)

    def get_recorded_observation(self, lease):
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            value = _input(session, run.id)
            request = session.get(ResearchExternalRequest, (run.id, REQUEST_ID))
            if request is None:
                return None
            if request.request_hash != value.request_hash or request.reservation_id is None:
                raise MetaProbeError("meta_probe_storage_unavailable")
            if request.status != "observed" or request.observation is None:
                raise MetaProbeError("meta_probe_outcome_unknown")
            return _parse(MetaObservation, request.observation)

    def _operator(self, session, approval):
        operator = session.scalar(
            select(OperatorSession)
            .where(OperatorSession.token_hash == approval.operator_session_hash)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        now = _clock(session)
        policy = self.store.operator_policy
        browser = session.get(BrowserSession, approval.browser_session_hash, populate_existing=True)
        if (
            operator is None
            or policy is None
            or not policy.configured
            or operator.credential_fingerprint != policy.credential_fingerprint
            or operator.actor_id != policy.actor_id
            or operator.display_name != policy.display_name
            or operator.actor_id != approval.actor_id
            or operator.actor_id not in self.store.readiness_policy.allowed_actor_ids
            or operator.browser_session_hash != approval.browser_session_hash
            or operator.revoked_at is not None
            or now >= operator.expires_at
            or browser is None
            or now >= browser.expires_at
            or now >= approval.expires_at
            or session.get(MetaProbeRevocation, approval.id) is not None
            or approval.probe_policy_sha256 != probe_policy_sha256(self.store.probe_policy)
        ):
            raise MetaProbeError("meta_probe_authority_expired")
        return operator

    def prepare(self, lease):
        with Session(self.engine) as session, session.begin():
            # Immutable lookup first, then the canonical fresh-grant lock order.
            value = _input(session, lease.run_id)
            approval = session.get(MetaProbeApproval, value.approval_id)
            self._operator(session, approval)
            account = _account_lock(session, value.account_id)
            run = _owned(session, lease)
            self._operator(session, approval)
            self.store._current(session, account, value.manifest, value.proposal)
            self.store._require_identity_bindings(session, value.manifest)
            owner = session.get(ResearchRunAccess, run.id)
            if owner is None or owner.session_hash != approval.browser_session_hash:
                raise MetaProbeError("meta_probe_storage_unavailable")
            if (
                account.native_scope != "probe_only"
                or account.native_control_epoch != value.armed_epoch
                or account.native_manifest_sha256 != approval.manifest_sha256
                or account.kill_switch
            ):
                raise MetaProbeError("meta_probe_authority_expired")
            prior = session.get(ResearchExternalRequest, (run.id, REQUEST_ID))
            if prior is not None:
                if prior.request_hash != value.request_hash or prior.reservation_id is None:
                    raise MetaProbeError("meta_probe_storage_unavailable")
                return None
            if run.external_request_in_flight:
                raise MetaProbeError("meta_probe_outcome_unknown")
            permit = NativeProbePermit(
                value.account_id,
                run.id,
                approval.manifest_sha256,
                value.armed_epoch,
                value.request_hash,
            )
            reservation = self.budget.reserve_in_session(
                session,
                value.account_id,
                run.id,
                call_key=_call_key(run.id, REQUEST_ID),
                estimated_microdollars=value.manifest.reservation_microdollars,
                native_permit=permit,
            )
            if not reservation.newly_reserved or not self.budget.claim_dispatch_in_session(
                session, reservation.id, native_permit=permit
            ):
                raise MetaProbeError("meta_probe_outcome_unknown")
            session.add(
                ResearchExternalRequest(
                    run_id=run.id,
                    request_id=REQUEST_ID,
                    status="prepared",
                    reservation_id=reservation.id,
                    dispatch_lease_token=lease.token,
                    request_hash=value.request_hash,
                )
            )
            run.external_request_id = REQUEST_ID
            run.external_request_in_flight = True
            _event(
                session,
                run,
                "native_probe_granted",
                "external_started",
                "One exact Meta canary reserved before sending; research remains unavailable",
            )
            session.flush()
            self._operator(session, approval)
            self.store._current(
                session, account, value.manifest, value.proposal, additional_reservation=False
            )
            _owned(session, lease)
            self.budget._scope_authorized(session, account, run.id, permit)
            self.budget._authorized(account, native_permit=permit)
            return DispatchGrant(
                run.id,
                REQUEST_ID,
                value.account_id,
                reservation.id,
                lease.token,
                value.request_hash,
            )

    def complete(self, grant, observation):
        observation = _parse(MetaObservation, observation)
        with Session(self.engine) as session:
            value = _input(session, grant.run_id)
            if (
                grant.request_id != REQUEST_ID
                or grant.account_id != value.account_id
                or grant.request_hash != value.request_hash
            ):
                raise MetaProbeError("meta_probe_storage_unavailable")
            assessment = assess_probe_usage(value.manifest, value.proposal, observation)
        known = assessment.actual_microdollars is not None
        return self.coordinator.complete(
            grant,
            observation=observation.model_dump(mode="json"),
            actual_microdollars=assessment.actual_microdollars,
            settlement_key="native-probe:" + assessment.observation_sha256 if known else None,
            prompt_tokens=assessment.input_tokens if known else None,
            completion_tokens=assessment.output_tokens if known else None,
            freeze_reason="native_probe_" + assessment.reason_code
            if assessment.freeze_required
            else None,
        )

    def mark_unknown(self, grant, reason="transport_error"):
        if reason not in {"timeout", "transport_error", "worker_lost", "usage_unavailable"}:
            reason = "transport_error"
        return self.coordinator.mark_unknown(grant, reason=reason)
