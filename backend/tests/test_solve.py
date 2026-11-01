"""Solve (key finder and key maker) against real PostgreSQL with synthetic runs."""

import uuid

import pytest
from test_converge import bridge_run, empty_run, meta_run
from test_frontier import finished_run
from test_memory import assign, owner
from test_worker import database as database_fixture

from polymath.frontier import FrontierStore
from polymath.jobs import JobRepository
from polymath.solve import ANGLES, SolveError, SolveStore, goal_for

database = database_fixture
pytestmark = pytest.mark.integration
PROBLEM = "How could a synthetic battery keep its charge through a thousand cycles?"


def answer_run(engine, token, *, gaps=None):
    """A finished owned Ask run whose saved answer states a known claim."""
    from test_answer_persistence import answer_result

    from polymath.evidence import EvidenceStore

    result = answer_result()
    if gaps is not None:
        result = result.model_copy(
            update={"finding": result.finding.model_copy(update={"unanswered_questions": gaps})}
        )
    jobs, store = JobRepository(engine), EvidenceStore(engine)
    run = jobs.create(mode="ask", goal=result.finding.question)
    assign(engine, run.id, token)
    lease = jobs.claim_next("synthetic-solve-find", lease_seconds=300)
    for source in result.sources:
        store.save_source(lease, source)
    saved = store.save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    return run.id


def step(store, token, programme):
    due = store.next_action(token, uuid.UUID(programme["id"]))
    assert due["state"] == "due", due
    return due


def attach(store, token, programme, due, run_id):
    return store.attach(
        token, uuid.UUID(programme["id"]), uuid.UUID(due["step_id"]), due["stage"], run_id
    )


def test_an_existing_key_is_found_and_nothing_more_is_spent(database):
    token, store = owner(database), SolveStore(database)
    programme = store.create(token, PROBLEM)
    due = step(store, token, programme)
    assert due["stage"] == "find" and due["mode"] == "ask"
    assert due["goal"].startswith("Has this problem already been solved")
    attach(store, token, programme, due, answer_run(database, token, gaps=[]))
    done = store.get(token, uuid.UUID(programme["id"]))
    assert done["outcome"] == "found" and done["status"] == "completed"
    assert [item["stage"] for item in done["steps"]] == ["find"]
    finding = done["steps"][0]["finding"]
    assert finding["kind"] == "answer" and finding["known"]
    assert store.next_action(token, uuid.UUID(programme["id"]))["state"] == "completed"


def test_close_fits_for_each_gap_are_forged_into_a_new_key(database):
    token, store = owner(database), SolveStore(database)
    programme = store.create(token, PROBLEM)
    find = step(store, token, programme)
    attach(
        store, token, programme, find, answer_run(database, token, gaps=["Gap one?", "Gap two?"])
    )
    first = step(store, token, programme)
    assert first["stage"] == "fit" and first["mode"] == "explore"
    assert "Gap one?" in first["goal"] and "where it falls short" in first["goal"]
    left_run, left = bridge_run(database, token, "Materials science", "Ecology", title="Fit A")
    attach(store, token, programme, first, left_run)
    second = step(store, token, programme)
    assert "Gap two?" in second["goal"]
    right_run, right = bridge_run(database, token, "Control theory", "Immunology", title="Fit B")
    attach(store, token, programme, second, right_run)
    forge = step(store, token, programme)
    assert forge["stage"] == "forge"
    assert set(forge["parent_result_ids"]) == {str(left), str(right)}
    assert forge["goal"].startswith("Combine these partial solutions")
    forged = meta_run(database, token, [left, right], "Materials science", "Immunology")
    attach(store, token, programme, forge, forged)
    done = store.get(token, uuid.UUID(programme["id"]))
    assert done["outcome"] == "forged" and done["status"] == "completed"
    fits = [item for item in done["steps"] if item["stage"] == "fit"]
    assert [item["finding"]["field_left"] for item in fits] == [
        "Materials science",
        "Control theory",
    ]
    assert fits[0]["finding"]["falls_short"]
    assert done["steps"][-1]["finding"]["kind"] == "bridge"


def test_without_named_gaps_three_distant_places_are_searched(database):
    token, store = owner(database), SolveStore(database)
    programme = store.create(token, PROBLEM)
    find = step(store, token, programme)
    attach(store, token, programme, find, finished_run(database, token, mode="ask"))
    goals = []
    for index in range(3):
        due = step(store, token, programme)
        goals.append(due["goal"])
        run = (
            bridge_run(database, token, "Physics", "Biology")[0]
            if index == 0
            else (empty_run(database, token))
        )
        attach(store, token, programme, due, run)
    assert all(angle in goal for angle, goal in zip(ANGLES, goals, strict=True))
    assert store.next_action(token, uuid.UUID(programme["id"]))["state"] == "completed"
    done = store.get(token, uuid.UUID(programme["id"]))
    assert done["outcome"] == "close"
    assert "forge" not in [item["stage"] for item in done["steps"]]


def test_when_nothing_fits_the_outcome_says_so(database):
    token, store = owner(database), SolveStore(database)
    programme = store.create(token, PROBLEM)
    attach(
        store,
        token,
        programme,
        step(store, token, programme),
        finished_run(database, token, mode="ask"),
    )
    for _ in range(3):
        attach(store, token, programme, step(store, token, programme), empty_run(database, token))
    assert store.next_action(token, uuid.UUID(programme["id"])) == {
        "state": "completed",
        "outcome": "none",
    }


def test_a_failed_stage_retries_once_then_the_forge_still_uses_the_fits_that_held(database):
    token, store = owner(database), SolveStore(database)
    programme = store.create(token, PROBLEM)
    find = step(store, token, programme)
    attach(store, token, programme, find, finished_run(database, token, mode="ask"))
    first = step(store, token, programme)
    left_run, left = bridge_run(database, token, "Optics", "Geology")
    attach(store, token, programme, first, left_run)
    second = step(store, token, programme)
    right_run, right = bridge_run(database, token, "Acoustics", "Botany")
    attach(store, token, programme, second, right_run)
    third = step(store, token, programme)
    for _ in range(2):
        failed = finished_run(database, token, status="failed")
        attach(store, token, programme, third, failed)
        if _ == 0:
            assert step(store, token, programme)["step_id"] == third["step_id"]
    forge = step(store, token, programme)
    assert forge["stage"] == "forge"
    assert set(forge["parent_result_ids"]) == {str(left), str(right)}


def test_attach_requires_the_due_stage_an_owned_run_the_right_mode_and_premises(database):
    token, store = owner(database), SolveStore(database)
    programme = store.create(token, PROBLEM)
    due = step(store, token, programme)
    identifier, step_id = uuid.UUID(programme["id"]), uuid.UUID(due["step_id"])
    explore = finished_run(database, token, mode="explore")
    with pytest.raises(SolveError, match="solve_run_invalid"):
        store.attach(token, identifier, step_id, "find", explore)
    with pytest.raises(SolveError, match="solve_step_not_due"):
        store.attach(token, identifier, step_id, "fit", explore)
    stranger = owner(database, "stranger-session")
    foreign = finished_run(database, stranger, mode="ask")
    with pytest.raises(SolveError, match="solve_run_invalid"):
        store.attach(token, identifier, step_id, "find", foreign)
    with pytest.raises(SolveError, match="solve_not_found"):
        store.get(stranger, identifier)
    paused = store.set_status(token, identifier, "paused")
    assert paused["status"] == "paused"
    assert store.next_action(token, identifier) == {"state": "paused"}


def test_a_frontier_step_can_be_solved_and_stays_linked(database):
    token, store = owner(database), SolveStore(database)
    frontier = FrontierStore(database).create(token, "An open synthetic question?")
    node = frontier["nodes"][0]
    programme = store.create(
        token,
        frontier_programme_id=uuid.UUID(frontier["id"]),
        frontier_node_id=uuid.UUID(node["id"]),
    )
    assert programme["problem"] == node["statement"]
    assert programme["frontier_node_id"] == node["id"]
    with pytest.raises(SolveError, match="solve_frontier_step_not_found"):
        store.create(
            owner(database, "stranger-session"),
            frontier_programme_id=uuid.UUID(frontier["id"]),
            frontier_node_id=uuid.UUID(node["id"]),
        )


def test_goals_are_built_from_stored_text_and_bounded():
    assert "Problem: x" in goal_for("forge", "x", "x")
    assert len(goal_for("fit", "y" * 9000, "z" * 9000)) <= 4000
    with pytest.raises(SolveError):
        goal_for("unknown", "x", "x")


def test_api_requires_a_session_and_csrf_and_never_echoes_input(database):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_run_api import bootstrap

    from polymath.run_api import create_run_router
    from polymath.solve_api import create_solve_router

    app = FastAPI()
    app.include_router(create_run_router(database, availability=lambda: True))
    app.include_router(create_solve_router(database))
    with TestClient(app) as client:
        assert client.get("/api/v1/solve").status_code == 401
        assert client.get("/api/v1/solve/capabilities").json()["spends_directly"] is False
        headers = bootstrap(client)
        assert client.post("/api/v1/solve", json={"problem": PROBLEM}).status_code == 403
        created = client.post("/api/v1/solve", headers=headers, json={"problem": PROBLEM})
        assert created.status_code == 201
        identifier = created.json()["id"]
        due = client.post(f"/api/v1/solve/{identifier}/next", headers=headers).json()["next"]
        assert due["stage"] == "find" and due["mode"] == "ask"
        secret = "private-problem-text-never-echoed"
        invalid = client.post("/api/v1/solve", headers=headers, json={"problem": secret * 200})
        assert invalid.status_code == 422 and secret not in invalid.text
        stopped = client.post(f"/api/v1/solve/{identifier}/stop", headers=headers)
        assert stopped.json()["status"] == "stopped"
        assert client.post(f"/api/v1/solve/{identifier}/resume", headers=headers).status_code == 409
