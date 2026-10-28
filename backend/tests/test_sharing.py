"""Explicit public grants with synthetic findings and real PostgreSQL."""

import json
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from test_assessments import assessment_result, enqueue, prepared, revised_result
from test_memory import complete, owner
from test_worker import database as database_fixture

from polymath.access_models import BrowserSession
from polymath.assessment_models import FindingHead
from polymath.assessments import AssessmentStore
from polymath.sharing import SharingError, SharingPolicy, SharingStore
from polymath.sharing_models import SharedFinding, SharedFindingRevocation

pytestmark = pytest.mark.integration
database = database_fixture


def prepare(engine, identity="sharing-owner"):
    token = owner(engine, identity)
    prior, original = complete(engine, token)
    return token, prior, original, SharingStore(engine, SharingPolicy(enabled=True))


def publish(engine, *, identity="sharing-owner"):
    token, prior, original, store = prepare(engine, identity)
    preview = store.preview(token, prior.id)
    saved = store.share(token, prior.id, preview.projection_sha256, uuid.uuid4())
    return token, prior, original, store, saved


def test_preview_is_exact_read_only_and_public_read_grants_no_private_authority(database):
    token, prior, original, store = prepare(database)
    before = store.public_map()
    assert before["nodes"] == []
    preview = store.preview(token, prior.id)
    assert preview.projection["finding"]["title"] == original.finding.title
    assert "source_result_id" not in json.dumps(preview.projection)
    with Session(database) as session:
        assert session.get(FindingHead, prior.id) is None
        assert session.scalar(select(func.count()).select_from(SharedFinding)) == 0
        assert preview.expires_at == session.get(BrowserSession, token).expires_at
    shared = store.share(token, prior.id, preview.projection_sha256, uuid.uuid4())
    assert len(shared.public_id) == 43 and shared.status == "current"
    detail = store.get_public(shared.public_id)
    assert detail["projection"] == preview.projection
    assert detail["projection_sha256"] == preview.projection_sha256
    assert set(detail) == {
        "public_id",
        "projection",
        "projection_sha256",
        "created_at",
        "expires_at",
        "status",
    }
    assert str(prior.id) not in json.dumps(detail)
    assert str(prior.run_id) not in json.dumps(detail)
    assert token not in json.dumps(detail)
    with Session(database) as session:
        assert session.get(FindingHead, prior.id).revision == 0


def test_exact_preview_hash_and_source_ownership_are_required_before_activation(database):
    token, prior, _, store = prepare(database)
    with pytest.raises(SharingError, match="sharing_projection_changed"):
        store.share(token, prior.id, "0" * 64, uuid.uuid4())
    with pytest.raises(SharingError, match="sharing_not_found"):
        store.preview(owner(database, "foreign-share-owner"), prior.id)
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(SharedFinding)) == 0
        assert session.get(FindingHead, prior.id) is None


def test_idempotency_returns_same_grant_and_conflicting_body_cannot_reuse_key(database):
    token, prior, _, store = prepare(database)
    preview, key = store.preview(token, prior.id), uuid.uuid4()
    one = store.share(token, prior.id, preview.projection_sha256, key)
    two = store.share(token, prior.id, preview.projection_sha256, key)
    assert one == two
    with pytest.raises(SharingError, match="sharing_idempotency_conflict"):
        store.share(token, prior.id, "0" * 64, key)
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(SharedFinding)) == 1


def test_unknown_post_retry_after_revocation_never_reactivates_original_handle(database):
    token, prior, _, store, saved = publish(database)
    revoked = store.revoke(token, saved.public_id)
    assert revoked.status == "revoked" and revoked.revoked_at is not None
    assert store.revoke(token, saved.public_id) == revoked
    retried = store.share(token, prior.id, saved.projection_sha256, saved.idempotency_key)
    assert retried.public_id == saved.public_id and retried.status == "revoked"
    with pytest.raises(SharingError, match="sharing_not_found"):
        store.get_public(saved.public_id)
    assert store.list_public()["items"] == [] and store.public_map()["nodes"] == []
    assert store.list_owned(token)[0].status == "revoked"
    replacement = store.share(token, prior.id, saved.projection_sha256, uuid.uuid4())
    assert replacement.public_id != saved.public_id


def test_unsupported_head_hides_discovery_but_retains_pinned_public_snapshot(database):
    token, prior, _, watch, versions, _ = prepared(database)
    store = SharingStore(database, SharingPolicy(enabled=True))
    preview = store.preview(token, prior.id)
    saved = store.share(token, prior.id, preview.projection_sha256, uuid.uuid4())
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    assessments = AssessmentStore(database)
    assessments.save(lease, assessment_result(database, assessments, lease))
    detail = store.get_public(saved.public_id)
    assert detail["status"] == "unsupported" and detail["projection"] == saved.projection
    assert store.list_public()["items"] == [] and store.public_map()["nodes"] == []
    assert store.share(token, prior.id, saved.projection_sha256, saved.idempotency_key).status == (
        "unsupported"
    )
    with pytest.raises(SharingError, match="sharing_source_not_current"):
        store.share(token, prior.id, saved.projection_sha256, uuid.uuid4())


def test_superseded_share_never_reveals_private_successor_or_auto_shares_it(database):
    token, prior, _, watch, versions, _ = prepared(database)
    store = SharingStore(database, SharingPolicy(enabled=True))
    saved = store.share(
        token, prior.id, store.preview(token, prior.id).projection_sha256, uuid.uuid4()
    )
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    assessments = AssessmentStore(database)
    applied = assessments.save(lease, revised_result(database, assessments, lease))
    detail = store.get_public(saved.public_id)
    assert detail["status"] == "superseded" and detail["projection"] == saved.projection
    assert str(applied.candidate_result_id) not in json.dumps(detail)
    assert str(applied.id) not in json.dumps(detail)
    assert store.list_public()["items"] == []
    assert len(store.list_owned(token)) == 1
    assert store.share(token, prior.id, saved.projection_sha256, saved.idempotency_key).status == (
        "superseded"
    )


def test_unchanged_assessment_keeps_exact_share_current(database):
    token, prior, _, watch, versions, _ = prepared(database)
    store = SharingStore(database, SharingPolicy(enabled=True))
    saved = store.share(
        token, prior.id, store.preview(token, prior.id).projection_sha256, uuid.uuid4()
    )
    lease = enqueue(database, token, prior.id, watch.id, [versions[0].version_id])
    assessments = AssessmentStore(database)
    assessments.save(
        lease, assessment_result(database, assessments, lease, disposition="unchanged")
    )
    assert store.get_public(saved.public_id)["status"] == "current"
    assert store.get_public(saved.public_id)["projection_sha256"] == saved.projection_sha256
    assert store.list_public()["items"][0]["public_id"] == saved.public_id


def test_foreign_owner_cannot_revoke_or_discover_private_management_record(database):
    _, _, _, store, saved = publish(database)
    other = owner(database, "foreign-sharing-session")
    assert store.list_owned(other) == ()
    with pytest.raises(SharingError, match="sharing_not_found"):
        store.revoke(other, saved.public_id)
    assert store.get_public(saved.public_id)["status"] == "current"


def test_disabled_gate_hides_public_and_new_grants_but_preserves_owner_controls(database):
    token, prior, _, _, saved = publish(database)
    disabled = SharingStore(database)
    with pytest.raises(SharingError, match="sharing_unavailable"):
        disabled.preview(token, prior.id)
    with pytest.raises(SharingError, match="sharing_unavailable"):
        disabled.share(token, prior.id, saved.projection_sha256, uuid.uuid4())
    for read in (
        lambda: disabled.get_public(saved.public_id),
        disabled.list_public,
        disabled.public_map,
    ):
        with pytest.raises(SharingError, match="sharing_not_found"):
            read()
    assert disabled.list_owned(token)[0].public_id == saved.public_id
    assert disabled.revoke(token, saved.public_id).status == "revoked"


def test_session_expiry_hides_all_public_surfaces_and_owner_cannot_grant_again(database):
    token, prior, _, store, saved = publish(database)
    with database.begin() as connection:
        connection.execute(
            update(BrowserSession)
            .where(BrowserSession.token_hash == token)
            .values(expires_at=func.clock_timestamp())
        )
    for read in (lambda: store.get_public(saved.public_id),):
        with pytest.raises(SharingError, match="sharing_not_found"):
            read()
    assert store.list_public()["items"] == [] and store.public_map()["nodes"] == []
    with pytest.raises(SharingError, match="sharing_not_found"):
        store.share(token, prior.id, saved.projection_sha256, saved.idempotency_key)


def test_public_literal_search_and_pagination_are_bounded_and_owner_history_is_complete(database):
    token, prior, _, store = prepare(database)
    projection = store.preview(token, prior.id)
    records = [
        store.share(token, prior.id, projection.projection_sha256, uuid.uuid4()) for _ in range(3)
    ]
    assert store.list_public(query="%_")["items"] == []
    assert store.list_public(query="Synthetic")["items"]
    page = store.list_public(limit=2)
    assert len(page["items"]) == 2 and page["next_cursor"] is not None
    rest = store.list_public(limit=2, before=page["next_cursor"])
    assert len(rest["items"]) == 1 and rest["next_cursor"] is None
    assert len(store.list_owned(token, source_result_id=prior.id)) == 3
    assert len(store.list_owned(token, limit=2, before=records[2].public_id)) == 2
    store.revoke(token, records[1].public_id)
    assert len(store.list_owned(token)) == 3
    assert len(store.list_public()["items"]) == 2
    for bad in ("x" * 201, "bad\x00query"):
        with pytest.raises(SharingError, match="sharing_invalid_request"):
            store.list_public(query=bad)


def test_public_map_contains_only_explicit_shared_fields_and_citation_papers(database):
    _, prior, _, store, saved = publish(database)
    graph = store.public_map()
    assert graph["scope"] == "explicitly_shared_current_findings" and not graph["truncated"]
    assert {node["kind"] for node in graph["nodes"]} == {"finding", "field", "paper"}
    ids = {node["id"] for node in graph["nodes"]}
    assert all(edge["source"] in ids and edge["target"] in ids for edge in graph["edges"])
    assert any(node["public_id"] == saved.public_id for node in graph["nodes"])
    assert str(prior.id) not in json.dumps(graph) and str(prior.run_id) not in json.dumps(graph)
    assert graph["coverage"] == {"max_findings": 60, "max_nodes": 200, "max_edges": 500}


def test_active_quotas_are_shared_across_owner_jobs_and_revoke_frees_capacity(database):
    token, prior, _, _ = prepare(database)
    store = SharingStore(database, SharingPolicy(enabled=True, max_active_per_owner=1))
    saved = store.share(
        token, prior.id, store.preview(token, prior.id).projection_sha256, uuid.uuid4()
    )
    with pytest.raises(SharingError, match="sharing_active_limit"):
        store.share(token, prior.id, saved.projection_sha256, uuid.uuid4())
    assert store.share(token, prior.id, saved.projection_sha256, saved.idempotency_key) == saved
    store.revoke(token, saved.public_id)
    assert store.share(token, prior.id, saved.projection_sha256, uuid.uuid4()).status == "current"


def test_global_active_quota_applies_across_different_browser_owners(database):
    _, _, _, _, _ = publish(database)
    token, prior, _, _ = prepare(database, "second-sharing-owner")
    store = SharingStore(database, SharingPolicy(enabled=True, max_active_global=1))
    preview = store.preview(token, prior.id)
    with pytest.raises(SharingError, match="sharing_global_limit"):
        store.share(token, prior.id, preview.projection_sha256, uuid.uuid4())


def test_projection_and_revocation_are_database_immutable(database):
    token, _, _, store, saved = publish(database)
    store.revoke(token, saved.public_id)
    for model, values in (
        (SharedFinding, {"projection_sha256": "0" * 64}),
        (SharedFindingRevocation, {"owner_session_hash": token}),
    ):
        for statement in (update(model).values(**values), delete(model)):
            with pytest.raises(IntegrityError):
                with database.begin() as connection:
                    connection.execute(statement)


def test_downgrade_refuses_public_history_and_schema_matches_models(database):
    publish(database)
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    command.check(config)
    with database.connect() as connection:
        before = connection.scalar(text("SELECT version_num FROM alembic_version"))
    with pytest.raises(DBAPIError):
        command.downgrade(config, "0017_arxiv_full_text")
    with database.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == before


def test_concurrent_idempotent_activation_creates_one_public_handle(database):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    token, prior, _, store = prepare(database)
    preview, key, ready = store.preview(token, prior.id), uuid.uuid4(), Barrier(2)

    def activate():
        ready.wait(timeout=10)
        return store.share(token, prior.id, preview.projection_sha256, key)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(activate), pool.submit(activate)
        assert first.result(timeout=10) == second.result(timeout=10)
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(SharedFinding)) == 1


def test_browser_expiry_while_waiting_head_cannot_commit_a_share(database):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import event

    from polymath.assessment_eligibility import lock_current_source

    token, prior, _, store = prepare(database)
    preview = store.preview(token, prior.id)
    with Session(database) as session, session.begin():
        lock_current_source(session, prior.id)
    waiting = Event()

    def observe(connection, cursor, statement, parameters, context, executemany):
        if "SELECT finding_heads" in statement and "FOR UPDATE" in statement:
            waiting.set()

    event.listen(database, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with database.begin() as blocker:
                blocker.execute(
                    select(FindingHead)
                    .where(FindingHead.root_result_id == prior.id)
                    .with_for_update()
                )
                waiting.clear()
                future = pool.submit(
                    store.share, token, prior.id, preview.projection_sha256, uuid.uuid4()
                )
                assert waiting.wait(timeout=10)
                with database.begin() as connection:
                    connection.execute(
                        update(BrowserSession)
                        .where(BrowserSession.token_hash == token)
                        .values(expires_at=func.clock_timestamp())
                    )
            with pytest.raises(SharingError, match="sharing_not_found"):
                future.result(timeout=10)
    finally:
        event.remove(database, "before_cursor_execute", observe)
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(SharedFinding)) == 0


def test_browser_expiry_while_waiting_global_quota_cannot_commit_a_share(database):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sqlalchemy import event

    from polymath.sharing import SHARING_QUOTA_LOCK

    token, prior, _, store = prepare(database)
    preview, waiting = store.preview(token, prior.id), Event()

    def observe(connection, cursor, statement, parameters, context, executemany):
        if "pg_advisory_xact_lock" in statement:
            waiting.set()

    event.listen(database, "before_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with database.begin() as blocker:
                blocker.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": SHARING_QUOTA_LOCK}
                )
                waiting.clear()
                future = pool.submit(
                    store.share, token, prior.id, preview.projection_sha256, uuid.uuid4()
                )
                assert waiting.wait(timeout=10)
                with database.begin() as connection:
                    connection.execute(
                        update(BrowserSession)
                        .where(BrowserSession.token_hash == token)
                        .values(expires_at=func.clock_timestamp())
                    )
            with pytest.raises(SharingError, match="sharing_not_found"):
                future.result(timeout=10)
    finally:
        event.remove(database, "before_cursor_execute", observe)
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(SharedFinding)) == 0


def test_expired_share_stays_in_owner_history_and_never_extends_with_browser(database):
    from threading import Event

    token, prior, _, store = prepare(database)
    with database.begin() as connection:
        connection.execute(
            update(BrowserSession)
            .where(BrowserSession.token_hash == token)
            .values(expires_at=func.clock_timestamp() + text("interval '2 seconds'"))
        )
    preview = store.preview(token, prior.id)
    saved = store.share(token, prior.id, preview.projection_sha256, uuid.uuid4())
    assert saved.expires_at == preview.expires_at
    # A future browser-renewal implementation must not extend an old public grant.
    with database.begin() as connection:
        connection.execute(
            update(BrowserSession)
            .where(BrowserSession.token_hash == token)
            .values(expires_at=func.clock_timestamp() + text("interval '1 day'"))
        )
    Event().wait(2.05)
    assert store.list_owned(token)[0].status == "expired"
    retried = store.share(token, prior.id, saved.projection_sha256, saved.idempotency_key)
    assert retried.public_id == saved.public_id and retried.status == "expired"
    with pytest.raises(SharingError, match="sharing_not_found"):
        store.get_public(saved.public_id)
    assert store.list_public()["items"] == []


def test_map_candidates_and_graph_size_are_explicitly_bounded(database):
    token, prior, _, _ = prepare(database)
    store = SharingStore(database, SharingPolicy(enabled=True, max_active_per_owner=100))
    preview = store.preview(token, prior.id)
    for _ in range(61):
        store.share(token, prior.id, preview.projection_sha256, uuid.uuid4())
    graph = store.public_map()
    assert graph["truncated"] and len(graph["nodes"]) <= 200 and len(graph["edges"]) <= 500
    assert sum(node["kind"] == "finding" for node in graph["nodes"]) == 60
    node_ids = {node["id"] for node in graph["nodes"]}
    assert all(edge["source"] in node_ids and edge["target"] in node_ids for edge in graph["edges"])


def test_public_cursor_uses_last_visible_item_when_candidate_is_revoked_during_read(
    database, monkeypatch
):
    import polymath.sharing as sharing

    token, prior, _, store = prepare(database)
    preview = store.preview(token, prior.id)
    oldest, middle, newest = [
        store.share(token, prior.id, preview.projection_sha256, uuid.uuid4()) for _ in range(3)
    ]
    original, changed = sharing._status, False

    def revoke_before_last_status(session, row):
        nonlocal changed
        if row.public_id == middle.public_id and not changed:
            changed = True
            store.revoke(token, row.public_id)
        return original(session, row)

    monkeypatch.setattr(sharing, "_status", revoke_before_last_status)
    page = store.list_public(limit=2)
    assert [item["public_id"] for item in page["items"]] == [newest.public_id]
    assert page["next_cursor"] == newest.public_id
    following = store.list_public(limit=2, before=page["next_cursor"])
    assert [item["public_id"] for item in following["items"]] == [oldest.public_id]
    assert following["next_cursor"] is None


def test_all_filtered_public_page_ends_safely_and_reload_discovers_remaining_shares(
    database, monkeypatch
):
    import polymath.sharing as sharing

    token, prior, _, store = prepare(database)
    preview = store.preview(token, prior.id)
    oldest, newest = [
        store.share(token, prior.id, preview.projection_sha256, uuid.uuid4()) for _ in range(2)
    ]
    original, changed = sharing._status, False

    def revoke_before_status(session, row):
        nonlocal changed
        if row.public_id == newest.public_id and not changed:
            changed = True
            store.revoke(token, row.public_id)
        return original(session, row)

    monkeypatch.setattr(sharing, "_status", revoke_before_status)
    page = store.list_public(limit=1)
    assert page == {"items": [], "next_cursor": None}
    reloaded = store.list_public(limit=1)
    assert [item["public_id"] for item in reloaded["items"]] == [oldest.public_id]
