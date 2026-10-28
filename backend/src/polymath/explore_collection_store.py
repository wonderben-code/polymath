"""Atomic model-ranked Explore collections over the shared scientific stage store."""

import hashlib
import json
import uuid

from sqlalchemy import select

from polymath.access_models import ResearchRunAccess
from polymath.discovery_models import StageCommit
from polymath.discovery_store import DiscoveryError, StageValidationError, _receipt
from polymath.explore_collection_contracts import (
    ExploreCollectionIntent,
    ExploreFinishPacket,
    ExploreRankingPacket,
    ExploreRankingReceipt,
    validate_explore_ranking,
    validate_explore_ranking_receipt,
)
from polymath.explore_collection_models import (
    ExploreCollectionFinish,
    ExploreCollectionStop,
    ExploreRankingMember,
    ExploreRankingRecord,
)
from polymath.models import ResearchExternalRequest, ResearchRun
from polymath.stage_owners import load_stage_owner


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _typed(cls, raw):
    try:
        return cls.model_validate_json(
            raw.model_dump_json() if isinstance(raw, cls) else canonical(raw)
        )
    except (ValueError, TypeError):
        raise DiscoveryError("discovery_storage_unavailable") from None


def _snapshot(row, field, cls=None):
    raw = getattr(row, field)
    encoded = canonical(raw)
    if (getattr(row, "canonical_" + field), getattr(row, field + "_sha256")) != (
        encoded,
        digest(encoded),
    ):
        raise DiscoveryError("discovery_storage_unavailable")
    return _typed(cls, raw) if cls is not None else raw


def validate_ranking_snapshot(row, *, intent, stage_rows, members, acknowledged_receipts=None):
    """Historical pinned coverage; fresh writer must pass ACTUAL server ack prefix.

    Merely repeating packet.visible_receipts is not authority to commit a rank.
    This helper never writes or builds that prefix from the latest durable list.
    """
    packet = _snapshot(row, "packet", ExploreRankingPacket)
    receipt = _snapshot(row, "receipt", ExploreRankingReceipt)
    try:
        validate_explore_ranking_receipt(packet, receipt, run_id=str(row.run_id))
        actual = tuple(_receipt(stage) for stage in stage_rows)
        validate_explore_ranking(packet, visible_receipts=actual, intent=intent)
        if acknowledged_receipts is not None:
            validate_explore_ranking(packet, visible_receipts=acknowledged_receipts, intent=intent)
    except (ValueError, DiscoveryError):
        raise DiscoveryError("discovery_storage_unavailable") from None
    if (
        str(row.id),
        row.model_request_id,
        row.action_ordinal,
        row.covered_through_ordinal,
        row.ranked_count,
        row.excluded_count,
    ) != (
        receipt.ranking_id,
        packet.model_request_id,
        packet.action_ordinal,
        len(actual) - 1 if actual else None,
        len(packet.ranked),
        len(packet.excluded),
    ):
        raise DiscoveryError("discovery_storage_unavailable")
    by_result = {
        item.result_id: (item, stage) for item, stage in zip(actual, stage_rows, strict=True)
    }
    expected = []
    for bucket, entries in (("ranked", packet.ranked), ("excluded", packet.excluded)):
        for position, entry in enumerate(entries):
            saved, stage = by_result[entry.reference.result_id]
            explanation = entry.rationale if bucket == "ranked" else entry.reason
            expected.append(
                (
                    bucket,
                    position,
                    saved.stage_id,
                    saved.result_id,
                    saved.result_sha256,
                    stage.receipt_sha256,
                    explanation,
                )
            )
    actual_members = [
        (
            member.bucket,
            member.position,
            str(member.stage_id),
            str(member.result_id),
            member.result_sha256,
            member.stage_receipt_sha256,
            member.explanation,
        )
        for member in members
    ]
    # Only group/position sorting is used to compare normalized rows; score is
    # absent and the model packet/receipt order is returned verbatim.
    if sorted(actual_members) != sorted(expected) or any(
        member.ranking_id != row.id or member.run_id != row.run_id for member in members
    ):
        raise DiscoveryError("discovery_storage_unavailable")
    return receipt


def validate_ranking_for_commit(packet, *, intent, acknowledged_receipts):
    """Fresh writer seam: mandatory trusted replay-visible ack list, never latest DB stages.

    The shared atomic writer ALSO validates the observed model action, live
    lease/owner, actual counters and exact replay key before this check/insert.
    """
    try:
        validate_explore_ranking(packet, visible_receipts=acknowledged_receipts, intent=intent)
    except ValueError:
        raise StageValidationError("explore_ranking_visibility_invalid") from None


def _owned(session, owner_hash, run_id):
    run = session.scalar(
        select(ResearchRun)
        .join(ResearchRunAccess, ResearchRunAccess.run_id == ResearchRun.id)
        .where(
            ResearchRun.id == uuid.UUID(str(run_id)), ResearchRunAccess.session_hash == owner_hash
        )
    )
    if run is None:
        raise DiscoveryError("explore_collection_not_found")
    owner = load_stage_owner(session, run)
    if owner is None or not isinstance(owner.intent, ExploreCollectionIntent):
        raise DiscoveryError("explore_collection_not_found")
    return run, owner


def _ranking(session, run, owner, row):
    if row is None or row.run_id != run.id:
        raise DiscoveryError("explore_collection_not_found")
    observed = session.get(ResearchExternalRequest, (run.id, row.model_request_id))
    if (
        observed is None
        or observed.status not in ("observed", "reconciled")
        or observed.observation is None
    ):
        raise DiscoveryError("discovery_storage_unavailable")
    query = select(StageCommit).where(StageCommit.run_id == run.id)
    # Never fetch later stages into historical ranking coverage or invent a
    # revised ordering after a new bridge, alias connector or reassessment.
    if row.covered_through_ordinal is None:
        stage_rows = ()
    else:
        stage_rows = tuple(
            session.scalars(
                query.where(StageCommit.ordinal <= row.covered_through_ordinal).order_by(
                    StageCommit.ordinal
                )
            ).all()
        )
    members = session.scalars(
        select(ExploreRankingMember).where(ExploreRankingMember.ranking_id == row.id)
    ).all()
    return validate_ranking_snapshot(
        row, intent=owner.intent, stage_rows=stage_rows, members=members
    )


def read_ranking(session, *, owner_hash, run_id, ranking_id):
    run, owner = _owned(session, owner_hash, run_id)
    return _ranking(
        session, run, owner, session.get(ExploreRankingRecord, uuid.UUID(str(ranking_id)))
    )


def read_finish(session, *, owner_hash, run_id):
    run, owner = _owned(session, owner_hash, run_id)
    finish = session.get(ExploreCollectionFinish, run.id)
    if finish is None:
        return None
    packet = _snapshot(finish, "packet", ExploreFinishPacket)
    row = session.get(ExploreRankingRecord, finish.ranking_id)
    ranking = _ranking(session, run, owner, row)
    if packet.ranking.model_dump(mode="json") != row.packet or (
        finish.model_request_id,
        finish.action_ordinal,
        finish.ranked_count,
    ) != (row.model_request_id, row.action_ordinal, row.ranked_count):
        raise DiscoveryError("discovery_storage_unavailable")
    selected = ranking.selected_reference
    if selected is None:
        expected = (None, None, None, None, None)
    else:
        stage_id = next(
            item.stage_id for item in packet.ranking.visible_receipts if item.reference == selected
        )
        expected = ("ranked", 0, stage_id, selected.result_id, selected.content_sha256)
    actual = (
        finish.selected_bucket,
        finish.selected_position,
        str(finish.selected_stage_id) if finish.selected_stage_id else None,
        str(finish.selected_result_id) if finish.selected_result_id else None,
        finish.selected_result_sha256,
    )
    if actual != expected:
        raise DiscoveryError("discovery_storage_unavailable")
    return {"ranking": ranking, "reason": packet.reason}


def read_stop(session, *, owner_hash, run_id):
    run, _ = _owned(session, owner_hash, run_id)
    stop = session.get(ExploreCollectionStop, run.id)
    if stop is None:
        return None
    payload = _snapshot(stop, "payload")
    if payload.get("status") != stop.status:
        raise DiscoveryError("discovery_storage_unavailable")
    if stop.finish_ranking_id is not None:
        finish = read_finish(session, owner_hash=owner_hash, run_id=run.id)
        if finish is None or finish["ranking"].ranking_id != str(stop.finish_ranking_id):
            raise DiscoveryError("discovery_storage_unavailable")
    elif stop.status == "completed":
        raise DiscoveryError("discovery_storage_unavailable")
    return payload


from sqlalchemy.orm import Session  # noqa: E402

from polymath.discovery_store import (  # noqa: E402
    ExploreStageStore,
    _action,
    _owner,
    _stage_protocol,
    _totals,
)
from polymath.explore_collection_contracts import (  # noqa: E402
    ExploreCollectionOutcome,
    ExploreRankingDraft,
)
from polymath.jobs import _clear_lease, _event  # noqa: E402
from polymath.meta_readiness import _clock  # noqa: E402


class ExploreCollectionStore(ExploreStageStore):
    """One run, source/evidence transaction and ledger; rank is a model decision."""

    def _owned_read(self, session, owner_hash, run_id):
        return _owned(session, owner_hash, run_id)[0]

    def _visible(self, session, run, packet, visible_stage_ids):
        if type(visible_stage_ids) is not tuple or len(visible_stage_ids) > 4:
            raise StageValidationError("explore_ranking_visibility_invalid")
        rows = tuple(
            session.scalars(
                select(StageCommit)
                .where(
                    StageCommit.run_id == run.id,
                    StageCommit.model_request_id < packet.model_request_id,
                )
                .order_by(StageCommit.ordinal)
            ).all()
        )
        if tuple(str(row.id) for row in rows) != visible_stage_ids:
            raise StageValidationError("explore_ranking_visibility_invalid")
        receipts = tuple(_receipt(row) for row in rows)
        acks = run.checkpoint.get("discovery_acks", {})
        if not isinstance(acks, dict) or any(
            acks.get(row.model_request_id) != receipt.model_dump(mode="json")
            or (row.model_request_id, row.action_ordinal)
            != (receipt.model_request_id, receipt.action_ordinal)
            for row, receipt in zip(rows, receipts, strict=True)
        ):
            raise DiscoveryError("discovery_storage_unavailable")
        validate_ranking_for_commit(
            packet,
            intent=_stage_protocol(session, run).intent,
            acknowledged_receipts=receipts,
        )
        return rows

    def _rank_in_session(self, session, run, packet, visible_stage_ids, *, finishing=False):
        wire = packet.model_dump(mode="json")
        encoded, packet_hash = canonical(wire), digest(canonical(wire))
        prior = session.scalar(
            select(ExploreRankingRecord).where(
                ExploreRankingRecord.run_id == run.id,
                ExploreRankingRecord.model_request_id == packet.model_request_id,
            )
        )
        if prior is not None:
            if prior.packet_sha256 != packet_hash or prior.packet != wire:
                raise DiscoveryError("discovery_replay_conflict")
            # Exact old acknowledgements precede current aliases, scopes or later stages.
            return prior, _ranking(session, run, load_stage_owner(session, run), prior)
        if session.get(ExploreCollectionFinish, run.id) is not None:
            raise DiscoveryError("discovery_replay_conflict")
        action = _action(session, run, packet.model_request_id)
        draft = ExploreRankingDraft(ranked=packet.ranked, excluded=packet.excluded)
        if (
            action.kind != ("finish_ranked" if finishing else "rank_saved")
            or action.ranking != draft
        ):
            raise DiscoveryError("explore_ranking_action_mismatch")
        rows = self._visible(session, run, packet, visible_stage_ids)
        previous = session.scalar(
            select(ExploreRankingRecord)
            .where(ExploreRankingRecord.run_id == run.id)
            .order_by(ExploreRankingRecord.model_request_id.desc())
            .limit(1)
        )
        if previous is not None and (
            previous.model_request_id >= packet.model_request_id
            or previous.action_ordinal >= packet.action_ordinal
        ):
            raise DiscoveryError("discovery_replay_conflict")
        if (
            session.scalar(
                select(ExploreRankingRecord.id).where(
                    ExploreRankingRecord.run_id == run.id,
                    ExploreRankingRecord.action_ordinal == packet.action_ordinal,
                )
            )
            is not None
        ):
            raise DiscoveryError("discovery_replay_conflict")
        ranking_id = uuid.uuid4()
        receipt = ExploreRankingReceipt(
            ranking_id=str(ranking_id),
            run_id=str(run.id),
            packet_sha256=packet_hash,
            model_request_id=packet.model_request_id,
            action_ordinal=packet.action_ordinal,
            covered_through_ordinal=len(rows) - 1 if rows else None,
            ranked=packet.ranked,
            excluded=packet.excluded,
        )
        receipt_wire = receipt.model_dump(mode="json")
        row = ExploreRankingRecord(
            id=ranking_id,
            run_id=run.id,
            model_request_id=packet.model_request_id,
            action_ordinal=packet.action_ordinal,
            covered_through_ordinal=receipt.covered_through_ordinal,
            ranked_count=len(packet.ranked),
            excluded_count=len(packet.excluded),
            packet=wire,
            canonical_packet=encoded,
            packet_sha256=packet_hash,
            receipt=receipt_wire,
            canonical_receipt=canonical(receipt_wire),
            receipt_sha256=digest(canonical(receipt_wire)),
        )
        session.add(row)
        session.flush()
        by_result = {str(stage.result_id): stage for stage in rows}
        for bucket, entries in (("ranked", packet.ranked), ("excluded", packet.excluded)):
            for position, entry in enumerate(entries):
                stage = by_result[entry.reference.result_id]
                session.add(
                    ExploreRankingMember(
                        ranking_id=ranking_id,
                        bucket=bucket,
                        position=position,
                        run_id=run.id,
                        stage_id=stage.id,
                        result_id=stage.result_id,
                        result_sha256=stage.content_sha256,
                        stage_receipt_sha256=stage.receipt_sha256,
                        explanation=entry.rationale if bucket == "ranked" else entry.reason,
                    )
                )
        session.flush()
        checkpoint = dict(run.checkpoint)
        acks = dict(checkpoint.get("explore_ranking_acks", {}))
        acks[packet.model_request_id] = receipt_wire
        checkpoint["explore_ranking_acks"] = acks
        run.checkpoint, run.checkpoint_version = checkpoint, run.checkpoint_version + 1
        _event(
            session,
            run,
            "explore_rank:" + packet.model_request_id,
            "bridges_ranked",
            "Saved the model's explicit bridge order and exclusions",
        )
        return row, receipt

    def rank(self, lease, packet, *, visible_stage_ids):
        packet = _typed(ExploreRankingPacket, packet)
        with Session(self.engine) as session, session.begin():
            run = self._owned_run(session, lease)
            _, receipt = self._rank_in_session(session, run, packet, visible_stage_ids)
            session.flush()
            self._owned_run(session, lease)
            return receipt

    def finish(self, lease, packet, *, visible_stage_ids):
        packet = _typed(ExploreFinishPacket, packet)
        wire = packet.model_dump(mode="json")
        with Session(self.engine) as session, session.begin():
            run = self._owned_run(session, lease)
            prior = session.get(ExploreCollectionFinish, run.id)
            if prior is not None:
                if prior.packet != wire or prior.packet_sha256 != digest(canonical(wire)):
                    raise DiscoveryError("discovery_replay_conflict")
                return read_finish(session, owner_hash=_owner(session, run.id), run_id=run.id)[
                    "ranking"
                ]
            action = _action(session, run, packet.ranking.model_request_id)
            if action.kind != "finish_ranked" or action.reason != packet.reason:
                raise DiscoveryError("explore_finish_action_mismatch")
            row, receipt = self._rank_in_session(
                session, run, packet.ranking, visible_stage_ids, finishing=True
            )
            selected = receipt.selected_reference
            selected_stage = next(
                (item for item in packet.ranking.visible_receipts if item.reference == selected),
                None,
            )
            session.add(
                ExploreCollectionFinish(
                    run_id=run.id,
                    ranking_id=row.id,
                    model_request_id=row.model_request_id,
                    action_ordinal=row.action_ordinal,
                    ranked_count=row.ranked_count,
                    selected_bucket="ranked" if selected else None,
                    selected_position=0 if selected else None,
                    selected_stage_id=uuid.UUID(selected_stage.stage_id)
                    if selected_stage
                    else None,
                    selected_result_id=uuid.UUID(selected.result_id) if selected else None,
                    selected_result_sha256=selected.content_sha256 if selected else None,
                    packet=wire,
                    canonical_packet=canonical(wire),
                    packet_sha256=digest(canonical(wire)),
                )
            )
            checkpoint = dict(run.checkpoint)
            checkpoint["explore_finish_ack"] = receipt.model_dump(mode="json")
            run.checkpoint, run.checkpoint_version = checkpoint, run.checkpoint_version + 1
            _event(
                session,
                run,
                "explore_finish:" + row.model_request_id,
                "collection_finished",
                "The model explicitly finished this collection",
            )
            session.flush()
            self._owned_run(session, lease)
            return receipt

    def record_stop(self, lease, outcome):
        outcome = _typed(ExploreCollectionOutcome, outcome)
        with Session(self.engine) as session, session.begin():
            run = self._owned_run(session, lease)
            stages = tuple(
                session.scalars(
                    select(StageCommit)
                    .where(StageCommit.run_id == run.id)
                    .order_by(StageCommit.ordinal)
                ).all()
            )
            if tuple(_receipt(row) for row in stages) != outcome.stages:
                raise DiscoveryError("discovery_stop_replay_conflict")
            rankings = tuple(
                session.scalars(
                    select(ExploreRankingRecord)
                    .where(ExploreRankingRecord.run_id == run.id)
                    .order_by(ExploreRankingRecord.model_request_id)
                ).all()
            )
            owner = load_stage_owner(session, run)
            if tuple(_ranking(session, run, owner, row) for row in rankings) != outcome.rankings:
                raise DiscoveryError("discovery_stop_replay_conflict")
            finish = session.get(ExploreCollectionFinish, run.id)
            if (finish is None) != (outcome.final_ranking is None) or (
                finish is not None and str(finish.ranking_id) != outcome.final_ranking.ranking_id
            ):
                raise DiscoveryError("discovery_stop_replay_conflict")
            result_id = outcome.selected_reference.result_id if outcome.selected_reference else None
            if not stages and outcome.result is not None:
                saved = self.evidence._save_result_in_session(session, lease, outcome.result)
                result_id = str(saved.id)
            totals, _ = _totals(session, run, None)
            usage = {key: int(value) for key, value in totals.items()}
            summary = {
                "reason": outcome.reason,
                "error_code": outcome.error_code,
                "status": outcome.status,
                "usage": usage,
            }
            prior_stop = session.get(ExploreCollectionStop, run.id)
            if prior_stop is not None:
                if (
                    _snapshot(prior_stop, "payload") != summary
                    or prior_stop.status != outcome.status
                    or prior_stop.finish_ranking_id != (finish.ranking_id if finish else None)
                ):
                    raise DiscoveryError("discovery_stop_replay_conflict")
            else:
                session.add(
                    ExploreCollectionStop(
                        run_id=run.id,
                        status=outcome.status,
                        finish_ranking_id=finish.ranking_id if finish else None,
                        payload=summary,
                        canonical_payload=canonical(summary),
                        payload_sha256=digest(canonical(summary)),
                    )
                )
            session.flush()
            # A receipt/FK flush can wait. Recheck live browser and lease before
            # converting the still-owned running job into a terminal state.
            self._owned_run(session, lease)
            run.status = (
                "interrupted"
                if outcome.status == "limit_reached"
                else "failed"
                if outcome.status == "failed"
                else "succeeded"
            )
            run.finished_at = run.finished_at or _clock(session)
            run.interruption_reason = outcome.error_code or (
                "explore_collection_limit_reached" if outcome.status == "limit_reached" else None
            )
            run.outcome = {
                "result_id": result_id,
                "research_status": "bridge" if outcome.selected_reference else outcome.status,
                "explore_collection_status": outcome.status,
                "error_code": outcome.error_code,
            }
            _clear_lease(run)
            _event(
                session,
                run,
                "explore_collection_stopped",
                run.status,
                "Explore stopped; committed bridges and exact ranking history retained",
            )

    def summary(self, owner_hash, run_id):
        with Session(self.engine) as session:
            run, owner = _owned(session, owner_hash, run_id)
            stages = session.scalars(
                select(StageCommit)
                .where(StageCommit.run_id == run.id)
                .order_by(StageCommit.ordinal)
            ).all()
            ranks = session.scalars(
                select(ExploreRankingRecord)
                .where(ExploreRankingRecord.run_id == run.id)
                .order_by(ExploreRankingRecord.model_request_id)
            ).all()
            rankings = [_ranking(session, run, owner, row).model_dump(mode="json") for row in ranks]
            finish = read_finish(session, owner_hash=owner_hash, run_id=run.id)
            stop = read_stop(session, owner_hash=owner_hash, run_id=run.id)
            totals, money = _totals(session, run, None)
            return {
                "run_id": str(run.id),
                "run_status": run.status,
                "explore_collection_intent": owner.intent.model_dump(mode="json"),
                "stages": [_receipt(row).model_dump(mode="json") for row in stages],
                "rankings": rankings,
                "selected_final_ranking": finish["ranking"].model_dump(mode="json")
                if finish
                else None,
                "stop_reason": (stop.get("reason") or stop.get("error_code"))
                if stop
                else run.interruption_reason
                or (run.status if run.status in ("cancelled", "interrupted", "failed") else None),
                "total_usage": totals,
                "accounting": money,
            }

    def list_rankings(self, owner_hash, run_id):
        with Session(self.engine) as session:
            run, owner = _owned(session, owner_hash, run_id)
            rows = session.scalars(
                select(ExploreRankingRecord)
                .where(ExploreRankingRecord.run_id == run.id)
                .order_by(ExploreRankingRecord.model_request_id)
            ).all()
            return {
                "run_id": str(run.id),
                "rankings": [
                    _ranking(session, run, owner, row).model_dump(mode="json") for row in rows
                ],
            }

    def get_ranking(self, owner_hash, run_id, ranking_id):
        with Session(self.engine) as session:
            return read_ranking(
                session, owner_hash=owner_hash, run_id=run_id, ranking_id=ranking_id
            ).model_dump(mode="json")
