"""Exact stage-protocol ownership, without login/head/profile/financial authority."""

import hashlib
import json
import uuid
from dataclasses import dataclass

from sqlalchemy import select

from polymath.discovery_contracts import DiscoveryIntent
from polymath.discovery_models import DiscoveryIntent as SurpriseIntentRow
from polymath.explore_collection_contracts import ExploreCollectionIntent
from polymath.explore_collection_models import (
    ExploreCollectionIntentRecord,
    ResearchStageOwner,
)


class StageOwnerError(ValueError):
    def __init__(self, code="stage_owner_integrity_failed"):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class StageOwnerBinding:
    run_id: uuid.UUID
    mode: str
    task_kind: str
    protocol: str
    config_sha256: str
    intent: DiscoveryIntent | ExploreCollectionIntent


def intent_sha256(intent):
    return hashlib.sha256(
        json.dumps(
            intent.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def validate_stage_owner_records(run, *, surprise, explore, owner):
    """Pure core shared by PG reader and no-DB restore/adversarial tests."""
    present = [value for value in (surprise, explore) if value is not None]
    if not present and owner is None:
        return None
    if len(present) != 1 or owner is None:
        raise StageOwnerError()
    concrete = present[0]
    try:
        intent = (
            DiscoveryIntent(
                protocol=surprise.protocol,
                max_stages=surprise.max_stages,
                max_depth=surprise.max_depth,
            )
            if surprise is not None
            else ExploreCollectionIntent(protocol=explore.protocol, max_bridges=explore.max_bridges)
        )
        expected_mode = "surprise" if surprise is not None else "explore"
        if (
            run.task_kind != "research"
            or run.mode != expected_mode
            or concrete.run_id != run.id
            or (owner.run_id, owner.mode, owner.task_kind, owner.protocol, owner.config_sha256)
            != (run.id, expected_mode, "research", intent.protocol, intent_sha256(intent))
            or concrete.config_sha256 != owner.config_sha256
            or owner.surprise_intent_run_id != (run.id if surprise is not None else None)
            or owner.explore_intent_run_id != (run.id if explore is not None else None)
        ):
            raise StageOwnerError()
        if explore is not None and (explore.mode, explore.task_kind) != ("explore", "research"):
            raise StageOwnerError()
        return StageOwnerBinding(
            run.id, run.mode, run.task_kind, intent.protocol, owner.config_sha256, intent
        )
    except (TypeError, ValueError, AttributeError) as error:
        if isinstance(error, StageOwnerError):
            raise
        raise StageOwnerError() from None


def load_stage_owner(session, run):
    return validate_stage_owner_records(
        run,
        surprise=session.get(SurpriseIntentRow, run.id),
        explore=session.get(ExploreCollectionIntentRecord, run.id),
        owner=session.get(ResearchStageOwner, run.id),
    )


def has_stage_intent(session, run_id):
    """Concrete intent presence, even if the registry is absent/corrupt."""
    return (
        session.get(SurpriseIntentRow, run_id) is not None
        or session.get(ExploreCollectionIntentRecord, run_id) is not None
    )


def no_collection_intent_condition(result):
    """Legacy-positive branch only; NEVER infer ordinary scope from missing owner.

    Exact committed-stage and current-support predicates remain separate. Both
    concrete intents suppress generic positives even after an incomplete restore.
    """
    return (
        ~select(SurpriseIntentRow.run_id)
        .where(SurpriseIntentRow.run_id == result.run_id)
        .correlate(result)
        .exists()
        & ~select(ExploreCollectionIntentRecord.run_id)
        .where(ExploreCollectionIntentRecord.run_id == result.run_id)
        .correlate(result)
        .exists()
    )
