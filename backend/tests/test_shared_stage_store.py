"""Protocol wrappers share the atomic store without sharing terminal authority."""

import inspect
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import aliased

from polymath.discovery_store import DiscoveryError, DiscoveryStore, ExploreStageStore
from polymath.evidence_models import EvidenceResult
from polymath.stage_owners import no_collection_intent_condition


def test_surprise_and_explore_use_one_atomic_commit_implementation():
    assert inspect.signature(DiscoveryStore) == inspect.signature(ExploreStageStore)
    assert DiscoveryStore._stage_mode == "surprise"
    assert ExploreStageStore._stage_mode == "explore"
    for method in ("commit", "begin_context", "record_search", "list_stages", "get_stage"):
        assert getattr(DiscoveryStore, method) is getattr(ExploreStageStore, method)


@pytest.mark.parametrize("operation", ("finish", "record_stop"))
def test_explore_cannot_accidentally_write_a_surprise_terminal_table(operation):
    engine = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    with pytest.raises(DiscoveryError, match="explore_collection_ranking_required"):
        getattr(ExploreStageStore(engine), operation)(None, None)


@pytest.mark.parametrize("result", (EvidenceResult, aliased(EvidenceResult)))
def test_both_concrete_intents_suppress_generic_positive_sql_even_without_registry(result):
    sql = str(no_collection_intent_condition(result))
    assert "research_discovery_intents" in sql
    assert "research_explore_collection_intents" in sql
    assert "research_stage_owners" not in sql
