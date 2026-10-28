"""Explicit native client preflight and single-send behavior; no paid calls."""

import copy
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from test_api_client import (
    CSRF,
    ORIGIN,
    OWNER,
    RUN,
    Response,
    client_module,
    initialized,
    session_response,
)
from test_api_client import wire as wire_fixture

HASH = "c" * 64
wire = wire_fixture


def available():
    return {
        "protocol": "polymath-research-access-v1",
        "audience": "public_sessions",
        "available": True,
        "reason": None,
        "expected_activation_sha256": HASH,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "limits": {
            "session_total_cap_microdollars": "50000",
            "session_daily_cap_microdollars": "25000",
            "max_jobs_per_session_day": 12,
            "max_active_jobs_per_session": 1,
            "task_kinds": [
                "research",
                "formalise",
                "write_summary",
                "write_paper",
                "reassess_finding",
            ],
            "discovery_intent": {
                "protocol": "polymath-surprise-ladder-v1",
                "max_stages": 3,
                "max_depth": 2,
            },
        },
        "remaining_session_allowance_microdollars": "50000",
        "remaining_session_jobs_today": 8,
        "retry_after_seconds": None,
    }


def mocked(monkeypatch, *, native=True):
    client = client_module.PolymathClient("http://127.0.0.1:8000")
    calls = []
    monkeypatch.setattr(client, "_session", lambda **kw: calls.append(("session", kw)))
    monkeypatch.setattr(
        client,
        "capabilities",
        lambda: {
            "native_research_required": native,
            "research_available": True,
            "meta_configured": native,
        },
    )
    monkeypatch.setattr(client, "research_access", lambda: calls.append(("access",)) or available())
    monkeypatch.setattr(
        client, "_send_write", lambda path, body: calls.append(("POST", path, body)) or {"id": RUN}
    )
    return client, calls


@pytest.mark.parametrize(
    "kind", ["research", "formalise", "write_summary", "write_paper", "assessment", "resume"]
)
def test_every_paid_kind_pins_one_current_public_hash_and_one_post(monkeypatch, kind):
    client, calls = mocked(monkeypatch)
    source, watch, version = [str(uuid.uuid4()) for _ in range(3)]
    if kind == "research":
        client.create_run("surprise")
        expected_path = "/api/v1/runs"
    elif kind in ("formalise", "write_summary", "write_paper"):
        client.create_artifact(kind, source)
        expected_path = "/api/v1/artifacts/jobs"
    elif kind == "assessment":
        client.create_assessment(source, watch, [version], 3)
        expected_path = "/api/v1/assessments/jobs"
    else:
        client.resume_run(RUN, "9223372036854775807", HASH)
        expected_path = f"/api/v1/runs/{RUN}/resume"
    assert [call[0] for call in calls] == ["session", "access", "POST"]
    assert calls[-1][1] == expected_path
    assert calls[-1][2]["expected_activation_sha256"] == HASH
    assert "operator" not in json.dumps(calls[-1][2])
    if kind == "resume":
        assert calls[-1][2]["expected_lease_token"] == "9223372036854775807"


def test_explicit_discovery_intent_stays_initial_input_not_grant(monkeypatch):
    client, calls = mocked(monkeypatch)
    intent = {"protocol": "polymath-surprise-ladder-v1", "max_stages": 3, "max_depth": 2}
    client.create_run("surprise", discovery_intent=intent, expected_activation_sha256=HASH)
    assert calls[-1][2]["discovery_intent"] == intent
    assert calls[-1][2]["expected_activation_sha256"] == HASH


def test_explicit_stale_public_version_never_sends_or_rebinds(monkeypatch):
    client, calls = mocked(monkeypatch)
    with pytest.raises(client_module.ClientError, match="research_activation_changed"):
        client.create_run("surprise", expected_activation_sha256="d" * 64)
    assert not any(call[0] == "POST" for call in calls)


@pytest.mark.parametrize(
    "reason",
    [
        "public_research_disabled",
        "public_research_authority_expired",
        "public_research_session_budget_exhausted",
    ],
)
def test_paid_closure_preserves_owned_saved_read_and_cancel(monkeypatch, reason):
    client, calls = mocked(monkeypatch)
    monkeypatch.setattr(
        client, "research_access", lambda: {**available(), "available": False, "reason": reason}
    )
    with pytest.raises(client_module.ClientError, match=reason):
        client.create_run("surprise")
    assert not any(call[0] == "POST" for call in calls)
    monkeypatch.setattr(client, "_private_json", lambda path: {"path": path})
    assert client.get_run(RUN) == {"path": f"/api/v1/runs/{RUN}"}
    client.cancel_run(RUN)
    assert calls[-1] == ("POST", f"/api/v1/runs/{RUN}/cancel", None)


def test_operator_browser_audience_does_not_invent_operator_credentials(monkeypatch):
    client, calls = mocked(monkeypatch)
    monkeypatch.setattr(
        client, "research_access", lambda: {**available(), "audience": "operator_browser"}
    )
    with pytest.raises(client_module.ClientError, match="research_operator_context_required"):
        client.create_run("surprise")
    assert not any(call[0] == "POST" for call in calls)


def test_native_access_is_checked_even_when_configuration_signal_is_false(monkeypatch):
    client, calls = mocked(monkeypatch)
    monkeypatch.setattr(
        client,
        "capabilities",
        lambda: {"native_research_required": True, "research_available": False},
    )
    client.create_run("surprise")
    assert calls[-1][2]["expected_activation_sha256"] == HASH


def test_legacy_submit_requires_explicit_no_native_capability_and_omits_hash(monkeypatch):
    client, calls = mocked(monkeypatch, native=False)
    client.create_run("surprise")
    assert [call[0] for call in calls] == ["session", "POST"]
    assert calls[-1][2] == {"mode": "surprise", "goal": "", "parent_result_ids": []}


@pytest.mark.parametrize("resume", [False, True])
def test_hash_or_resume_cannot_silently_downgrade_to_legacy(monkeypatch, resume):
    client, calls = mocked(monkeypatch, native=False)
    with pytest.raises(client_module.ClientError, match="research_native_access_required"):
        if resume:
            client.resume_run(RUN, "1", HASH)
        else:
            client.create_run("surprise", expected_activation_sha256=HASH)
    assert not any(call[0] == "POST" for call in calls)


@pytest.mark.parametrize("token", [1, True, "01", "-1", "1.0", "9223372036854775808", " 1", "1\n"])
def test_resume_requires_exact_original_decimal_lease_before_preflight(monkeypatch, token):
    client, calls = mocked(monkeypatch)
    with pytest.raises(client_module.ClientError, match="invalid_arguments"):
        client.resume_run(RUN, token, HASH)
    assert calls == []


@pytest.mark.parametrize("digest", [True, "", "d" * 63, "D" * 64, "private-secret"])
def test_invalid_hash_never_bootstraps_owner_or_echoes_input(monkeypatch, digest):
    client, calls = mocked(monkeypatch)
    with pytest.raises(client_module.ClientError, match="invalid_arguments") as error:
        client.create_run("surprise", expected_activation_sha256=digest)
    assert calls == [] and "private-secret" not in str(error.value)


@pytest.mark.parametrize(
    "capabilities",
    [
        {},
        {"research_available": True},
        {"native_research_required": "false", "research_available": True, "meta_configured": False},
        {"native_research_required": False, "research_available": 1, "meta_configured": False},
    ],
)
def test_ambiguous_capabilities_never_fall_back_to_unbound_post(monkeypatch, capabilities):
    client = client_module.PolymathClient("http://127.0.0.1:8000")
    monkeypatch.setattr(client, "_json", lambda *a, **kw: copy.deepcopy(capabilities))
    with pytest.raises(client_module.ClientError, match="invalid_capabilities_response"):
        client.capabilities()


def test_unknown_post_outcome_is_not_retried_or_rediscovered(monkeypatch):
    client, calls = mocked(monkeypatch)

    def fail(path, body):
        calls.append(("POST", path, body))
        raise client_module.ClientError("write_outcome_unknown")

    monkeypatch.setattr(client, "_send_write", fail)
    with pytest.raises(client_module.ClientError, match="write_outcome_unknown"):
        client.create_run("surprise")
    assert [call[0] for call in calls] == ["session", "access", "POST"]


@pytest.mark.parametrize("action", ["artifact", "assessment"])
def test_downstream_inputs_are_validated_before_owner_preflight(monkeypatch, action):
    client, calls = mocked(monkeypatch)
    with pytest.raises(client_module.ClientError):
        if action == "artifact":
            client.create_artifact("formalise", RUN, formalisation_id=RUN)
        else:
            client.create_assessment(RUN, RUN, [RUN, RUN], 0)
    assert calls == []


def test_valid_access_wire_and_private_operator_projection_preserve_exact_facts():
    data = available()
    assert client_module._research_access(data) == data
    private = {
        **data,
        "audience": "operator_browser",
        "available": False,
        "reason": "operator_required",
        "expected_activation_sha256": None,
        "expires_at": None,
        "limits": None,
        "remaining_session_allowance_microdollars": None,
        "remaining_session_jobs_today": None,
    }
    assert client_module._research_access(private) == private
    assert client_module._research_access({**private, "audience": None})["audience"] is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(protocol="unreviewed"),
        lambda data: data.update(audience="all_users"),
        lambda data: data.update(available=1),
        lambda data: data.update(reason="PRIVATE-SERVER-DETAIL"),
        lambda data: data.update(reason="public_research_disabled"),
        lambda data: data.update(expected_activation_sha256="C" * 64),
        lambda data: data.update(expires_at="2026-10-30T12:00:00"),
        lambda data: data.update(expires_at="2026-10-30T12:00:00+02:00"),
        lambda data: data.update(expires_at="2026-02-30T12:00:00Z"),
        lambda data: data.update(limits=None),
        lambda data: data.update(remaining_session_allowance_microdollars="05000"),
        lambda data: data.update(remaining_session_allowance_microdollars=None),
        lambda data: data.update(remaining_session_allowance_microdollars="0"),
        lambda data: data.update(remaining_session_allowance_microdollars="50001"),
        lambda data: data.update(remaining_session_jobs_today=13),
        lambda data: data.update(remaining_session_jobs_today=None),
        lambda data: data.update(remaining_session_jobs_today=True),
        lambda data: data.update(retry_after_seconds=-1),
        lambda data: data.update(retry_after_seconds=86401),
        lambda data: data.update(account_id="PRIVATE-ACCOUNT"),
        lambda data: data["limits"].update(session_total_cap_microdollars="150000001"),
        lambda data: data["limits"].update(session_daily_cap_microdollars="50001"),
        lambda data: data["limits"].update(max_active_jobs_per_session=2),
        lambda data: data["limits"].update(max_jobs_per_session_day=13),
        lambda data: data["limits"].update(max_active_jobs_per_session=True),
        lambda data: data["limits"].update(task_kinds=["research"]),
        lambda data: data["limits"].update(
            discovery_intent={
                "protocol": "polymath-surprise-ladder-v1",
                "max_stages": 5,
                "max_depth": 2,
            }
        ),
        lambda data: data["limits"].update(secret="PRIVATE-KEY"),
    ],
)
def test_access_parser_rejects_ambiguous_or_private_wire_without_echo(mutation):
    data = available()
    mutation(data)
    with pytest.raises(
        client_module.ClientError, match="invalid_research_access_response"
    ) as error:
        client_module._research_access(data)
    assert "PRIVATE" not in str(error.value)


def test_closed_public_access_preserves_reviewed_metadata_and_zero_remaining():
    data = {
        **available(),
        "available": False,
        "reason": "public_research_session_budget_exhausted",
        "remaining_session_allowance_microdollars": "0",
    }
    assert client_module._research_access(data) == data
    null_facts = {
        **data,
        "expected_activation_sha256": None,
        "expires_at": None,
        "limits": None,
        "remaining_session_allowance_microdollars": None,
        "remaining_session_jobs_today": None,
    }
    assert client_module._research_access(null_facts) == null_facts


def test_job_quota_blocks_new_creation_but_not_exact_original_resume(monkeypatch):
    client, calls = mocked(monkeypatch)
    monkeypatch.setattr(
        client, "research_access", lambda: {**available(), "remaining_session_jobs_today": 0}
    )
    with pytest.raises(client_module.ClientError, match="public_research_resource_unavailable"):
        client.create_run("surprise")
    assert not any(call[0] == "POST" for call in calls)
    client.resume_run(RUN, "123", HASH)
    assert calls[-1] == (
        "POST",
        f"/api/v1/runs/{RUN}/resume",
        {
            "expected_lease_token": "123",
            "expected_activation_sha256": HASH,
        },
    )


@pytest.mark.parametrize(
    "approved",
    [
        None,
        {"protocol": "polymath-surprise-ladder-v1", "max_stages": 2, "max_depth": 2},
        {"protocol": "polymath-surprise-ladder-v1", "max_stages": 3, "max_depth": 1},
    ],
)
def test_ladder_never_silently_enlarges_the_current_approved_profile(monkeypatch, approved):
    client, calls = mocked(monkeypatch)
    access = available()
    access["limits"]["discovery_intent"] = approved
    monkeypatch.setattr(client, "research_access", lambda: access)
    with pytest.raises(client_module.ClientError, match="research_discovery_not_approved"):
        client.create_run(
            "surprise",
            discovery_intent={
                "protocol": "polymath-surprise-ladder-v1",
                "max_stages": 3,
                "max_depth": 2,
            },
        )
    assert not any(call[0] == "POST" for call in calls)


def test_actual_client_preflight_sends_only_owner_cookie_and_single_post(
    monkeypatch, tmp_path, wire
):
    _, path = initialized(tmp_path, monkeypatch)
    pending, requests, _ = wire
    session_body, session_headers = session_response()
    pending.extend(
        [
            Response(session_body, response_headers=session_headers),
            Response(
                json.dumps(
                    {
                        "native_research_required": True,
                        "research_available": True,
                        "meta_configured": True,
                    }
                ).encode()
            ),
            Response(json.dumps(available()).encode()),
            Response(json.dumps({"id": RUN}).encode(), status=201),
        ]
    )
    client = client_module.PolymathClient(ORIGIN, cookie_jar=path)
    assert client.create_run("surprise") == {"id": RUN}
    assert [request[:2] for request in requests] == [
        ("GET", "/api/v1/session"),
        ("GET", "/api/v1/capabilities"),
        ("GET", "/api/v1/research/access"),
        ("POST", "/api/v1/runs"),
    ]
    assert "Cookie" not in requests[1][3]
    assert requests[2][3]["Cookie"] == "polymath_session=" + OWNER
    assert requests[-1][3]["X-CSRF-Token"] == CSRF and requests[-1][3]["Origin"] == ORIGIN
    assert not any("operator" in key.lower() for request in requests for key in request[3])
    assert json.loads(requests[-1][2])["expected_activation_sha256"] == HASH


def test_read_only_access_uses_existing_owner_without_session_or_post(monkeypatch, tmp_path, wire):
    _, path = initialized(tmp_path, monkeypatch)
    pending, requests, _ = wire
    pending.append(Response(json.dumps(available()).encode()))
    client = client_module.PolymathClient(ORIGIN, cookie_jar=path)
    assert client.research_access()["expected_activation_sha256"] == HASH
    assert len(requests) == 1 and requests[0][:2] == ("GET", "/api/v1/research/access")
    unowned = client_module.PolymathClient(ORIGIN)
    with pytest.raises(client_module.ClientError, match="research_session_required"):
        unowned.research_access()
    assert len(requests) == 1


@pytest.mark.parametrize(
    "arguments",
    [
        ["research", "access"],
        [
            "runs",
            "resume",
            RUN,
            "--expected-lease-token",
            "1",
            "--expected-activation-sha256",
            HASH,
        ],
        ["artifacts", "create", "--kind", "write_summary", "--source-result", RUN],
        [
            "assessments",
            "create",
            "--source-result",
            RUN,
            "--watch",
            RUN,
            "--new-source-version",
            RUN,
            "--expected-head-revision",
            "0",
        ],
    ],
)
def test_new_private_cli_commands_require_explicit_owner_jar(arguments, monkeypatch, capsys):
    monkeypatch.setattr(
        client_module, "PolymathClient", lambda *a, **kw: pytest.fail("constructed")
    )
    assert client_module.main(arguments) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "cookie_jar_required"


@pytest.mark.parametrize(
    "kind",
    [
        "public",
        "public_zero_jobs",
        "closed_public",
        "closed_unknown_allowance",
        "closed_unknown_jobs",
        "closed_unknown_counters",
        "operator",
        "absent",
    ],
)
def test_client_parses_actual_pure_access_wire_without_private_fields(kind):
    from polymath.public_research_contracts import PUBLIC_ACCESS_REASONS, PublicResearchAccess

    assert client_module.PUBLIC_ACCESS_REASONS == PUBLIC_ACCESS_REASONS
    value = available()
    if kind == "public_zero_jobs":
        value["remaining_session_jobs_today"] = 0
    elif kind.startswith("closed_"):
        value.update(
            available=False,
            reason="public_research_session_budget_exhausted",
            remaining_session_allowance_microdollars="0",
        )
        if kind in ("closed_unknown_allowance", "closed_unknown_counters"):
            value["remaining_session_allowance_microdollars"] = None
        if kind in ("closed_unknown_jobs", "closed_unknown_counters"):
            value["remaining_session_jobs_today"] = None
    elif kind in ("operator", "absent"):
        value.update(
            audience="operator_browser" if kind == "operator" else None,
            available=False,
            reason="operator_required"
            if kind == "operator"
            else "public_research_activation_required",
            expected_activation_sha256=None,
            expires_at=None,
            limits=None,
            remaining_session_allowance_microdollars=None,
            remaining_session_jobs_today=None,
        )
    contract = PublicResearchAccess.model_validate_json(json.dumps(value))
    wire_value = json.loads(contract.model_dump_json())
    assert client_module._research_access(wire_value) == wire_value
