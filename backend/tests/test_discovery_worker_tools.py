"""Discovery receipt control boundaries without model or provider requests."""

import json
from types import SimpleNamespace

import pytest
from test_discovery_controller import assessment_data

from polymath.discovery_contracts import StageAssessment, StageCommitReceipt
from polymath.research.models import SearchResults
from polymath.worker import ExecutionStopped, _RecordedDiscoverySink, _RecordedDiscoveryTools


class Store:
    def __init__(self):
        self.calls = []
        self.receipt = StageCommitReceipt(
            stage_id="12345678-1234-1234-1234-123456789abc",
            run_id="12345678-1234-1234-1234-123456789abd",
            result_id="12345678-1234-1234-1234-123456789abe",
            result_sha256="a" * 64,
            ordinal=0,
            depth=1,
            assessment=StageAssessment.model_validate_json(json.dumps(assessment_data())),
            parent_refs=(),
            action_ordinal=7,
            model_request_id="model-0007",
        )

    def begin_context(self, lease, context):
        self.calls.append(("context", context.ordinal))

    def commit(self, lease, packet):
        self.calls.append(("commit", packet))
        return self.receipt

    def read_parent(self, lease, result_id, ordinal, *, visible_stage_ids, model_request_id):
        self.calls.append(("parent", result_id, ordinal, visible_stage_ids))
        return SimpleNamespace(result_id=result_id)

    def read_parent_source(
        self, lease, result_id, retrieval_id, ordinal, *, visible_stage_ids, model_request_id
    ):
        self.calls.append(("source", result_id, retrieval_id, ordinal, visible_stage_ids))
        return SimpleNamespace(retrieval_id=retrieval_id)

    def record_search(self, lease, ordinal, model_id, query, purpose, results):
        self.calls.append(("search", ordinal, model_id, query, purpose, results))

    def finish(self, lease, packet):
        self.calls.append(("finish", packet))


def sink(store=None, *, step=2):
    return _RecordedDiscoverySink(
        SimpleNamespace(lease=object()), store or Store(), SimpleNamespace(step=step)
    )


async def test_only_acknowledged_stages_become_visible_at_this_replay_point():
    value = sink()
    assert value.visible_stage_ids == []
    await value.read_parent("earlier-owned-result", 0)
    assert value.store.calls[-1][-1] == ()
    receipt = await value.commit("original-action-packet")
    await value.read_parent("acknowledged-result", 1)
    assert value.store.calls[-1][-1] == (receipt.stage_id,)
    await value.read_parent_source("acknowledged-result", "exact-retrieval", 1)
    assert value.store.calls[-1][-1] == (receipt.stage_id,)
    replay = sink(value.store)
    assert replay.visible_stage_ids == []
    await replay.commit("original-action-packet")
    await replay.commit("original-action-packet")
    assert replay.visible_stage_ids == [receipt.stage_id]


async def test_commit_response_loss_is_control_stop_and_exact_receipt_replays():
    class LostResponse(Store):
        committed = False

        def commit(self, lease, packet):
            if not self.committed:
                self.committed = True
                raise RuntimeError("synthetic committed database response lost")
            return self.receipt

    store = LostResponse()
    first = sink(store)
    with pytest.raises(ExecutionStopped, match="discovery_store_interrupted"):
        try:
            await first.commit("same-packet")
        except Exception:
            pytest.fail("An ambiguous commit became a repairable model observation")
    assert first.visible_stage_ids == []
    replacement = sink(store)
    receipt = await replacement.commit("same-packet")
    assert replacement.visible_stage_ids == [receipt.stage_id]


@pytest.mark.parametrize("error", [RuntimeError, ValueError, OSError])
async def test_opaque_store_failures_never_become_model_repairs(error):
    class BrokenStore(Store):
        def commit(self, lease, packet):
            raise error("synthetic private database failure")

    with pytest.raises(ExecutionStopped, match="discovery_store_interrupted"):
        await sink(BrokenStore()).commit("packet")


async def test_only_explicit_stage_validation_is_repairable():
    from polymath.discovery_contracts import DiscoveryRejection
    from polymath.discovery_store import StageValidationError

    class Rejected(Store):
        def commit(self, lease, packet):
            raise StageValidationError("missing_parent_quote")

    with pytest.raises(DiscoveryRejection):
        await sink(Rejected()).commit("packet")


async def test_missing_commit_acknowledgement_cannot_become_a_repairable_action():
    class MissingReceipt(Store):
        def commit(self, lease, packet):
            return None

    with pytest.raises(ExecutionStopped, match="discovery_commit_receipt_invalid"):
        await sink(MissingReceipt()).commit("packet")


async def test_search_journal_failure_stops_instead_of_spending_a_repair_call():
    from polymath.discovery_store import StageValidationError

    class BrokenJournal(Store):
        def record_search(self, *args):
            raise StageValidationError("search_receipt_conflict")

    value = sink(BrokenJournal())
    await value.begin_context(SimpleNamespace(ordinal=0))
    with pytest.raises(ExecutionStopped, match="discovery_journal_not_saved"):
        await value.record_search("query", "counterevidence", SearchResults(sources=()))


async def test_contexts_share_one_recorded_tool_object_and_global_model_ids():
    class Tools:
        def __init__(self):
            self.step = 0

        async def search(self, query, purpose):
            self.step += 1
            return SearchResults(sources=())

        async def read(self, source_id):
            return source_id

    value, ordinary = sink(), Tools()
    tools = _RecordedDiscoveryTools(ordinary, value)
    await value.begin_context(SimpleNamespace(ordinal=0))
    await tools.search("first query", "counterevidence")
    value.model.step = 7
    await value.begin_context(SimpleNamespace(ordinal=1))
    await tools.search("second query", "prior_work")
    assert ordinary.step == tools.step == 2
    assert await tools.read("existing-source") == "existing-source"
    searches = [item for item in value.store.calls if item[0] == "search"]
    assert [(item[1], item[2]) for item in searches] == [(0, "model-0002"), (1, "model-0007")]


async def test_search_without_an_observed_context_cannot_be_journalled():
    with pytest.raises(ExecutionStopped, match="discovery_context_missing"):
        await sink().record_search("query", "prior_work", SearchResults(sources=()))
