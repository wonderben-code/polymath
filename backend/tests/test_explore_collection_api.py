"""Actual owned admission/capability boundaries; no scientific HTTP or model calls."""

import json
import uuid
from datetime import datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_meta_readiness import readiness as readiness_fixture
from test_native_research_worker import native as native_fixture
from test_public_delegations import public_active
from test_run_api import bootstrap
from test_scientific_native_worker import scientific_client
from test_scientific_protocols_api import counts, private_headers, session_reads
from test_worker import database as database_fixture

from polymath.budget import BudgetService
from polymath.budget_models import BudgetRunLimit
from polymath.explore_collection_contracts import ExploreCollectionIntent
from polymath.explore_collection_models import ExploreCollectionIntentRecord, ResearchStageOwner
from polymath.meta_probe_contracts import ProviderAccountAttestation
from polymath.meta_research import _original_input, native_run_input_sha256
from polymath.meta_research_contracts import (
    NativeResearchReview,
    ScientificContextIntent,
    task_profile_sha256,
)
from polymath.meta_research_models import NativeResearchRunBinding
from polymath.models import ResearchRun
from polymath.public_research_contracts import PublicLimits
from polymath.run_api import COOKIE_NAME, _hash_token

database = database_fixture
native = native_fixture
readiness = readiness_fixture
pytestmark = pytest.mark.integration
PATH = "/api/v1/research/explore-protocols"
INTENT = {"protocol": "polymath-explore-collection-v1", "max_bridges": 3}
EMPTY = {
    "protocol": "polymath-explore-collection-access-v1",
    "activation_sha256": None,
    "explore_collection_intent": None,
}


def approve_collection(value, *, public=False, max_bridges=3, scientific=False):
    """Review a new extension on the same synthetic account/proposal/canary."""
    record = value.public_activation if public else value.activation
    manifest = record.to_response()["manifest"]
    review = NativeResearchReview.model_validate_json(json.dumps(manifest["review"]))
    attestation = ProviderAccountAttestation.model_validate_json(
        json.dumps(manifest["provider_account_attestation"])
    )
    extensions = {"explore_collection": ExploreCollectionIntent(max_bridges=max_bridges)}
    if scientific:
        extensions["scientific_context"] = ScientificContextIntent()
    arguments = (
        value.request,
        manifest["readiness_id"],
        manifest["canary"]["receipt_id"],
        review,
        attestation,
    )
    if public:
        preview = value.delegations.preview(
            *arguments,
            PublicLimits.model_validate_json(json.dumps(manifest["public_limits"])),
            datetime.fromisoformat(manifest["valid_until"]),
            **extensions,
        )
        value.public_preview = preview
        value.public_activation = value.delegations.activate(
            value.request, preview.manifest, preview.manifest_sha256
        )
        value.public_activation_sha256 = preview.manifest_sha256
    else:
        preview = value.store.preview(*arguments, **extensions)
        value.activation = value.store.activate(
            value.request, preview.manifest, preview.manifest_sha256
        )
        value.activation_sha256 = preview.manifest_sha256
    return value


def create_collection(client, value, *, intent=None, public=False, headers=None):
    return client.post(
        "/api/v1/runs",
        headers=headers or value.headers,
        json={
            "mode": "explore",
            "goal": "How does redundancy protect structure under noise?",
            "expected_activation_sha256": value.public_activation_sha256
            if public
            else value.activation_sha256,
            "explore_collection_intent": intent or INTENT,
        },
    )


def test_anonymous_explore_capability_creates_no_owner_or_work(readiness):
    public = public_active(readiness, short_issuer=False)
    with scientific_client(public, public=True) as client:
        before = counts(public.engine)
        response = client.get(PATH)
        assert response.status_code == 401, response.text
        private_headers(response)
        assert not client.cookies and counts(public.engine) == before


def test_legacy_then_approved_public_explore_capability_has_three_keys_and_one_read(readiness):
    public = public_active(readiness, short_issuer=False)
    with scientific_client(public, public=True) as visitor:
        bootstrap(visitor)
        owner = _hash_token(visitor.cookies.get(COOKIE_NAME))
        before, reads = counts(public.engine), session_reads(public.engine, owner)
        response = visitor.get(PATH)
        assert response.status_code == 200, response.text
        assert response.json() == {**EMPTY, "activation_sha256": public.public_activation_sha256}
        assert session_reads(public.engine, owner) == reads + 1
        approve_collection(public, public=True)
        response = visitor.get(PATH)
        assert response.status_code == 200, response.text
        assert response.json() == {
            **EMPTY,
            "activation_sha256": public.public_activation_sha256,
            "explore_collection_intent": INTENT,
        }
        assert set(response.json()) == set(EMPTY)
        assert session_reads(public.engine, owner) == reads + 2
        assert counts(public.engine) == before
        private_headers(response)
        assert "polymath_operator" not in dict(visitor.cookies)
        assert all(
            private not in response.text
            for private in (
                public.policy.api_key.get_secret_value(),
                "actor_id",
                "model_id",
                "controller_sha256",
                "profile_sha256",
                "public_limits",
                "account_id",
            )
        )


def test_public_explore_capability_survives_issuer_logout_and_closes_on_stop(readiness):
    public = approve_collection(public_active(readiness, short_issuer=False), public=True)
    with scientific_client(public, public=True) as visitor:
        bootstrap(visitor)
        before = counts(public.engine)
        response = public.issuer_client.post(
            "/api/v1/operator/logout", headers=public.issuer_headers
        )
        assert response.status_code == 200
        current = visitor.get(PATH)
        assert current.status_code == 200, current.text
        assert current.json()["explore_collection_intent"] == INTENT
        assert current.json()["activation_sha256"] == public.public_activation_sha256
        BudgetService(public.engine).set_kill_switch(public.policy.native_account_id, active=True)
        closed = visitor.get(PATH)
        assert closed.status_code == 200 and closed.json() == EMPTY
        assert counts(public.engine) == before


async def test_real_private_current_projects_exact_scientific_and_explore_extensions(native):
    with scientific_client(native) as client:
        baseline = client.get("/api/v1/budget/research/current")
        assert baseline.status_code == 200, baseline.text
        assert "scientific_context_profiles" not in baseline.json()["activation"]
        assert "explore_collection_profile" not in baseline.json()["activation"]
        approve_collection(native, scientific=True)
        current = client.get("/api/v1/budget/research/current")
        assert current.status_code == 200, current.text
        activation = current.json()["activation"]
        manifest = native.activation.to_response()["manifest"]
        assert activation["manifest_sha256"] == native.activation_sha256
        for key in ("scientific_context_profiles", "explore_collection_profile"):
            assert activation[key] == manifest[key]
        public = client.get(PATH)
        assert public.status_code == 200 and public.json() == EMPTY


async def test_unapproved_explore_request_rolls_back_job_and_intent(native):
    with scientific_client(native) as client:
        before = counts(native.engine)
        response = create_collection(client, native)
        assert response.status_code == 409, response.text
        assert response.json() == {"detail": "meta_research_explore_protocol_unavailable"}
        assert counts(native.engine) == before
        with Session(native.engine) as session:
            assert (
                session.scalar(select(func.count()).select_from(ExploreCollectionIntentRecord)) == 0
            )
            assert session.scalar(select(func.count()).select_from(ResearchStageOwner)) == 0


async def test_tighter_run_intent_preserves_original_approved_profile_and_hash(native):
    approve_collection(native)
    with scientific_client(native) as client:
        intent = {**INTENT, "max_bridges": 1}
        response = create_collection(client, native, intent=intent)
        assert response.status_code == 201, response.text
        assert response.json()["explore_collection_intent"] == intent
        run_id = uuid.UUID(response.json()["id"])
        with Session(native.engine) as session:
            run = session.get(ResearchRun, run_id)
            binding = session.get(NativeResearchRunBinding, run_id)
            original = native.activation.to_response()["manifest"]["explore_collection_profile"]
            assert binding.profile == original["profile"]
            assert binding.profile["max_bridges"] == 3
            assert binding.profile_sha256 == original["profile_sha256"]
            assert binding.input_sha256 == native_run_input_sha256(session, run)
            assert session.get(ExploreCollectionIntentRecord, run_id).max_bridges == 1
            assert session.get(ResearchStageOwner, run_id).explore_intent_run_id == run_id
            profile = _original_input(session, run_id, check_input=True).profile
            assert binding.profile_sha256 == task_profile_sha256(profile)
            limits = session.get(BudgetRunLimit, (binding.account_id, run_id))
            assert limits.max_calls == binding.profile["max_model_calls"]
            assert limits.cap_microdollars == native.proposal.account.run_cap_microdollars


async def test_requested_ceiling_cannot_expand_reviewed_profile(native):
    approve_collection(native, max_bridges=1)
    with scientific_client(native) as client:
        before = counts(native.engine)
        response = create_collection(client, native)
        assert response.status_code == 409, response.text
        assert response.json() == {"detail": "meta_research_explore_protocol_unavailable"}
        assert counts(native.engine) == before


async def test_owned_collection_reads_are_empty_without_work_and_hide_foreign_runs(native):
    approve_collection(native)
    with scientific_client(native) as client, scientific_client(native) as foreign:
        response = create_collection(client, native)
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        before = counts(native.engine)
        foreign.cookies.clear()
        bootstrap(foreign)
        after_bootstrap = counts(native.engine)
        paths = ("explore-collection", "bridges", "rankings")
        for suffix in paths:
            path = f"/api/v1/runs/{run_id}/{suffix}"
            own = client.get(path)
            assert own.status_code == 200, own.text
            assert own.json()["run_id"] == run_id
            private_headers(own)
            hidden = foreign.get(path)
            assert hidden.status_code == 404, hidden.text
            assert hidden.json() == {"detail": "explore_collection_not_found"}
            private_headers(hidden)
        summary = client.get(f"/api/v1/runs/{run_id}/explore-collection").json()
        assert summary["stages"] == summary["rankings"] == []
        assert summary["selected_final_ranking"] is None
        assert summary["explore_collection_intent"] == INTENT
        assert summary["total_usage"]["model_calls"] == 0
        assert before[1:] == after_bootstrap[1:]
        assert counts(native.engine) == after_bootstrap


async def test_collection_reader_sanitizes_storage_failure_without_leaking_or_enqueuing(
    native, monkeypatch
):
    from polymath.explore_collection_store import ExploreCollectionStore

    approve_collection(native)
    with scientific_client(native) as client:
        response = create_collection(client, native)
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        before = counts(native.engine)

        def unavailable(*args, **kwargs):
            raise RuntimeError("sensitive internal database or provider diagnostic")

        monkeypatch.setattr(ExploreCollectionStore, "summary", unavailable)
        failed = client.get(f"/api/v1/runs/{run_id}/explore-collection")
        assert failed.status_code == 503
        assert failed.json() == {"detail": "explore_collection_storage_unavailable"}
        private_headers(failed)
        assert counts(native.engine) == before


@pytest.mark.parametrize(
    "change",
    [
        {"explore_collection_intent": None},
        {"explore_collection_intent": {**INTENT, "max_bridges": True}},
        {"explore_collection_intent": {**INTENT, "max_bridges": 5}},
        {"mode": "ask"},
        {"mode": "surprise"},
        {"parent_result_ids": [str(uuid.UUID(int=1)), str(uuid.UUID(int=2))]},
        {"ask_overview_intent": {"protocol": "polymath-ask-claim-overview-v1"}},
        {
            "discovery_intent": {
                "protocol": "polymath-surprise-ladder-v1",
                "max_stages": 3,
                "max_depth": 2,
            }
        },
    ],
)
async def test_incompatible_collection_requests_are_rejected_without_creating_jobs(native, change):
    with scientific_client(native) as client:
        before = counts(native.engine)
        response = client.post(
            "/api/v1/runs",
            headers=native.headers,
            json={
                "mode": "explore",
                "goal": "Synthetic scope boundary",
                "expected_activation_sha256": native.activation_sha256,
                "explore_collection_intent": INTENT,
                **change,
            },
        )
        assert response.status_code == 422, response.text
        assert response.json() == {"detail": "invalid_research_request"}
        assert counts(native.engine) == before
