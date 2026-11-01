"""Studio concept art against real PostgreSQL with a mocked Meta image endpoint."""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from test_converge import bridge_run, meta_run
from test_memory import owner
from test_solve import answer_run
from test_worker import database as database_fixture

from polymath.budget import BudgetError, BudgetService, NativeImagePermit
from polymath.budget_models import BudgetAccount, BudgetReservation, BudgetRunLimit
from polymath.jobs import JobRepository
from polymath.studio import StudioError, StudioStore
from polymath.studio_images import IMAGES_URL, ImagePolicy, StudioImages, mime_of
from polymath.studio_models import StudioImage

database = database_fixture
pytestmark = pytest.mark.integration
ACCOUNT = "studio-image-account"
KEY = "synthetic-meta-key-never-real"
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 24
PRICE = 10_000


def account(engine, *, scope="research", kill=False):
    BudgetService(engine).create_account(
        ACCOUNT, hard_cap_microdollars=1_000_000, daily_cap_microdollars=500_000, max_in_flight=2
    )
    with Session(engine) as session, session.begin():
        session.execute(
            update(BudgetAccount)
            .where(BudgetAccount.id == ACCOUNT)
            .values(native_scope=scope, native_manifest_sha256="a" * 64, kill_switch=kill)
        )


def policy(**changes):
    values = dict(enabled=True, api_key=SecretStr(KEY), model="muse-image-1.0", account_id=ACCOUNT)
    return ImagePolicy(**{**values, **changes})


def designed(engine, token, title="Hush: a rotor that flies like an owl"):
    """A Studio programme with a saved invention."""
    store = StudioStore(engine)
    programme = store.create(token, "A quiet city helicopter", ["Under 65 dB"])
    identifier = uuid.UUID(programme["id"])

    def attach(run_id):
        due = store.next_action(token, identifier)
        store.attach(token, identifier, uuid.UUID(due["step_id"]), due["stage"], run_id)

    attach(answer_run(engine, token, gaps=["Quiet lift?", "Safe descent?"]))
    first_run, first = bridge_run(engine, token, "Ornithology", "Aeroacoustics")
    attach(first_run)
    second_run, second = bridge_run(engine, token, "Botany", "Aerospace")
    attach(second_run)
    attach(meta_run(engine, token, [first, second], "Ornithology", "Botany", title=title))
    return identifier


def meta(responder):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responder(request)

    return httpx.MockTransport(handler), seen


def image_reply(content=WEBP):
    return lambda request: httpx.Response(
        200, json={"created": 1, "data": [{"b64_json": base64.b64encode(content).decode()}]}
    )


def reservation(engine, image_id):
    with Session(engine) as session:
        image = session.get(StudioImage, image_id)
        return session.scalar(
            select(BudgetReservation).where(BudgetReservation.run_id == image.run_id)
        )


def test_a_design_is_painted_once_reserved_settled_at_the_flat_price_and_stored(database):
    account(database)
    token = owner(database)
    programme = designed(database, token)
    transport, seen = meta(image_reply())
    images = StudioImages(database, policy(), transport=transport)
    image_id, reservation_id = images.request(token, programme)
    held = reservation(database, image_id)
    assert held.status == "in_flight" and held.estimated_microdollars == PRICE
    assert images.list(token, programme)[0]["status"] == "rendering"
    with pytest.raises(StudioError, match="studio_image_rendering"):
        images.request(token, programme)
    assert images.execute(image_id, reservation_id) == "succeeded"
    assert len(seen) == 1 and str(seen[0].url) == IMAGES_URL
    assert seen[0].headers["Authorization"] == f"Bearer {KEY}"
    body = json.loads(seen[0].content)
    assert body["model"] == "muse-image-1.0" and body["n"] == 1
    assert body["response_format"] == "b64_json" and "Hush: a rotor" in body["prompt"]
    assert body["tool_enablement"] == {
        "enable_image_search": False,
        "enable_web_search": False,
        "enable_shell": False,
    }
    settled = reservation(database, image_id)
    assert settled.status == "settled" and settled.actual_microdollars == PRICE
    assert images.content(token, image_id) == (WEBP, "image/webp")
    with pytest.raises(StudioError, match="studio_image_not_found"):
        images.content(owner(database, "stranger-session"), image_id)
    # Executing again never sends a second request.
    assert images.execute(image_id, reservation_id) == "skipped" and len(seen) == 1


def test_meta_refusals_cost_nothing_and_ambiguous_outcomes_stay_unknown(database):
    account(database)
    token = owner(database)
    programme = designed(database, token)
    refusal = lambda request: httpx.Response(  # noqa: E731
        400, json={"error": {"code": "content_policy_violation", "param": "prompt"}}
    )

    def timeout(request):
        raise httpx.ReadTimeout("slow")

    for responder, status, held, error in (
        (refusal, "refused", "settled", "content_policy_400"),
        (
            lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("slow")),
            "unknown",
            "unknown",
            "timeout",
        ),
        (image_reply(b"not an image at all"), "unknown", "unknown", "usage_unavailable"),
    ):
        transport, _ = meta(responder)
        images = StudioImages(database, policy(), transport=transport)
        try:
            image_id, reservation_id = images.request(token, programme)
        except StudioError as failure:  # The two unknown holds use up the invention's limit.
            assert str(failure) == "studio_image_limit" and status == "unknown"
            break
        assert images.execute(image_id, reservation_id) == status
        row = reservation(database, image_id)
        assert row.status == held
        if held == "settled":
            assert row.actual_microdollars == 0
        assert images.list(token, programme)[-1]["error"] == error


def test_requests_respect_the_switch_the_armed_account_the_design_and_every_limit(database):
    token = owner(database)
    programme = designed(database, token)
    with pytest.raises(StudioError, match="studio_images_unavailable"):
        StudioImages(database, policy(enabled=False)).request(token, programme)
    account(database, scope="probe_only")
    with pytest.raises(StudioError, match="studio_images_unavailable"):
        StudioImages(database, policy()).request(token, programme)
    with Session(database) as session, session.begin():
        session.execute(
            update(BudgetAccount)
            .where(BudgetAccount.id == ACCOUNT)
            .values(native_scope="research", kill_switch=True)
        )
    with pytest.raises(StudioError, match="studio_image_budget"):
        StudioImages(database, policy()).request(token, programme)
    with Session(database) as session, session.begin():
        assert session.scalar(select(StudioImage.id)) is None  # Nothing half-made survives.
        session.execute(
            update(BudgetAccount).where(BudgetAccount.id == ACCOUNT).values(kill_switch=False)
        )
    undesigned = uuid.UUID(StudioStore(database).create(token, "Something else")["id"])
    with pytest.raises(StudioError, match="studio_image_needs_design"):
        StudioImages(database, policy()).request(token, undesigned)
    with pytest.raises(StudioError, match="studio_not_found"):
        StudioImages(database, policy()).request(owner(database, "stranger-session"), programme)
    transport, _ = meta(image_reply())
    images = StudioImages(database, policy(session_daily_limit=3), transport=transport)
    for _ in range(2):
        images.execute(*images.request(token, programme))
    with pytest.raises(StudioError, match="studio_image_limit"):
        images.request(token, programme)
    second = designed(database, token, title="Second invention")
    tight = StudioImages(database, policy(session_daily_limit=2), transport=transport)
    with pytest.raises(StudioError, match="studio_image_daily_limit"):
        tight.request(token, second)


def test_a_render_lost_in_flight_becomes_an_unknown_hold_never_free(database):
    account(database)
    token = owner(database)
    programme = designed(database, token)
    images = StudioImages(database, policy())
    image_id, _ = images.request(token, programme)
    with Session(database) as session, session.begin():
        session.execute(
            update(StudioImage)
            .where(StudioImage.id == image_id)
            .values(created_at=datetime.now(UTC) - timedelta(minutes=30))
        )
    assert images.list(token, programme)[0] == {
        **images.list(token, programme)[0],
        "status": "unknown",
        "error": "worker_lost",
    }
    assert reservation(database, image_id).status == "unknown"


def test_the_image_permit_only_covers_render_runs(database):
    account(database)
    run = JobRepository(database).create(mode="explore", goal="Synthetic research run")
    with Session(database) as session, session.begin():
        session.add(
            BudgetRunLimit(account_id=ACCOUNT, run_id=run.id, cap_microdollars=PRICE, max_calls=1)
        )
    with Session(database) as session, session.begin(), pytest.raises(BudgetError) as failure:
        BudgetService(database).reserve_in_session(
            session,
            ACCOUNT,
            run.id,
            call_key="studio-image-wrong-kind",
            estimated_microdollars=PRICE,
            native_permit=NativeImagePermit(ACCOUNT, run.id, "c" * 64),
        )
    assert failure.value.code == "native_dispatch_scope_mismatch"
    assert mime_of(WEBP) == "image/webp" and mime_of(b"\x89PNG\r\n\x1a\n...") == "image/png"
    assert mime_of(b"<svg>") is None


def test_routes_paint_in_the_background_and_serve_only_the_owner(database):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from test_run_api import bootstrap

    from polymath.run_api import create_run_router
    from polymath.studio_api import create_studio_router

    account(database)
    transport, seen = meta(image_reply())
    images = StudioImages(database, policy(), transport=transport)
    app = FastAPI()
    app.include_router(create_run_router(database, availability=lambda: True))
    app.include_router(create_studio_router(database, images=images))
    with TestClient(app) as client:
        assert client.get("/api/v1/studio/capabilities").json()["images_available"] is True
        headers = bootstrap(client)
        from polymath.run_api import COOKIE_NAME, _hash_token

        programme = designed(database, _hash_token(client.cookies.get(COOKIE_NAME)))
        path = f"/api/v1/studio/{programme}/images"
        assert client.post(path).status_code == 403
        started = client.post(path, headers=headers)
        assert started.status_code == 202 and started.json()["status"] == "rendering"
        listed = client.get(path).json()
        assert listed["available"] is True and listed["images"][0]["status"] == "succeeded"
        picture = client.get(f"/api/v1/studio/images/{started.json()['id']}")
        assert picture.status_code == 200 and picture.content == WEBP
        assert picture.headers["content-type"] == "image/webp"
        assert picture.headers["cache-control"] == "private, no-store"
        assert len(seen) == 1
    with TestClient(app) as stranger:
        bootstrap(stranger)
        assert stranger.get(f"/api/v1/studio/images/{started.json()['id']}").status_code == 404
