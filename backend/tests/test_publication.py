"""Exact local publication authorization; no remote publishing calls are made."""

import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from pydantic import SecretStr, ValidationError
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from test_artifact_api import artifacts_api as artifacts_api_fixture
from test_artifact_api import complete_artifact
from test_knowledge import seed

from polymath.access_models import BrowserSession, ResearchRunAccess
from polymath.operator_auth import OperatorPolicy
from polymath.operator_models import OperatorSession
from polymath.publication import PublicationError, PublicationStore, hash_manifest
from polymath.publication_contracts import (
    Creator,
    PublicationManifest,
    PublicationMetadata,
    PublicationPolicy,
    PublisherIdentity,
)
from polymath.publication_models import (
    PublicationApproval,
    PublicationManifestRecord,
    PublicationRevocation,
)

artifacts_api = artifacts_api_fixture


def metadata(**changes):
    return PublicationMetadata.model_validate(
        {
            "title": "Synthetic publication review",
            "description": "Synthetic document for approval checks, not a scientific discovery.",
            "creators": [{"name": "Synthetic Fixture Author"}],
            "keywords": ["synthetic fixture"],
            "license": "cc-by-4.0",
            **changes,
        }
    )


@pytest.mark.parametrize("orcid", ["0000-0002-1825-0097", "0000-0002-9079-593X"])
def test_creator_requires_canonical_checksummed_orcid(orcid):
    assert Creator(name="Synthetic author", orcid=orcid).orcid == orcid


@pytest.mark.parametrize(
    "changes",
    [
        {"creators": []},
        {"creators": [{"name": "Synthetic", "orcid": "0000-0002-1825-0098"}]},
        {"creators": [{"name": "Synthetic", "orcid": "0000-0002-9079-593x"}]},
        {"title": "<script>fixture</script>"},
        {"description": "An unreviewed URL: https://example.invalid/fixture"},
        {"keywords": ["Noise", "noise"]},
        {"license": "unreviewed-license"},
        {"upload_type": "dataset"},
        {"approved": True},
        {"title": "Private\u0000metadata"},
        {"title": "\ud800"},
    ],
)
def test_metadata_cannot_add_authority_or_unsupported_fields(changes):
    with pytest.raises(ValidationError):
        metadata(**changes)


def test_license_is_explicit_and_policy_is_disabled_by_default():
    raw = metadata().model_dump(mode="json")
    del raw["license"]
    with pytest.raises(ValidationError):
        PublicationMetadata.model_validate(raw)
    assert not PublicationPolicy().configured
    for duration in (True, 0, 59, 3601):
        with pytest.raises(ValueError):
            PublicationPolicy(approval_seconds=duration)


@pytest.fixture
def publication(artifacts_api):
    engine, factory = artifacts_api
    client = factory(enabled=True)
    _, source_id = seed(engine, client)
    _, artifact = complete_artifact(engine, client, source_id, kind="write_paper")
    operator_policy = OperatorPolicy(
        bootstrap_token=SecretStr(
            "Synthetic_fixture_operator_0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        ),
        actor_id="synthetic-publisher",
        display_name="Synthetic Publisher",
    )
    policy = PublicationPolicy(
        publisher=PublisherIdentity(
            identifier="synthetic-publisher", display_name="Synthetic Publisher"
        ),
        allowed_destinations=("sandbox", "production"),
    )
    with Session(engine) as session, session.begin():
        owner_hash = session.scalar(
            select(ResearchRunAccess.session_hash).where(
                ResearchRunAccess.run_id == artifact.run_id
            )
        )
        now = session.scalar(select(func.clock_timestamp()))
        operator = OperatorSession(
            token_hash=hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
            browser_session_hash=owner_hash,
            credential_fingerprint=operator_policy.credential_fingerprint,
            actor_id=operator_policy.actor_id,
            display_name=operator_policy.display_name,
            expires_at=now + timedelta(seconds=operator_policy.session_seconds),
        )
        session.add(operator)
        session.flush()
        session.expunge(operator)
    return SimpleNamespace(
        engine=engine,
        client=client,
        artifact=artifact,
        owner_hash=owner_hash,
        operator=operator,
        operator_policy=operator_policy,
        policy=policy,
        store=PublicationStore(engine, policy, operator_policy=operator_policy),
    )


def prepare(case, **changes):
    return case.store.prepare(case.owner_hash, case.artifact.id, metadata(**changes), "sandbox")


def approve(case, manifest):
    return case.store.approve(case.owner_hash, manifest.id, manifest.manifest_sha256, case.operator)


@pytest.mark.integration
class TestPublicationStore:
    @pytest.mark.parametrize("operation", ["approve", "revoke"])
    def test_browser_expiry_during_manifest_lock_wait_blocks_write(
        self, publication, monkeypatch, operation
    ):
        import polymath.publication as publications

        case = publication
        manifest = prepare(case)
        receipt = approve(case, manifest) if operation == "revoke" else None
        reached_lock = Event()
        original = publications._owned_manifest

        def signalled_lock(session, owner_hash, manifest_id, *, lock=False):
            if lock:
                reached_lock.set()
            return original(session, owner_hash, manifest_id, lock=lock)

        monkeypatch.setattr(publications, "_owned_manifest", signalled_lock)
        pool = ThreadPoolExecutor(max_workers=1)
        with case.engine.connect() as held:
            transaction = held.begin()
            held.execute(
                select(PublicationManifestRecord)
                .where(PublicationManifestRecord.id == manifest.id)
                .with_for_update()
            )
            try:
                future = pool.submit(
                    case.store.approve if operation == "approve" else case.store.revoke,
                    case.owner_hash,
                    manifest.id if operation == "approve" else receipt.id,
                    *(
                        (manifest.manifest_sha256, case.operator)
                        if operation == "approve"
                        else (case.operator,)
                    ),
                )
                assert reached_lock.wait(timeout=5)
                with Session(case.engine) as session, session.begin():
                    session.execute(
                        update(BrowserSession)
                        .where(BrowserSession.token_hash == case.owner_hash)
                        .values(expires_at=func.clock_timestamp() - text("interval '1 second'"))
                    )
                transaction.commit()
                with pytest.raises(PublicationError, match="publication_not_found"):
                    future.result(timeout=10)
            finally:
                if transaction.is_active:
                    transaction.rollback()
                pool.shutdown(wait=True)
        with Session(case.engine) as session:
            assert session.scalar(select(func.count()).select_from(PublicationApproval)) == (
                0 if operation == "approve" else 1
            )
            assert session.scalar(select(func.count()).select_from(PublicationRevocation)) == 0

    def test_prepare_is_exact_idempotent_and_not_publication(self, publication):
        case = publication
        first, second = prepare(case), prepare(case)
        assert first.id == second.id
        manifest = PublicationManifest.model_validate_json(json.dumps(first.manifest))
        assert hash_manifest(manifest) == first.manifest_sha256
        assert manifest.markdown_sha256 == case.artifact.markdown_sha256
        assert manifest.markdown_bytes == len(case.artifact.markdown.encode("utf-8"))
        assert manifest.file_name == f"polymath-{case.artifact.id}.md"
        assert manifest.payload_sha256 == case.artifact.payload_sha256
        assert manifest.basis_sha256 == case.artifact.basis_sha256
        base = {key: value for key, value in first.manifest.items() if key != "remote_marker"}
        expected = hashlib.sha256(
            json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert manifest.remote_marker == "polymath-" + expected
        assert first.to_response()["publication_status"] == "not_published"
        assert first.approval is None

    def test_concurrent_identical_prepare_creates_one_manifest(self, publication):
        barrier = Barrier(2)

        def create():
            barrier.wait(timeout=5)
            return prepare(publication)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.submit(create), pool.submit(create)
            assert first.result(timeout=15).id == second.result(timeout=15).id
        with Session(publication.engine) as session:
            assert session.scalar(select(func.count()).select_from(PublicationManifestRecord)) == 1

    def test_approval_uses_database_actor_and_exact_session_lifetime(self, publication):
        case = publication
        manifest = prepare(case)
        case.operator.actor_id = "forged-client-actor"
        receipt = approve(case, manifest)
        assert receipt.valid
        assert receipt.actor_id == case.operator_policy.actor_id
        assert receipt.expires_at <= case.operator.expires_at
        assert approve(case, manifest).id == receipt.id
        assert case.store.validate_approval(
            case.owner_hash,
            receipt.id,
            manifest_id=manifest.id,
            expected_manifest_sha256=manifest.manifest_sha256,
        ).valid
        response = case.store.get(case.owner_hash, manifest.id).to_response()
        assert response["publication_status"] == "not_published"
        assert response["approval"]["id"] == str(receipt.id)
        assert case.owner_hash not in str(response)
        assert case.operator.token_hash not in str(response)

    def test_stale_hash_cannot_approve_edited_metadata(self, publication):
        case = publication
        first = prepare(case)
        second = prepare(case, title="An edited synthetic title")
        assert first.id != second.id
        assert first.manifest_sha256 != second.manifest_sha256
        assert first.manifest["remote_marker"] != second.manifest["remote_marker"]
        with pytest.raises(PublicationError, match="publication_manifest_changed"):
            case.store.approve(case.owner_hash, second.id, first.manifest_sha256, case.operator)
        with Session(case.engine) as session:
            assert session.scalar(select(func.count()).select_from(PublicationApproval)) == 0

    def test_receipt_cannot_authorize_another_manifest(self, publication):
        case = publication
        first = prepare(case)
        receipt = approve(case, first)
        changed = prepare(case, license="cc0-1.0")
        with pytest.raises(PublicationError, match="publication_manifest_changed"):
            case.store.validate_approval(case.owner_hash, receipt.id, manifest_id=changed.id)
        assert case.store.get(case.owner_hash, changed.id).approval is None

    @pytest.mark.parametrize("change", ["publisher", "destination", "disabled"])
    def test_policy_changes_invalidate_old_receipts(self, publication, change):
        case = publication
        manifest = prepare(case)
        receipt = approve(case, manifest)
        if change == "publisher":
            policy = replace(
                case.policy,
                publisher=PublisherIdentity(
                    identifier="new-publisher", display_name="New Publisher"
                ),
            )
        elif change == "destination":
            policy = replace(case.policy, allowed_destinations=("production",))
        else:
            policy = PublicationPolicy()
        changed = PublicationStore(case.engine, policy, operator_policy=case.operator_policy)
        assert not changed.get(case.owner_hash, manifest.id).approval.valid
        with pytest.raises(PublicationError, match="publication_manifest_changed"):
            changed.validate_approval(case.owner_hash, receipt.id)

    def test_credential_rotation_invalidates_existing_receipt(self, publication):
        case = publication
        manifest = prepare(case)
        approve(case, manifest)
        rotated = replace(
            case.operator_policy,
            bootstrap_token=SecretStr(
                "Rotated_fixture_operator_0123456789-ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            ),
        )
        changed = PublicationStore(case.engine, case.policy, operator_policy=rotated)
        assert not changed.get(case.owner_hash, manifest.id).approval.valid
        with pytest.raises(PublicationError, match="operator_session_required"):
            changed.approve(case.owner_hash, manifest.id, manifest.manifest_sha256, case.operator)

    @pytest.mark.parametrize("change", ["expired", "revoked", "browser", "actor"])
    def test_operator_authority_is_reloaded_from_database(self, publication, change):
        case = publication
        manifest = prepare(case)
        receipt = approve(case, manifest)
        with Session(case.engine) as session, session.begin():
            operator = session.get(OperatorSession, case.operator.token_hash)
            now = session.scalar(select(func.clock_timestamp()))
            if change == "expired":
                operator.expires_at = now - timedelta(seconds=1)
            elif change == "revoked":
                operator.revoked_at = now
            elif change == "actor":
                operator.actor_id = "different-database-actor"
            else:
                foreign_hash = "f" * 64
                session.add(
                    BrowserSession(token_hash=foreign_hash, expires_at=now + timedelta(days=1))
                )
                session.flush()
                operator.browser_session_hash = foreign_hash
        assert not case.store.get(case.owner_hash, manifest.id).approval.valid
        with pytest.raises(PublicationError, match="operator_session_required"):
            case.store.approve(
                case.owner_hash, manifest.id, manifest.manifest_sha256, case.operator
            )
        with pytest.raises(PublicationError, match="publication_manifest_changed"):
            case.store.validate_approval(case.owner_hash, receipt.id)

    def test_receipt_expires_while_operator_remains_active(self, publication, monkeypatch):
        import polymath.publication as publications

        case = publication
        case.store.policy = replace(case.policy, approval_seconds=60)
        manifest = prepare(case)
        receipt = approve(case, manifest)
        future = receipt.approved_at + timedelta(seconds=61)
        assert future < case.operator.expires_at
        monkeypatch.setattr(publications, "_now", lambda _: future)
        assert not case.store.get(case.owner_hash, manifest.id).approval.valid
        with pytest.raises(PublicationError, match="publication_manifest_changed"):
            case.store.validate_approval(case.owner_hash, receipt.id)

    def test_revocation_is_idempotent_and_preserves_history(self, publication):
        case = publication
        manifest = prepare(case)
        receipt = approve(case, manifest)
        revoked = case.store.revoke(case.owner_hash, receipt.id, case.operator)
        assert not revoked.valid and revoked.revoked_at is not None
        assert case.store.revoke(case.owner_hash, receipt.id, case.operator) == revoked
        with pytest.raises(PublicationError, match="publication_manifest_changed"):
            case.store.validate_approval(case.owner_hash, receipt.id)
        replacement = approve(case, manifest)
        assert replacement.valid and replacement.id != receipt.id

    @pytest.mark.parametrize("table", ["manifest", "approval", "revocation"])
    @pytest.mark.parametrize("operation", ["update", "delete"])
    def test_authorization_history_cannot_be_rewritten(self, publication, table, operation):
        case = publication
        manifest = prepare(case)
        receipt = approve(case, manifest)
        case.store.revoke(case.owner_hash, receipt.id, case.operator)
        model = {
            "manifest": PublicationManifestRecord,
            "approval": PublicationApproval,
            "revocation": PublicationRevocation,
        }[table]
        if operation == "delete":
            statement = delete(model)
        elif table == "manifest":
            statement = update(model).values(manifest_sha256="0" * 64)
        else:
            statement = update(model).values(actor_id="forged-actor")
        with pytest.raises(IntegrityError), Session(case.engine) as session, session.begin():
            session.execute(statement)
        assert case.store.get(case.owner_hash, manifest.id).approval.revoked_at is not None

    def test_foreign_session_cannot_read_or_revoke(self, publication):
        case = publication
        manifest = prepare(case)
        receipt = approve(case, manifest)
        foreign = "e" * 64
        with Session(case.engine) as session, session.begin():
            now = session.scalar(select(func.clock_timestamp()))
            session.add(BrowserSession(token_hash=foreign, expires_at=now + timedelta(days=1)))
        with pytest.raises(PublicationError, match="publication_not_found"):
            case.store.get(foreign, manifest.id)
        assert case.store.list(foreign) == ()
        with pytest.raises(PublicationError):
            case.store.revoke(foreign, receipt.id, case.operator)
        assert case.store.get(case.owner_hash, manifest.id).approval.valid

    def test_formalisation_and_missing_artifacts_cannot_be_published(self, publication):
        case = publication
        _, source_id = seed(case.engine, case.client, suffix="formalisation")
        _, formalisation = complete_artifact(case.engine, case.client, source_id, kind="formalise")
        for artifact_id in (formalisation.id, uuid.uuid4()):
            with pytest.raises(PublicationError, match="publication_not_found"):
                case.store.prepare(case.owner_hash, artifact_id, metadata(), "sandbox")

    def test_disabled_policy_cannot_prepare_or_approve(self, publication):
        case = publication
        empty = PublicationStore(case.engine)
        assert empty.capabilities() == {
            "configured": False,
            "publisher": None,
            "destinations": [],
            "can_prepare": False,
        }
        with pytest.raises(PublicationError, match="publication_unavailable"):
            empty.prepare(case.owner_hash, case.artifact.id, metadata(), "sandbox")
        manifest = prepare(case)
        with pytest.raises(PublicationError, match="operator_session_required"):
            empty.approve(case.owner_hash, manifest.id, manifest.manifest_sha256, case.operator)

    def test_typed_copy_cannot_forge_marker_or_filename(self, publication):
        manifest = prepare(publication)
        typed = PublicationManifest.model_validate_json(json.dumps(manifest.manifest))
        forged = typed.model_copy(update={"remote_marker": "polymath-" + "0" * 64})
        with pytest.raises(PublicationError, match="invalid_publication_manifest"):
            hash_manifest(forged)
        with pytest.raises(ValidationError):
            PublicationManifest.model_validate_json(
                json.dumps(dict(manifest.manifest, file_name="../unapproved.md"))
            )

    def test_downgrade_refuses_to_erase_authorization_history(self, publication):
        case = publication
        manifest = prepare(case)
        approve(case, manifest)
        config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
        with case.engine.connect() as connection:
            original_head = connection.scalar(text("SELECT version_num FROM alembic_version"))
        with pytest.raises(Exception, match="Cannot discard publication review"):
            command.downgrade(config, "0011_operator_sessions")
        with case.engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version")) == original_head
            )
        command.check(config)
