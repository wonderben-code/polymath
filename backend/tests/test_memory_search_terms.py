"""Term-based, claim-level memory search against real PostgreSQL with synthetic findings."""

import pytest
from test_answer_persistence import answer_result
from test_memory import assign, complete, current, owner
from test_worker import database as database_fixture

from polymath.evidence import EvidenceStore
from polymath.jobs import JobRepository
from polymath.knowledge import MAX_QUERY_TERMS, query_terms

database = database_fixture
pytestmark = pytest.mark.integration


def save_answer(engine, token_hash):
    result = answer_result()
    jobs, store = JobRepository(engine), EvidenceStore(engine)
    run = jobs.create(mode="ask", goal=result.finding.question)
    assign(engine, run.id, token_hash)
    lease = jobs.claim_next("synthetic-answer-parent", lease_seconds=300)
    assert lease.run_id == run.id
    for source in result.sources:
        store.save_source(lease, source)
    saved = store.save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    return saved


def found(memory, query):
    return [item.result_id for item in memory.search(query).findings]


def test_terms_need_not_be_adjacent_but_every_term_must_match(database):
    token = owner(database)
    saved, _ = complete(database, token, title="Synthetic redundancy bridge for signalling")
    memory, _ = current(database, token)
    assert found(memory, "signalling redundancy") == [str(saved.id)]
    assert found(memory, "SIGNALLING   bridge") == [str(saved.id)]
    assert found(memory, "signalling unrelatedterm") == []


def test_individual_answer_claims_are_searchable(database):
    token = owner(database)
    saved = save_answer(database, token)
    memory, _ = current(database, token)
    # This phrase appears only inside one classified claim, not the headline text.
    assert found(memory, "hypothetical cross-domain analogy") == [str(saved.id)]
    assert found(memory, "broader mechanism") == [str(saved.id)]


def test_terms_can_be_split_between_finding_text_and_quoted_source_titles(database):
    token = owner(database)
    saved, _ = complete(
        database, token, title="Synthetic memory bridge", source_title="Percolation fixture paper"
    )
    memory, _ = current(database, token)
    assert found(memory, "percolation memory") == [str(saved.id)]


def test_wildcards_stay_literal_in_every_term(database):
    token = owner(database)
    saved, _ = complete(database, token, title="Synthetic 50%_bridge")
    memory, _ = current(database, token)
    assert found(memory, "50%_ synthetic") == [str(saved.id)]
    assert found(memory, "5_% synthetic") == []


def test_other_owners_findings_never_match_any_term(database):
    complete(database, owner(database, "another-session"), title="Foreign signalling bridge")
    token = owner(database)
    memory, _ = current(database, token)
    assert found(memory, "foreign signalling") == []


def test_query_terms_are_distinct_and_bounded():
    assert query_terms("  Noise noise SIGNAL  signal ") == ("Noise", "SIGNAL")
    many = " ".join(f"term{index}" for index in range(MAX_QUERY_TERMS + 4))
    assert len(query_terms(many)) == MAX_QUERY_TERMS
    assert query_terms("   ") == ()
