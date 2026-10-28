"""Immutable initial ladder intent, written only with original scientific admission."""

import hashlib
import json

from sqlalchemy.orm import Session

from polymath.discovery_contracts import DiscoveryIntent
from polymath.discovery_models import DiscoveryIntent as DiscoveryIntentRecord
from polymath.meta_research_errors import MetaResearchError
from polymath.models import ResearchRun


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
    row = session.get(DiscoveryIntentRecord, run.id)
    if row is None:
        return None
    try:
        intent = DiscoveryIntent(
            protocol=row.protocol, max_stages=row.max_stages, max_depth=row.max_depth
        )
        if (
            run.task_kind != "research"
            or run.mode != "surprise"
            or row.config_sha256 != _intent_sha256(intent)
        ):
            raise ValueError
        return intent
    except (ValueError, TypeError, AttributeError):
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
    return intent
