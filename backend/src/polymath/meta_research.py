"""Explicit owner-browser research consent and immutable original run profiles.

No model or network is constructed here. Admission locks Operator, then the
caller takes the shared queue, then Account before any run/finding-head work.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, timedelta

from sqlalchemy import func, or_, select, text, tuple_
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.artifact_models import ArtifactRequest
from polymath.assessment_models import AssessmentInputSource, AssessmentRequest
from polymath.budget import BudgetService
from polymath.budget_models import BudgetAccount, BudgetReservation, BudgetRunLimit
from polymath.discovery_admission import load_discovery_intent
from polymath.dispatch import _call_key
from polymath.evidence import _canonical, _hash
from polymath.explore_collection_admission import load_explore_collection_intent
from polymath.jobs import _clear_lease, _event, _owned
from polymath.lineage_models import ResearchRunParent
from polymath.meta_probe_models import (
    MetaProbeApproval,
    MetaProbeManifestRecord,
    MetaProbeReceipt,
    MetaProbeRequest,
)
from polymath.meta_probes import MetaProbeError, MetaProbeStore, _parse, _receipt, _uuid
from polymath.meta_readiness import _account_lock, _clock, _stored
from polymath.meta_readiness_contracts import ReadinessPolicy, validate_proposal_time
from polymath.meta_research_errors import MetaResearchError
from polymath.meta_research_models import (
    MetaResearchApproval,
    MetaResearchManifestRecord,
    MetaResearchRevocation,
    NativeResearchRunBinding,
)
from polymath.models import ResearchExternalRequest, ResearchRun
from polymath.operator_auth import require_operator
from polymath.operator_models import OperatorSession
from polymath.readiness_models import MetaReadinessProposalRecord
from polymath.run_api import ACTIVE_STATES, QUEUE_LOCK_KEY, _PrivateHTTPException


def _id(value):
    try:
        return _uuid(value)
    except (ValueError, MetaProbeError):
        raise MetaResearchError("meta_research_invalid_request") from None


def _digest(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise MetaResearchError("meta_research_invalid_request")
    return value


def _dto(cls, value):
    try:
        return _parse(cls, value)
    except Exception:
        raise MetaResearchError("meta_research_invalid_request") from None


def native_run_input_sha256(session, run):
    """Hash exact saved scientific intent, never today's mutable controller defaults."""
    artifact = session.get(ArtifactRequest, run.id)
    assessment = session.get(AssessmentRequest, run.id)
    parents = session.scalars(
        select(ResearchRunParent)
        .where(ResearchRunParent.run_id == run.id)
        .order_by(ResearchRunParent.ordinal)
    ).all()
    value = {
        "protocol": "polymath-native-scientific-input-v1",
        "mode": run.mode,
        "task_kind": run.task_kind,
        "goal": run.goal,
        "parents": [
            {
                "result_id": str(p.parent_result_id),
                "run_id": str(p.parent_run_id),
                "ordinal": p.ordinal,
                "sha256": p.content_sha256,
            }
            for p in parents
        ],
    }
    discovery = load_discovery_intent(session, run)
    if discovery is not None:
        value["discovery_intent"] = discovery.model_dump(mode="json")
    explore = load_explore_collection_intent(session, run)
    if explore is not None:
        value["explore_collection_intent"] = explore.model_dump(mode="json")
    if artifact is not None:
        value["artifact"] = {
            "kind": artifact.kind,
            "source_result_id": str(artifact.source_result_id),
            "source_run_id": str(artifact.source_run_id),
            "source_sha256": artifact.source_sha256,
            "formalisation_id": str(artifact.formalisation_id)
            if artifact.formalisation_id
            else None,
            "formalisation_sha256": artifact.formalisation_sha256,
        }
    if assessment is not None:
        value["assessment"] = {
            "root_result_id": str(assessment.root_result_id),
            "source_result_id": str(assessment.source_result_id),
            "source_sha256": assessment.source_sha256,
            "watch_id": str(assessment.watch_id),
            "expected_head_revision": assessment.expected_head_revision,
            "basis_sha256": assessment.basis_sha256,
            "sources": [
                {
                    "ordinal": x.ordinal,
                    "role": x.role,
                    "retrieval_id": str(x.retrieval_id),
                    "version_id": str(x.version_id),
                }
                for x in session.scalars(
                    select(AssessmentInputSource)
                    .where(AssessmentInputSource.run_id == run.id)
                    .order_by(AssessmentInputSource.ordinal)
                )
            ],
        }
    from polymath.scientific_context_contracts import (
        ArtifactScientificContextIntent,
        AskOverviewIntent,
        AskReassessmentOverviewIntent,
    )
    from polymath.scientific_context_store import load_scientific_intent

    intent = load_scientific_intent(session, run)
    if isinstance(intent, ArtifactScientificContextIntent):
        from polymath.scientific_context_models import ArtifactContextBinding

        context = session.get(ArtifactContextBinding, run.id)
        value["artifact"]["scientific_context_sha256"] = context.context_sha256
    elif isinstance(intent, AskOverviewIntent):
        value["ask_overview_intent"] = intent.model_dump(mode="json")
    elif isinstance(intent, AskReassessmentOverviewIntent):
        value["assessment_overview_intent"] = intent.model_dump(mode="json")
    return _hash(_canonical(value))


def native_run_controls(session, run):
    """Structural recovery hint only; fresh role/approval checks remain in resume."""
    binding = session.get(NativeResearchRunBinding, run.id)
    if binding is None:
        return None
    pending = session.scalar(
        select(ResearchExternalRequest.run_id)
        .where(
            ResearchExternalRequest.run_id == run.id, ResearchExternalRequest.status == "prepared"
        )
        .limit(1)
    )
    open_hold = session.scalar(
        select(BudgetReservation.id)
        .where(
            BudgetReservation.run_id == run.id,
            BudgetReservation.status.in_(("reserved", "in_flight", "unknown")),
        )
        .limit(1)
    )
    return {
        "activation_sha256": binding.manifest_sha256,
        "expected_lease_token": str(run.lease_token),
        "can_resume": run.status == "interrupted"
        and not run.external_request_in_flight
        and pending is None
        and open_hold is None,
    }


def native_run_outcome_unknown(session, run):
    """A known transport reply does not imply known billing or a released hold."""
    if session.get(NativeResearchRunBinding, run.id) is None:
        return False
    return bool(
        run.external_request_in_flight
        or session.scalar(
            select(ResearchExternalRequest.run_id)
            .where(
                ResearchExternalRequest.run_id == run.id,
                ResearchExternalRequest.status == "prepared",
            )
            .limit(1)
        )
        is not None
        or session.scalar(
            select(BudgetReservation.id)
            .where(
                BudgetReservation.run_id == run.id,
                BudgetReservation.status.in_(("reserved", "in_flight", "unknown")),
            )
            .limit(1)
        )
        is not None
    )


@dataclass(frozen=True)
class NativeResearchInput:
    run_id: uuid.UUID
    account_id: str
    manifest: object
    proposal: object
    profile: object
    approval_id: uuid.UUID
    armed_epoch: int
    input_sha256: str
    activation_sha256: str
    profile_sha256: str


@dataclass
class NativeResearchAdmission:
    request: object = field(repr=False)
    approval_id: uuid.UUID
    operator_hash: str
    browser_session_hash: str
    activation_sha256: str
    transaction: object = field(repr=False)
    account: BudgetAccount | None = field(default=None, repr=False)
    audience: str = "operator_browser"
    network_bucket: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class MetaResearchRecord:
    id: uuid.UUID
    payload: dict

    def to_response(self):
        return {"id": str(self.id), **self.payload}


@dataclass(frozen=True)
class MetaResearchPreview:
    manifest: object
    manifest_sha256: str

    def to_response(self):
        return {
            "manifest": self.manifest.model_dump(mode="json"),
            "manifest_sha256": self.manifest_sha256,
            "research_execution_available": False,
        }


def _canary(session, receipt_id):
    from polymath.meta_research_contracts import PassedCanaryBinding

    row = session.get(MetaProbeReceipt, _id(receipt_id))
    if row is None:
        raise MetaResearchError("meta_research_not_found")
    try:
        receipt = _receipt(session, row).to_response()
        if receipt["native_status"] != "passed":
            raise ValueError
        from polymath.meta_probes import _input

        original = _input(session, row.run_id)
        return PassedCanaryBinding(
            receipt_id=str(row.id),
            receipt_sha256=row.payload_sha256,
            account_id=original.account_id,
            model_id=original.proposal.model_id,
            credential_binding_sha256=original.manifest.credential_binding_sha256,
        )
    except (ValueError, TypeError, AttributeError, KeyError):
        raise MetaResearchError("meta_research_account_conflict") from None


def _saved_canary(session, receipt_id):
    """Validate immutable v1 proof bindings, without today's canary action parser.

    New activation still calls the full canary verifier. Historical accounting
    relies on the original saved verdict and exact journal/ledger hashes.
    """
    from polymath.meta_research_contracts import PassedCanaryBinding

    receipt = session.get(MetaProbeReceipt, _id(receipt_id))
    if receipt is None:
        raise MetaResearchError("meta_research_storage_unavailable")
    try:
        payload = receipt.payload
        binding = session.get(MetaProbeRequest, receipt.run_id)
        saved = session.get(MetaProbeManifestRecord, binding.manifest_id)
        approval = session.get(MetaProbeApproval, binding.approval_id)
        manifest = saved.manifest
        journal = session.get(ResearchExternalRequest, (receipt.run_id, "model-0001"))
        reservation = session.get(BudgetReservation, journal.reservation_id)
        facts = journal.observation
        witness = payload["bindings"]
        assessment = payload["assessment"]
        model = manifest["request"]["payload"]["model"]
        if (
            _hash(_canonical(payload)) != receipt.payload_sha256
            or _hash(_canonical(manifest)) != saved.manifest_sha256
            or _hash(_canonical(manifest["request"])) != manifest["request_sha256"]
            or payload["native_status"] != "passed"
            or assessment["status"] != "passed"
            or payload["research_execution_available"] is not False
            or assessment["research_execution_available"] is not False
            or payload["run_id"] != str(receipt.run_id)
            or receipt.manifest_sha256 != saved.manifest_sha256
            or binding.manifest_sha256 != saved.manifest_sha256
            or approval.manifest_id != saved.id
            or approval.manifest_sha256 != saved.manifest_sha256
            or binding.armed_epoch != approval.armed_epoch
            or witness["armed_epoch"] != approval.armed_epoch
            or witness["approval_id"] != str(approval.id)
            or binding.account_id != saved.account_id
            or manifest["account_id"] != saved.account_id
            or payload["manifest_sha256"] != saved.manifest_sha256
            or witness["proposal_sha256"] != manifest["proposal_sha256"]
            or witness["credential_binding_sha256"] != manifest["credential_binding_sha256"]
            or manifest["protocol"] != "polymath-meta-probe-v1"
            or manifest["request"]["method"] != "POST"
            or manifest["request"]["endpoint"] != "https://api.meta.ai/v1/chat/completions"
            or journal.status != "observed"
            or journal.request_hash != manifest["request_sha256"]
            or binding.request_sha256 != manifest["request_sha256"]
            or witness["request_sha256"] != manifest["request_sha256"]
            or _hash(_canonical(facts)) != receipt.observation_sha256
            or payload["observation_sha256"] != receipt.observation_sha256
            or assessment["observation_sha256"] != receipt.observation_sha256
            or facts["requested_model_id"] != model
            or facts["observed_model_id"] != model
            or facts["body_complete"] is not True
            or facts["http_status"] != 200
            or facts["finish_reason"] != "stop"
            or facts["request_sha256"] != manifest["request_sha256"]
            or reservation.status != "settled"
            or reservation.account_id != saved.account_id
            or reservation.run_id != receipt.run_id
            or witness["reservation_id"] != str(reservation.id)
            or reservation.call_key != _call_key(receipt.run_id, "model-0001")
            or reservation.estimated_microdollars != manifest["reservation_microdollars"]
            or reservation.actual_microdollars != assessment["actual_microdollars"]
            or reservation.prompt_tokens != facts["input_tokens"]
            or reservation.completion_tokens != facts["output_tokens"]
            or reservation.settlement_key != "native-probe:" + receipt.observation_sha256
        ):
            raise ValueError
        return PassedCanaryBinding(
            receipt_id=str(receipt.id),
            receipt_sha256=receipt.payload_sha256,
            account_id=saved.account_id,
            model_id=model,
            credential_binding_sha256=manifest["credential_binding_sha256"],
        )
    except (ValueError, TypeError, AttributeError, KeyError):
        raise MetaResearchError("meta_research_storage_unavailable") from None


def _activation(session, approval_id):
    from polymath.meta_readiness_contracts import MetaReadinessProposal
    from polymath.meta_research_contracts import (
        MetaResearchManifest,
        research_manifest_sha256,
        validate_research_manifest,
    )

    approval = session.get(MetaResearchApproval, approval_id)
    if approval is None:
        raise MetaResearchError("meta_research_activation_required")
    row = session.get(MetaResearchManifestRecord, approval.manifest_id)
    ready = session.get(MetaReadinessProposalRecord, row.readiness_id)
    try:
        from polymath.public_research_contracts import (
            PublicResearchManifest,
            public_research_manifest_sha256,
            validate_public_research_manifest,
        )
        from polymath.public_research_models import PublicResearchDelegation

        public = row.manifest.get("protocol") == "polymath-meta-public-research-v1"
        manifest_type = PublicResearchManifest if public else MetaResearchManifest
        manifest = manifest_type.model_validate_json(json.dumps(row.manifest))
        proposal = MetaReadinessProposal.model_validate_json(
            json.dumps(_stored(ready, _clock(session)).proposal)
        )
        if public:
            from polymath.public_delegations import validate_public_delegation

            validate_public_research_manifest(manifest, proposal)
            validate_public_delegation(session, approval, row, manifest)
        else:
            validate_research_manifest(manifest, proposal)
            if session.get(PublicResearchDelegation, approval.id) is not None:
                raise ValueError
        digest = (
            public_research_manifest_sha256(manifest)
            if public
            else research_manifest_sha256(manifest)
        )
        if (
            digest != row.manifest_sha256
            or approval.manifest_sha256 != row.manifest_sha256
            or approval.armed_epoch != manifest.native_control_epoch + 1
            or manifest.readiness_id != str(row.readiness_id)
            or row.account_id != manifest.account_id
            or manifest.canary.receipt_id != str(row.canary_receipt_id)
            or manifest.canary != _saved_canary(session, row.canary_receipt_id)
        ):
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise MetaResearchError("meta_research_storage_unavailable") from None
    return approval, row, manifest, proposal


def _original_input(session, run_id, *, check_input=False):
    from polymath.meta_research_contracts import select_profile_with_explore, task_profile_sha256
    from polymath.public_research_contracts import PublicResearchManifest
    from polymath.public_research_models import PublicResearchRunAccess
    from polymath.scientific_context_store import load_scientific_intent

    binding = session.get(NativeResearchRunBinding, run_id)
    if binding is None:
        raise MetaResearchError("meta_research_not_found")
    approval, row, manifest, proposal = _activation(session, binding.approval_id)
    run = session.get(ResearchRun, run_id)
    discovery = load_discovery_intent(session, run)
    public = isinstance(manifest, PublicResearchManifest)
    try:
        profile = select_profile_with_explore(
            manifest,
            binding.task_kind,
            run.mode,
            discovery=discovery,
            scientific_intent=load_scientific_intent(session, run),
            explore_collection_intent=load_explore_collection_intent(session, run),
        )
    except ValueError:
        raise MetaResearchError("meta_research_storage_unavailable") from None
    owner = session.get(ResearchRunAccess, run_id)
    public_access = session.get(PublicResearchRunAccess, run_id)
    if public:
        if (
            public_access is None
            or public_access.approval_id != approval.id
            or public_access.manifest_sha256 != binding.manifest_sha256
            or public_access.account_id != binding.account_id
            or public_access.requester_session_hash != binding.browser_session_hash
            or public_access.public_policy_sha256 != manifest.public_policy_sha256
        ):
            raise MetaResearchError("meta_research_storage_unavailable")
    elif public_access is not None or binding.browser_session_hash != approval.browser_session_hash:
        raise MetaResearchError("meta_research_storage_unavailable")
    if (
        binding.manifest_id != row.id
        or binding.manifest_sha256 != row.manifest_sha256
        or binding.account_id != row.account_id
        or binding.armed_epoch != approval.armed_epoch
        or run.task_kind != binding.task_kind
        or run.mode != binding.mode
        or owner is None
        or owner.session_hash != binding.browser_session_hash
        or binding.profile != profile.model_dump(mode="json")
        or binding.profile_sha256 != task_profile_sha256(profile)
    ):
        raise MetaResearchError("meta_research_storage_unavailable")
    if check_input and native_run_input_sha256(session, run) != binding.input_sha256:
        raise MetaResearchError("meta_research_input_changed")
    return NativeResearchInput(
        run_id,
        binding.account_id,
        manifest,
        proposal,
        profile,
        approval.id,
        binding.armed_epoch,
        binding.input_sha256,
        row.manifest_sha256,
        binding.profile_sha256,
    )


def _fresh_locked_run(session, run_id):
    """Refresh every mutable fence after waits, even with a preloaded ORM row."""
    run = session.scalar(
        select(ResearchRun)
        .where(ResearchRun.id == run_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise MetaResearchError("meta_research_not_found")
    return run


class MetaResearchStore:
    def __init__(
        self,
        engine,
        research_policy=None,
        readiness_policy=None,
        operator_policy=None,
        *,
        public_policy=None,
        resource_controls=None,
    ):
        from polymath.meta_research_contracts import NativeResearchPolicy

        if engine.dialect.name != "postgresql":
            raise ValueError("MetaResearchStore requires PostgreSQL")
        self.engine = engine
        from polymath.public_research_contracts import PublicResearchPolicy

        self.public_policy = public_policy or PublicResearchPolicy()
        self.resource_controls = resource_controls
        self.research_policy = research_policy or NativeResearchPolicy()
        self.readiness_policy = readiness_policy or ReadinessPolicy()
        self.operator_policy = operator_policy

    def _authorize(self, session, request, *, write):
        if not self.readiness_policy.allowed_actor_ids:
            raise MetaResearchError("readiness_role_unavailable")
        operator = require_operator(session, request, write=write, policy=self.operator_policy)
        if not write:
            session.refresh(operator, with_for_update=True)
            operator = require_operator(session, request, write=False, policy=self.operator_policy)
        if operator.actor_id not in self.readiness_policy.allowed_actor_ids:
            raise MetaResearchError("meta_research_actor_forbidden")
        return operator

    def _enabled(self):
        if (
            not self.readiness_policy.preparation_enabled
            or not self.research_policy.paid_research_enabled
            or not self.research_policy.configured
        ):
            raise MetaResearchError("meta_research_unavailable")

    def _current_facts(
        self,
        session,
        account,
        manifest,
        proposal,
        *,
        reserve=True,
        pending_request=None,
        allow_prepared=False,
    ):
        self._enabled()
        now = _clock(session)
        try:
            validate_proposal_time(proposal, now)
        except ValueError:
            raise MetaResearchError("meta_research_authority_expired") from None
        if (
            account is None
            or account.frozen
            or self.research_policy.native_account_id != manifest.account_id
            or self.research_policy.model_id != manifest.model_id
            or self.research_policy.credential_binding_sha256 != manifest.credential_binding_sha256
            or now >= manifest.valid_until
            or now < manifest.review.observed_at
            or now >= manifest.review.valid_until
            or now < manifest.provider_account_attestation.observed_at
            or now >= manifest.provider_account_attestation.valid_until
        ):
            raise MetaResearchError("meta_research_authority_expired")
        from polymath.meta_research_contracts import (
            derive_discovery_ladder_profile,
            derive_task_profiles,
            validate_current_explore_collection_profile,
            validate_current_scientific_context_profiles,
        )

        if manifest.task_profiles != derive_task_profiles(proposal):
            raise MetaResearchError("meta_research_authority_expired")
        if manifest.explore_collection_profile is not None:
            try:
                validate_current_explore_collection_profile(
                    manifest.explore_collection_profile, proposal
                )
            except ValueError:
                raise MetaResearchError("meta_research_authority_expired") from None
        if manifest.scientific_context_profiles is not None:
            try:
                validate_current_scientific_context_profiles(
                    manifest.scientific_context_profiles, proposal
                )
            except ValueError:
                raise MetaResearchError("meta_research_authority_expired") from None
        if manifest.discovery_ladder_profile is not None:
            from polymath.discovery_contracts import DiscoveryIntent

            extension = manifest.discovery_ladder_profile
            if extension != derive_discovery_ladder_profile(
                proposal,
                DiscoveryIntent(
                    max_stages=extension.profile.max_stages,
                    max_depth=extension.profile.max_depth,
                ),
            ):
                raise MetaResearchError("meta_research_authority_expired")
        caps = proposal.account
        if (
            account.id != caps.account_id
            or (
                account.hard_cap_microdollars,
                account.daily_cap_microdollars,
                account.max_in_flight,
            )
            != (caps.hard_cap_microdollars, caps.daily_cap_microdollars, caps.max_in_flight)
            or caps.hard_cap_microdollars > self.readiness_policy.max_hard_cap_microdollars
        ):
            raise MetaResearchError("meta_research_account_conflict")
        unknown = session.scalar(
            select(BudgetReservation.id)
            .where(
                BudgetReservation.account_id == account.id, BudgetReservation.status == "unknown"
            )
            .limit(1)
        )
        if unknown is not None:
            raise MetaResearchError("meta_research_prior_unresolved")
        prepared_marker = (
            select(ResearchExternalRequest.run_id)
            .where(
                ResearchExternalRequest.run_id == ResearchRun.id,
                ResearchExternalRequest.request_id == ResearchRun.external_request_id,
                ResearchExternalRequest.status == "prepared",
            )
            .correlate(ResearchRun)
            .exists()
        )
        unexplained_marker = session.scalar(
            select(ResearchRun.id)
            .join(NativeResearchRunBinding, NativeResearchRunBinding.run_id == ResearchRun.id)
            .where(
                NativeResearchRunBinding.account_id == account.id,
                ResearchRun.external_request_in_flight.is_(True),
                ~prepared_marker,
            )
            .limit(1)
        )
        if unexplained_marker is not None:
            raise MetaResearchError("meta_research_prior_unresolved")
        pending = (
            select(ResearchExternalRequest.run_id)
            .join(BudgetReservation, BudgetReservation.id == ResearchExternalRequest.reservation_id)
            .where(
                BudgetReservation.account_id == account.id,
                ResearchExternalRequest.status == "prepared",
            )
        )
        if pending_request is not None:
            pending = pending.where(
                or_(
                    ResearchExternalRequest.run_id != pending_request[0],
                    ResearchExternalRequest.request_id != pending_request[1],
                )
            )
        if allow_prepared:
            # Queueing is free; only a correctly fenced, live native call can
            # be treated as known in-flight work. A stranded prepared journal
            # remains ambiguous even before its hold is marked unknown.
            live_binding = (
                select(NativeResearchRunBinding.run_id)
                .where(
                    NativeResearchRunBinding.run_id == ResearchExternalRequest.run_id,
                    NativeResearchRunBinding.account_id == account.id,
                    NativeResearchRunBinding.manifest_sha256 == account.native_manifest_sha256,
                    NativeResearchRunBinding.armed_epoch == account.native_control_epoch,
                )
                .exists()
            )
            pending = pending.join(
                ResearchRun, ResearchRun.id == ResearchExternalRequest.run_id
            ).where(
                or_(
                    ResearchRun.status != "running",
                    ResearchRun.lease_expires_at.is_(None),
                    ResearchRun.lease_expires_at <= now,
                    ResearchExternalRequest.dispatch_lease_token != ResearchRun.lease_token,
                    ResearchRun.external_request_in_flight.is_(False),
                    ResearchRun.external_request_id.is_(None),
                    ResearchRun.external_request_id != ResearchExternalRequest.request_id,
                    BudgetReservation.status != "in_flight",
                    ~live_binding,
                )
            )
        if session.scalar(pending.limit(1)) is not None:
            raise MetaResearchError("meta_research_prior_unresolved")
        from polymath.meta_readiness_contracts import quote_proposal

        reservation = quote_proposal(proposal).per_call_reservation_microdollars if reserve else 0
        usage = BudgetService._usage(session, account, now.date())
        attestation = manifest.provider_account_attestation
        if usage.committed_microdollars + reservation > min(
            attestation.available_credit_microdollars,
            attestation.remaining_provider_allowance_microdollars,
        ):
            raise MetaResearchError("meta_research_account_conflict")
        try:
            MetaProbeStore._require_identity_bindings(self, session, manifest)
        except MetaProbeError:
            raise MetaResearchError("meta_research_account_conflict") from None

    _identity_keys = staticmethod(MetaProbeStore._identity_keys)
    _require_identity_bindings_partial = staticmethod(
        MetaProbeStore._require_identity_bindings_partial
    )

    def _approval_current(
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
        from polymath.meta_research_contracts import research_policy_sha256
        from polymath.public_research_contracts import PublicResearchManifest

        if isinstance(manifest, PublicResearchManifest):
            return self.public_service.require_current(
                session,
                approval,
                manifest,
                proposal,
                account,
                operator=operator,
                reserve=reserve,
                pending_request=pending_request,
                run_id=run_id,
                requester_hash=requester_hash,
                allow_prepared=allow_prepared,
            )

        browser = session.get(BrowserSession, approval.browser_session_hash, populate_existing=True)
        now = _clock(session)
        if (
            operator is None
            or self.operator_policy is None
            or not self.operator_policy.configured
            or operator.token_hash != approval.operator_session_hash
            or operator.browser_session_hash != approval.browser_session_hash
            or operator.actor_id != approval.actor_id
            or operator.credential_fingerprint != self.operator_policy.credential_fingerprint
            or operator.actor_id != self.operator_policy.actor_id
            or operator.display_name != self.operator_policy.display_name
            or operator.actor_id not in self.readiness_policy.allowed_actor_ids
            or operator.revoked_at is not None
            or now >= operator.expires_at
            or browser is None
            or now >= browser.expires_at
            or now >= approval.expires_at
            or session.get(MetaResearchRevocation, approval.id) is not None
            or approval.research_policy_sha256 != research_policy_sha256(self.research_policy)
            or account.native_scope != "research"
            or account.native_manifest_sha256 != approval.manifest_sha256
            or account.native_control_epoch != approval.armed_epoch
            or account.kill_switch
        ):
            raise MetaResearchError("meta_research_authority_expired")
        self._current_facts(
            session, account, manifest, proposal, reserve=reserve, pending_request=pending_request
        )
        return operator

    @property
    def public_service(self):
        from polymath.public_research import PublicResearchService

        return PublicResearchService(self)

    @property
    def public_delegations(self):
        from polymath.public_delegations import PublicDelegationStore

        return PublicDelegationStore(self)

    def public_preview(self, *args, **kwargs):
        return self.public_delegations.preview(*args, **kwargs)

    def public_activate(self, *args, **kwargs):
        return self.public_delegations.activate(*args, **kwargs)

    @staticmethod
    def _public_manifest_requested(session, digest):
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            return False
        return (
            session.scalar(
                select(MetaResearchManifestRecord.id).where(
                    MetaResearchManifestRecord.manifest_sha256 == digest,
                    MetaResearchManifestRecord.manifest["protocol"].astext
                    == "polymath-meta-public-research-v1",
                )
            )
            is not None
        )

    def preview(
        self,
        request,
        readiness_id,
        canary_receipt_id,
        review,
        provider_account_attestation,
        *,
        discovery=None,
        scientific_context=None,
        explore_collection=None,
    ):
        from polymath.meta_probe_contracts import ProviderAccountAttestation
        from polymath.meta_readiness_contracts import MetaReadinessProposal
        from polymath.meta_research_contracts import (
            NativeResearchReview,
            build_research_manifest,
            research_manifest_sha256,
        )

        with Session(self.engine) as session, session.begin():
            operator = self._authorize(session, request, write=True)
            self._enabled()
            row = session.get(MetaReadinessProposalRecord, _id(readiness_id))
            if row is None:
                raise MetaResearchError("meta_research_not_found")
            account = _account_lock(session, row.account_id)
            operator = self._authorize(session, request, write=True)
            proposal = MetaReadinessProposal.model_validate_json(
                json.dumps(_stored(row, _clock(session)).proposal)
            )
            canary = _canary(session, canary_receipt_id)
            review = _dto(NativeResearchReview, review)
            attestation = _dto(ProviderAccountAttestation, provider_account_attestation)
            browser = session.get(BrowserSession, operator.browser_session_hash)
            expiry = min(
                browser.expires_at,
                operator.expires_at,
                proposal.valid_until,
                review.valid_until,
                attestation.valid_until,
                _clock(session) + timedelta(seconds=self.research_policy.consent_seconds),
            )
            try:
                manifest = build_research_manifest(
                    str(row.id),
                    proposal,
                    policy=self.research_policy,
                    native_control_epoch=account.native_control_epoch,
                    canary=canary,
                    review=review,
                    provider_account_attestation=attestation,
                    valid_until=expiry,
                    discovery=discovery,
                    scientific_context=scientific_context,
                    explore_collection=explore_collection,
                )
            except ValueError:
                raise MetaResearchError("meta_research_invalid_request") from None
            self._authorize(session, request, write=True)
            self._current_facts(session, account, manifest, proposal)
            return MetaResearchPreview(manifest, research_manifest_sha256(manifest))

    def activate(self, request, manifest, expected_manifest_sha256):
        from polymath.meta_research_contracts import (
            MetaResearchManifest,
            research_manifest_sha256,
            research_policy_sha256,
        )

        manifest = _dto(MetaResearchManifest, manifest)
        digest = research_manifest_sha256(manifest)
        if digest != expected_manifest_sha256:
            raise MetaResearchError("meta_research_manifest_changed")
        with Session(self.engine) as session, session.begin():
            operator = self._authorize(session, request, write=True)
            account = _account_lock(session, manifest.account_id)
            operator = self._authorize(session, request, write=True)
            prior = session.scalar(
                select(MetaResearchManifestRecord).where(
                    MetaResearchManifestRecord.manifest_sha256 == digest
                )
            )
            if prior is not None:
                return self._record(session, prior, operator)
            self._current_facts(session, account, manifest, manifest.proposal)
            ready = session.get(MetaReadinessProposalRecord, _id(manifest.readiness_id))
            if ready is None or manifest.canary != _canary(session, manifest.canary.receipt_id):
                raise MetaResearchError("meta_research_account_conflict")
            if ready.proposal_sha256 != manifest.proposal_sha256 or _stored(
                ready, _clock(session)
            ).proposal != manifest.proposal.model_dump(mode="json"):
                raise MetaResearchError("meta_research_manifest_changed")
            if account.native_control_epoch != manifest.native_control_epoch:
                raise MetaResearchError("meta_research_manifest_changed")
            pending = session.scalar(
                select(BudgetReservation.id)
                .where(
                    BudgetReservation.account_id == account.id,
                    BudgetReservation.status.in_(("reserved", "in_flight", "unknown")),
                )
                .limit(1)
            )
            if pending is not None:
                raise MetaResearchError("meta_research_prior_unresolved")
            now = _clock(session)
            browser = session.get(BrowserSession, operator.browser_session_hash)
            expiry = min(
                manifest.valid_until,
                browser.expires_at,
                operator.expires_at,
                now + timedelta(seconds=self.research_policy.consent_seconds),
            )
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
                research_policy_sha256=research_policy_sha256(self.research_policy),
                armed_epoch=account.native_control_epoch + 1,
                approved_at=now,
                expires_at=expiry,
            )
            session.add(approval)
            self._authorize(session, request, write=True)
            self._current_facts(session, account, manifest, manifest.proposal)
            account.native_scope = "research"
            account.native_manifest_sha256 = digest
            account.native_control_epoch = approval.armed_epoch
            account.kill_switch = False
            session.flush()
            operator = self._authorize(session, request, write=True)
            self._approval_current(
                session, approval, manifest, manifest.proposal, account, operator=operator
            )
            return self._record(session, row, operator)

    def _record(self, session, row, viewer):
        if row.manifest.get("protocol") != "polymath-meta-research-activation-v1":
            raise MetaResearchError("meta_research_not_found")
        approval = session.scalar(
            select(MetaResearchApproval).where(MetaResearchApproval.manifest_id == row.id)
        )
        approval, row, manifest, proposal = _activation(session, approval.id)
        account = session.get(BudgetAccount, row.account_id)
        operator = session.get(
            OperatorSession, approval.operator_session_hash, populate_existing=True
        )
        revoked = session.get(MetaResearchRevocation, approval.id)
        try:
            self._approval_current(
                session, approval, manifest, proposal, account, operator=operator
            )
            valid = True
        except Exception:
            valid = False
        eligible = bool(
            valid
            and viewer.token_hash == approval.operator_session_hash
            and viewer.browser_session_hash == approval.browser_session_hash
        )
        return MetaResearchRecord(
            row.id,
            {
                "manifest_sha256": row.manifest_sha256,
                "manifest": row.manifest,
                "audience": "operator_browser",
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
                "eligible_for_current_browser": eligible,
                "research_execution_available": eligible,
                "created_at": row.created_at.astimezone(UTC).isoformat(),
            },
        )

    def current(self, request):
        with Session(self.engine) as session, session.begin():
            operator = self._authorize(session, request, write=False)
            try:
                self._enabled()
            except MetaResearchError as error:
                return {"activation": None, "reason": error.code}
            account = (
                session.get(BudgetAccount, self.research_policy.native_account_id)
                if self.research_policy.native_account_id
                else None
            )
            if account is None or account.native_scope != "research":
                return {"activation": None, "reason": "meta_research_activation_required"}
            row = session.scalar(
                select(MetaResearchManifestRecord)
                .join(MetaResearchApproval)
                .where(
                    MetaResearchManifestRecord.manifest_sha256 == account.native_manifest_sha256,
                    MetaResearchApproval.operator_session_hash == operator.token_hash,
                    MetaResearchApproval.browser_session_hash == operator.browser_session_hash,
                )
            )
            if row is None:
                return {"activation": None, "reason": "meta_research_activation_required"}
            if row.manifest.get("protocol") != "polymath-meta-research-activation-v1":
                return {"activation": None, "reason": "meta_research_activation_required"}
            approval = session.scalar(
                select(MetaResearchApproval).where(MetaResearchApproval.manifest_id == row.id)
            )
            approval, _, manifest, proposal = _activation(session, approval.id)
            try:
                self._approval_current(
                    session, approval, manifest, proposal, account, operator=operator
                )
            except MetaResearchError as error:
                self._authorize(session, request, write=False)
                return {"activation": None, "reason": error.code}
            record = self._record(session, row, operator).to_response()
            self._authorize(session, request, write=False)
            if not record["eligible_for_current_browser"]:
                return {"activation": None, "reason": "meta_research_authority_expired"}
            projection = {
                "id": record["id"],
                "manifest_sha256": record["manifest_sha256"],
                "audience": "operator_browser",
                "expires_at": record["approval"]["expires_at"],
                "profiles": [x["profile"] for x in record["manifest"]["task_profiles"]],
                "research_execution_available": True,
            }
            if manifest.discovery_ladder_profile is not None:
                projection["discovery_ladder_profile"] = (
                    manifest.discovery_ladder_profile.model_dump(mode="json")
                )
            if manifest.scientific_context_profiles is not None:
                projection["scientific_context_profiles"] = (
                    manifest.scientific_context_profiles.model_dump(mode="json")
                )
            if manifest.explore_collection_profile is not None:
                projection["explore_collection_profile"] = (
                    manifest.explore_collection_profile.model_dump(mode="json")
                )
            return {"activation": projection, "reason": None}

    def list_activations(self, request, *, limit=20, before=None):
        if type(limit) is not int or not 1 <= limit <= 21:
            raise MetaResearchError("meta_research_invalid_request")
        with Session(self.engine) as session, session.begin():
            operator = self._authorize(session, request, write=False)
            query = select(MetaResearchManifestRecord).where(
                MetaResearchManifestRecord.manifest["protocol"].astext
                == "polymath-meta-research-activation-v1"
            )
            if before is not None:
                anchor = session.get(MetaResearchManifestRecord, _id(before))
                if anchor is None:
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
            result = tuple(self._record(session, row, operator) for row in rows)
            self._authorize(session, request, write=False)
            return result

    def get_activation(self, request, activation_id):
        with Session(self.engine) as session, session.begin():
            operator = self._authorize(session, request, write=False)
            row = session.get(MetaResearchManifestRecord, _id(activation_id))
            if row is None:
                raise MetaResearchError("meta_research_not_found")
            result = self._record(session, row, operator)
            self._authorize(session, request, write=False)
            return result

    def revoke(self, request, activation_id):
        with Session(self.engine) as session, session.begin():
            operator = self._authorize(session, request, write=True)
            row = session.get(MetaResearchManifestRecord, _id(activation_id))
            if row is None:
                raise MetaResearchError("meta_research_not_found")
            account = _account_lock(session, row.account_id)
            operator = self._authorize(session, request, write=True)
            approval = session.scalar(
                select(MetaResearchApproval).where(MetaResearchApproval.manifest_id == row.id)
            )
            if session.get(MetaResearchRevocation, approval.id) is None:
                session.add(
                    MetaResearchRevocation(approval_id=approval.id, actor_id=operator.actor_id)
                )
            account.native_control_epoch += 1
            account.kill_switch = True
            session.flush()
            operator = self._authorize(session, request, write=True)
            return self._record(session, row, operator)

    def begin_admission(self, session, request, expected_activation_sha256):
        if self._public_manifest_requested(session, expected_activation_sha256):
            return self.public_service.begin_admission(session, request, expected_activation_sha256)
        operator = self._authorize(session, request, write=True)
        digest = _digest(expected_activation_sha256)
        row = session.scalar(
            select(MetaResearchManifestRecord)
            .join(MetaResearchApproval)
            .where(
                MetaResearchManifestRecord.manifest_sha256 == digest,
                MetaResearchApproval.operator_session_hash == operator.token_hash,
                MetaResearchApproval.browser_session_hash == operator.browser_session_hash,
            )
        )
        if row is None:
            raise MetaResearchError("meta_research_activation_required")
        approval = session.scalar(
            select(MetaResearchApproval).where(MetaResearchApproval.manifest_id == row.id)
        )
        return NativeResearchAdmission(
            request,
            approval.id,
            operator.token_hash,
            operator.browser_session_hash,
            digest,
            session.get_transaction(),
        )

    def lock_account_in_session(self, session, admission):
        if admission.audience == "public_sessions":
            return self.public_service.lock_account(session, admission)
        if admission.transaction is not session.get_transaction():
            raise MetaResearchError("meta_research_invalid_request")
        approval, _, manifest, proposal = _activation(session, admission.approval_id)
        account = _account_lock(session, manifest.account_id)
        operator = self._authorize(session, admission.request, write=True)
        self._approval_current(session, approval, manifest, proposal, account, operator=operator)
        admission.account = account
        return account

    def bind_run_in_session(self, session, admission, run, input_sha256):
        if admission.audience == "public_sessions":
            return self.public_service.bind_run(session, admission, run, input_sha256)
        if admission.transaction is not session.get_transaction() or admission.account is None:
            raise MetaResearchError("meta_research_invalid_request")
        approval, row, manifest, proposal = _activation(session, admission.approval_id)
        operator = self._authorize(session, admission.request, write=True)
        self._approval_current(
            session, approval, manifest, proposal, admission.account, operator=operator
        )
        from polymath.meta_research_contracts import (
            select_profile_with_explore,
            task_profile_sha256,
        )
        from polymath.scientific_context_store import load_scientific_intent

        discovery = load_discovery_intent(session, run)
        intent = load_scientific_intent(session, run)
        explore = load_explore_collection_intent(session, run)
        try:
            profile = select_profile_with_explore(
                manifest,
                run.task_kind,
                run.mode,
                discovery=discovery,
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
        owner = session.get(ResearchRunAccess, run.id)
        if owner is None or owner.session_hash != admission.browser_session_hash:
            raise MetaResearchError("meta_research_activation_required")
        if _digest(input_sha256) != native_run_input_sha256(session, run):
            raise MetaResearchError("meta_research_input_changed")
        if (
            session.get(NativeResearchRunBinding, run.id) is not None
            or session.get(BudgetRunLimit, (row.account_id, run.id)) is not None
        ):
            raise MetaResearchError("meta_research_account_conflict")
        binding = NativeResearchRunBinding(
            run_id=run.id,
            approval_id=approval.id,
            manifest_id=row.id,
            manifest_sha256=row.manifest_sha256,
            account_id=row.account_id,
            browser_session_hash=admission.browser_session_hash,
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
        operator = self._authorize(session, admission.request, write=True)
        self._approval_current(
            session, approval, manifest, proposal, admission.account, operator=operator
        )
        return binding

    def load_original_input(self, lease):
        with Session(self.engine) as session, session.begin():
            run = _owned(session, lease)
            return _original_input(session, run.id, check_input=True)

    def resume_in_session(
        self, session, request, run_id, expected_lease_token, expected_activation_sha256
    ):
        if self._public_manifest_requested(session, expected_activation_sha256):
            return self.public_service.resume(
                session, request, run_id, expected_lease_token, expected_activation_sha256
            )
        operator = self._authorize(session, request, write=True)
        value = _original_input(session, _id(run_id), check_input=True)
        if value.activation_sha256 != _digest(expected_activation_sha256):
            raise MetaResearchError("meta_research_manifest_changed")
        approval, _, manifest, proposal = _activation(session, value.approval_id)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": QUEUE_LOCK_KEY})
        account = _account_lock(session, value.account_id)
        run = _fresh_locked_run(session, value.run_id)
        value = _original_input(session, run.id, check_input=True)
        operator = self._authorize(session, request, write=True)
        self._approval_current(session, approval, manifest, proposal, account, operator=operator)
        if (
            operator.token_hash != approval.operator_session_hash
            or operator.browser_session_hash != approval.browser_session_hash
            or type(expected_lease_token) is not int
            or run.lease_token != expected_lease_token
            or run.status != "interrupted"
        ):
            raise MetaResearchError("meta_research_authority_expired")
        if (
            run.external_request_in_flight
            or session.scalar(
                select(ResearchExternalRequest.run_id)
                .where(
                    ResearchExternalRequest.run_id == run.id,
                    ResearchExternalRequest.status == "prepared",
                )
                .limit(1)
            )
            is not None
        ):
            raise MetaResearchError("meta_research_outcome_unknown")
        own = session.scalar(
            select(func.count())
            .select_from(ResearchRun)
            .join(ResearchRunAccess)
            .where(
                ResearchRunAccess.session_hash == approval.browser_session_hash,
                ResearchRun.id != run.id,
                ResearchRun.status.in_(ACTIVE_STATES),
            )
        )
        total = session.scalar(
            select(func.count())
            .select_from(ResearchRun)
            .where(ResearchRun.id != run.id, ResearchRun.status.in_(ACTIVE_STATES))
        )
        if own >= 1 or total >= 100:
            raise _PrivateHTTPException(429, "active_run_limit" if own else "research_queue_full")
        run.status = "queued"
        run.interruption_reason = None
        _clear_lease(run)
        run.finished_at = None
        _event(
            session,
            run,
            f"native_resume:{run.lease_token}",
            "resumed",
            "Original approved native research queued for explicit resume",
        )
        session.flush()
        operator = self._authorize(session, request, write=True)
        self._approval_current(session, approval, manifest, proposal, account, operator=operator)
        return run

    def finalize_known_call(self, run_id, request_id):
        from polymath.meta_research_dispatch import finalize_known_call

        return finalize_known_call(self.engine, _id(run_id), request_id)
