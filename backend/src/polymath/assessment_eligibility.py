"""Current finding eligibility; immutable historical findings remain inspectable.

The query helpers never lock a head. Publication mutation grants use the explicit
locking helper before the publication job lock, while assessment commits lock the
same head before their research run. Callers authorize ownership before using the
internal two-argument helpers; the optional owner form checks ownership itself.
"""

import uuid

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polymath.access_models import ResearchRunAccess
from polymath.assessment_models import (
    AssessmentInputSource,
    AssessmentRequest,
    FindingAssessment,
    FindingHead,
)
from polymath.discovery_models import StageCommit
from polymath.evidence_models import EvidenceQuote, EvidenceResult
from polymath.models import ResearchRun


class EligibilityError(ValueError):
    """Static private failure, with no foreign finding existence disclosure."""


def completed_result_condition(result=EvidenceResult):
    """Completed scientific records, including exact independently committed stages.

    This is a historical/read predicate. Fresh use additionally requires current
    supported-head and stage structural checks. A succeeded ladder run cannot
    make an uncommitted positive row eligible.
    """
    from polymath.stage_owners import no_collection_intent_condition

    # A missing registry cannot make a concrete collection ordinary research.
    ladder = ~no_collection_intent_condition(result)
    stage = exists(
        select(StageCommit.id)
        .where(
            StageCommit.result_id == result.id,
            StageCommit.run_id == result.run_id,
            StageCommit.content_sha256 == result.content_sha256,
        )
        .correlate(result)
    )
    completed = exists(
        select(ResearchRun.id)
        .where(
            ResearchRun.id == result.run_id,
            or_(
                and_(
                    ResearchRun.task_kind == "research",
                    or_(
                        and_(ResearchRun.status == "succeeded", ~ladder),
                        and_(ladder, stage),
                    ),
                ),
                and_(
                    ResearchRun.task_kind == "reassess_finding", ResearchRun.status == "succeeded"
                ),
            ),
        )
        .correlate(result)
    )
    return and_(result.status.in_(("bridge", "answer")), completed)


def _stage_current_supported(session, result):
    from polymath.discovery_store import stage_current_supported

    return stage_current_supported(session, result)


def current_supported_condition(result=EvidenceResult):
    """SQL candidate predicate; require_current_supported adds stage integrity."""
    head_current = exists(
        select(FindingHead.root_result_id)
        .where(
            FindingHead.root_result_id == result.id,
            FindingHead.current_result_id == result.id,
            FindingHead.state == "supported",
        )
        .correlate(result)
    )
    no_root_head = ~exists(
        select(FindingHead.root_result_id)
        .where(FindingHead.root_result_id == result.id)
        .correlate(result)
    )
    applied_candidate = exists(
        select(FindingAssessment.id)
        .join(AssessmentRequest, AssessmentRequest.run_id == FindingAssessment.run_id)
        .join(FindingHead, FindingHead.root_result_id == AssessmentRequest.root_result_id)
        .where(
            FindingAssessment.candidate_result_id == result.id,
            FindingAssessment.applied.is_(True),
            FindingHead.current_result_id == result.id,
            FindingHead.state == "supported",
        )
        .correlate(result)
    )
    eligible_run = exists(
        select(ResearchRun.id)
        .where(
            ResearchRun.id == result.run_id,
            or_(
                and_(ResearchRun.task_kind == "research", or_(no_root_head, head_current)),
                and_(ResearchRun.task_kind == "reassess_finding", applied_candidate),
            ),
        )
        .correlate(result)
    )
    return and_(completed_result_condition(result), eligible_run)


def _arguments(owner_or_result, result_id):
    try:
        return (
            (None, uuid.UUID(str(owner_or_result)))
            if result_id is None
            else (owner_or_result, uuid.UUID(str(result_id)))
        )
    except (ValueError, TypeError, AttributeError):
        raise EligibilityError("finding_not_available") from None


def _result(session: Session, owner, result_id):
    query = select(EvidenceResult).where(EvidenceResult.id == result_id)
    if owner is not None:
        query = query.join(
            ResearchRunAccess, ResearchRunAccess.run_id == EvidenceResult.run_id
        ).where(ResearchRunAccess.session_hash == owner)
    result = session.scalar(query)
    if result is None or result.status not in ("bridge", "answer"):
        raise EligibilityError("finding_not_available")
    return result


def _root(session: Session, result: EvidenceResult) -> uuid.UUID:
    run = session.get(ResearchRun, result.run_id)
    if run is None or run.task_kind not in ("research", "reassess_finding"):
        raise EligibilityError("finding_not_available")
    if run.task_kind == "research":
        return result.id
    request = session.get(AssessmentRequest, result.run_id)
    if request is None:
        raise EligibilityError("finding_not_available")
    return request.root_result_id


def result_assessment_state(session: Session, owner_or_result, result_id=None) -> dict:
    owner, result_id = _arguments(owner_or_result, result_id)
    result = _result(session, owner, result_id)
    root_id = _root(session, result)
    head = session.get(FindingHead, root_id, populate_existing=True)
    current_id = root_id if head is None else head.current_result_id
    state = "supported" if head is None else head.state
    revision = 0 if head is None else head.revision
    eligible = (
        session.scalar(
            select(EvidenceResult.id).where(
                EvidenceResult.id == result_id, current_supported_condition()
            )
        )
        is not None
    )
    if eligible:
        eligible = _stage_current_supported(session, result)
    current = result.id == current_id
    return {
        "root_result_id": str(root_id),
        "current_result_id": str(current_id),
        "state": state,
        "revision": revision,
        "last_assessment_id": (
            str(head.last_assessment_id) if head and head.last_assessment_id else None
        ),
        "is_current": current,
        "is_supported": eligible,
        "is_historical": not current or state == "unsupported",
    }


def require_current_supported(session: Session, owner_or_result, result_id=None) -> None:
    from polymath.evidence import _canonical, _hash

    owner, result_id = _arguments(owner_or_result, result_id)
    result = _result(session, owner, result_id)
    if _hash(_canonical(result.payload)) != result.content_sha256:
        raise EligibilityError("finding_not_available")
    if session.scalar(
        select(EvidenceResult.id).where(
            EvidenceResult.id == result_id, current_supported_condition()
        )
    ) is None or not _stage_current_supported(session, result):
        raise EligibilityError("finding_not_available")


def lock_current_source(session: Session, owner_or_result, result_id=None) -> FindingHead:
    """Upsert then lock even a legacy head, serializing first negative assessment."""
    owner, result_id = _arguments(owner_or_result, result_id)
    result = _result(session, owner, result_id)
    require_current_supported(session, owner_or_result, result_id if owner is not None else None)
    root_id = _root(session, result)
    session.execute(
        insert(FindingHead)
        .values(root_result_id=root_id, current_result_id=root_id, state="supported", revision=0)
        .on_conflict_do_nothing(index_elements=[FindingHead.root_result_id])
    )
    head = session.scalar(
        select(FindingHead)
        .where(FindingHead.root_result_id == root_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    require_current_supported(session, owner_or_result, result_id if owner is not None else None)
    return head


def assessment_version_status(session: Session, root_result_id, version_id) -> str:
    """Contextual pending status for an already-owned watch's finding root."""
    try:
        root_id, version_id = uuid.UUID(str(root_result_id)), uuid.UUID(str(version_id))
    except (ValueError, TypeError, AttributeError):
        raise EligibilityError("finding_not_available") from None
    head = session.get(FindingHead, root_id, populate_existing=True)
    current_id = head.current_result_id if head else root_id
    if (
        session.scalar(
            select(EvidenceQuote.result_id)
            .where(
                EvidenceQuote.result_id.in_((root_id, current_id)),
                EvidenceQuote.version_id == version_id,
            )
            .limit(1)
        )
        is not None
    ):
        return "already_cited"
    if (
        session.scalar(
            select(AssessmentInputSource.run_id)
            .join(AssessmentRequest, AssessmentRequest.run_id == AssessmentInputSource.run_id)
            .join(FindingAssessment, FindingAssessment.run_id == AssessmentRequest.run_id)
            .where(
                AssessmentRequest.root_result_id == root_id,
                AssessmentInputSource.role == "new",
                AssessmentInputSource.version_id == version_id,
                FindingAssessment.applied.is_(True),
            )
            .limit(1)
        )
        is not None
    ):
        return "assessed"
    return "unassessed"
