"""Explicit immutable initial Explore intent, with one exact stage owner."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from polymath.explore_collection_contracts import ExploreCollectionIntent
from polymath.explore_collection_models import (
    ExploreCollectionIntentRecord,
    ResearchStageOwner,
)
from polymath.lineage_models import ResearchRunParent
from polymath.meta_research_errors import MetaResearchError
from polymath.models import ResearchRun
from polymath.stage_owners import StageOwnerError, intent_sha256, load_stage_owner


def load_explore_collection_intent(
    session: Session, run: ResearchRun
) -> ExploreCollectionIntent | None:
    """Historical metadata only: no login deadline, head, key or current profile."""
    try:
        owner = load_stage_owner(session, run)
        return owner.intent if owner is not None and owner.mode == "explore" else None
    except StageOwnerError:
        raise MetaResearchError("meta_research_storage_unavailable") from None


def bind_explore_collection_intent(
    session: Session, run: ResearchRun, intent: ExploreCollectionIntent | None
) -> ExploreCollectionIntent | None:
    """Call inside the original owned queue-admission transaction, before native binding."""
    if intent is None:
        return None
    try:
        intent = ExploreCollectionIntent.model_validate_json(intent.model_dump_json())
    except (ValueError, TypeError, AttributeError):
        raise MetaResearchError("meta_research_invalid_request") from None
    if (
        run.task_kind != "research"
        or run.mode != "explore"
        or run.status != "queued"
        or run.lease_token != 0
        or session.scalar(
            select(ResearchRunParent.run_id).where(ResearchRunParent.run_id == run.id).limit(1)
        )
        is not None
    ):
        raise MetaResearchError("meta_research_input_changed")
    prior = load_explore_collection_intent(session, run)
    if prior is not None:
        if prior != intent:
            raise MetaResearchError("meta_research_input_changed")
        return prior
    session.add(
        ExploreCollectionIntentRecord(
            run_id=run.id,
            mode=run.mode,
            task_kind=run.task_kind,
            protocol=intent.protocol,
            max_bridges=intent.max_bridges,
            config_sha256=intent_sha256(intent),
        )
    )
    session.flush()
    session.add(
        ResearchStageOwner(
            run_id=run.id,
            mode=run.mode,
            task_kind=run.task_kind,
            protocol=intent.protocol,
            config_sha256=intent_sha256(intent),
            explore_intent_run_id=run.id,
        )
    )
    session.flush()
    return intent
