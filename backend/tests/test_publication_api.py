"""Owned publication previews and human receipts, with no external publishing."""

import hashlib
import json
import uuid
from datetime import timedelta

import pytest
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_artifact_api import artifacts_api as artifact_api_fixture
from test_artifact_api import complete_artifact
from test_knowledge import seed
from test_run_api import bootstrap

from polymath.operator_auth import OperatorPolicy, create_operator_router
from polymath.operator_models import OperatorSession
from polymath.publication_api import create_publication_router
from polymath.publication_contracts import PublicationPolicy, PublisherIdentity
from polymath.publication_models import (
    PublicationApproval,
    PublicationManifestRecord,
    PublicationRevocation,
)

artifacts_api = artifact_api_fixture
pytestmark = pytest.mark.integration
FIXTURE_TOKEN = "Fixture_Operator_0123456789abcdef-ABCDEF"
ROTATED_TOKEN = "Rotated_Operator_fedcba9876543210-FEDCBA"


def publisher_policy(*, identifier="fixture-publisher", approval_seconds=1200):
    return PublicationPolicy(
        publisher=PublisherIdentity(identifier=identifier, display_name="Synthetic Publisher"),
        allowed_destinations=("sandbox", "production"),
        approval_seconds=approval_seconds,
    )


def operator_policy(*, token=FIXTURE_TOKEN):
    return OperatorPolicy(
        bootstrap_token=SecretStr(token),
        actor_id="fixture-publisher",
        display_name="Synthetic Publisher",
    )


@pytest.fixture
def publications_api(artifacts_api):
    engine, artifact_factory = artifacts_api

    def factory(*, publication=True, operator=True, publication_policy=None, auth_policy=None):
        client = artifact_factory(enabled=True)
        pub_policy = publication_policy or (publisher_policy() if publication else None)
        op_policy = auth_policy or (operator_policy() if operator else None)
        client.app.include_router(create_operator_router(engine, op_policy))
        client.app.include_router(
            create_publication_router(engine, policy=pub_policy, operator_policy=op_policy)
        )
        return client

    return engine, factory


def metadata(**changes):
    return {
        "title": "Synthetic research document",
        "description": "A source-backed synthetic fixture, not a scientific discovery.",
        "creators": [{"name": "Synthetic Author"}],
        "keywords": ["synthetic fixture"],
        "license": "cc-by-4.0",
        **changes,
    }


def prepare(client, version_id, *, headers=None, destination="sandbox", **changes):
    return client.post(
        "/api/v1/publications",
        headers=bootstrap(client) if headers is None else headers,
        json={
            "artifact_id": str(version_id),
            "metadata": metadata(),
            "destination": destination,
            **changes,
        },
    )


def document(engine, client, *, suffix="publication"):
    _, source_id = seed(engine, client, suffix=suffix)
    return complete_artifact(engine, client, source_id)[1]


def login(client, *, token=FIXTURE_TOKEN):
    headers = bootstrap(client)
    response = client.post("/api/v1/operator/session", headers=headers, json={"token": token})
    assert response.status_code == 200, response.text
    assert response.json()["authenticated"]
    return {**headers, "X-Operator-CSRF-Token": response.json()["csrf_token"]}


def approve(client, record, headers):
    return client.post(
        f"/api/v1/publications/{record['id']}/approve",
        headers=headers,
        json={"expected_manifest_sha256": record["manifest_sha256"]},
    )


def test_default_policy_capabilities_and_prepare_are_disabled(publications_api):
    engine, factory = publications_api
    client = factory(publication=False, operator=False)
    capabilities = client.get("/api/v1/publications/capabilities")
    assert capabilities.status_code == 200
    assert capabilities.json() == {
        "configured": False,
        "publisher": None,
        "destinations": [],
        "can_prepare": False,
    }
    assert capabilities.headers["cache-control"] == "no-store"
    assert capabilities.headers["x-content-type-options"] == "nosniff"
    artifact = document(engine, client)
    response = prepare(client, artifact.id)
    assert response.status_code == 503
    assert response.json() == {"detail": "publication_unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationManifestRecord)) == 0


def test_preview_pins_exact_bytes_metadata_publisher_and_destination(publications_api):
    engine, factory = publications_api
    client = factory()
    artifact = document(engine, client)
    response = prepare(client, artifact.id)
    assert response.status_code == 201, response.text
    record = response.json()
    manifest = record["manifest"]
    assert record["publication_status"] == "not_published"
    assert record["approval"] is None
    assert manifest["artifact_id"] == str(artifact.id)
    assert manifest["markdown_sha256"] == artifact.markdown_sha256
    assert manifest["markdown_bytes"] == len(artifact.markdown.encode("utf-8"))
    assert manifest["payload_sha256"] == artifact.payload_sha256
    assert manifest["basis_sha256"] == artifact.basis_sha256
    assert manifest["metadata"]["title"] == metadata()["title"]
    assert manifest["publisher"] == {
        "identifier": "fixture-publisher",
        "display_name": "Synthetic Publisher",
    }
    assert manifest["destination"] == "sandbox"
    canonical = json.dumps(manifest, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert hashlib.sha256(canonical.encode("utf-8")).hexdigest() == record["manifest_sha256"]
    marker_basis = {key: value for key, value in manifest.items() if key != "remote_marker"}
    marker_json = json.dumps(
        marker_basis, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    assert (
        manifest["remote_marker"]
        == "polymath-" + hashlib.sha256(marker_json.encode("utf-8")).hexdigest()
    )
    assert client.get(record["artifact_export_url"]).content == artifact.markdown.encode("utf-8")
    assert client.get(f"/api/v1/publications/{record['id']}").json() == record
    same = prepare(client, artifact.id).json()
    assert same["id"] == record["id"] and same["manifest_sha256"] == record["manifest_sha256"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationManifestRecord)) == 1
        assert session.scalar(select(func.count()).select_from(PublicationApproval)) == 0


def test_approval_requires_exact_manifest_and_both_browser_and_operator_auth(publications_api):
    engine, factory = publications_api
    client = factory()
    artifact = document(engine, client)
    record = prepare(client, artifact.id).json()
    browser_headers = bootstrap(client)
    assert approve(client, record, browser_headers).status_code == 401
    headers = login(client)
    assert approve(client, record, {}).status_code == 403
    assert (
        approve(client, record, {**headers, "X-Operator-CSRF-Token": "incorrect"}).status_code
        == 403
    )
    assert (
        approve(client, record, {**headers, "Origin": "https://attacker.invalid"}).status_code
        == 403
    )
    changed = client.post(
        f"/api/v1/publications/{record['id']}/approve",
        headers=headers,
        json={"expected_manifest_sha256": "0" * 64},
    )
    assert changed.status_code == 409
    assert changed.json() == {"detail": "publication_manifest_changed"}
    approved = approve(client, record, headers)
    assert approved.status_code == 200, approved.text
    outcome = approved.json()
    assert outcome["publication_status"] == "not_published"
    receipt = outcome["approval"]
    assert receipt["valid"]
    assert receipt["actor_id"] == "fixture-publisher"
    assert receipt["manifest_sha256"] == record["manifest_sha256"]
    assert receipt["revoked_at"] is None
    repeated = approve(client, record, headers)
    assert repeated.status_code == 200
    assert repeated.json()["approval"]["id"] == receipt["id"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationApproval)) == 1


def test_metadata_destination_and_publisher_changes_cannot_reuse_approval(publications_api):
    engine, factory = publications_api
    client = factory()
    artifact = document(engine, client)
    original = prepare(client, artifact.id).json()
    headers = login(client)
    original_approved = approve(client, original, headers).json()
    changed = prepare(client, artifact.id, metadata=metadata(title="Changed reviewed title")).json()
    production = prepare(client, artifact.id, destination="production").json()
    for record in (changed, production):
        assert record["manifest_sha256"] != original["manifest_sha256"]
        assert record["id"] != original["id"]
        assert record["approval"] is None
    stale = client.post(
        f"/api/v1/publications/{changed['id']}/approve",
        headers=headers,
        json={"expected_manifest_sha256": original["manifest_sha256"]},
    )
    assert stale.status_code == 409
    assert client.get(f"/api/v1/publications/{original['id']}").json() == original_approved
    changed_publisher = factory(
        publication_policy=publisher_policy(identifier="different-publisher")
    )
    changed_publisher.cookies.update(client.cookies)
    publisher_manifest = prepare(changed_publisher, artifact.id).json()
    assert publisher_manifest["manifest_sha256"] != original["manifest_sha256"]
    assert publisher_manifest["approval"] is None
    assert approve(changed_publisher, original, headers).status_code == 409


def test_revocation_keeps_history_and_is_idempotent(publications_api):
    engine, factory = publications_api
    client = factory()
    artifact = document(engine, client)
    record = prepare(client, artifact.id).json()
    headers = login(client)
    receipt = approve(client, record, headers).json()["approval"]
    no_operator_csrf = client.post(
        f"/api/v1/publications/approvals/{receipt['id']}/revoke", headers=bootstrap(client)
    )
    assert no_operator_csrf.status_code == 403
    revoked = client.post(f"/api/v1/publications/approvals/{receipt['id']}/revoke", headers=headers)
    assert revoked.status_code == 200, revoked.text
    withdrawal = revoked.json()["approval"]
    assert withdrawal["id"] == receipt["id"]
    assert withdrawal["revoked_at"] is not None and not withdrawal["valid"]
    again = client.post(f"/api/v1/publications/approvals/{receipt['id']}/revoke", headers=headers)
    assert again.status_code == 200
    assert again.json()["approval"] == withdrawal
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationApproval)) == 1
        assert session.scalar(select(func.count()).select_from(PublicationRevocation)) == 1


def test_foreign_preview_receipt_and_artifact_are_indistinguishable_from_missing(publications_api):
    engine, factory = publications_api
    own, other = factory(), factory()
    artifact = document(engine, own)
    record = prepare(own, artifact.id).json()
    receipt = approve(own, record, login(own)).json()["approval"]
    other_headers = login(other)
    foreign_prepare = prepare(other, artifact.id)
    missing_prepare = prepare(other, uuid.uuid4())
    assert foreign_prepare.status_code == missing_prepare.status_code == 404
    assert foreign_prepare.json() == missing_prepare.json()
    for target in (record["id"], str(uuid.uuid4())):
        found = other.get(f"/api/v1/publications/{target}")
        assert found.status_code == 404
        assert found.json() == {"detail": "publication_not_found"}
        forged = approve(other, {**record, "id": target}, other_headers)
        assert forged.status_code == 404
    revoked = other.post(
        f"/api/v1/publications/approvals/{receipt['id']}/revoke", headers=other_headers
    )
    assert revoked.status_code == 404
    assert other.get("/api/v1/publications").json()["manifests"] == []


@pytest.mark.parametrize(
    "changes",
    [
        {"artifact_id": "private-invalid-version"},
        {"artifact_id": str(uuid.uuid4()).upper()},
        {"destination": "private-invalid-destination"},
        {"metadata": metadata(description="<script>private-unreviewed-content</script>")},
        {"metadata": metadata(description="https://private-unreviewed.invalid/metadata")},
        {"metadata": metadata(creators=[])},
        {"metadata": metadata(license="private-invalid-license")},
        {"metadata": metadata(extra="private-unknown-field")},
        {"extra": "private-unknown-field"},
    ],
)
def test_invalid_publication_inputs_are_redacted_and_do_not_create_manifests(
    publications_api, changes
):
    engine, factory = publications_api
    client = factory()
    artifact = document(engine, client)
    response = prepare(client, artifact.id, **changes)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_publication_request"}
    assert "private-" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationManifestRecord)) == 0


def test_publication_history_filters_pages_and_rejects_foreign_cursor(publications_api):
    engine, factory = publications_api
    client, other = factory(), factory()
    artifact = document(engine, client)
    first = prepare(client, artifact.id).json()
    second = prepare(client, artifact.id, metadata=metadata(title="Second version")).json()
    page = client.get("/api/v1/publications?limit=1").json()
    assert [record["id"] for record in page["manifests"]] == [second["id"]]
    assert page["next_cursor"] == second["id"]
    following = client.get(
        "/api/v1/publications", params={"limit": 1, "before": page["next_cursor"]}
    ).json()
    assert [record["id"] for record in following["manifests"]] == [first["id"]]
    assert following["next_cursor"] is None
    filtered = client.get("/api/v1/publications", params={"artifact_id": str(artifact.id)}).json()
    assert len(filtered["manifests"]) == 2
    assert (
        client.get("/api/v1/publications", params={"artifact_id": str(uuid.uuid4())}).json()[
            "manifests"
        ]
        == []
    )
    bootstrap(other)
    assert other.get(f"/api/v1/publications?before={first['id']}").status_code == 404


@pytest.mark.parametrize("state", ["expired", "revoked", "rotated"])
def test_inactive_operator_cannot_approve_or_leave_valid_old_receipts(publications_api, state):
    engine, factory = publications_api
    client = factory()
    artifact = document(engine, client)
    record = prepare(client, artifact.id).json()
    headers = login(client)
    receipt = approve(client, record, headers).json()["approval"]
    if state == "rotated":
        current = factory(auth_policy=operator_policy(token=ROTATED_TOKEN))
        current.cookies.update(client.cookies)
    else:
        current = client
        with Session(engine) as session, session.begin():
            operator = session.scalar(select(OperatorSession))
            now = session.scalar(select(func.clock_timestamp()))
            if state == "expired":
                operator.expires_at = now - timedelta(seconds=1)
            else:
                operator.revoked_at = now
    rejected = approve(current, record, headers)
    assert rejected.status_code == 401
    refreshed = current.get(f"/api/v1/publications/{record['id']}").json()
    assert refreshed["approval"]["id"] == receipt["id"]
    assert not refreshed["approval"]["valid"]


def test_expired_browser_cannot_access_manifests_or_approve(publications_api):
    from polymath.access_models import BrowserSession

    engine, factory = publications_api
    client = factory()
    artifact = document(engine, client)
    record = prepare(client, artifact.id).json()
    headers = login(client)
    with Session(engine) as session, session.begin():
        browser = session.scalar(select(BrowserSession))
        browser.expires_at = session.scalar(select(func.clock_timestamp())) - timedelta(seconds=1)
    assert client.get("/api/v1/publications").status_code == 401
    assert client.get(f"/api/v1/publications/{record['id']}").status_code == 401
    assert approve(client, record, headers).status_code == 401


def test_manifest_preparation_never_authorizes_or_publishes(publications_api):
    engine, factory = publications_api
    client = factory(operator=False)
    artifact = document(engine, client)
    preview = prepare(client, artifact.id)
    assert preview.status_code == 201
    record = preview.json()
    assert record["approval"] is None and record["publication_status"] == "not_published"
    denied = approve(client, record, bootstrap(client))
    assert denied.status_code == 503
    assert denied.json() == {"detail": "operator_unavailable"}
    assert client.post(f"/api/v1/publications/{record['id']}/publish").status_code == 404
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(PublicationApproval)) == 0
