"""Experimental Frontier programmes against real PostgreSQL with synthetic runs."""

import pytest
from test_memory import assign, complete, owner
from test_memory_search_terms import save_answer
from test_worker import database as database_fixture

from polymath.frontier import FrontierError, FrontierLimits, FrontierStore, goal_for, weakest
from polymath.jobs import JobRepository

database = database_fixture
pytestmark = pytest.mark.integration
QUESTION = "How could synthetic fixtures close an open question?"


def finished_run(engine, token, *, mode="explore", status="succeeded"):
    jobs = JobRepository(engine)
    run = jobs.create(mode=mode, goal="Synthetic frontier attempt")
    assign(engine, run.id, token)
    lease = jobs.claim_next("synthetic-frontier", lease_seconds=300)
    jobs.finish(lease, status=status, outcome={} if status == "succeeded" else {"error_code": "x"})
    return run.id


def due(store, token, programme_id):
    return store.next_action(token, programme_id)


def nodes_by_kind(snapshot, kind):
    return [node for node in snapshot["nodes"] if node["kind"] == kind]


def test_survey_names_gaps_and_a_bridge_transfers_one(database):
    token = owner(database)
    store = FrontierStore(database)
    programme = store.create(token, QUESTION)
    pid = programme["id"]
    step = due(store, token, pid)
    assert step["state"] == "due" and step["action"] == "survey" and step["mode"] == "ask"
    answer = save_answer(database, token)
    snapshot = store.attach(token, pid, step["node_id"], "survey", answer.run_id)
    assert snapshot["nodes"][0]["status"] == "running"
    assert due(store, token, pid)["state"] == "due"
    snapshot = store.get(token, pid)
    gaps = nodes_by_kind(snapshot, "gap")
    assert [gap["statement"] for gap in gaps] == [
        "Would actual literature support this hypothesis?"
    ]
    step = due(store, token, pid)
    assert step["action"] == "transfer" and step["mode"] == "explore"
    assert "Would actual literature support this hypothesis?" in step["goal"]
    bridge, _ = complete(database, token, title="Synthetic transferred bridge")
    store.attach(token, pid, step["node_id"], "transfer", bridge.run_id)
    assert due(store, token, pid) == {"state": "completed", "reason": "no_open_actions"}
    final = store.get(token, pid)
    assert final["status"] == "completed"
    assert nodes_by_kind(final, "gap")[0]["status"] == "transferred"
    assert final["attempts_used"] == 2


def test_unbridged_gap_is_decomposed_into_steps_resolved_by_their_weakest(database):
    token = owner(database)
    store = FrontierStore(database)
    pid = store.create(token, QUESTION, limits=FrontierLimits(max_depth=3))["id"]
    # An insufficient survey makes the whole question the gap.
    step = due(store, token, pid)
    store.attach(token, pid, step["node_id"], "survey", finished_run(database, token, mode="ask"))
    step = due(store, token, pid)
    assert step["action"] == "transfer"
    store.attach(token, pid, step["node_id"], "transfer", finished_run(database, token))
    step = due(store, token, pid)
    assert step["action"] == "decompose" and "intermediate results" in step["goal"]
    store.attach(token, pid, step["node_id"], "decompose", save_answer(database, token).run_id)
    assert due(store, token, pid)["action"] == "attempt"
    snapshot = store.get(token, pid)
    steps = nodes_by_kind(snapshot, "step")
    assert len(steps) == 3 and all(node["pending_action"] == "attempt" for node in steps)
    # Unfinished steps keep the gap in progress rather than open.
    assert nodes_by_kind(snapshot, "gap")[0]["resolved_status"] == "pending"
    for _ in steps:
        step = due(store, token, pid)
        assert step["action"] == "attempt"
        store.attach(token, pid, step["node_id"], "attempt", save_answer(database, token).run_id)
    assert due(store, token, pid)["state"] == "completed"
    final = store.get(token, pid)
    # The fixture answer has known, postulated and speculative claims; at the
    # depth limit a step is labelled by its weakest claim, and so is the gap.
    assert {node["status"] for node in nodes_by_kind(final, "step")} == {"conjectured"}
    gap = nodes_by_kind(final, "gap")[0]
    assert gap["status"] == "decomposed" and gap["resolved_status"] == "conjectured"


def test_failed_attempts_retry_then_mark_the_node_open(database):
    token = owner(database)
    store = FrontierStore(database)
    pid = store.create(token, QUESTION)["id"]
    for _ in range(2):
        step = due(store, token, pid)
        assert step["action"] == "survey"
        store.attach(
            token,
            pid,
            step["node_id"],
            "survey",
            finished_run(database, token, mode="ask", status="failed"),
        )
    assert due(store, token, pid)["state"] == "completed"
    assert store.get(token, pid)["nodes"][0]["status"] == "open"


def test_attach_requires_the_due_action_an_owned_run_and_the_right_mode(database):
    token, other = owner(database), owner(database, "frontier-other")
    store = FrontierStore(database)
    pid = store.create(token, QUESTION)["id"]
    step = due(store, token, pid)
    with pytest.raises(FrontierError, match="frontier_action_not_due"):
        store.attach(token, pid, step["node_id"], "transfer", finished_run(database, token))
    with pytest.raises(FrontierError, match="frontier_run_invalid"):
        store.attach(token, pid, step["node_id"], "survey", finished_run(database, token))
    with pytest.raises(FrontierError, match="frontier_run_invalid"):
        store.attach(
            token, pid, step["node_id"], "survey", finished_run(database, other, mode="ask")
        )
    run = finished_run(database, token, mode="ask")
    store.attach(token, pid, step["node_id"], "survey", run)
    with pytest.raises(FrontierError, match="frontier_action_not_due"):
        store.attach(token, pid, step["node_id"], "survey", run)
    with pytest.raises(FrontierError, match="frontier_not_found"):
        store.get(other, pid)


def test_pause_prune_and_attempt_limit(database):
    token = owner(database)
    store = FrontierStore(database)
    pid = store.create(token, QUESTION, limits=FrontierLimits(max_attempts=2))["id"]
    step = due(store, token, pid)
    store.attach(token, pid, step["node_id"], "survey", save_answer(database, token).run_id)
    store.set_status(token, pid, "paused")
    assert due(store, token, pid) == {"state": "paused"}
    store.set_status(token, pid, "active")
    gap = nodes_by_kind(store.get(token, pid), "gap")[0]
    snapshot = store.prune(token, pid, gap["id"])
    assert nodes_by_kind(snapshot, "gap")[0]["status"] == "skipped"
    assert due(store, token, pid) == {"state": "completed", "reason": "no_open_actions"}
    with pytest.raises(FrontierError, match="frontier_finished"):
        store.set_status(token, pid, "active")


def test_inputs_and_limits_are_validated_without_echoing_text():
    with pytest.raises(FrontierError, match="invalid_frontier_limits"):
        FrontierLimits(max_depth=9)
    assert weakest(["supported", "derived"]) == "derived"
    assert weakest([]) == "open"
    long = "x " * 3000
    assert len(goal_for("decompose", long, long)) <= 4000
    with pytest.raises(FrontierError, match="invalid_frontier_action"):
        goal_for("unknown", "a", "b")


@pytest.fixture
def frontier_api(monkeypatch):
    import os
    import uuid
    from contextlib import ExitStack
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url

    from polymath.frontier_api import create_frontier_router
    from polymath.run_api import create_run_router

    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL for Frontier API checks")
    url = make_url(raw)
    schema = "polymath_frontier_" + uuid.uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    engine = create_engine(scoped, hide_parameters=True)
    enabled = {"value": True}
    with ExitStack() as clients:
        try:
            command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
            app = FastAPI()
            app.include_router(create_run_router(engine, availability=lambda: True))
            app.include_router(create_frontier_router(engine, lambda: enabled["value"]))
            yield engine, clients.enter_context(TestClient(app)), enabled
        finally:
            clients.close()
            engine.dispose()
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def test_api_requires_session_and_csrf_and_never_echoes_input(frontier_api):
    from test_run_api import bootstrap

    _, client, enabled = frontier_api
    assert client.get("/api/v1/frontier").status_code == 401
    capabilities = client.get("/api/v1/frontier/capabilities").json()
    assert capabilities["experimental"] is True and capabilities["spends_directly"] is False
    headers = bootstrap(client)
    assert client.post("/api/v1/frontier", json={"question": QUESTION}).status_code == 403
    created = client.post("/api/v1/frontier", headers=headers, json={"question": QUESTION})
    assert created.status_code == 201
    programme = created.json()
    assert programme["nodes"][0]["kind"] == "question"
    assert programme["limits"] == {"max_attempts": 24, "max_depth": 4, "max_branches": 6}
    step = client.post(f"/api/v1/frontier/{programme['id']}/next", headers=headers).json()
    assert step["next"]["action"] == "survey"
    secret = "private-question-text-never-echoed"
    invalid = client.post(
        "/api/v1/frontier", headers=headers, json={"question": secret, "max_depth": 99}
    )
    assert invalid.status_code == 422 and secret not in invalid.text
    assert client.get(f"/api/v1/frontier/{uuid_text()}").status_code == 404
    paused = client.post(f"/api/v1/frontier/{programme['id']}/pause", headers=headers)
    assert paused.json()["status"] == "paused"
    enabled["value"] = False
    assert client.get("/api/v1/frontier").status_code == 503


def uuid_text():
    import uuid

    return str(uuid.uuid4())


def save_two_gap_answer(engine, token):
    from test_answer_persistence import answer_result

    from polymath.evidence import EvidenceStore

    result = answer_result()
    result = result.model_copy(
        update={
            "finding": result.finding.model_copy(
                update={"unanswered_questions": ["First synthetic gap?", "Second synthetic gap?"]}
            )
        }
    )
    jobs, store = JobRepository(engine), EvidenceStore(engine)
    run = jobs.create(mode="ask", goal=result.finding.question)
    assign(engine, run.id, token)
    lease = jobs.claim_next("synthetic-frontier-gaps", lease_seconds=300)
    for source in result.sources:
        store.save_source(lease, source)
    saved = store.save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    return saved.run_id


def run_with_parents(engine, token, bridges):
    from sqlalchemy.orm import Session

    from polymath.lineage_models import ResearchRunParent

    jobs = JobRepository(engine)
    run = jobs.create(mode="explore", goal="Synthetic convergence")
    assign(engine, run.id, token)
    with Session(engine) as session, session.begin():
        for index, saved in enumerate(bridges):
            session.add(
                ResearchRunParent(
                    run_id=run.id,
                    parent_result_id=saved.id,
                    parent_run_id=saved.run_id,
                    ordinal=index,
                    content_sha256="a" * 64,
                )
            )
    return run.id


def test_two_transferred_bridges_queue_a_convergence_on_exactly_those_bridges(database):
    token = owner(database)
    store = FrontierStore(database)
    pid = store.create(token, QUESTION)["id"]
    step = due(store, token, pid)
    store.attach(token, pid, step["node_id"], "survey", save_two_gap_answer(database, token))
    bridges = []
    for index in range(2):
        step = due(store, token, pid)
        assert step["action"] == "transfer" and step["parent_result_ids"] == []
        saved, _ = complete(database, token, title=f"Synthetic bridge {index}", suffix=str(index))
        bridges.append(saved)
        store.attach(token, pid, step["node_id"], "transfer", saved.run_id)
    step = due(store, token, pid)
    assert step["action"] == "converge" and step["mode"] == "explore"
    assert step["parent_result_ids"] == [str(saved.id) for saved in bridges]
    with pytest.raises(FrontierError, match="frontier_run_invalid"):
        store.attach(
            token, pid, step["node_id"], "converge", run_with_parents(database, token, bridges[:1])
        )
    store.attach(
        token, pid, step["node_id"], "converge", run_with_parents(database, token, bridges)
    )
    convergence = nodes_by_kind(store.get(token, pid), "convergence")
    assert len(convergence) == 1 and convergence[0]["status"] == "running"
