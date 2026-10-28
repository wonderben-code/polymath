"""Immutable initial ladder intent, written only with original scientific admission."""

import hashlib
import json

from sqlalchemy.orm import Session

from polymath.discovery_contracts import DiscoveryIntent
from polymath.discovery_models import DiscoveryIntent as DiscoveryIntentRecord
from polymath.explore_collection_models import ResearchStageOwner
from polymath.meta_research_errors import MetaResearchError
from polymath.models import ResearchRun
from polymath.stage_owners import StageOwnerError, load_stage_owner


def _intent_sha256(intent: DiscoveryIntent) -> str:
    return hashlib.sha256(
        json.dumps(
            intent.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def load_discovery_intent(session: Session, run: ResearchRun) -> DiscoveryIntent | None:
    try:
        owner = load_stage_owner(session, run)
        return owner.intent if owner is not None and owner.mode == "surprise" else None
    except StageOwnerError:
        raise MetaResearchError("meta_research_storage_unavailable") from None


def bind_discovery_intent(
    session: Session, run: ResearchRun, intent: DiscoveryIntent | None
) -> DiscoveryIntent | None:
    """Initial transaction only; never attach or expand a running job's permission."""
    if intent is None:
        return None
    try:
        intent = DiscoveryIntent.model_validate_json(intent.model_dump_json())
    except (ValueError, TypeError, AttributeError):
        raise MetaResearchError("meta_research_invalid_request") from None
    if (
        run.task_kind != "research"
        or run.mode != "surprise"
        or run.status != "queued"
        or run.lease_token != 0
    ):
        raise MetaResearchError("meta_research_input_changed")
    prior = load_discovery_intent(session, run)
    if prior is not None:
        if prior != intent:
            raise MetaResearchError("meta_research_input_changed")
        return prior
    session.add(
        DiscoveryIntentRecord(
            run_id=run.id,
            protocol=intent.protocol,
            max_stages=intent.max_stages,
            max_depth=intent.max_depth,
            config_sha256=_intent_sha256(intent),
        )
    )
    session.flush()
    session.add(
        ResearchStageOwner(
            run_id=run.id,
            mode=run.mode,
            task_kind=run.task_kind,
            protocol=intent.protocol,
            config_sha256=_intent_sha256(intent),
            surprise_intent_run_id=run.id,
        )
    )
    session.flush()
    return intent
