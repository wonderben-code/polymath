"""Visitor ownership under an explicitly issued durable funding delegation.

This service extends admission and current authority on the existing native
worker/ledger. It does not create a model, reserve money or schedule follow-ups.
"""

from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.budget_models import BudgetAccount, BudgetRunLimit
from polymath.discovery_admission import load_discovery_intent
from polymath.discovery_contracts import DiscoveryIntent
from polymath.explore_collection_admission import load_explore_collection_intent
from polymath.explore_collection_contracts import ExploreCollectionIntent
from polymath.jobs import _clear_lease, _event
from polymath.meta_readiness import _account_lock, _clock
from polymath.meta_readiness_contracts import quote_proposal
from polymath.meta_research_contracts import (
    SCIENTIFIC_CAPABILITIES,
    TASK_KINDS,
    PublicResearchProtocols,
    research_policy_sha256,
    select_profile_with_explore,
    task_profile_sha256,
)
from polymath.meta_research_errors import MetaResearchError
from polymath.meta_research_models import (
    MetaResearchApproval,
    MetaResearchManifestRecord,
    MetaResearchRevocation,
    NativeResearchRunBinding,
)
from polymath.models import ResearchExternalRequest, ResearchRun
from polymath.operator_models import OperatorSession
from polymath.public_research_contracts import (
    PublicAccessLimits,
    PublicResearchAccess,
    PublicResearchManifest,
    public_sponsor_policy_sha256,
    validate_current_public_research_manifest,
)
from polymath.public_research_models import PublicResearchDelegation, PublicResearchRunAccess
from polymath.public_research_quota import (
    check_public_admission,
    check_public_dispatch,
    public_session_usage,
    record_public_admission,
)
from polymath.run_api import (
    ACTIVE_STATES,
    QUEUE_LOCK_KEY,
    _authenticated,
    _authorize_write,
    _PrivateHTTPException,
)


class PublicResearchService:
    def __init__(self, parent):
        self.parent = parent
        self.engine = parent.engine

    def _enabled(self):
        self.parent._enabled()
        if not self.parent.public_policy.enabled:
            raise MetaResearchError("public_research_disabled")

    def _sponsor(self, session, approval, *, lock):
        query = select(OperatorSession).where(
            OperatorSession.token_hash == approval.operator_session_hash
        )
        if lock:
            query = query.with_for_update()
        operator = session.scalar(query.execution_options(populate_existing=True))
        if operator is None:
            raise MetaResearchError("meta_research_authority_expired")
        return operator

    def require_current(
        self,
        session,
        approval,
        manifest,
        proposal,
        account,
        *,
        operator=None,
        reserve=True,
        pending_request=None,
        run_id=None,
        requester_hash=None,
        allow_prepared=False,
    ):
        from polymath.meta_research import _original_input
        from polymath.public_delegations import validate_public_delegation

        self._enabled()
        row = session.get(MetaResearchManifestRecord, approval.manifest_id)
        delegation = validate_public_delegation(session, approval, row, manifest)
        policy = self.parent.operator_policy
        now = _clock(session)
        if (
            not isinstance(manifest, PublicResearchManifest)
            or delegation is None
            or operator is None
            or policy is None
            or not policy.configured
            or operator.token_hash != approval.operator_session_hash
            or operator.browser_session_hash != approval.browser_session_hash
            or operator.actor_id != approval.actor_id
            or operator.actor_id != policy.actor_id
            or operator.display_name != policy.display_name
            or operator.credential_fingerprint != policy.credential_fingerprint
            or operator.actor_id not in self.parent.readiness_policy.allowed_actor_ids
            or approval.expires_at <= now
            or session.get(MetaResearchRevocation, approval.id) is not None
            or approval.research_policy_sha256
            != research_policy_sha256(self.parent.research_policy)
            or account is None
            or account.native_scope != "research"
            or account.native_manifest_sha256 != approval.manifest_sha256
            or account.native_control_epoch != approval.armed_epoch
            or account.kill_switch
        ):
            raise MetaResearchError("meta_research_authority_expired")
        # Issuer logout and issuer/browser expiry deliberately do not end this
        # separately reviewed durable grant. Fresh visitor ownership still does.
        try:
            sponsor_hash = public_sponsor_policy_sha256(
                operator_fingerprint=policy.credential_fingerprint,
                actor_id=policy.actor_id,
                readiness_policy=self.parent.readiness_policy,
            )
            validate_current_public_research_manifest(
                manifest,
                research_policy=self.parent.research_policy,
                public_policy=self.parent.public_policy,
                sponsor_policy_sha256=sponsor_hash,
                now=now,
            )
        except ValueError:
            raise MetaResearchError("meta_research_authority_expired") from None
        self.parent._current_facts(
            session,
            account,
            manifest,
            proposal,
            reserve=reserve,
            pending_request=pending_request,
            allow_prepared=allow_prepared,
        )
        access = None
        if run_id is not None:
            value = _original_input(session, run_id, check_input=True)
            access = session.get(PublicResearchRunAccess, run_id)
            if access is None or value.approval_id != approval.id:
                raise MetaResearchError("meta_research_storage_unavailable")
            requester_hash = access.requester_session_hash
        if requester_hash is not None:
            requester = session.get(BrowserSession, requester_hash, populate_existing=True)
            if requester is None or requester.expires_at <= _clock(session):
                raise MetaResearchError("public_research_requester_expired")
        if access is not None:
            reservation_id = None
            if pending_request is not None:
                journal = session.get(ResearchExternalRequest, pending_request)
                if journal is None or journal.run_id != run_id:
                    raise MetaResearchError("meta_research_storage_unavailable")
                reservation_id = journal.reservation_id
            check_public_dispatch(
                session,
                access=access,
                delegation=delegation,
                estimated_microdollars=quote_proposal(proposal).per_call_reservation_microdollars,
                reservation_id=reservation_id,
            )
        return operator

    def _identity(self, request, owner):
        from polymath.public_resource_controls import PublicRequestIdentity

        controls = self.parent.resource_controls
        identity = getattr(request.state, "public_resource_identity", None)
        if (
            controls is None
            or not controls.policy.enabled
            or not isinstance(identity, PublicRequestIdentity)
            or identity.owner_hash != owner.token_hash
        ):
            raise MetaResearchError("public_research_resource_unavailable")
        return identity

    def begin_admission(self, session, request, digest):
        from polymath.meta_research import NativeResearchAdmission, _activation, _digest

        _authorize_write(request)
        owner = _authenticated(session, request)
        self._enabled()
        identity = self._identity(request, owner)
        approval = session.scalar(
            select(MetaResearchApproval).where(
                MetaResearchApproval.manifest_sha256 == _digest(digest)
            )
        )
        if approval is None:
            raise MetaResearchError("meta_research_activation_required")
        _, _, manifest, _ = _activation(session, approval.id)
        if not isinstance(manifest, PublicResearchManifest):
            raise MetaResearchError("meta_research_activation_required")
        self._sponsor(session, approval, lock=True)
        return NativeResearchAdmission(
            request,
            approval.id,
            approval.operator_session_hash,
            owner.token_hash,
            digest,
            session.get_transaction(),
            audience="public_sessions",
            network_bucket=identity.network_bucket,
        )

    def lock_account(self, session, admission):
        from polymath.meta_research import _activation

        if admission.transaction is not session.get_transaction():
            raise MetaResearchError("meta_research_invalid_request")
        approval, _, manifest, proposal = _activation(session, admission.approval_id)
        account = _account_lock(session, manifest.account_id)
        owner = _authenticated(session, admission.request)
        identity = self._identity(admission.request, owner)
        if (
            owner.token_hash != admission.browser_session_hash
            or identity.network_bucket != admission.network_bucket
        ):
            raise MetaResearchError("meta_research_activation_required")
        self.require_current(
            session,
            approval,
            manifest,
            proposal,
            account,
            operator=self._sponsor(session, approval, lock=False),
            requester_hash=owner.token_hash,
            allow_prepared=True,
        )
        check_public_admission(
            session,
            delegation=session.get(PublicResearchDelegation, approval.id),
            requester_hash=owner.token_hash,
            network_bucket=identity.network_bucket,
            estimated_microdollars=quote_proposal(proposal).per_call_reservation_microdollars,
        )
        admission.account = account
        return account

    def bind_run(self, session, admission, run, input_sha256):
        from polymath.meta_research import _activation, _digest, native_run_input_sha256

        if admission.transaction is not session.get_transaction() or admission.account is None:
            raise MetaResearchError("meta_research_invalid_request")
        approval, row, manifest, proposal = _activation(session, admission.approval_id)
        owner = _authenticated(session, admission.request)
        if owner.token_hash != admission.browser_session_hash:
            raise MetaResearchError("meta_research_activation_required")
        self._identity(admission.request, owner)
        self.require_current(
            session,
            approval,
            manifest,
            proposal,
            admission.account,
            operator=self._sponsor(session, approval, lock=False),
            requester_hash=owner.token_hash,
            allow_prepared=True,
        )
        from polymath.scientific_context_store import load_scientific_intent

        intent = load_scientific_intent(session, run)
        explore = load_explore_collection_intent(session, run)
        try:
            profile = select_profile_with_explore(
                manifest,
                run.task_kind,
                run.mode,
                discovery=load_discovery_intent(session, run),
                scientific_intent=intent,
                explore_collection_intent=explore,
            )
        except ValueError:
            raise MetaResearchError(
                "meta_research_explore_protocol_unavailable"
                if explore is not None
                else "meta_research_scientific_protocol_unavailable"
                if intent is not None
                else "meta_research_activation_required"
            ) from None
        access = session.get(ResearchRunAccess, run.id)
        if (
            access is None
            or access.session_hash != owner.token_hash
            or _digest(input_sha256) != native_run_input_sha256(session, run)
            or session.get(NativeResearchRunBinding, run.id) is not None
            or session.get(BudgetRunLimit, (row.account_id, run.id)) is not None
        ):
            raise MetaResearchError("meta_research_input_changed")
        binding = NativeResearchRunBinding(
            run_id=run.id,
            approval_id=approval.id,
            manifest_id=row.id,
            manifest_sha256=row.manifest_sha256,
            account_id=row.account_id,
            browser_session_hash=owner.token_hash,
            armed_epoch=approval.armed_epoch,
            task_kind=run.task_kind,
            mode=run.mode,
            input_sha256=input_sha256,
            profile=profile.model_dump(mode="json"),
            profile_sha256=task_profile_sha256(profile),
        )
        session.add(binding)
        session.add(
            BudgetRunLimit(
                account_id=row.account_id,
                run_id=run.id,
                cap_microdollars=proposal.account.run_cap_microdollars,
                max_calls=profile.max_model_calls,
            )
        )
        session.flush()
        record_public_admission(
            session,
            run=run,
            binding=binding,
            delegation=session.get(PublicResearchDelegation, approval.id),
            requester_hash=owner.token_hash,
            network_bucket=admission.network_bucket,
        )
        session.flush()
        self.require_current(
            session,
            approval,
            manifest,
            proposal,
            admission.account,
            operator=self._sponsor(session, approval, lock=False),
            run_id=run.id,
            allow_prepared=True,
        )
        return binding

    def resume(self, session, request, run_id, expected_lease_token, digest):
        from polymath.meta_research import (
            _activation,
            _digest,
            _fresh_locked_run,
            _id,
            _original_input,
        )

        _authorize_write(request)
        owner = _authenticated(session, request)
        self._identity(request, owner)
        value = _original_input(session, _id(run_id), check_input=True)
        if not isinstance(
            value.manifest, PublicResearchManifest
        ) or value.activation_sha256 != _digest(digest):
            raise MetaResearchError("meta_research_manifest_changed")
        approval, _, manifest, proposal = _activation(session, value.approval_id)
        operator = self._sponsor(session, approval, lock=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUEUE_LOCK_KEY})
        account = _account_lock(session, value.account_id)
        run = _fresh_locked_run(session, value.run_id)
        access = session.get(PublicResearchRunAccess, run.id)
        owner = _authenticated(session, request)
        if (
            access is None
            or access.requester_session_hash != owner.token_hash
            or type(expected_lease_token) is not int
            or run.lease_token != expected_lease_token
            or run.status != "interrupted"
        ):
            raise MetaResearchError("meta_research_authority_expired")
        self.require_current(
            session,
            approval,
            manifest,
            proposal,
            account,
            operator=operator,
            run_id=run.id,
        )
        if run.external_request_in_flight:
            raise MetaResearchError("meta_research_outcome_unknown")
        own = session.scalar(
            select(func.count())
            .select_from(ResearchRun)
            .join(ResearchRunAccess)
            .where(
                ResearchRunAccess.session_hash == owner.token_hash,
                ResearchRun.id != run.id,
                ResearchRun.status.in_(ACTIVE_STATES),
            )
        )
        total = session.scalar(
            select(func.count())
            .select_from(ResearchRun)
            .where(
                ResearchRun.id != run.id,
                ResearchRun.status.in_(ACTIVE_STATES),
            )
        )
        if own >= 1 or total >= 100:
            raise _PrivateHTTPException(429, "active_run_limit" if own else "research_queue_full")
        check_public_admission(
            session,
            delegation=session.get(PublicResearchDelegation, approval.id),
            requester_hash=owner.token_hash,
            network_bucket=access.network_bucket,
            estimated_microdollars=quote_proposal(proposal).per_call_reservation_microdollars,
            resume_run_id=run.id,
        )
        run.status = "queued"
        run.interruption_reason = None
        _clear_lease(run)
        run.finished_at = None
        _event(
            session,
            run,
            f"native_resume:{run.lease_token}",
            "resumed",
            "Original public research queued for explicit owner resume",
        )
        session.flush()
        self.require_current(
            session,
            approval,
            manifest,
            proposal,
            account,
            operator=operator,
            run_id=run.id,
        )
        return run

    def protocols(self, request):
        """Approved public scientific variants; discovery creates no authority."""
        approved = self._protocol_manifest(request)
        return PublicResearchProtocols(
            activation_sha256=approved[0] if approved is not None else None,
            capabilities=SCIENTIFIC_CAPABILITIES
            if approved is not None and approved[1].scientific_context_profiles is not None
            else (),
        ).model_dump(mode="json")

    def explore_protocols(self, request):
        """Separate minimal Explore capability; never extends the strict SC DTO."""
        from polymath.meta_research_contracts import ExploreCollectionAccess

        approved = self._protocol_manifest(request)
        extension = approved[1].explore_collection_profile if approved is not None else None
        return ExploreCollectionAccess(
            activation_sha256=approved[0] if approved is not None else None,
            explore_collection_intent=ExploreCollectionIntent(
                max_bridges=extension.profile.max_bridges
            )
            if extension is not None
            else None,
        ).model_dump(mode="json")

    def _protocol_manifest(self, request):
        """Current public metadata only; each POST separately rechecks authority."""
        from polymath.meta_research import _activation

        access = self.access(request)
        if not access["available"] or access["audience"] != "public_sessions":
            return None
        with Session(self.engine) as session:
            owner = _authenticated(session, request)
            row = session.scalar(
                select(MetaResearchManifestRecord).where(
                    MetaResearchManifestRecord.manifest_sha256
                    == access["expected_activation_sha256"]
                )
            )
            if row is None:
                return None
            approval = session.scalar(
                select(MetaResearchApproval).where(MetaResearchApproval.manifest_id == row.id)
            )
            if approval is None:
                return None
            approval, _, manifest, proposal = _activation(session, approval.id)
            if not isinstance(manifest, PublicResearchManifest):
                return None
            account = session.get(BudgetAccount, manifest.account_id, populate_existing=True)
            try:
                self.require_current(
                    session,
                    approval,
                    manifest,
                    proposal,
                    account,
                    operator=self._sponsor(session, approval, lock=False),
                    requester_hash=owner.token_hash,
                    reserve=False,
                    allow_prepared=True,
                )
            except MetaResearchError:
                return None
            return row.manifest_sha256, manifest

    def access(self, request):
        from polymath.meta_research import _activation

        empty = {
            "audience": None,
            "available": False,
            "reason": "public_research_disabled",
            "expected_activation_sha256": None,
            "expires_at": None,
            "limits": None,
            "remaining_session_allowance_microdollars": None,
            "remaining_session_jobs_today": None,
            "retry_after_seconds": None,
        }
        with Session(self.engine) as session:
            owner = _authenticated(session, request)
            account_id = self.parent.research_policy.native_account_id
            account = session.get(BudgetAccount, account_id) if account_id else None
            row = (
                session.scalar(
                    select(MetaResearchManifestRecord).where(
                        MetaResearchManifestRecord.manifest_sha256 == account.native_manifest_sha256
                    )
                )
                if account and account.native_manifest_sha256
                else None
            )
            if row is None:
                empty["reason"] = (
                    "public_research_activation_required"
                    if self.parent.public_policy.enabled
                    else "public_research_disabled"
                )
                return PublicResearchAccess(**empty).model_dump(mode="json")
            approval = session.scalar(
                select(MetaResearchApproval).where(MetaResearchApproval.manifest_id == row.id)
            )
            approval, _, manifest, proposal = _activation(session, approval.id)
            if not isinstance(manifest, PublicResearchManifest):
                if approval.browser_session_hash == owner.token_hash:
                    empty.update(audience="operator_browser", reason="operator_required")
                return PublicResearchAccess(**empty).model_dump(mode="json")
            extension = manifest.discovery_ladder_profile
            limits = PublicAccessLimits(
                session_total_cap_microdollars=str(
                    manifest.public_limits.session_total_cap_microdollars
                ),
                session_daily_cap_microdollars=str(
                    manifest.public_limits.session_daily_cap_microdollars
                ),
                max_jobs_per_session_day=manifest.public_limits.max_jobs_per_session_day,
                max_active_jobs_per_session=1,
                task_kinds=TASK_KINDS,
                discovery_intent=DiscoveryIntent(
                    max_stages=extension.profile.max_stages, max_depth=extension.profile.max_depth
                )
                if extension
                else None,
            )
            delegation = session.get(PublicResearchDelegation, approval.id)
            usage = public_session_usage(
                session, delegation=delegation, requester_hash=owner.token_hash
            )
            result = {
                **empty,
                "audience": "public_sessions",
                "expected_activation_sha256": row.manifest_sha256,
                "expires_at": manifest.valid_until,
                "limits": limits,
                "remaining_session_allowance_microdollars": str(
                    min(
                        usage["remaining_total_microdollars"], usage["remaining_daily_microdollars"]
                    )
                ),
                "remaining_session_jobs_today": max(
                    0, manifest.public_limits.max_jobs_per_session_day - usage["session_jobs_today"]
                ),
            }
            try:
                controls = self.parent.resource_controls
                if controls is None or not controls.policy.enabled:
                    raise MetaResearchError("public_research_resource_unavailable")
                self.require_current(
                    session,
                    approval,
                    manifest,
                    proposal,
                    account,
                    operator=self._sponsor(session, approval, lock=False),
                    requester_hash=owner.token_hash,
                    allow_prepared=True,
                )
                minimum = quote_proposal(proposal).per_call_reservation_microdollars
                for key, reason in (
                    ("remaining_public_total_microdollars", "public_research_budget_exhausted"),
                    (
                        "remaining_public_daily_microdollars",
                        "public_research_daily_budget_exhausted",
                    ),
                    ("remaining_total_microdollars", "public_research_session_budget_exhausted"),
                    (
                        "remaining_daily_microdollars",
                        "public_research_session_daily_budget_exhausted",
                    ),
                ):
                    if usage[key] < minimum:
                        raise MetaResearchError(reason)
                result.update(available=True, reason=None)
            except MetaResearchError as error:
                if account.kill_switch:
                    reason = "public_research_stopped"
                elif account.frozen:
                    reason = "public_research_frozen"
                else:
                    reason = {
                        "meta_research_unavailable": "public_research_disabled",
                        "meta_research_authority_expired": "public_research_authority_expired",
                        "meta_research_prior_unresolved": "public_research_prior_unresolved",
                    }.get(error.code, error.code)
                from polymath.public_research_contracts import PUBLIC_ACCESS_REASONS

                result["reason"] = (
                    reason if reason in PUBLIC_ACCESS_REASONS else "research_unavailable"
                )
            return PublicResearchAccess(**result).model_dump(mode="json")
