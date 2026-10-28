"""External client over actual loopback uvicorn/PostgreSQL and synthetic workers."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import socket
import sys
import threading
import time
import urllib.request
import uuid
from contextlib import contextmanager
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_artifact_worker import ArtifactFixture
from test_full_text import FullTextFixtureModel, full_text_fixture_transport
from test_research_controller import AdaptiveFixture
from test_research_modes import AskFixtureModel, ModeFixtureTools, SurpriseFixtureModel
from test_worker import database as database_fixture
from test_worker import setup_worker

from polymath.access_models import BrowserSession
from polymath.budget import BudgetService
from polymath.config import Settings
from polymath.jobs import JobRepository
from polymath.literature import OpenLiteratureTools
from polymath.main import create_app
from polymath.models import ResearchRun

database = database_fixture
pytestmark = pytest.mark.integration
SCRIPT = Path(__file__).parents[2] / "scripts" / "polymath-client.py"


@pytest.fixture(scope="module")
def client_module():
    spec = importlib.util.spec_from_file_location("polymath_external_http_fixture", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def api_server(database):
    gates = SimpleNamespace(research=True, sharing=False)
    app = create_app(
        Settings(
            database_url=database.url.render_as_string(hide_password=False),
            environment="test",
            model_api_key=None,
            operator_bootstrap_token=None,
            _env_file=None,
        ),
        research_availability=lambda: gates.research,
        sharing_availability=lambda: gates.sharing,
    )
    with serve_app(app) as server:
        server.gates = gates
        yield server


@contextmanager
def serve_app(app):
    """Real HTTP fixture shared by legacy and native-public SDK journeys."""
    requests = []

    class RecordingApp:
        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                headers = dict(scope["headers"])
                requests.append(
                    {
                        "method": scope["method"],
                        "path": scope["path"],
                        "cookie": b"cookie" in headers,
                        "operator_header": any(b"operator" in name for name in headers),
                        "operator_cookie": b"polymath_operator" in headers.get(b"cookie", b""),
                        "last_event_id": headers.get(b"last-event-id", b"").decode("ascii"),
                    }
                )
            await app(scope, receive, send)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    sock.setblocking(False)
    server = uvicorn.Server(
        uvicorn.Config(
            RecordingApp(),
            host="127.0.0.1",
            port=sock.getsockname()[1],
            log_config=None,
            log_level="critical",
            access_log=False,
            lifespan="on",
            timeout_graceful_shutdown=1,
        )
    )
    errors = []

    def run():
        try:
            asyncio.run(server.serve(sockets=[sock]))
        except BaseException as error:
            errors.append(error)

    thread = threading.Thread(target=run, daemon=True, name="polymath-loopback-http-fixture")
    thread.start()
    deadline = time.monotonic() + 5
    try:
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started and not errors, "Loopback fixture server failed to start"
        yield SimpleNamespace(
            base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", requests=requests
        )
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        assert not thread.is_alive(), "Loopback fixture server did not stop"


def count(engine, model):
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model))


def owner_json(server, cookie_path, method, path, payload=None):
    """Producer routes outside the SDK, through actual HTTP and owned cookies."""
    jar = MozillaCookieJar(str(cookie_path))
    jar.load(ignore_discard=True, ignore_expires=True)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    headers = {"Accept": "application/json"}
    if method != "GET":
        with opener.open(server.base_url + "/api/v1/session", timeout=5) as response:
            csrf = json.load(response)["csrf_token"]
        headers |= {
            "X-CSRF-Token": csrf,
            "Origin": server.base_url,
            "Content-Type": "application/json",
        }
    request = urllib.request.Request(
        server.base_url + path,
        method=method,
        headers=headers,
        data=None if payload is None else json.dumps(payload).encode(),
    )
    with opener.open(request, timeout=5) as response:
        return json.load(response)


@contextmanager
def sdk(module, server, path):
    client = module.PolymathClient(server.base_url, cookie_jar=path, timeout=5)
    try:
        yield client
    finally:
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["explore", "ask", "surprise"])
async def test_external_http_create_worker_status_events_replay_and_result(
    database, api_server, client_module, tmp_path, mode
):
    cookie_path = tmp_path / "owner.cookies"
    with sdk(client_module, api_server, cookie_path) as client:
        session = client.init_session()
        assert set(session) == {"expires_at"}
        goal = "What does the synthetic redundancy evidence establish?"
        created = client.create_run(mode, goal)
        assert created["status"] == "queued" and created["mode"] == mode
        run_id = created["id"]
        if mode == "explore":
            model = FullTextFixtureModel()
            worker = setup_worker(
                database,
                model,
                tools_factory=lambda _: OpenLiteratureTools(
                    transport=full_text_fixture_transport()
                ),
            )
        else:
            model = AskFixtureModel() if mode == "ask" else SurpriseFixtureModel()
            worker = setup_worker(database, model, tools_factory=lambda _: ModeFixtureTools())
        outcome = await worker.run_once("loopback-http-client-fixture")
        assert outcome.status == "succeeded" and outcome.result_id
        status = client.get_run(run_id)
        assert status["status"] == "succeeded" and status["result_id"] == outcome.result_id
        result = client.get_result(run_id)
        assert result["mode"] == mode and result["status"] == (
            "answer" if mode == "ask" else "bridge"
        )
        if mode == "ask":
            assert result["finding"]["question"] == goal
            assert {claim["label"] for claim in result["finding"]["claims"]} == {
                "known",
                "postulated",
                "speculative",
            }
        if mode == "explore":
            assert len([doc for doc in result["sources"] if doc.get("coverage")]) == 2
        initial = list(client.events(run_id, after=0, max_events=2, max_seconds=5))
        activities = [event for event in initial if event["event"] == "activity"]
        assert [event["id"] for event in activities] == [1, 2]
        replay = list(
            client.events(run_id, after=activities[-1]["id"], max_events=100, max_seconds=5)
        )
        replay_ids = [event["id"] for event in replay if event["event"] == "activity"]
        assert replay_ids and min(replay_ids) > 2 and replay_ids == sorted(set(replay_ids))
        assert replay[-1]["event"] == "end"
        assert any(
            request["last_event_id"] == "2"
            for request in api_server.requests
            if request["path"].endswith("/events")
        )
    before = len(api_server.requests)
    with sdk(client_module, api_server, cookie_path) as reloaded:
        assert reloaded.get_run(run_id)["status"] == "succeeded"
        assert reloaded.list_runs()["runs"][0]["id"] == run_id
    assert not any(request["path"] == "/api/v1/session" for request in api_server.requests[before:])
    assert count(database, BrowserSession) == count(database, ResearchRun) == 1


def test_external_http_cancel_disabled_503_and_private_ownership(
    database, api_server, client_module, tmp_path
):
    with sdk(client_module, api_server, tmp_path / "owner.cookies") as owner:
        owner.init_session()
        created = owner.create_run("explore", "Synthetic queued cancellation.")
        cancelled = owner.cancel_run(created["id"])
        assert cancelled["status"] == "cancelled"
        assert JobRepository(database).get(uuid.UUID(created["id"])).status == "cancelled"
        with sdk(client_module, api_server, tmp_path / "foreign.cookies") as foreign:
            with pytest.raises(client_module.ClientError) as unauthenticated:
                foreign.get_run(created["id"])
            assert unauthenticated.value.status == 401 and count(database, BrowserSession) == 1
            foreign.init_session()
            with pytest.raises(client_module.ClientError) as denied:
                foreign.get_run(created["id"])
            assert denied.value.status == 404
        api_server.gates.research = False
        with pytest.raises(client_module.ClientError) as disabled:
            owner.create_run("ask", "Synthetic disabled request.")
        assert disabled.value.status == 503
        assert count(database, ResearchRun) == 1
        assert count(database, BrowserSession) == 2


@pytest.mark.asyncio
async def test_external_http_artifact_export_matches_exact_saved_markdown_hash(
    database, api_server, client_module, tmp_path
):
    cookie_path = tmp_path / "owner.cookies"
    with sdk(client_module, api_server, cookie_path) as client:
        client.init_session()
        client.create_run("explore", "Synthetic document source investigation.")
        worker = setup_worker(database, AdaptiveFixture())
        source = await worker.run_once("loopback-document-research")
        assert source.status == "succeeded" and source.result_id
        created = client.create_artifact("write_summary", source.result_id)
        model = ArtifactFixture()
        worker.model_factory = lambda _: model
        outcome = await worker.run_once("loopback-document-writer")
        assert outcome.status == "succeeded" and outcome.artifact_id
        artifact = client.get_artifact(outcome.artifact_id)
        assert artifact["run_id"] == created["id"] and artifact["status"] == "ready"
        output = tmp_path / "saved.md"
        receipt = client.export_artifact(outcome.artifact_id, output)
        payload = output.read_bytes()
        assert payload == artifact["markdown"].encode("utf-8")
        assert hashlib.sha256(payload).hexdigest() == artifact["markdown_sha256"]
        assert artifact["markdown_sha256"] in receipt.values()
        assert len(payload) in receipt.values()


@pytest.mark.asyncio
async def test_external_public_library_and_detail_send_no_owner_cookie_or_create_jobs(
    database, api_server, client_module, tmp_path
):
    api_server.gates.sharing = True
    cookie_path = tmp_path / "owner.cookies"
    with sdk(client_module, api_server, cookie_path) as client:
        client.init_session()
        client.create_run("explore", "Synthetic explicitly shared source investigation.")
        model = AdaptiveFixture()
        outcome = await setup_worker(database, model).run_once("loopback-shared-research")
        assert outcome.status == "succeeded"
        preview = owner_json(
            api_server,
            cookie_path,
            "GET",
            "/api/v1/shares/preview?source_result_id=" + outcome.result_id,
        )
        record = owner_json(
            api_server,
            cookie_path,
            "POST",
            "/api/v1/shares",
            {
                "source_result_id": outcome.result_id,
                "expected_projection_sha256": preview["projection_sha256"],
                "idempotency_key": str(uuid.uuid4()),
            },
        )
        before = len(api_server.requests)
        reservations = BudgetService(database).usage("fixture").reservations
        library = client.public_library()
        detail = client.public_finding(record["public_id"])
        graph = client.public_map()
        public_requests = api_server.requests[before:]
        assert {item["public_id"] for item in library["items"]} == {record["public_id"]}
        assert detail["projection_sha256"] == record["projection_sha256"]
        assert "source_result_id" not in detail
        assert graph["scope"] == "explicitly_shared_current_findings"
        assert public_requests and all(
            item["method"] == "GET"
            and not item["cookie"]
            and item["path"].startswith("/api/v1/public/")
            for item in public_requests
        )
        assert count(database, BrowserSession) == count(database, ResearchRun) == 1
        assert BudgetService(database).usage("fixture").reservations == reservations
