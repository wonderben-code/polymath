"""Explore worker acknowledgements are exact local observations, never future state."""

import json
import uuid
from types import SimpleNamespace

import pytest
from test_explore_adversarial import receipt, uid
from test_explore_collection_contracts import commit_packet, ranking_receipt

from polymath.discovery_contracts import DiscoveryRejection
from polymath.discovery_store import StageValidationError
from polymath.explore_collection_contracts import ExploreFinishPacket, ExploreRankingPacket
from polymath.research.models import SearchResults, Usage
from polymath.worker import (
    ExecutionStopped,
    _RecordedDiscoveryTools,
    _RecordedExploreCollectionSink,
)


def packet(index=0):
    value = commit_packet()
    return value.model_copy(
        update={
            "assessment": receipt(index).assessment,
            "context": value.context.model_copy(
                update={
                    "ordinal": index,
                    "model_request_id": f"model-{index * 4 + 1:04d}",
                    "action_ordinal": index * 4 + 1,
                }
            ),
            "model_request_id": f"model-{index * 4 + 4:04d}",
            "action_ordinal": index * 4 + 4,
            "result": value.result.model_copy(update={"usage": Usage(model_calls=index * 4 + 4)}),
        }
    )


def ranking(receipts=()):
    step = len(receipts) * 4 + 1
    return ExploreRankingPacket.model_validate_json(
        json.dumps(
            {
                "model_request_id": f"model-{step:04d}",
                "action_ordinal": step,
                "visible_receipts": [item.model_dump(mode="json") for item in receipts],
                "ranked": [
                    {
                        "reference": item.reference.model_dump(mode="json"),
                        "rationale": "Model order.",
                    }
                    for item in reversed(receipts)
                ],
                "excluded": [],
            }
        )
    )


class Store:
    def __init__(self):
        self.calls = []
        self.next_receipt = receipt()

    def begin_context(self, lease, context):
        self.calls.append(("begin", context))

    def commit(self, lease, packet):
        self.calls.append(("commit", packet))
        return self.next_receipt

    def rank(self, lease, packet, *, visible_stage_ids):
        self.calls.append(("rank", visible_stage_ids))
        return ranking_receipt(packet)

    def finish(self, lease, packet, *, visible_stage_ids):
        self.calls.append(("finish", visible_stage_ids))
        return ranking_receipt(packet.ranking)

    def record_search(self, lease, ordinal, request_id, query, purpose, results):
        self.calls.append(("search", ordinal, request_id, query, purpose))


def sink(store=None):
    return _RecordedExploreCollectionSink(
        SimpleNamespace(lease=SimpleNamespace(run_id=uuid.UUID(uid(900)))),
        store or Store(),
        SimpleNamespace(step=2),
    )


async def test_exact_local_prefix_is_supplied_to_rank_and_finish_without_loading_future_rows():
    value = sink()
    first = await value.commit(packet())
    interim = await value.rank(ranking((first,)))
    value.store.next_receipt = receipt(1)
    second = await value.commit(packet(1))
    final = await value.finish(
        ExploreFinishPacket(ranking=ranking((first, second)), reason="The model finishes here.")
    )
    assert interim.covered_through_ordinal == 0
    assert final.ranked[0].reference == second.reference
    assert value.store.calls[-1] == ("finish", (first.stage_id, second.stage_id))
    assert value.visible_receipts == [first, second]
    replay = sink(value.store)
    assert replay.visible_stage_ids == [] and replay.visible_receipts == []
    with pytest.raises(ExecutionStopped, match="ranking_visibility_invalid"):
        await replay.rank(ranking((first, second)))
    assert value.store.calls[-1][0] == "finish"


@pytest.mark.parametrize("operation", ["rank", "finish"])
async def test_empty_explicit_ranking_uses_current_run_identity(operation):
    value = sink()
    current = ranking()
    result = await getattr(value, operation)(
        ExploreFinishPacket(ranking=current, reason="No saved bridge is selected.")
        if operation == "finish"
        else current
    )
    assert result.run_id == uid(900) and result.selected_reference is None
    assert value.store.calls[-1] == (operation, ())


@pytest.mark.parametrize("operation", ["commit", "rank", "finish"])
@pytest.mark.parametrize("error", [RuntimeError, ValueError, OSError])
async def test_ambiguous_persistence_failures_never_become_repair_observations(operation, error):
    store = Store()

    def broken(*args, **kwargs):
        raise error("synthetic private persistence failure")

    setattr(store, operation, broken)
    value = sink(store)
    argument = packet() if operation == "commit" else ranking()
    if operation == "finish":
        argument = ExploreFinishPacket(ranking=argument, reason="Explicit finish.")
    with pytest.raises(ExecutionStopped, match="explore_collection_store_interrupted"):
        await getattr(value, operation)(argument)
    assert value.visible_stage_ids == [] and value.visible_receipts == []


@pytest.mark.parametrize("operation", ["commit", "rank", "finish"])
async def test_only_explicit_static_validation_errors_may_request_bounded_model_repair(operation):
    store = Store()

    def rejected(*args, **kwargs):
        raise StageValidationError("explore_invalid_partition")

    setattr(store, operation, rejected)
    argument = packet() if operation == "commit" else ranking()
    if operation == "finish":
        argument = ExploreFinishPacket(ranking=argument, reason="Explicit finish.")
    with pytest.raises(DiscoveryRejection, match="explore_invalid_partition"):
        await getattr(sink(store), operation)(argument)


@pytest.mark.parametrize(
    "change",
    [
        {"run_id": uid(999)},
        {"model_request_id": "model-0005"},
        {"action_ordinal": 5},
        {"ordinal": 1},
        {"depth": 2},
        {"assessment": receipt(score=1).assessment},
    ],
)
async def test_forged_commit_acknowledgement_stops_before_it_becomes_visible(change):
    store = Store()
    store.next_receipt = receipt().model_copy(update=change)
    value = sink(store)
    with pytest.raises(ExecutionStopped, match="explore_collection_commit_receipt_invalid"):
        await value.commit(packet())
    assert value.visible_stage_ids == [] and value.visible_receipts == []


@pytest.mark.parametrize("operation", ["rank", "finish"])
@pytest.mark.parametrize("change", [{"packet_sha256": "f" * 64}, {"run_id": uid(999)}, None])
async def test_missing_or_forged_ranking_acknowledgement_cannot_select_a_final_result(
    operation, change
):
    store = Store()
    original = getattr(store, operation)

    def forged(*args, **kwargs):
        result = original(*args, **kwargs)
        return result.model_copy(update=change) if change is not None else None

    setattr(store, operation, forged)
    argument = ranking()
    if operation == "finish":
        argument = ExploreFinishPacket(ranking=argument, reason="Explicit finish.")
    with pytest.raises(ExecutionStopped, match="explore_collection_ranking_receipt_invalid"):
        await getattr(sink(store), operation)(argument)


async def test_lost_commit_acknowledgement_replays_exactly_without_future_prefix_injection():
    class ResponseLost(Store):
        first = True

        def commit(self, lease, packet):
            if self.first:
                self.first = False
                raise RuntimeError("synthetic committed response loss")
            return super().commit(lease, packet)

    store = ResponseLost()
    value = sink(store)
    with pytest.raises(ExecutionStopped):
        await value.commit(packet())
    assert value.visible_receipts == []
    replay = sink(store)
    saved = await replay.commit(packet())
    assert replay.visible_stage_ids == [saved.stage_id]
    await replay.finish(ExploreFinishPacket(ranking=ranking((saved,)), reason="Finish replay."))
    assert replay.visible_receipts == [saved]


async def test_explore_search_receipts_share_the_original_tool_counter_and_exact_context():
    class Tools:
        step = 0

        async def search(self, query, purpose):
            self.step += 1
            return SearchResults(sources=())

    value = sink()
    tools = _RecordedDiscoveryTools(Tools(), value)
    await value.begin_context(packet().context)
    await tools.search("first query", "counterevidence")
    value.model.step = 6
    await value.begin_context(packet(1).context)
    await tools.search("second query", "prior_work")
    assert tools.step == 2
    assert value.store.calls[-1] == ("search", 1, "model-0006", "second query", "prior_work")


async def test_search_receipt_failure_is_never_a_paid_repair():
    class Broken(Store):
        def record_search(self, *args):
            raise StageValidationError("explore_search_not_saved")

    value = sink(Broken())
    await value.begin_context(packet().context)
    with pytest.raises(ExecutionStopped, match="explore_collection_journal_not_saved"):
        await value.record_search("query", "prior_work", SearchResults(sources=()))
