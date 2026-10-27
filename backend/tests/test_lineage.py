"""Real PostgreSQL selection/ownership checks; all research evidence is synthetic."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_answer_persistence import QUESTION, answer_result
from test_knowledge import seed
from test_run_api import api as base_api
from test_run_api import bootstrap, submit

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.evidence_models import EvidenceResult
from polymath.jobs import JobRepository, LeaseLost
from polymath.lineage import LineageError, bind_parents, get_parent_refs
from polymath.lineage_models import ResearchRunParent
from polymath.models import ResearchRun
from polymath.run_api import COOKIE_NAME

pytestmark = pytest.mark.integration
api = base_api


def selected(client, headers, parents, **changes):
    return submit(client, headers, parent_result_ids=[str(parent) for parent in parents], **changes)


def parents_for(engine, client):
    first = seed(engine, client, title="Synthetic first bridge", suffix="one")
    second = seed(engine, client, title="Synthetic second bridge", suffix="two")
    return [first[1], second[1]]


def test_selection_is_ordered_hash_bound_and_reloadable(api):
    engine, factory = api
    client = factory(enabled=True)
    parents = parents_for(engine, client)
    response = selected(client, bootstrap(client), parents[::-1])
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    run_id = uuid.UUID(response.json()["id"])
    assert response.json()["goal"] == "Synthetic fixture concept"
    assert "parent" not in response.json()["goal"]
    jobs = JobRepository(engine)
    lease = jobs.claim_next("parent-fixture")
    assert lease.run_id == run_id
    refs = get_parent_refs(engine, lease)
    assert [ref.result_id for ref in refs] == [str(parent) for parent in parents[::-1]]
    with Session(engine) as session:
        for ref in refs:
            stored = session.get(EvidenceResult, uuid.UUID(ref.result_id))
            assert ref.run_id == str(stored.run_id)
            assert ref.content_sha256 == stored.content_sha256
    jobs.interrupt(lease)
    snapshot = jobs.get(run_id)
    jobs.resume_interrupted(run_id, expected_token=snapshot.lease_token)
    replacement = jobs.claim_next("replacement")
    assert get_parent_refs(engine, replacement) == refs
    with pytest.raises(LeaseLost):
        get_parent_refs(engine, lease)


@pytest.mark.parametrize(
    "changes",
    [
        {"parent_result_ids": [str(uuid.uuid4())]},
        {"parent_result_ids": [str(uuid.uuid4())] * 2},
        {"parent_result_ids": [str(uuid.uuid4()) for _ in range(5)]},
        {"parent_result_ids": [str(uuid.uuid4()), "private-invalid-selection"]},
        {"parent_result_ids": [str(uuid.uuid4()).upper(), str(uuid.uuid4())]},
        {"parent_result_ids": [str(uuid.uuid4()).replace("-", ""), str(uuid.uuid4())]},
        {"parent_result_ids": [True, str(uuid.uuid4())]},
        {"parent_result_ids": "private-invalid-selection"},
        {"mode": "ask", "parent_result_ids": [str(uuid.uuid4()), str(uuid.uuid4())]},
        {"mode": "surprise", "parent_result_ids": [str(uuid.uuid4()), str(uuid.uuid4())]},
    ],
)
def test_invalid_selection_is_sanitized_before_enqueue(api, changes):
    engine, factory = api
    client = factory(enabled=True)
    response = submit(client, bootstrap(client), **changes)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_research_request"}
    assert response.headers["cache-control"] == "no-store"
    assert "private-invalid-selection" not in response.text
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 0


def test_foreign_and_missing_selection_are_indistinguishable_and_atomic(api):
    engine, factory = api
    own, other = factory(enabled=True), factory(enabled=True)
    own_parents = parents_for(engine, own)
    foreign_parents = parents_for(engine, other)
    headers = bootstrap(own)
    foreign = selected(own, headers, [own_parents[0], foreign_parents[0]])
    missing = selected(own, headers, [own_parents[0], uuid.uuid4()])
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json() == {"detail": "research_basis_not_found"}
    assert foreign.headers["cache-control"] == "no-store"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRun)) == 4
        assert session.scalar(select(func.count()).select_from(ResearchRunAccess)) == 4
        assert session.scalar(select(func.count()).select_from(ResearchRunParent)) == 0


def test_selection_cannot_bypass_availability_csrf_or_active_quota(api):
    engine, factory = api
    client = factory(enabled=True)
    parents = parents_for(engine, client)
    headers = bootstrap(client)
    csrf = selected(client, {}, parents)
    assert csrf.status_code == 403
    disabled = factory(enabled=False)
    disabled.cookies.set(COOKIE_NAME, client.cookies.get(COOKIE_NAME))
    assert selected(disabled, headers, parents).status_code == 503
    assert selected(client, headers, parents).status_code == 201
    assert selected(client, headers, parents).status_code == 429
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRunParent)) == 2


def test_only_finished_bridge_results_are_eligible(api):
    engine, factory = api
    client = factory(enabled=True)
    parents = parents_for(engine, client)
    with Session(engine) as session, session.begin():
        parent = session.get(EvidenceResult, parents[0])
        session.get(ResearchRun, parent.run_id).status = "failed"
    response = selected(client, bootstrap(client), parents)
    assert response.status_code == 404
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRunParent)) == 0


def test_answer_is_memory_but_cannot_be_selected_as_a_parent_bridge(api):
    engine, factory = api
    client = factory(enabled=True)
    parents = parents_for(engine, client)
    created = submit(client, bootstrap(client), mode="ask", goal=QUESTION)
    assert created.status_code == 201
    jobs = JobRepository(engine)
    lease = jobs.claim_next("answer-fixture")
    from polymath.evidence import EvidenceStore

    store = EvidenceStore(engine)
    result = answer_result()
    for source in result.sources:
        store.save_source(lease, source)
    saved = store.save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    response = selected(client, bootstrap(client), [parents[0], saved.id])
    assert response.status_code == 404
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRunParent)) == 0


def test_four_bridges_are_accepted_and_binding_cannot_be_replaced(api):
    engine, factory = api
    client = factory(enabled=True)
    parents = parents_for(engine, client)
    parents.extend(
        seed(engine, client, title=f"Synthetic bridge {number}", suffix=str(number))[1]
        for number in (3, 4)
    )
    created = selected(client, bootstrap(client), parents)
    assert created.status_code == 201
    run_id = uuid.UUID(created.json()["id"])
    with Session(engine) as session, session.begin():
        run = session.get(ResearchRun, run_id)
        owner_hash = session.scalar(
            select(ResearchRunAccess.session_hash).where(ResearchRunAccess.run_id == run_id)
        )
        with pytest.raises(LineageError, match="research_basis_already_bound"):
            bind_parents(session, run, owner_hash, [str(value) for value in parents[:2]])
    lease = JobRepository(engine).claim_next("four-parent-fixture")
    assert [ref.result_id for ref in get_parent_refs(engine, lease)] == [
        str(value) for value in parents
    ]


def test_future_result_is_ineligible_even_when_same_owner_and_finished(api):
    engine, factory = api
    client = factory(enabled=True)
    parents = parents_for(engine, client)
    with Session(engine) as session, session.begin():
        original = session.get(EvidenceResult, parents[0])
        future_id = uuid.uuid4()
        session.add(
            EvidenceResult(
                id=future_id,
                run_id=original.run_id,
                content_sha256="f" * 64,
                status="bridge",
                payload=original.payload,
                created_at=session.scalar(select(func.clock_timestamp())) + timedelta(days=1),
            )
        )
    response = selected(client, bootstrap(client), [future_id, parents[1]])
    assert response.status_code == 404


def test_worker_requires_live_session_and_current_fence(api):
    engine, factory = api
    client = factory(enabled=True)
    parents = parents_for(engine, client)
    response = selected(client, bootstrap(client), parents)
    assert response.status_code == 201
    jobs = JobRepository(engine)
    lease = jobs.claim_next("parent-fixture")
    assert len(get_parent_refs(engine, lease)) == 2
    with Session(engine) as session, session.begin():
        session.execute(
            text("UPDATE browser_sessions SET expires_at = clock_timestamp() - interval '1 second'")
        )
    with pytest.raises(LineageError, match="research_basis_not_found"):
        get_parent_refs(engine, lease)
    jobs.request_cancel(lease.run_id)
    with pytest.raises(LeaseLost):
        get_parent_refs(engine, lease)


def test_helper_rejects_unowned_or_self_selection(api):
    engine, factory = api
    client = factory(enabled=True)
    parents = parents_for(engine, client)
    jobs = JobRepository(engine)
    accessless = jobs.create(mode="explore", goal="Synthetic accessless fixture")
    with Session(engine) as session, session.begin():
        run = session.get(ResearchRun, accessless.id)
        owner_hash = session.scalar(select(BrowserSession.token_hash))
        with pytest.raises(LineageError, match="research_basis_not_found"):
            bind_parents(session, run, owner_hash, [str(value) for value in parents])
    lease = jobs.claim_next("accessless-fixture")
    assert get_parent_refs(engine, lease) == ()
    jobs.finish(lease, status="succeeded", outcome={})
    # A result on the current run is rejected even if a trusted caller changes
    # its state; cycles cannot be introduced through the binding helper.
    created = submit(client, bootstrap(client))
    current_id = uuid.UUID(created.json()["id"])
    with Session(engine) as session, session.begin():
        run = session.get(ResearchRun, current_id)
        owner_hash = session.scalar(
            select(ResearchRunAccess.session_hash).where(ResearchRunAccess.run_id == current_id)
        )
        self_id = uuid.uuid4()
        session.add(
            EvidenceResult(
                id=self_id,
                run_id=current_id,
                content_sha256="e" * 64,
                status="bridge",
                payload={},
                created_at=run.created_at - timedelta(seconds=1),
            )
        )
        with pytest.raises(LineageError, match="research_basis_not_found"):
            bind_parents(session, run, owner_hash, [str(self_id), str(parents[0])])


@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_parent_history_cannot_be_mutated(api, operation):
    engine, factory = api
    client = factory(enabled=True)
    parents = parents_for(engine, client)
    assert selected(client, bootstrap(client), parents).status_code == 201
    statement = (
        "UPDATE research_run_parents SET ordinal = ordinal"
        if operation == "UPDATE"
        else "DELETE FROM research_run_parents"
    )
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(text(statement))
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(ResearchRunParent)) == 2
