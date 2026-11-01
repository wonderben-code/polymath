"""Innovation Studio against real PostgreSQL with synthetic runs."""

import uuid

import pytest
from test_converge import bridge_run, empty_run, meta_run
from test_frontier import finished_run
from test_memory import owner
from test_solve import answer_run
from test_worker import database as database_fixture

from polymath.studio import ANGLES, WHERE, StudioError, StudioStore, goal_for

database = database_fixture
pytestmark = pytest.mark.integration
BRIEF = "A city helicopter quiet enough to land beside a hospital at night"
SPECS = ["Under 65 dB at 150 m", "Four passengers", "Safe if power fails"]


def step(store, token, programme):
    due = store.next_action(token, uuid.UUID(programme["id"]))
    assert due["state"] == "due", due
    return due


def attach(store, token, programme, due, run_id):
    return store.attach(
        token, uuid.UUID(programme["id"]), uuid.UUID(due["step_id"]), due["stage"], run_id
    )


def test_functions_become_inspirations_then_one_invention_then_a_stress_test(database):
    token, store = owner(database), StudioStore(database)
    programme = store.create(token, BRIEF, SPECS, "strange")
    assert programme["specs"] == SPECS and programme["wildness"] == "strange"
    brief = step(store, token, programme)
    assert brief["stage"] == "brief" and brief["mode"] == "ask"
    assert "Under 65 dB at 150 m" in brief["goal"] and "How could a design achieve" in brief["goal"]
    functions = ["How could a rotor achieve quiet lift?", "How could it descend without power?"]
    attach(store, token, programme, brief, answer_run(database, token, gaps=functions))

    premises = []
    for function, (left, right) in zip(
        functions, [("Ornithology", "Aeroacoustics"), ("Botany", "Aerospace")], strict=True
    ):
        due = step(store, token, programme)
        assert due["stage"] == "inspire" and due["mode"] == "explore"
        assert function in due["goal"] and WHERE["strange"] in due["goal"]
        run_id, result_id = bridge_run(database, token, left, right, title=f"{left} idea")
        premises.append(result_id)
        attach(store, token, programme, due, run_id)

    design = step(store, token, programme)
    assert design["stage"] == "design" and design["mode"] == "explore"
    assert set(design["parent_result_ids"]) == {str(item) for item in premises}
    assert design["goal"].startswith("Invent one concrete, new A city helicopter")
    assert "which are your own invention" in design["goal"]
    attach(
        store,
        token,
        programme,
        design,
        meta_run(database, token, premises, "Ornithology", "Aerospace", title="Hush rotor"),
    )

    test = step(store, token, programme)
    assert test["stage"] == "test" and test["mode"] == "ask" and test["parent_result_ids"] == []
    assert "Hush rotor" in test["goal"] and "Four passengers" in test["goal"]
    attach(store, token, programme, test, answer_run(database, token, gaps=["Rain erosion?"]))

    done = store.get(token, uuid.UUID(programme["id"]))
    assert done["status"] == "completed" and done["outcome"] == "designed"
    assert [item["stage"] for item in done["steps"]] == [
        "brief",
        "inspire",
        "inspire",
        "design",
        "test",
    ]
    assert done["steps"][3]["finding"]["title"] == "Hush rotor"
    assert done["steps"][3]["finding"]["mappings"]
    tested = done["steps"][4]["finding"]
    assert tested["kind"] == "answer" and tested["claims"]
    assert tested["open_questions"] == ["Rain erosion?"]
    assert store.next_action(token, uuid.UUID(programme["id"])) == {
        "state": "completed",
        "outcome": "designed",
    }


def test_without_named_functions_three_places_are_searched_and_one_idea_still_designs(database):
    token, store = owner(database), StudioStore(database)
    programme = store.create(token, BRIEF)
    attach(
        store,
        token,
        programme,
        step(store, token, programme),
        finished_run(database, token, mode="ask"),
    )
    goals = []
    for index in range(3):
        due = step(store, token, programme)
        goals.append(due["goal"])
        run = (
            bridge_run(database, token, "Mycology", "Materials", title="Fungal lattice")[0]
            if index == 1
            else empty_run(database, token)
        )
        attach(store, token, programme, due, run)
    assert all(angle in goal for angle, goal in zip(ANGLES, goals, strict=True))
    assert all(WHERE["far"] in goal for goal in goals)

    design = step(store, token, programme)
    # One inspiration cannot be a lineage parent, so it travels in the goal instead.
    assert design["parent_result_ids"] == [] and "Fungal lattice" in design["goal"]
    with pytest.raises(StudioError, match="studio_run_invalid"):
        attach(store, token, programme, design, finished_run(database, token, mode="ask"))
    attach(store, token, programme, design, bridge_run(database, token, "Mycology", "Rotors")[0])
    assert step(store, token, programme)["stage"] == "test"


def test_when_no_mechanism_is_found_the_outcome_says_so(database):
    token, store = owner(database), StudioStore(database)
    programme = store.create(token, BRIEF, wildness="near")
    attach(
        store,
        token,
        programme,
        step(store, token, programme),
        finished_run(database, token, mode="ask"),
    )
    for _ in range(3):
        due = step(store, token, programme)
        assert WHERE["near"] in due["goal"]
        attach(store, token, programme, due, empty_run(database, token))
    assert store.next_action(token, uuid.UUID(programme["id"])) == {
        "state": "completed",
        "outcome": "none",
    }
    stages = [item["stage"] for item in store.get(token, uuid.UUID(programme["id"]))["steps"]]
    assert "design" not in stages


def test_a_failed_design_retries_once_then_the_inspirations_remain(database):
    token, store = owner(database), StudioStore(database)
    programme = store.create(token, BRIEF, SPECS)
    attach(
        store,
        token,
        programme,
        step(store, token, programme),
        answer_run(database, token, gaps=["Quiet lift?", "Light blades?"]),
    )
    attach(
        store,
        token,
        programme,
        step(store, token, programme),
        bridge_run(database, token, "Entomology", "Aerospace")[0],
    )
    attach(store, token, programme, step(store, token, programme), empty_run(database, token))
    design = step(store, token, programme)
    for attempt in range(2):
        attach(store, token, programme, design, finished_run(database, token, status="failed"))
        if attempt == 0:
            assert step(store, token, programme)["step_id"] == design["step_id"]
    assert store.next_action(token, uuid.UUID(programme["id"])) == {
        "state": "completed",
        "outcome": "inspired",
    }


def test_requests_are_validated_owned_and_pausable(database):
    token, store = owner(database), StudioStore(database)
    for specs, wildness in (
        (["x"] * 2, "far"),
        ([f"spec {index}" for index in range(7)], "far"),
        (["ok"], "wild"),
        ([""], "far"),
        (["y" * 201], "far"),
    ):
        with pytest.raises(StudioError, match="invalid_studio_request"):
            store.create(token, BRIEF, specs, wildness)
    with pytest.raises(StudioError, match="invalid_studio_request"):
        store.create(token, "   ")
    programme = store.create(token, BRIEF)
    identifier = uuid.UUID(programme["id"])
    due = step(store, token, programme)
    with pytest.raises(StudioError, match="studio_step_not_due"):
        store.attach(token, identifier, uuid.UUID(due["step_id"]), "inspire", uuid.uuid4())
    stranger = owner(database, "stranger-session")
    foreign = finished_run(database, stranger, mode="ask")
    with pytest.raises(StudioError, match="studio_run_invalid"):
        attach(store, token, programme, due, foreign)
    with pytest.raises(StudioError, match="studio_not_found"):
        store.get(stranger, identifier)
    assert store.set_status(token, identifier, "paused")["status"] == "paused"
    assert store.next_action(token, identifier) == {"state": "paused"}
    store.set_status(token, identifier, "stopped")
    with pytest.raises(StudioError, match="studio_finished"):
        store.set_status(token, identifier, "active")


def test_goals_are_built_from_stored_text_and_bounded():
    long = "z" * 9000
    for stage in ("brief", "inspire", "design", "test"):
        assert len(goal_for(stage, long, long, [long[:200]] * 6, "far")) <= 4000
    assert "these mechanisms from different fields" in goal_for("design", "b", "b", [], "far")
    assert "this mechanism from another field: idea" in goal_for("design", "idea", "b", [], "far")
    with pytest.raises(StudioError):
        goal_for("unknown", "x", "x", [], "far")
    with pytest.raises(StudioError):
        goal_for("brief", "x", "x", [], "wild")


def test_api_requires_a_session_and_csrf_and_never_echoes_input(database):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_run_api import bootstrap

    from polymath.run_api import create_run_router
    from polymath.studio_api import create_studio_router

    app = FastAPI()
    app.include_router(create_run_router(database, availability=lambda: True))
    app.include_router(create_studio_router(database))
    with TestClient(app) as client:
        assert client.get("/api/v1/studio").status_code == 401
        capabilities = client.get("/api/v1/studio/capabilities").json()
        assert capabilities["spends_directly"] is False
        assert capabilities["max_investigations"] == 7 and capabilities["max_specs"] == 6
        headers = bootstrap(client)
        body = {"brief": BRIEF, "specs": SPECS, "wildness": "strange"}
        assert client.post("/api/v1/studio", json=body).status_code == 403
        created = client.post("/api/v1/studio", headers=headers, json=body)
        assert created.status_code == 201
        identifier = created.json()["id"]
        assert client.get("/api/v1/studio", headers=headers).json()["programmes"][0]["id"] == (
            identifier
        )
        due = client.post(f"/api/v1/studio/{identifier}/next", headers=headers).json()["next"]
        assert due["stage"] == "brief" and due["mode"] == "ask"
        secret = "private-brief-text-never-echoed"
        invalid = client.post(
            "/api/v1/studio", headers=headers, json={"brief": secret, "wildness": "wild"}
        )
        assert invalid.status_code == 422 and secret not in invalid.text
        stopped = client.post(f"/api/v1/studio/{identifier}/stop", headers=headers)
        assert stopped.json()["status"] == "stopped"
        assert (
            client.post(f"/api/v1/studio/{identifier}/resume", headers=headers).status_code == 409
        )
