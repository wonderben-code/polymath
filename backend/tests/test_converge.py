"""Experimental Converge programmes against real PostgreSQL with synthetic runs."""

import pytest
from sqlalchemy.orm import Session
from test_evidence import document, result_for
from test_memory import assign, owner
from test_worker import database as database_fixture

from polymath.converge import (
    MAX_FIELDS,
    STEP_ESTIMATE_MICRODOLLARS,
    ConvergeError,
    ConvergeStore,
    meta_goal,
    thread_goal,
)
from polymath.converge_fields import CATALOGUE, DOMAINS, match, normalise, spread, suggest
from polymath.evidence import EvidenceStore
from polymath.jobs import JobRepository
from polymath.lineage import bind_parents
from polymath.lineage_models import ResearchRunParent
from polymath.memory import ResearchMemory
from polymath.models import ResearchRun

database = database_fixture
CEILING = 25_000_000


# Pure behaviour ----------------------------------------------------------------


def test_catalogue_spreads_across_domains_and_skips_chosen_fields():
    assert len(CATALOGUE) >= MAX_FIELDS
    assert len({normalise(name) for name in CATALOGUE}) == len(CATALOGUE)
    picked = spread(["Ecology", "statistical MECHANICS"], 2 + len(DOMAINS))
    assert len(picked) == len(DOMAINS)
    assert "Statistical mechanics" not in picked and "Ecology" not in picked
    # One from each domain before any domain is used twice.
    domains = {domain for name in picked for domain, names in DOMAINS.items() if name in names}
    assert len(domains) == len(DOMAINS)
    assert suggest("eco")[:2] == ["Ecology", "Economics"]
    assert match("ecology (population dynamics)", ["Physics", "Ecology"]) == 1
    assert match("Tiny", ["Physics"]) is None


def test_goals_are_built_from_stored_names_and_stay_bounded():
    fields = list(CATALOGUE[:MAX_FIELDS])
    goal = thread_goal("A synthetic theme", fields, {0, 1}, ["Optics – Ecology"])
    assert goal.startswith("Converge across fields about A synthetic theme.")
    assert len(goal) <= 4000
    assert "level 3" in meta_goal(None, 3)


# Store behaviour -------------------------------------------------------------


def bridge_run(engine, token, left, right, *, title="Synthetic thread"):
    """A finished owned Explore run whose saved bridge names two fields."""
    jobs, store = JobRepository(engine), EvidenceStore(engine)
    run = jobs.create(mode="explore", goal="Synthetic converge step")
    assign(engine, run.id, token)
    lease = jobs.claim_next("synthetic-converge", lease_seconds=300)
    assert lease.run_id == run.id
    suffix = f":{run.id}"
    result = result_for(document("fixture:left" + suffix), document("fixture:right" + suffix))
    result = result.model_copy(
        update={
            "finding": result.finding.model_copy(
                update={"title": title, "field_left": left, "field_right": right}
            )
        }
    )
    for source in result.sources:
        store.save_source(lease, source)
    saved = store.save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    SAVED[saved.id] = result
    return run.id, saved.id


SAVED: dict = {}


def meta_run(engine, token, parents, left, right, title="Deeper"):
    """A finished deeper Explore that revisits every premise's original evidence."""
    from test_parent_evidence import child_result, read_refs

    jobs, store = JobRepository(engine), EvidenceStore(engine)
    run = jobs.create(mode="explore", goal="Synthetic deeper connection")
    assign(engine, run.id, token)
    with Session(engine) as session, session.begin():
        bind_parents(session, session.get(ResearchRun, run.id), token, [str(p) for p in parents])
    lease = jobs.claim_next("synthetic-converge-meta", lease_seconds=300)
    assert lease.run_id == run.id
    pairs = [(_Saved(value), SAVED[value]) for value in parents]
    refs = read_refs(ResearchMemory(engine, lease), pairs)
    sources = tuple(result.sources[index % 2] for index, (_, result) in enumerate(pairs))
    result = child_result(sources, refs)
    result = result.model_copy(
        update={
            "finding": result.finding.model_copy(
                update={"title": title, "field_left": left, "field_right": right}
            )
        }
    )
    for source in result.sources:
        store.save_source(lease, source)
    saved = store.save_result(lease, result)
    jobs.finish(lease, status="succeeded", outcome={"result_id": str(saved.id)})
    SAVED[saved.id] = result
    return run.id


class _Saved:
    def __init__(self, identifier):
        self.id = identifier


def empty_run(engine, token, *, parents=()):
    jobs = JobRepository(engine)
    run = jobs.create(mode="explore", goal="Synthetic converge step")
    assign(engine, run.id, token)
    with Session(engine) as session, session.begin():
        for index, saved in enumerate(parents):
            session.add(
                ResearchRunParent(
                    run_id=run.id,
                    parent_result_id=saved,
                    parent_run_id=_run_of(session, saved),
                    ordinal=index,
                    content_sha256="a" * 64,
                )
            )
    lease = jobs.claim_next("synthetic-converge", lease_seconds=300)
    jobs.finish(lease, status="succeeded", outcome={})
    return run.id


def _run_of(session, result_id):
    from polymath.evidence_models import EvidenceResult

    return session.get(EvidenceResult, result_id).run_id


def thread(store, engine, token, pid, left, right):
    due = store.next_action(token, pid)
    assert due["state"] == "due" and due["kind"] == "thread" and due["level"] == 1
    run_id, result_id = bridge_run(engine, token, left, right)
    store.attach(token, pid, "thread", [], run_id)
    return result_id


@pytest.mark.integration
def test_threads_touch_the_circle_then_converge_inward_to_one(database):
    token = owner(database)
    store = ConvergeStore(database)
    programme = store.create(
        token,
        theme="A synthetic theme",
        field_count=4,
        fields=["Ecology", "Optics", "Economics", "Linguistics"],
        ceiling_microdollars=CEILING,
    )
    pid = programme["id"]
    assert [field["origin"] for field in programme["fields"]] == ["chosen"] * 4
    first = store.next_action(token, pid)
    assert "Not yet touched: Ecology; Optics; Economics; Linguistics." in first["goal"]
    held = [
        thread(store, database, token, pid, "Ecology", "Optics"),
        thread(store, database, token, pid, "economics", "Linguistics (syntax)"),
    ]
    # Every field touched: the two held connections are drawn inward.
    due = store.next_action(token, pid)
    snapshot = store.get(token, pid)
    assert all(field["touched"] for field in snapshot["fields"])
    assert [step["field_indices"] for step in snapshot["steps"]] == [[0, 1], [2, 3]]
    assert due["kind"] == "meta" and due["level"] == 2
    assert due["parent_result_ids"] == [str(value) for value in held]
    wrong = empty_run(database, token, parents=held[:1])
    with pytest.raises(ConvergeError, match="converge_run_invalid"):
        store.attach(token, pid, "meta", due["parent_result_ids"], wrong)
    deeper = meta_run(database, token, held, "Living systems", "Signals")
    store.attach(token, pid, "meta", due["parent_result_ids"], deeper)
    assert store.next_action(token, pid) == {"state": "completed", "reason": "converged"}
    final = store.get(token, pid)
    meta = final["steps"][-1]
    assert meta["status"] == "held" and meta["level"] == 2
    assert meta["field_indices"] == [0, 1, 2, 3]
    assert meta["premise_step_ids"] == [final["steps"][0]["id"], final["steps"][1]["id"]]
    assert final["deepest_step_ids"] == [meta["id"]]


@pytest.mark.integration
def test_spread_fields_fill_the_circle_and_new_fields_are_found(database):
    token = owner(database, "converge-found")
    store = ConvergeStore(database)
    programme = store.create(
        token, theme=None, field_count=6, fields=["Ecology"], ceiling_microdollars=CEILING
    )
    assert len(programme["fields"]) == 6
    assert [field["origin"] for field in programme["fields"]].count("spread") == 5
    pid = programme["id"]
    thread(store, database, token, pid, "Ecology", "Mycorrhizal signalling")
    store.next_action(token, pid)
    fields = store.get(token, pid)["fields"]
    assert fields[-1] == {"name": "Mycorrhizal signalling", "origin": "found", "touched": True}


@pytest.mark.integration
def test_failed_threads_stop_after_a_streak_and_widening_tries_again(database):
    token = owner(database, "converge-streak")
    store = ConvergeStore(database)
    pid = store.create(token, theme=None, field_count=5, fields=[], ceiling_microdollars=CEILING)[
        "id"
    ]
    for _ in range(3):
        due = store.next_action(token, pid)
        assert due["kind"] == "thread"
        store.attach(token, pid, "thread", [], empty_run(database, token))
    assert store.next_action(token, pid) == {"state": "completed", "reason": "no_connections"}
    assert [step["status"] for step in store.get(token, pid)["steps"]] == ["not_held"] * 3
    widened = store.extend(token, pid, fields=["Ecology"], add_count=2, ceiling_microdollars=None)
    assert widened["status"] == "active" and len(widened["fields"]) == 7
    assert store.next_action(token, pid)["kind"] == "thread"


@pytest.mark.integration
def test_the_ceiling_holds_back_a_step_before_it_starts(database):
    token = owner(database, "converge-ceiling")
    store = ConvergeStore(database)
    small = STEP_ESTIMATE_MICRODOLLARS - 1
    pid = store.create(token, theme=None, field_count=3, fields=[], ceiling_microdollars=small)[
        "id"
    ]
    assert store.next_action(token, pid) == {"state": "completed", "reason": "ceiling"}
    with pytest.raises(ConvergeError, match="invalid_converge_ceiling"):
        store.extend(token, pid, fields=[], add_count=0, ceiling_microdollars=100_000)
    raised = store.extend(token, pid, fields=[], add_count=0, ceiling_microdollars=CEILING)
    assert raised["status"] == "active"
    assert store.next_action(token, pid)["state"] == "due"


@pytest.mark.integration
def test_requests_are_validated_and_owner_scoped(database):
    token, other = owner(database, "converge-a"), owner(database, "converge-b")
    store = ConvergeStore(database)
    for bad in (
        {"field_count": 2, "fields": []},
        {"field_count": 201, "fields": []},
        {"field_count": 3, "fields": ["A", "B", "C", "D"]},
        {"field_count": 3, "fields": ["\x00bad"]},
    ):
        with pytest.raises(ConvergeError):
            store.create(token, theme=None, ceiling_microdollars=CEILING, **bad)
    with pytest.raises(ConvergeError, match="invalid_converge_ceiling"):
        store.create(token, theme=None, field_count=3, fields=[], ceiling_microdollars=10)
    pid = store.create(token, theme=None, field_count=3, fields=[], ceiling_microdollars=CEILING)[
        "id"
    ]
    with pytest.raises(ConvergeError, match="converge_not_found"):
        store.get(other, pid)
    due = store.next_action(token, pid)
    run_id, _ = bridge_run(database, other, "Ecology", "Optics")
    with pytest.raises(ConvergeError, match="converge_run_invalid"):
        store.attach(token, pid, due["kind"], [], run_id)
    paused = store.set_status(token, pid, "paused")
    assert paused["status"] == "paused" and store.next_action(token, pid) == {"state": "paused"}


@pytest.fixture
def converge_api(monkeypatch):
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

    from polymath.converge_api import create_converge_router
    from polymath.run_api import create_run_router

    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL for Converge API checks")
    url = make_url(raw)
    schema = "polymath_converge_" + uuid.uuid4().hex
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
            app.include_router(create_converge_router(engine, lambda: enabled["value"]))
            yield engine, clients.enter_context(TestClient(app)), enabled
        finally:
            clients.close()
            engine.dispose()
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


@pytest.mark.integration
def test_api_requires_session_and_csrf_and_never_echoes_input(converge_api):
    from test_run_api import bootstrap

    _, client, enabled = converge_api
    assert client.get("/api/v1/converge").status_code == 401
    capabilities = client.get("/api/v1/converge/capabilities").json()
    assert capabilities["experimental"] is True and capabilities["spends_directly"] is False
    assert capabilities["max_fields"] == MAX_FIELDS
    assert client.get("/api/v1/converge/fields", params={"q": "eco"}).json()["fields"][0] == (
        "Ecology"
    )
    body = {"field_count": 12, "fields": ["Ecology"], "ceiling_microdollars": CEILING}
    headers = bootstrap(client)
    assert client.post("/api/v1/converge", json=body).status_code == 403
    created = client.post("/api/v1/converge", headers=headers, json=body)
    assert created.status_code == 201
    programme = created.json()
    assert len(programme["fields"]) == 12 and programme["spent_microdollars"] == 0
    step = client.post(f"/api/v1/converge/{programme['id']}/next", headers=headers).json()
    assert step["next"]["kind"] == "thread" and step["next"]["mode"] == "explore"
    secret = "private-theme-text-never-echoed"
    invalid = client.post(
        "/api/v1/converge", headers=headers, json={**body, "theme": secret, "field_count": 999}
    )
    assert invalid.status_code == 422 and secret not in invalid.text
    widened = client.post(
        f"/api/v1/converge/{programme['id']}/extend", headers=headers, json={"add_count": 3}
    )
    assert len(widened.json()["fields"]) == 15
    paused = client.post(f"/api/v1/converge/{programme['id']}/pause", headers=headers)
    assert paused.json()["status"] == "paused"
    enabled["value"] = False
    assert client.get("/api/v1/converge").status_code == 503
