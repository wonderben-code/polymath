"""Publishing levels and the autopilot ledger, against real PostgreSQL."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from test_publication import artifacts_api as artifacts_api_fixture
from test_publication import publication as publication_fixture

from polymath.artifact_models import ArtifactRequest
from polymath.publication_contracts import PublicationMetadata
from polymath.publication_job_models import PublicationJob
from polymath.publication_jobs import PublicationJobRepository
from polymath.publishing import PublishingError, PublishingStore
from polymath.publishing_models import PublishingPreference

artifacts_api = artifacts_api_fixture
publication = publication_fixture
pytestmark = pytest.mark.integration
ACTOR = "synthetic-publisher"
AUTHOR = [{"name": "Synthetic Author", "affiliation": None, "orcid": None}]


def settings(level, **changes):
    values = {
        "level": level,
        "draft_destination": "sandbox",
        "write_kind": "write_paper",
        "full_text_only": False,
        "daily_cap": 3,
        "creators": AUTHOR,
        "license": "cc-by-4.0",
        "keywords": ["synthetic"],
    }
    return {**values, **changes}


def enable(case, level, **changes):
    store = PublishingStore(case.engine, destinations=("sandbox", "production"))
    store.set_preferences(ACTOR, **settings(level, **changes))
    # The fixture's finding was saved moments before; count it as new.
    with Session(case.engine) as session, session.begin():
        session.execute(
            update(PublishingPreference)
            .where(PublishingPreference.actor_id == ACTOR)
            .values(enabled_at=func.clock_timestamp() - timedelta(hours=1))
        )
    return store


def source_of(case):
    with Session(case.engine) as session:
        return session.get(ArtifactRequest, case.artifact.run_id).source_result_id


def walk_to_ready(case, store):
    step = store.next_step(case.owner_hash, ACTOR)
    assert step["action"] == "write" and step["kind"] == "write_paper"
    assert step["result_id"] == str(source_of(case))
    item = step["item_id"]
    store.attach(case.owner_hash, ACTOR, item, "write", case.artifact.run_id)
    step = store.next_step(case.owner_hash, ACTOR)
    assert step["action"] == "prepare" and step["artifact_id"] == str(case.artifact.id)
    assert step["metadata"]["creators"][0]["name"] == "Synthetic Author"
    assert "not independent scientific verification" in step["metadata"]["description"]
    manifest = case.store.prepare(
        case.owner_hash,
        case.artifact.id,
        PublicationMetadata.model_validate(step["metadata"]),
        step["destination"],
    )
    store.attach(case.owner_hash, ACTOR, item, "prepare", manifest.id)
    return item, manifest


def test_level_one_does_nothing_and_level_two_stops_at_ready(publication):
    store = PublishingStore(publication.engine, destinations=("sandbox",))
    assert store.preferences(ACTOR)["level"] == 1
    assert store.next_step(publication.owner_hash, ACTOR) == {"action": "none", "reason": "manual"}
    store = enable(publication, 2)
    item, _ = walk_to_ready(publication, store)
    # Ready waits for the publisher's tap and never blocks newer findings.
    assert store.next_step(publication.owner_hash, ACTOR) == {
        "action": "none",
        "reason": "nothing_new",
    }
    [view] = store.items(publication.owner_hash, ACTOR)
    assert view["id"] == item and view["status"] == "ready" and view["destination"] == "sandbox"


def test_level_three_approves_and_queues_for_the_sandbox_then_records_publication(publication):
    store = enable(publication, 3)
    item, manifest = walk_to_ready(publication, store)
    step = store.next_step(publication.owner_hash, ACTOR)
    assert step["action"] == "approve" and step["manifest_sha256"] == manifest.manifest_sha256
    receipt = publication.store.approve(
        publication.owner_hash, manifest.id, manifest.manifest_sha256, publication.operator
    )
    store.attach(publication.owner_hash, ACTOR, item, "approve", receipt.id)
    step = store.next_step(publication.owner_hash, ACTOR)
    assert step["action"] == "queue" and step["approval_id"] == str(receipt.id)
    job = PublicationJobRepository(publication.engine, publication.store).enqueue(
        publication.owner_hash,
        manifest.id,
        receipt.id,
        manifest.manifest_sha256,
        publication.operator,
    )
    store.attach(publication.owner_hash, ACTOR, item, "queue", job.id)
    with Session(publication.engine) as session, session.begin():
        session.execute(
            update(PublicationJob).where(PublicationJob.id == job.id).values(status="published")
        )
    [view] = store.items(publication.owner_hash, ACTOR)
    assert view["status"] == "published" and view["job_id"] == str(job.id)


def test_production_needs_a_confirmation_and_lowering_the_level_never_lets_it_through(
    publication,
):
    store = PublishingStore(publication.engine, destinations=("sandbox", "production"))
    with pytest.raises(PublishingError, match="production_confirmation_required"):
        store.set_preferences(ACTOR, **settings(4))
    store = enable(publication, 4, confirm_production=True)
    assert store.preferences(ACTOR)["production_confirmed"] is True
    item, manifest = walk_to_ready(publication, store)
    assert manifest.manifest["destination"] == "production"
    store.set_preferences(ACTOR, **settings(3))
    assert store.preferences(ACTOR)["production_confirmed"] is False
    # A production-ready item is not approved at the sandbox level.
    assert store.next_step(publication.owner_hash, ACTOR)["action"] == "none"
    assert store.items(publication.owner_hash, ACTOR)[0]["status"] == "ready"
    skipped = store.skip(publication.owner_hash, ACTOR, item)
    assert skipped["status"] == "skipped"


def test_links_must_be_exact_and_settings_are_validated(publication):
    store = enable(publication, 2)
    step = store.next_step(publication.owner_hash, ACTOR)
    with pytest.raises(PublishingError, match="publishing_link_invalid"):
        store.attach(
            publication.owner_hash, ACTOR, step["item_id"], "write", publication.artifact.id
        )
    with pytest.raises(PublishingError, match="publishing_stage_not_due"):
        store.attach(
            publication.owner_hash, ACTOR, step["item_id"], "queue", publication.artifact.id
        )
    with pytest.raises(PublishingError, match="publishing_item_not_found"):
        store.attach("0" * 64, ACTOR, step["item_id"], "write", publication.artifact.run_id)
    with pytest.raises(PublishingError, match="publishing_metadata_invalid"):
        store.set_preferences(ACTOR, **settings(2, creators=[]))
    sandbox_only = PublishingStore(publication.engine, destinations=("sandbox",))
    with pytest.raises(PublishingError, match="publishing_destination_unavailable"):
        sandbox_only.set_preferences(ACTOR, **settings(4, confirm_production=True))


def test_full_text_bar_and_daily_cap_hold_back_new_work(publication):
    store = enable(publication, 2, full_text_only=True)
    assert store.next_step(publication.owner_hash, ACTOR) == {
        "action": "none",
        "reason": "nothing_new",
    }
    store = enable(publication, 2, daily_cap=1)
    assert store.next_step(publication.owner_hash, ACTOR)["action"] == "write"
    with Session(publication.engine) as session:
        assert session.scalar(select(func.count()).select_from(PublishingPreference)) == 1


@pytest.mark.parametrize("level", [1, 2])
def test_switching_on_never_sweeps_up_earlier_work(publication, level):
    store = PublishingStore(publication.engine, destinations=("sandbox",))
    store.set_preferences(ACTOR, **settings(level))
    expected = "manual" if level == 1 else "nothing_new"
    assert store.next_step(publication.owner_hash, ACTOR) == {"action": "none", "reason": expected}


def test_routes_need_a_publisher_session_and_report_status_plainly(monkeypatch):
    import os
    import uuid
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import make_url
    from test_run_api import bootstrap

    from polymath.publication_contracts import PublicationPolicy, PublisherIdentity
    from polymath.publishing_api import create_publishing_router
    from polymath.run_api import create_run_router

    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Set TEST_DATABASE_URL for publishing route checks")
    url = make_url(raw)
    schema = "polymath_publishing_" + uuid.uuid4().hex
    admin = create_engine(url, hide_parameters=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    scoped = url.update_query_dict({"options": f"-csearch_path={schema}"})
    monkeypatch.setenv("DATABASE_URL", scoped.render_as_string(hide_password=False))
    engine = create_engine(scoped, hide_parameters=True)
    try:
        command.upgrade(Config(str(Path(__file__).parents[1] / "alembic.ini")), "head")
        policy = PublicationPolicy(
            publisher=PublisherIdentity(identifier="synthetic", display_name="Synthetic Publisher"),
            allowed_destinations=("sandbox",),
        )
        app = FastAPI()
        app.include_router(create_run_router(engine, availability=lambda: True))
        app.include_router(create_publishing_router(engine, publication_policy=policy))
        with TestClient(app) as client:
            status = client.get("/api/v1/publishing/status").json()
            assert status == {
                "configured": True,
                "publisher": "Synthetic Publisher",
                "destinations": ["sandbox"],
                "publishing_runs": False,
            }
            headers = bootstrap(client)
            # No operator is configured, so no publisher session can exist.
            assert client.get("/api/v1/publishing/preferences").status_code == 503
            assert client.post("/api/v1/publishing/next", headers=headers).status_code == 503
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
