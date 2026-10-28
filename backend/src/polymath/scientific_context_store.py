"""Pinned scientific inputs and historical reads, separate from live paid authority.

Callers retain admission locks, quotas, approved-profile and full input preflight
checks. Historical reads validate original immutable bindings without consulting
a current login, model, profile or finding head.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import func, select

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.artifact_models import ArtifactRequest, GeneratedArtifact
from polymath.assessment_eligibility import EligibilityError, require_current_supported
from polymath.assessment_models import AssessmentRequest, FindingHead
from polymath.discovery_models import DiscoveryIntent as DiscoveryIntentRow
from polymath.evidence_models import EvidenceResult
from polymath.models import ResearchRun
from polymath.research.models import MemoryReadReference
from polymath.scientific_context_contracts import (
    ArtifactScientificContext,
    ArtifactScientificContextBinding,
    ArtifactScientificContextIntent,
    AskOverviewIntent,
    AskReassessmentOverviewIntent,
    bind_scientific_context,
    canonical_scientific_context_json,
    validate_result_overview,
)
from polymath.scientific_context_models import (
    ArtifactContextBinding,
    AskOverviewIntentRow,
    AskReassessmentOverviewIntentRow,
)


class ContextStoreError(ValueError):
    """Static, private errors only; no source/actor/query text."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _id(value):
    if isinstance(value, uuid.UUID):
        return value
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value:
            raise ValueError
        return parsed
    except (ValueError, AttributeError, TypeError):
        raise ContextStoreError("scientific_context_not_available") from None


def _typed(cls, value):
    try:
        return cls.model_validate_json(
            canonical(value.model_dump(mode="json") if isinstance(value, cls) else value)
        )
    except (ValueError, TypeError, AttributeError) as error:
        details = error.errors() if hasattr(error, "errors") else ()
        if any(
            str(item.get("ctx", {}).get("error")) == "artifact_context_too_large"
            for item in details
        ):
            raise ContextStoreError("artifact_context_too_large") from None
        raise ContextStoreError("scientific_context_integrity_failed") from None


def _builder_error(error):
    code = {
        "artifact_context_too_large": "artifact_context_too_large",
        "artifact_context_not_available": "scientific_context_not_available",
        "artifact_context_integrity_failed": "scientific_context_integrity_failed",
        "artifact_context_changed": "scientific_context_admission_changed",
    }.get(getattr(error, "code", None), "scientific_context_integrity_failed")
    return ContextStoreError(code)


def _owner(session, owner_hash, run_id, *, live=False):
    """Original immutable ownership is distinct from current admission authority."""
    run = session.scalar(
        select(ResearchRun)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == ResearchRun.id)
        .join(BrowserSession, BrowserSession.token_hash == ResearchRunAccess.session_hash)
        .where(
            ResearchRun.id == _id(run_id),
            ResearchRunAccess.session_hash == owner_hash,
        )
        .where(BrowserSession.expires_at > func.clock_timestamp() if live else True)
        .execution_options(populate_existing=True)
    )
    if run is None:
        raise ContextStoreError("scientific_context_not_available")
    return run


def _fresh(run):
    if run.status != "queued" or run.lease_token != 0:
        raise ContextStoreError("scientific_context_admission_closed")


def _source(session, owner_hash, identifier):
    row = session.get(EvidenceResult, _id(identifier))
    if row is None:
        raise ContextStoreError("scientific_context_not_available")
    _owner(session, owner_hash, row.run_id)
    if digest(canonical(row.payload)) != row.content_sha256:
        raise ContextStoreError("scientific_context_integrity_failed")
    return row


def _intent(row, cls):
    value = _typed(cls, row.intent)
    encoded = canonical(value.model_dump(mode="json"))
    if (row.protocol, row.canonical_intent, row.intent_sha256) != (
        value.protocol,
        encoded,
        digest(encoded),
    ):
        raise ContextStoreError("scientific_context_integrity_failed")
    return value


def _artifact_loaded(session, owner_hash, run, row):
    request = session.get(ArtifactRequest, run.id)
    context = _typed(ArtifactScientificContext, row.context)
    encoded = canonical_scientific_context_json(context)
    ref = context.source_reference
    source = _source(session, owner_hash, row.source_result_id)
    if (
        request is None
        or (
            row.protocol,
            row.canonical_context,
            row.context_sha256,
            row.source_result_id,
            row.source_run_id,
            row.source_sha256,
        )
        != (
            context.protocol,
            encoded,
            digest(encoded),
            request.source_result_id,
            request.source_run_id,
            request.source_sha256,
        )
        or (ref.result_id, ref.run_id, ref.content_sha256)
        != (str(row.source_result_id), str(row.source_run_id), row.source_sha256)
        or request.kind != run.task_kind
    ):
        raise ContextStoreError("scientific_context_integrity_failed")
    if (
        source is None
        or source.run_id != row.source_run_id
        or source.content_sha256 != row.source_sha256
        or digest(canonical(source.payload)) != source.content_sha256
        or context.ancestry_nodes[0].finding.model_dump(mode="json")
        != source.payload["result"]["finding"]
    ):
        raise ContextStoreError("scientific_context_integrity_failed")
    if context.prior_formalisation is None:
        if request.formalisation_id is not None:
            raise ContextStoreError("scientific_context_integrity_failed")
    else:
        prior = context.prior_formalisation
        artifact = session.get(GeneratedArtifact, _id(prior.artifact_id))
        if artifact is None or (artifact.id, artifact.payload_sha256, artifact.basis_sha256) != (
            request.formalisation_id,
            prior.payload_sha256,
            prior.basis_sha256,
        ):
            raise ContextStoreError("scientific_context_integrity_failed")
        prior_run = _owner(session, owner_hash, artifact.run_id)
        if (
            artifact.kind != "formalise"
            or artifact.status != "ready"
            or prior_run.status != "succeeded"
            or artifact.basis.get("reference") != ref.model_dump(mode="json")
        ):
            raise ContextStoreError("scientific_context_integrity_failed")
        if artifact.payload_sha256 != request.formalisation_sha256:
            raise ContextStoreError("scientific_context_integrity_failed")
        if (
            digest(canonical(artifact.payload)) != artifact.payload_sha256
            or digest(canonical(artifact.basis)) != artifact.basis_sha256
        ):
            raise ContextStoreError("scientific_context_integrity_failed")
        original = artifact.basis.get("scientific_context")
        if prior.context_sha256 != (original["context_sha256"] if original else None):
            raise ContextStoreError("scientific_context_integrity_failed")
    return ArtifactScientificContextBinding(context=context, context_sha256=row.context_sha256)


def load_pinned_artifact_context(session, *, owner_hash, run_id):
    """Only saved sidecar/request bytes. Never rebuild ancestry/current heads."""
    run = _owner(session, owner_hash, run_id)
    row = session.get(ArtifactContextBinding, run.id)
    return _artifact_loaded(session, owner_hash, run, row) if row is not None else None


def bind_artifact_context(session, *, run, owner_hash, intent, builder=None):
    """Within enqueue: actual caller profile/basis/prompt preflight remains required."""
    intent = _typed(ArtifactScientificContextIntent, intent)
    run = _owner(session, owner_hash, run.id, live=True)
    prior = session.get(ArtifactContextBinding, run.id)
    if prior is not None:
        return _artifact_loaded(session, owner_hash, run, prior)
    _fresh(run)
    request = session.get(ArtifactRequest, run.id)
    if request is None or run.task_kind != request.kind:
        raise ContextStoreError("scientific_context_not_available")
    if builder is None:
        from polymath.scientific_context_builder import build_artifact_scientific_context

        builder = build_artifact_scientific_context
    from polymath.scientific_context_builder import ScientificContextError

    try:
        context = builder(
            session,
            owner_hash=owner_hash,
            source_result_id=request.source_result_id,
            formalisation_id=request.formalisation_id,
        )
    except ScientificContextError as error:
        raise _builder_error(error) from None
    context = _typed(ArtifactScientificContext, context)
    binding = bind_scientific_context(context)
    encoded = canonical_scientific_context_json(context)
    if len(encoded.encode("utf-8")) > 262144:
        raise ContextStoreError("artifact_context_too_large")
    row = ArtifactContextBinding(
        run_id=run.id,
        protocol=intent.protocol,
        source_result_id=request.source_result_id,
        source_run_id=request.source_run_id,
        source_sha256=request.source_sha256,
        context=context.model_dump(mode="json"),
        canonical_context=encoded,
        context_sha256=binding.context_sha256,
    )
    session.add(row)
    session.flush()
    _artifact_loaded(session, owner_hash, run, row)
    _fresh(_owner(session, owner_hash, run.id, live=True))
    try:
        require_current_supported(session, owner_hash, request.source_result_id)
    except EligibilityError:
        raise ContextStoreError("scientific_context_admission_changed") from None
    head = session.get(
        FindingHead, _id(context.source_revision.root_reference.result_id), populate_existing=True
    )
    revision = head.revision if head is not None else 0
    if revision != context.source_revision.revision:
        raise ContextStoreError("scientific_context_admission_changed")
    return binding


def load_ask_overview_intent(session, *, owner_hash, run_id):
    run = _owner(session, owner_hash, run_id)
    row = session.get(AskOverviewIntentRow, run.id)
    return _intent(row, AskOverviewIntent) if row is not None else None


def bind_ask_overview_intent(session, *, run, owner_hash, intent):
    intent = _typed(AskOverviewIntent, intent)
    run = _owner(session, owner_hash, run.id, live=True)
    prior = session.get(AskOverviewIntentRow, run.id)
    if prior is not None:
        return _intent(prior, AskOverviewIntent)
    _fresh(run)
    if (
        (run.mode, run.task_kind) != ("ask", "research")
        or (session.get(DiscoveryIntentRow, run.id) is not None)
        or session.scalar(select(EvidenceResult.id).where(EvidenceResult.run_id == run.id).limit(1))
    ):
        raise ContextStoreError("ask_overview_wrong_run")
    encoded = canonical(intent.model_dump(mode="json"))
    session.add(
        AskOverviewIntentRow(
            run_id=run.id,
            protocol=intent.protocol,
            mode=run.mode,
            task_kind=run.task_kind,
            intent=intent.model_dump(mode="json"),
            canonical_intent=encoded,
            intent_sha256=digest(encoded),
        )
    )
    session.flush()
    _fresh(_owner(session, owner_hash, run.id, live=True))
    return intent


def _validated_prior_basis(session, owner_hash, request, source):
    from polymath.scientific_context_builder import (
        ScientificContextError,
        validated_owned_result_packet,
    )

    try:
        packet = validated_owned_result_packet(session, owner_hash=owner_hash, result_id=source.id)
    except ScientificContextError as error:
        raise _builder_error(error) from None
    reference = MemoryReadReference(
        result_id=str(source.id), run_id=str(source.run_id), content_sha256=source.content_sha256
    )
    expected = {
        "prior_reference": reference.model_dump(mode="json"),
        "mode": packet.mode,
        "prior_finding": packet.finding.model_dump(mode="json"),
        "prior_quotes": [quote.model_dump(mode="json") for quote in packet.quotes],
        "field_selections": [
            selection.model_dump(mode="json") for selection in packet.field_selections
        ],
    }
    if (
        request.source_result_id != source.id
        or request.source_sha256 != source.content_sha256
        or digest(canonical(request.basis)) != request.basis_sha256
        or any(request.basis.get(name) != value for name, value in expected.items())
    ):
        raise ContextStoreError("scientific_context_integrity_failed")
    return packet


def _assessment_loaded(session, owner_hash, run, row):
    request = session.get(AssessmentRequest, run.id)
    value = _intent(row, AskReassessmentOverviewIntent)
    source = _source(session, owner_hash, row.source_result_id)
    raw = source.payload["result"]
    validate_result_overview(raw)
    overview = raw.get("overview_binding")
    if request is None or (
        row.source_result_id,
        row.source_run_id,
        row.source_sha256,
        row.basis_sha256,
    ) != (request.source_result_id, source.run_id, request.source_sha256, request.basis_sha256):
        raise ContextStoreError("scientific_context_integrity_failed")
    _validated_prior_basis(session, owner_hash, request, source)
    if (
        digest(canonical(request.basis)) != row.basis_sha256
        or (row.prior_overview_binding != overview)
        or request.basis.get("prior_overview_binding") != overview
    ):
        raise ContextStoreError("scientific_context_integrity_failed")
    if overview is not None:
        encoded = canonical(overview)
        if (row.prior_overview_canonical, row.prior_overview_sha256) != (encoded, digest(encoded)):
            raise ContextStoreError("scientific_context_integrity_failed")
    elif any(
        value is not None for value in (row.prior_overview_canonical, row.prior_overview_sha256)
    ):
        raise ContextStoreError("scientific_context_integrity_failed")
    return value


def load_assessment_overview_intent(session, *, owner_hash, run_id):
    run = _owner(session, owner_hash, run_id)
    row = session.get(AskReassessmentOverviewIntentRow, run.id)
    return _assessment_loaded(session, owner_hash, run, row) if row is not None else None


def bind_assessment_overview_intent(session, *, run, owner_hash, intent=None):
    """Bound prior answers require the explicit variant before the entire call."""
    run = _owner(session, owner_hash, run.id, live=True)
    prior = session.get(AskReassessmentOverviewIntentRow, run.id)
    if prior is not None:
        if intent is None:
            raise ContextStoreError("ask_reassessment_overview_required")
        _typed(AskReassessmentOverviewIntent, intent)
        return _assessment_loaded(session, owner_hash, run, prior)
    _fresh(run)
    request = session.get(AssessmentRequest, run.id)
    if request is None:
        raise ContextStoreError("scientific_context_not_available")
    source = _source(session, owner_hash, request.source_result_id)
    packet = _validated_prior_basis(session, owner_hash, request, source)
    raw = source.payload["result"]
    validate_result_overview(raw)
    overview = raw.get("overview_binding")
    if intent is None:
        if overview is not None:
            raise ContextStoreError("ask_reassessment_overview_required")
        return None
    value = _typed(AskReassessmentOverviewIntent, intent)
    if (run.mode, run.task_kind, packet.status) != ("ask", "reassess_finding", "answer"):
        raise ContextStoreError("ask_overview_wrong_run")
    if request.basis.get("prior_overview_binding") != overview or (
        "prior_overview_binding" in request.basis and overview is None
    ):
        raise ContextStoreError("scientific_context_integrity_failed")
    encoded = canonical(value.model_dump(mode="json"))
    prior_encoded = canonical(overview) if overview is not None else None
    row = AskReassessmentOverviewIntentRow(
        run_id=run.id,
        protocol=value.protocol,
        mode=run.mode,
        task_kind=run.task_kind,
        intent=value.model_dump(mode="json"),
        canonical_intent=encoded,
        intent_sha256=digest(encoded),
        source_result_id=source.id,
        source_run_id=source.run_id,
        source_sha256=source.content_sha256,
        basis_sha256=request.basis_sha256,
        prior_overview_binding=overview,
        prior_overview_canonical=prior_encoded,
        prior_overview_sha256=digest(prior_encoded) if prior_encoded else None,
    )
    session.add(row)
    session.flush()
    _fresh(_owner(session, owner_hash, run.id, live=True))
    return _assessment_loaded(session, owner_hash, run, row)


def validate_result_overview_for_run(session, *, owner_hash, run_id, result):
    """Additional check AFTER existing exact quote/evidence validation; no save."""
    run = session.get(ResearchRun, _id(run_id))
    if run is None:
        raise ContextStoreError("scientific_context_not_available")
    cls = AskOverviewIntentRow if run.task_kind == "research" else AskReassessmentOverviewIntentRow
    row = session.get(cls, run.id)
    if row is not None:
        _owner(session, owner_hash, run.id)
        (
            _intent(row, AskOverviewIntent)
            if cls is AskOverviewIntentRow
            else _assessment_loaded(session, owner_hash, run, row)
        )
    try:
        validate_result_overview(
            result.model_dump(mode="json") if hasattr(result, "model_dump") else result,
            expected=row is not None,
        )
    except ValueError as error:
        raise ContextStoreError(str(error)) from None


def load_scientific_intent(session, run):
    """Read original protocol metadata without live authority or scientific rebuild.

    This is an internal input-hash/profile reconstruction helper, not an HTTP
    authentication boundary. It makes no model, key, head or builder query.
    """
    rows = tuple(
        row
        for cls in (ArtifactContextBinding, AskOverviewIntentRow, AskReassessmentOverviewIntentRow)
        if (row := session.get(cls, run.id)) is not None
    )
    if not rows:
        return None
    if len(rows) != 1 or session.get(ResearchRunAccess, run.id) is None:
        raise ContextStoreError("scientific_context_integrity_failed")
    row = rows[0]
    if isinstance(row, AskOverviewIntentRow):
        if (run.mode, run.task_kind, row.mode, row.task_kind) != (
            "ask",
            "research",
            "ask",
            "research",
        ):
            raise ContextStoreError("scientific_context_integrity_failed")
        return _intent(row, AskOverviewIntent)
    if isinstance(row, AskReassessmentOverviewIntentRow):
        request = session.get(AssessmentRequest, run.id)
        if (
            (run.mode, run.task_kind, row.mode, row.task_kind)
            != ("ask", "reassess_finding", "ask", "reassess_finding")
            or request is None
            or (row.source_result_id, row.source_sha256, row.basis_sha256)
            != (request.source_result_id, request.source_sha256, request.basis_sha256)
        ):
            raise ContextStoreError("scientific_context_integrity_failed")
        if digest(canonical(request.basis)) != row.basis_sha256:
            raise ContextStoreError("scientific_context_integrity_failed")
        overview = request.basis.get("prior_overview_binding")
        if overview != row.prior_overview_binding:
            raise ContextStoreError("scientific_context_integrity_failed")
        if overview is None:
            if row.prior_overview_canonical is not None or row.prior_overview_sha256 is not None:
                raise ContextStoreError("scientific_context_integrity_failed")
        elif (row.prior_overview_canonical, row.prior_overview_sha256) != (
            canonical(overview),
            digest(canonical(overview)),
        ):
            raise ContextStoreError("scientific_context_integrity_failed")
        return _intent(row, AskReassessmentOverviewIntent)
    request = session.get(ArtifactRequest, run.id)
    if (
        request is None
        or run.task_kind != request.kind
        or (row.source_result_id, row.source_run_id, row.source_sha256)
        != (request.source_result_id, request.source_run_id, request.source_sha256)
    ):
        raise ContextStoreError("scientific_context_integrity_failed")
    context = _typed(ArtifactScientificContext, row.context)
    if (row.protocol, row.canonical_context, row.context_sha256) != (
        context.protocol,
        canonical_scientific_context_json(context),
        digest(canonical_scientific_context_json(context)),
    ):
        raise ContextStoreError("scientific_context_integrity_failed")
    return ArtifactScientificContextIntent(protocol=row.protocol)
