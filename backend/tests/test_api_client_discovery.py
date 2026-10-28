"""Standalone ladder opt-in and owned receipts; no model/provider/network sends."""

import copy
import json
import uuid

import pytest
from test_api_client import RUN, client_module, initialized

INTENT = {"protocol": "polymath-surprise-ladder-v1", "max_stages": 3, "max_depth": 2}


def receipt(ordinal=0):
    return {
        "stage_id": str(uuid.uuid4()),
        "run_id": RUN,
        "ordinal": ordinal,
        "result_id": str(uuid.uuid4()),
        "result_sha256": "c" * 64,
        "depth": 1,
        "assessment": {
            "novelty": "not_found_in_bounded_search",
            "novelty_rationale": "The fixture search did not identify this bridge.",
            "confidence": "tentative",
            "confidence_score": 40,
            "confidence_scope": "uncalibrated_model_judgement",
            "confidence_rationale": "A model judgement, not a truth measurement.",
            "limitations": ["Synthetic evidence only."],
        },
        "parent_refs": [],
        "action_ordinal": 7 + ordinal * 5,
        "model_request_id": f"model-{7 + ordinal * 5:04}",
    }


def summary():
    stage = receipt()
    return {
        "run_id": RUN,
        "run_status": "interrupted",
        "discovery_intent": dict(INTENT),
        "stages": [stage],
        "selected_final_stage": None,
        "stop_reason": "model_call_limit",
        "total_usage": {
            "model_calls": 7,
            "input_tokens_reported": "70",
            "output_tokens_reported": "35",
            "calls_without_usage": 0,
        },
        "accounting": {
            "counted_spend_microdollars": "175",
            "open_hold_microdollars": "0",
            "unknown_hold_microdollars": "0",
        },
    }


def detail(stage=None):
    return {
        "stage": stage or receipt(),
        "result": {"mode": "surprise", "status": "bridge", "finding": {"outcome": "bridge"}},
        "parent_edges": [],
        "primary_paper_groups": [],
        "status": "current_supported",
    }


def test_explicit_intent_is_validated_before_write_and_default_wire_unchanged(monkeypatch):
    client = client_module.PolymathClient("http://127.0.0.1:8000")
    calls = []
    monkeypatch.setattr(
        client, "_scientific_write", lambda path, body, **kwargs: calls.append((path, body))
    )
    client.create_run("surprise")
    client.create_run("surprise", discovery_intent=INTENT)
    assert calls == [
        ("/api/v1/runs", {"mode": "surprise", "goal": "", "parent_result_ids": []}),
        (
            "/api/v1/runs",
            {"mode": "surprise", "goal": "", "parent_result_ids": [], "discovery_intent": INTENT},
        ),
    ]
    calls[1][1]["discovery_intent"]["max_stages"] = 1
    assert INTENT["max_stages"] == 3


@pytest.mark.parametrize(
    "change",
    [
        {"mode": "ask"},
        {"mode": "explore"},
        {"max_stages": True},
        {"max_stages": 0},
        {"max_stages": 5},
        {"max_depth": 0},
        {"max_depth": 4},
        {"max_depth": 1.5},
        {"protocol": "different"},
        {"extra": "ignored?"},
    ],
)
def test_invalid_intent_never_bootstraps_or_sends(change, monkeypatch):
    intent = {**INTENT, **{key: value for key, value in change.items() if key != "mode"}}
    client = client_module.PolymathClient("http://127.0.0.1:8000")
    monkeypatch.setattr(
        client, "_scientific_write", lambda *args, **kwargs: pytest.fail("invalid input sent")
    )
    with pytest.raises(client_module.ClientError, match="invalid_arguments"):
        client.create_run(change.get("mode", "surprise"), "Synthetic goal", discovery_intent=intent)


def test_owned_discovery_gets_do_not_bootstrap_and_return_unselected_partial(monkeypatch, tmp_path):
    client, _ = initialized(tmp_path, monkeypatch)
    data = summary()
    saved = detail(data["stages"][0])
    calls = []
    replies = [data, {"run_id": RUN, "stages": [saved]}, saved]

    def read(path):
        calls.append(path)
        return replies.pop(0)

    monkeypatch.setattr(client, "_private_json", read)
    assert client.get_discovery(RUN) == data
    assert client.list_discovery_stages(RUN)["stages"] == [saved]
    assert client.get_discovery_stage(RUN, saved["stage"]["stage_id"]) == saved
    assert calls == [
        f"/api/v1/runs/{RUN}/discovery",
        f"/api/v1/runs/{RUN}/discovery/stages",
        f"/api/v1/runs/{RUN}/discovery/stages/{saved['stage']['stage_id']}",
    ]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(run_id=str(uuid.uuid4())),
        lambda value: value["stages"][0].update(run_id=str(uuid.uuid4())),
        lambda value: value["stages"][0].update(result_sha256="not-a-hash"),
        lambda value: value["stages"][0].update(stage_id=RUN.upper()),
        lambda value: value["stages"][0].update(ordinal=True),
        lambda value: value["stages"][0]["assessment"].update(confidence_scope="calibrated"),
        lambda value: value["stages"][0]["assessment"].update(confidence_score=101),
        lambda value: value.update(selected_final_stage=receipt()),
        lambda value: value["accounting"].update(unknown_hold_microdollars="1"),
        lambda value: value["accounting"].update(open_hold_microdollars="01"),
        lambda value: value["total_usage"].update(calls_without_usage=8),
        lambda value: value["total_usage"].update(model_calls=0),
    ],
)
def test_summary_rejects_wrong_binding_and_invented_totals(mutation, monkeypatch):
    data = summary()
    mutation(data)
    client = client_module.PolymathClient("http://127.0.0.1:8000")
    monkeypatch.setattr(client, "_private_json", lambda path: data)
    with pytest.raises(client_module.ClientError, match="invalid_discovery_response"):
        client.get_discovery(RUN)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["stage"].update(stage_id=str(uuid.uuid4())),
        lambda data: data["stage"].update(run_id=str(uuid.uuid4())),
        lambda data: data.update(status="unknown"),
        lambda data: data["result"].update(mode="ask"),
        lambda data: data["result"].update(status="insufficient_evidence"),
        lambda data: data["stage"].update(
            parent_refs=[
                {"run_id": RUN, "result_id": str(uuid.uuid4()), "content_sha256": "b" * 64}
            ]
        ),
    ],
)
def test_exact_stage_binding_and_protocol_are_checked(mutation, monkeypatch):
    data = detail()
    stage_id = data["stage"]["stage_id"]
    mutation(data)
    client = client_module.PolymathClient("http://127.0.0.1:8000")
    monkeypatch.setattr(client, "_private_json", lambda path: data)
    with pytest.raises(client_module.ClientError, match="invalid_discovery_response"):
        client.get_discovery_stage(RUN, stage_id)


def test_same_run_parents_require_earlier_exact_receipts(monkeypatch):
    stages = [receipt(ordinal) for ordinal in range(3)]
    stages[2]["depth"] = 2
    stages[2]["parent_refs"] = [
        {"result_id": stage["result_id"], "run_id": RUN, "content_sha256": stage["result_sha256"]}
        for stage in stages[:2]
    ]
    data = summary()
    data.update(stages=stages, selected_final_stage=copy.deepcopy(stages[0]))
    data["total_usage"]["model_calls"] = 17
    client = client_module.PolymathClient("http://127.0.0.1:8000")
    monkeypatch.setattr(client, "_private_json", lambda path: data)
    assert client.get_discovery(RUN)["selected_final_stage"] == stages[0]
    stages[2]["parent_refs"][0]["content_sha256"] = "d" * 64
    with pytest.raises(client_module.ClientError, match="invalid_discovery_response"):
        client.get_discovery(RUN)


@pytest.mark.parametrize(
    "args",
    [
        ["--mode", "ask", "--discovery-ladder"],
        ["--mode", "surprise", "--max-depth", "2"],
        ["--mode", "surprise", "--discovery-ladder", "--max-stages", "5"],
    ],
)
def test_cli_invalid_opt_in_fails_before_create(args, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        client_module.PolymathClient, "create_run", lambda *a, **kw: pytest.fail("sent")
    )
    assert client_module.main(["--cookie-jar", str(tmp_path / "jar"), "runs", "create", *args]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "invalid_arguments"


def test_cli_explicit_opt_in_and_read_commands(monkeypatch, tmp_path, capsys):
    calls = []
    monkeypatch.setattr(
        client_module.PolymathClient, "create_run", lambda *a, **kw: calls.append(kw) or {}
    )
    prefix = ["--cookie-jar", str(tmp_path / "jar")]
    assert (
        client_module.main([*prefix, "runs", "create", "--mode", "surprise", "--discovery-ladder"])
        == 0
    )
    assert calls == [{"discovery_intent": INTENT}]
    monkeypatch.setattr(
        client_module.PolymathClient, "get_discovery", lambda self, run: {"run": run}
    )
    assert client_module.main([*prefix, "discovery", "show", RUN]) == 0
    assert '"run"' in capsys.readouterr().out


def test_cli_discovery_without_owner_file_never_constructs_client(monkeypatch, capsys):
    monkeypatch.setattr(
        client_module, "PolymathClient", lambda *a, **kw: pytest.fail("constructed")
    )
    assert client_module.main(["discovery", "show", RUN]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "cookie_jar_required"
