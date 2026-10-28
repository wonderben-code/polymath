"""Standalone SDK selects approved protocols and verifies saved extensions only."""

import copy
import hashlib
import json
import uuid

import pytest
from test_api_client import RUN, Response, client_module, initialized
from test_api_client import wire as wire_fixture
from test_api_client_access import HASH, mocked
from test_artifact_scientific_context import context_data
from test_ask_overview_contracts import result_data

from polymath.scientific_context_contracts import ArtifactScientificContext, bind_scientific_context

wire = wire_fixture


def protocols(capabilities=(), *, digest=HASH):
    return {
        "protocol": "polymath-research-protocols-v1",
        "activation_sha256": digest,
        "capabilities": list(capabilities),
    }


def scientific(monkeypatch, capabilities=(), *, source=None):
    client, calls = mocked(monkeypatch)
    monkeypatch.setattr(
        client,
        "research_protocols",
        lambda: calls.append(("protocols",)) or protocols(capabilities),
    )
    monkeypatch.setattr(
        client,
        "get_assessment_finding",
        lambda identifier: (
            calls.append(("source", identifier))
            or {"payload": source or {"mode": "explore", "status": "bridge"}}
        ),
    )
    return client, calls


def artifact_detail(*, revised=False, parents=False):
    context = ArtifactScientificContext(**context_data(revised=revised, parents=parents))
    reference = context.source_reference
    return {
        "id": RUN,
        "source_result_id": reference.result_id,
        "source_run_id": reference.run_id,
        "source_sha256": reference.content_sha256,
        "scientific_context": bind_scientific_context(context).model_dump(mode="json"),
    }


def rehash_context(value):
    binding = value["scientific_context"]
    binding["context_sha256"] = hashlib.sha256(
        json.dumps(
            binding["context"],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


@pytest.mark.parametrize(
    "capabilities", [(), ("ask_claim_overview_v1",), client_module.SCIENTIFIC_CAPABILITIES]
)
def test_protocols_parse_exact_canonical_subset(capabilities):
    assert client_module._research_protocols(protocols(capabilities)) == protocols(capabilities)
    assert client_module._research_protocols(protocols(digest=None))["activation_sha256"] is None


@pytest.mark.parametrize(
    "change",
    [
        {"protocol": "polymath-research-protocols-v2"},
        {"capabilities": ["ask_claim_overview_v1", "ask_claim_overview_v1"]},
        {"capabilities": ["ask_claim_overview_v1", "artifact_scientific_context_v1"]},
        {"capabilities": ["unreviewed-model-feature"]},
        {"capabilities": "ask_claim_overview_v1"},
        {"capabilities": None},
        {"activation_sha256": "PRIVATE-NON-DIGEST"},
        {"activation_sha256": None, "capabilities": ["ask_claim_overview_v1"]},
        {"model": "PRIVATE-MODEL"},
    ],
)
def test_protocol_metadata_never_accepts_ambiguous_or_private_authority(change):
    with pytest.raises(
        client_module.ClientError, match="invalid_research_protocols_response"
    ) as error:
        client_module._research_protocols({**protocols(), **change})
    assert "PRIVATE" not in str(error.value)


def test_ordinary_ask_automatically_selects_exact_approved_variant(monkeypatch):
    client, calls = scientific(monkeypatch, ["ask_claim_overview_v1"])
    client.create_run("ask", "What do these sources show?")
    assert [item[0] for item in calls] == ["session", "access", "protocols", "POST"]
    assert calls[-1][2] == {
        "mode": "ask",
        "goal": "What do these sources show?",
        "parent_result_ids": [],
        "ask_overview_intent": {"protocol": client_module.ASK_OVERVIEW_PROTOCOL},
        "expected_activation_sha256": HASH,
    }


@pytest.mark.parametrize("kind", ["formalise", "write_summary", "write_paper"])
def test_each_ordinary_artifact_automatically_selects_context_without_client_provenance(
    monkeypatch, kind
):
    client, calls = scientific(monkeypatch, ["artifact_scientific_context_v1"])
    client.create_artifact(kind, RUN)
    assert calls[-1][2] == {
        "kind": kind,
        "source_result_id": RUN,
        "expected_activation_sha256": HASH,
        "scientific_context_intent": {"protocol": client_module.ARTIFACT_CONTEXT_PROTOCOL},
    }


@pytest.mark.parametrize("action", ["ask", "artifact"])
def test_absent_capability_preserves_old_exact_body(action, monkeypatch):
    client, calls = scientific(monkeypatch)
    if action == "ask":
        client.create_run("ask", "A synthetic question?")
        expected = {"mode": "ask", "goal": "A synthetic question?", "parent_result_ids": []}
    else:
        client.create_artifact("formalise", RUN)
        expected = {"kind": "formalise", "source_result_id": RUN}
    assert calls[-1][2] == {**expected, "expected_activation_sha256": HASH}


@pytest.mark.parametrize("action", ["ask", "artifact", "assessment"])
def test_explicit_new_intent_without_approval_fails_before_any_post(action, monkeypatch):
    source = result_data()
    source.pop("overview_binding")
    client, calls = scientific(monkeypatch, source=source)
    with pytest.raises(client_module.ClientError, match="scientific_context_protocol_unavailable"):
        if action == "ask":
            client.create_run(
                "ask",
                "A question?",
                ask_overview_intent={"protocol": client_module.ASK_OVERVIEW_PROTOCOL},
            )
        elif action == "artifact":
            client.create_artifact(
                "formalise",
                RUN,
                scientific_context_intent={"protocol": client_module.ARTIFACT_CONTEXT_PROTOCOL},
            )
        else:
            client.create_assessment(
                RUN,
                RUN,
                [RUN],
                0,
                assessment_overview_intent={"protocol": client_module.ASSESSMENT_OVERVIEW_PROTOCOL},
            )
    assert not any(item[0] == "POST" for item in calls)


def test_hash_change_between_access_and_protocol_discovery_never_rebinds_or_sends(monkeypatch):
    client, calls = scientific(monkeypatch, ["ask_claim_overview_v1"])
    monkeypatch.setattr(
        client, "research_protocols", lambda: protocols(["ask_claim_overview_v1"], digest="d" * 64)
    )
    with pytest.raises(client_module.ClientError, match="research_activation_changed"):
        client.create_run("ask", "A question?")
    assert not any(item[0] == "POST" for item in calls)


@pytest.mark.parametrize("bound", [False, True])
def test_reassessment_uses_exact_ask_source_and_approved_variant(bound, monkeypatch):
    source = result_data()
    if not bound:
        source.pop("overview_binding")
    client, calls = scientific(monkeypatch, ["ask_reassessment_overview_v1"], source=source)
    identifier = str(uuid.uuid4())
    client.create_assessment(identifier, RUN, [RUN], 2)
    assert ("source", identifier) in calls
    assert calls[-1][2]["source_result_id"] == identifier
    assert calls[-1][2]["assessment_overview_intent"] == {
        "protocol": client_module.ASSESSMENT_OVERVIEW_PROTOCOL
    }


def test_bound_ask_source_cannot_drop_its_guarantee_when_profile_is_unavailable(monkeypatch):
    client, calls = scientific(monkeypatch, source=result_data())
    with pytest.raises(client_module.ClientError, match="ask_reassessment_overview_required"):
        client.create_assessment(RUN, RUN, [RUN], 0)
    assert not any(item[0] == "POST" for item in calls)


def test_bridge_reassessment_keeps_old_body_even_with_ask_profile(monkeypatch):
    client, calls = scientific(monkeypatch, ["ask_reassessment_overview_v1"])
    client.create_assessment(RUN, RUN, [RUN], 0)
    assert "assessment_overview_intent" not in calls[-1][2]
    with pytest.raises(client_module.ClientError, match="scientific_context_protocol_unavailable"):
        client.create_assessment(
            RUN,
            RUN,
            [RUN],
            0,
            assessment_overview_intent={"protocol": client_module.ASSESSMENT_OVERVIEW_PROTOCOL},
        )
    assert sum(item[0] == "POST" for item in calls) == 1


@pytest.mark.parametrize("action", ["ask", "artifact", "assessment"])
def test_explicit_new_protocol_cannot_fall_back_to_legacy_execution(action, monkeypatch):
    client, calls = mocked(monkeypatch, native=False)
    with pytest.raises(client_module.ClientError, match="scientific_context_protocol_unavailable"):
        if action == "ask":
            client.create_run(
                "ask",
                "A question?",
                ask_overview_intent={"protocol": client_module.ASK_OVERVIEW_PROTOCOL},
            )
        elif action == "artifact":
            client.create_artifact(
                "formalise",
                RUN,
                scientific_context_intent={"protocol": client_module.ARTIFACT_CONTEXT_PROTOCOL},
            )
        else:
            client.create_assessment(
                RUN,
                RUN,
                [RUN],
                0,
                assessment_overview_intent={"protocol": client_module.ASSESSMENT_OVERVIEW_PROTOCOL},
            )
    assert not any(item[0] == "POST" for item in calls)


def test_selected_post_response_loss_never_retries_or_falls_back(monkeypatch):
    client, calls = scientific(monkeypatch, ["ask_claim_overview_v1"])

    def lost(path, body):
        calls.append(("POST", path, body))
        raise client_module.ClientError("write_outcome_unknown")

    monkeypatch.setattr(client, "_send_write", lost)
    with pytest.raises(client_module.ClientError, match="write_outcome_unknown"):
        client.create_run("ask", "A question?")
    assert [item[0] for item in calls] == ["session", "access", "protocols", "POST"]


def test_protocol_read_uses_only_existing_owner_and_never_bootstraps(tmp_path, monkeypatch, wire):
    client, _ = initialized(tmp_path, monkeypatch)
    pending, requests, _ = wire
    # initialized() replaced _request on this instance; restore real transport.
    monkeypatch.delattr(client, "_request", raising=False)
    pending.append(Response(json.dumps(protocols()).encode()))
    assert client.research_protocols() == protocols()
    assert [(item[0], item[1]) for item in requests] == [("GET", "/api/v1/research/protocols")]


@pytest.mark.parametrize("retry", ["7", "86400", "PRIVATE", "86401", "-1"])
def test_protocol_read_429_reports_bounded_wait_without_any_retry(
    tmp_path, monkeypatch, wire, retry
):
    client, _ = initialized(tmp_path, monkeypatch)
    monkeypatch.delattr(client, "_request", raising=False)
    pending, requests, _ = wire
    from test_api_client import headers

    pending.append(
        Response(
            b'{"detail":"public_request_rate_limited"}',
            status=429,
            response_headers=headers(**{"Retry-After": retry}),
        )
    )
    with pytest.raises(client_module.ClientError) as error:
        client.research_protocols()
    assert error.value.status == 429
    assert error.value.retry_after_seconds == (int(retry) if retry in ("7", "86400") else None)
    assert len(requests) == 1 and requests[0][0] == "GET"


def test_actual_bound_ask_result_keeps_exact_unicode_text_order_and_labels(monkeypatch):
    client = client_module.PolymathClient("http://127.0.0.1:8000")
    value = result_data()
    monkeypatch.setattr(client, "_private_json", lambda _: value)
    assert client.get_result(RUN) is value
    assert "λ 😀 e\u0301" in value["finding"]["summary"]


@pytest.mark.parametrize(
    "change",
    [
        lambda value: value.update(overview_binding=None),
        lambda value: value.update(mode="explore"),
        lambda value: value["overview_binding"].update(claim_ids=["claim_a", "claim_a"]),
        lambda value: value["overview_binding"].update(claim_ids=["missing"]),
        lambda value: value["overview_binding"].update(summary="Unclassified extra prose"),
        lambda value: value["finding"].update(summary="A paraphrased unclassified summary."),
        lambda value: value["finding"]["claims"][0].update(label="verified"),
        lambda value: value["finding"]["claims"][0].update(evidence=[]),
        lambda value: value["finding"]["claims"][0].update(text="x" * 2001),
    ],
)
def test_overview_parser_rejects_dropped_or_unbound_claim_metadata(change):
    value = result_data()
    change(value)
    with pytest.raises(client_module.ClientError, match="invalid_overview_response"):
        client_module._answer_overview(value)


@pytest.mark.parametrize(
    "revised,parents", [(False, False), (False, True), (True, False), (True, True)]
)
def test_artifact_context_parser_accepts_actual_pure_contract_bytes(revised, parents):
    value = artifact_detail(revised=revised, parents=parents)
    assert client_module._artifact_scientific_context(value) is value


@pytest.mark.parametrize(
    "problem",
    ["null", "hash", "source", "unknown_node", "cycle", "receipt", "revision", "oversize"],
)
def test_artifact_context_rejects_misbound_or_corrupt_provenance(problem):
    value = artifact_detail(revised=True, parents=True)
    binding, context = value["scientific_context"], value["scientific_context"]["context"]
    if problem == "null":
        value["scientific_context"] = None
    elif problem == "hash":
        binding["context_sha256"] = "f" * 64
    elif problem == "source":
        value["source_sha256"] = "f" * 64
    elif problem == "unknown_node":
        context["ancestry_nodes"][-1]["reference"]["result_id"] = str(uuid.uuid4())
        rehash_context(value)
    elif problem == "cycle":
        edge = copy.deepcopy(context["ancestry_edges"][0])
        edge["child"], edge["parent"] = edge["parent"], edge["child"]
        context["ancestry_edges"].append(edge)
        rehash_context(value)
    elif problem == "receipt":
        context["stage_origin"]["receipt_sha256"] = "f" * 64
        rehash_context(value)
    elif problem == "revision":
        context["source_revision"]["applied_assessments"].clear()
        rehash_context(value)
    else:
        context["ancestry_nodes"][0]["finding"]["explanation"] = "λ" * 140000
        rehash_context(value)
    with pytest.raises(client_module.ClientError, match="invalid_artifact_context_response"):
        client_module._artifact_scientific_context(value)


def test_old_history_and_optional_new_fields_are_preserved_exactly():
    old = {"id": RUN, "status": "succeeded"}
    assert client_module._scientific_history(old) is old
    new = {
        **old,
        "scientific_context_intent": {"protocol": client_module.ARTIFACT_CONTEXT_PROTOCOL},
        "scientific_context_sha256": HASH,
    }
    assert client_module._scientific_history({"jobs": [old, new]})["jobs"][1] is new
    for broken in (
        {**new, "scientific_context_sha256": None},
        {**old, "ask_overview_intent": None},
        {**old, "scientific_context_intent": {"protocol": client_module.ARTIFACT_CONTEXT_PROTOCOL}},
    ):
        with pytest.raises(client_module.ClientError, match="invalid_scientific_history_response"):
            client_module._scientific_history(broken)


def public_answer():
    from test_sharing_projection import answer_result

    from polymath.sharing_projection import build_public_projection

    projection = build_public_projection(answer_result()).model_dump(mode="json")
    projection["overview_binding"] = {
        "protocol": client_module.ASK_OVERVIEW_PROTOCOL,
        "claim_ids": ["claim-3", "claim-1"],
    }
    by_id = {claim["id"]: claim for claim in projection["finding"]["claims"]}
    projection["finding"]["summary"] = "\n\n".join(
        by_id[identifier]["text"] for identifier in projection["overview_binding"]["claim_ids"]
    )
    return {
        "projection": projection,
        "projection_sha256": hashlib.sha256(
            json.dumps(
                projection,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        ).hexdigest(),
    }


def test_public_bound_ask_keeps_only_local_claim_selection_labels_and_citations(monkeypatch):
    value = public_answer()
    client = client_module.PolymathClient("http://127.0.0.1:8000")
    calls = []
    monkeypatch.setattr(client, "_json", lambda method, path: calls.append((method, path)) or value)
    assert client.public_finding("p" * 43) is not value
    assert calls == [("GET", "/api/v1/public/findings/" + "p" * 43)]
    assert "PRIVATE" not in json.dumps(value)
    assert client_module._public_scientific_metadata(value) == value


@pytest.mark.parametrize(
    "problem",
    ["null", "private_claim", "unknown_citation", "extra_prose", "hash", "private_context"],
)
def test_public_scientific_metadata_rejects_private_or_misbound_projection(problem):
    value = public_answer()
    projection = value["projection"]
    if problem == "null":
        projection["overview_binding"] = None
    elif problem == "private_claim":
        projection["finding"]["claims"][0]["id"] = "PRIVATE_KNOWN_ID"
    elif problem == "unknown_citation":
        projection["finding"]["claims"][0]["citation_ids"] = ["citation-64"]
    elif problem == "extra_prose":
        projection["finding"]["summary"] += "\n\nAn unclassified new inference."
    elif problem == "hash":
        value["projection_sha256"] = "f" * 64
    else:
        projection["scientific_context"] = artifact_detail()["scientific_context"]
    with pytest.raises(
        client_module.ClientError, match="invalid_(?:public_scientific|overview)_response"
    ):
        client_module._public_scientific_metadata(value)


def test_exact_source_preflight_reads_original_result_not_an_envelope_or_current_head(monkeypatch):
    client = client_module.PolymathClient("http://127.0.0.1:8000")
    value = {"id": RUN, "run_id": RUN, "payload": result_data()}
    calls = []
    monkeypatch.setattr(client, "_private_json", lambda path: calls.append(path) or value)
    assert client.get_assessment_finding(RUN) is value
    assert calls == ["/api/v1/assessments/findings/" + RUN]
    for broken in (
        {**value, "id": str(uuid.uuid4())},
        {**value, "payload": {"result": result_data()}},
        {**value, "payload": {**result_data(), "overview_binding": None}},
    ):
        monkeypatch.setattr(client, "_private_json", lambda path, saved=broken: saved)
        with pytest.raises(client_module.ClientError, match="invalid_assessment_finding_response"):
            client.get_assessment_finding(RUN)


@pytest.mark.parametrize(
    "args",
    [
        ["research", "protocols"],
        ["runs", "create", "--mode", "ask", "--goal", "A question?", "--claim-overview"],
        [
            "artifacts",
            "create",
            "--kind",
            "formalise",
            "--source-result",
            RUN,
            "--scientific-context",
        ],
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
            "--claim-overview",
        ],
    ],
)
def test_new_cli_commands_require_explicit_cookie_jar_without_any_request(
    args, monkeypatch, capsys
):
    monkeypatch.setattr(
        client_module.PolymathClient, "_request", lambda *a, **kw: pytest.fail("HTTP request")
    )
    assert client_module.main(args) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "cookie_jar_required"


@pytest.mark.parametrize(
    "code",
    [
        "meta_research_scientific_protocol_unavailable",
        "scientific_context_not_available",
        "scientific_context_admission_changed",
        "ask_reassessment_overview_required",
    ],
)
def test_server_protocol_refusal_is_explicit_and_never_retried(code, tmp_path, monkeypatch, wire):
    client, _ = initialized(tmp_path, monkeypatch)
    monkeypatch.delattr(client, "_request", raising=False)
    pending, requests, _ = wire
    pending.append(Response(json.dumps({"detail": code}).encode(), status=409))
    with pytest.raises(client_module.ClientError) as error:
        client._send_write("/api/v1/runs", {"mode": "ask"})
    assert error.value.code == code and error.value.status == 409
    assert len(requests) == 1 and requests[0][0] == "POST"


def test_cli_read_rate_limit_prints_bounded_wait_without_request_retry(
    monkeypatch, capsys, tmp_path
):
    def limited(_client):
        raise client_module.ClientError("public_request_rate_limited", 429, 7)

    monkeypatch.setattr(client_module.PolymathClient, "research_protocols", limited)
    assert (
        client_module.main(
            ["--cookie-jar", str(tmp_path / "owner.cookies"), "research", "protocols"]
        )
        == 2
    )
    assert json.loads(capsys.readouterr().err) == {
        "error": "public_request_rate_limited",
        "status": 429,
        "retry_after_seconds": 7,
    }
