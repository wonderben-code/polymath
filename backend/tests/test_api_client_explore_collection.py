"""Standalone Explore capability, single-send and exact owned history contracts."""

import copy
import hashlib
import json
import uuid

import pytest
from test_api_client import RUN, client_module
from test_api_client_access import HASH, mocked
from test_api_client_discovery import detail, receipt, summary
from test_sharing_projection import bridge_result

INTENT = {"protocol": "polymath-explore-collection-v1", "max_bridges": 3}


def canonical_hash(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def protocols(maximum=3, *, digest=HASH):
    return {
        "protocol": "polymath-explore-collection-access-v1",
        "activation_sha256": digest,
        "explore_collection_intent": None
        if maximum is None
        else {**INTENT, "max_bridges": maximum},
    }


def collection_client(monkeypatch, maximum=3, *, digest=HASH, native=True):
    client, calls = mocked(monkeypatch, native=native)
    monkeypatch.setattr(
        client,
        "explore_protocols",
        lambda: calls.append(("explore-protocols",)) or protocols(maximum, digest=digest),
    )
    return client, calls


def rank(stages, *, action=13, order=(1, 0), excluded=()):
    def reference(item):
        return {
            "run_id": RUN,
            "result_id": item["result_id"],
            "content_sha256": item["result_sha256"],
        }

    packet = {
        "model_request_id": f"model-{action:04}",
        "action_ordinal": action,
        "visible_receipts": stages,
        "ranked": [
            {
                "reference": reference(stages[index]),
                "rationale": "Prefer relevance to the original goal.",
            }
            for index in order
        ],
        "excluded": [
            {"reference": reference(stages[index]), "reason": "A weaker model-authored candidate."}
            for index in excluded
        ],
    }
    return {
        **{key: value for key, value in packet.items() if key != "visible_receipts"},
        "ranking_id": str(uuid.uuid4()),
        "run_id": RUN,
        "covered_through_ordinal": len(stages) - 1 if stages else None,
        "packet_sha256": canonical_hash(packet),
    }


def collection():
    value = summary()
    value.pop("discovery_intent")
    value.pop("selected_final_stage")
    stages = [receipt(), receipt(1)]
    stages[0]["assessment"]["confidence_score"] = 80
    stages[1]["assessment"]["confidence_score"] = 30
    ranking = rank(stages)
    return {
        **value,
        "explore_collection_intent": dict(INTENT),
        "stages": stages,
        "rankings": [ranking],
        "selected_final_ranking": ranking,
        "total_usage": {**value["total_usage"], "model_calls": 13},
    }


def bridge(stage=None):
    value = detail(copy.deepcopy(stage) if stage else None)
    value["result"] = bridge_result().model_dump(mode="json")
    return value


@pytest.mark.parametrize("maximum", [None, 1, 2, 3, 4])
def test_public_automatic_selection_is_exact_and_preserves_legacy_body(monkeypatch, maximum):
    client, calls = collection_client(monkeypatch, maximum)
    client.create_run("explore", "A synthetic question")
    assert [call[0] for call in calls] == ["session", "access", "explore-protocols", "POST"]
    expected = {
        "mode": "explore",
        "goal": "A synthetic question",
        "parent_result_ids": [],
        "expected_activation_sha256": HASH,
    }
    if maximum is not None:
        expected["explore_collection_intent"] = {**INTENT, "max_bridges": min(3, maximum)}
    assert calls[-1][2] == expected


def test_explicit_narrower_intent_keeps_requested_maximum_and_one_post(monkeypatch):
    client, calls = collection_client(monkeypatch, 4)
    requested = {**INTENT, "max_bridges": 1}
    client.create_run("explore", "A synthetic question", explore_collection_intent=requested)
    assert calls[-1][2]["explore_collection_intent"] == requested
    assert requested["max_bridges"] == 1
    assert sum(call[0] == "POST" for call in calls) == 1


@pytest.mark.parametrize(
    "maximum,digest,requested,code",
    [
        (None, HASH, INTENT, "research_explore_collection_not_approved"),
        (2, HASH, INTENT, "research_explore_collection_not_approved"),
        (3, "d" * 64, None, "research_activation_changed"),
        (None, None, None, "research_activation_changed"),
    ],
)
def test_unavailable_or_changed_approval_never_falls_back_or_posts(
    monkeypatch, maximum, digest, requested, code
):
    client, calls = collection_client(monkeypatch, maximum, digest=digest)
    with pytest.raises(client_module.ClientError, match=code):
        client.create_run("explore", "A synthetic question", explore_collection_intent=requested)
    assert not any(call[0] == "POST" for call in calls)


def test_read_rate_limit_is_exposed_without_post_or_resend(monkeypatch):
    client, calls = collection_client(monkeypatch)

    def unavailable():
        raise client_module.ClientError("public_request_rate_limited", 429, retry_after_seconds=17)

    monkeypatch.setattr(client, "explore_protocols", unavailable)
    with pytest.raises(client_module.ClientError) as error:
        client.create_run("explore", "A synthetic question")
    assert error.value.status == 429 and error.value.retry_after_seconds == 17
    assert not any(call[0] == "POST" for call in calls)


def test_failed_post_is_never_retried_or_replaced_with_legacy(monkeypatch):
    client, calls = collection_client(monkeypatch)

    def send(path, body):
        calls.append(("POST", path, body))
        raise client_module.ClientError("transport_failed")

    monkeypatch.setattr(client, "_send_write", send)
    with pytest.raises(client_module.ClientError, match="transport_failed"):
        client.create_run("explore", "A synthetic question")
    assert sum(call[0] == "POST" for call in calls) == 1


@pytest.mark.parametrize("kind", ["ask", "surprise", "f3", "resume", "legacy"])
def test_unrelated_modes_parents_resume_and_legacy_have_no_new_read(monkeypatch, kind):
    client, calls = collection_client(monkeypatch, native=kind != "legacy")
    if kind == "resume":
        client.resume_run(RUN, "1", HASH)
    elif kind == "f3":
        client.create_run("explore", "Parent question", [str(uuid.uuid4()), str(uuid.uuid4())])
    else:
        client.create_run("explore" if kind == "legacy" else kind, "A synthetic question")
    assert not any(call[0] == "explore-protocols" for call in calls)
    assert "explore_collection_intent" not in calls[-1][2]


def test_explicit_collection_rejects_legacy_execution(monkeypatch):
    client, calls = collection_client(monkeypatch, native=False)
    with pytest.raises(client_module.ClientError, match="research_explore_collection_not_approved"):
        client.create_run("explore", "A synthetic question", explore_collection_intent=INTENT)
    assert not any(call[0] == "POST" for call in calls)


@pytest.mark.parametrize(
    "change",
    [
        {"max_bridges": True},
        {"max_bridges": 0},
        {"max_bridges": 5},
        {"max_bridges": 1.5},
        {"protocol": "wrong"},
        {"extra": "PRIVATE"},
    ],
)
def test_invalid_collection_intent_fails_before_session(monkeypatch, change):
    client, calls = collection_client(monkeypatch)
    with pytest.raises(client_module.ClientError, match="invalid_arguments"):
        client.create_run("explore", "Question", explore_collection_intent={**INTENT, **change})
    assert calls == []


@pytest.mark.parametrize(
    "mode,parents,other",
    [
        ("ask", [], {}),
        ("surprise", [], {}),
        ("explore", [RUN, str(uuid.uuid4())], {}),
        ("explore", [], {"ask_overview_intent": {"protocol": client_module.ASK_OVERVIEW_PROTOCOL}}),
    ],
)
def test_conflicting_scope_fails_before_session(monkeypatch, mode, parents, other):
    client, calls = collection_client(monkeypatch)
    with pytest.raises(client_module.ClientError, match="invalid_arguments"):
        client.create_run(mode, "Question", parents, explore_collection_intent=INTENT, **other)
    assert calls == []


@pytest.mark.parametrize("maximum,digest", [(None, None), (None, HASH), (1, HASH), (4, HASH)])
def test_capability_exact_minimal_dto(maximum, digest):
    value = protocols(maximum, digest=digest)
    assert client_module._explore_protocols(value) == value


@pytest.mark.parametrize(
    "change",
    [
        {"protocol": "wrong"},
        {"activation_sha256": "PRIVATE"},
        {"activation_sha256": None},
        {"explore_collection_intent": {}},
        {"explore_collection_intent": {**INTENT, "max_bridges": True}},
        {"model": "PRIVATE"},
    ],
)
def test_malformed_or_private_capability_fails_closed(change):
    with pytest.raises(
        client_module.ClientError, match="invalid_explore_protocols_response"
    ) as error:
        client_module._explore_protocols({**protocols(), **change})
    assert "PRIVATE" not in str(error.value)


def test_all_owned_reads_are_bounded_get_only_and_keep_model_order(monkeypatch):
    client, _ = collection_client(monkeypatch)
    monkeypatch.delattr(client, "explore_protocols")
    value = collection()
    details = [bridge(stage) for stage in value["stages"]]
    replies = [
        protocols(),
        value,
        {"run_id": RUN, "stages": details},
        details[0],
        {"run_id": RUN, "rankings": value["rankings"]},
    ]
    paths = []
    monkeypatch.setattr(client, "_private_json", lambda path: paths.append(path) or replies.pop(0))
    assert client.explore_protocols() == protocols()
    result = client.get_explore_collection(RUN)
    assert (
        result["rankings"][0]["ranked"][0]["reference"]["result_id"]
        == value["stages"][1]["result_id"]
    )
    assert client.list_explore_bridges(RUN)["stages"] == details
    assert client.get_explore_bridge(RUN, details[0]["stage"]["stage_id"]) == details[0]
    assert client.list_explore_rankings(RUN)["rankings"] == value["rankings"]
    assert paths == [
        "/api/v1/research/explore-protocols",
        f"/api/v1/runs/{RUN}/explore-collection",
        f"/api/v1/runs/{RUN}/bridges",
        f"/api/v1/runs/{RUN}/bridges/{details[0]['stage']['stage_id']}",
        f"/api/v1/runs/{RUN}/rankings",
    ]


@pytest.mark.parametrize("finished", [False, True])
def test_empty_and_excluded_final_are_valid_without_manufactured_selection(monkeypatch, finished):
    client, _ = collection_client(monkeypatch)
    value = collection()
    value["stages"] = []
    ranking = rank([], action=1, order=())
    value["rankings"] = [ranking]
    value["selected_final_ranking"] = ranking if finished else None
    monkeypatch.setattr(client, "_private_json", lambda path: value)
    assert client.get_explore_collection(RUN)["selected_final_ranking"] == (
        ranking if finished else None
    )
    value["stages"] = [receipt()]
    ranking = rank(value["stages"], order=(), excluded=(0,))
    value["rankings"] = [ranking]
    value["selected_final_ranking"] = ranking if finished else None
    assert client.get_explore_collection(RUN)["rankings"][0]["ranked"] == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v.update(run_id=str(uuid.uuid4())),
        lambda v: v.update(model="PRIVATE"),
        lambda v: v["explore_collection_intent"].update(max_bridges=1),
        lambda v: v["stages"][1].update(depth=2),
        lambda v: v["stages"][1].update(action_ordinal=17),
        lambda v: v["stages"][1].update(model_request_id="model-0021"),
        lambda v: v["rankings"][0].update(packet_sha256="d" * 64),
        lambda v: v["rankings"][0]["ranked"][0]["reference"].update(content_sha256="d" * 64),
        lambda v: v["rankings"][0].update(ranked=v["rankings"][0]["ranked"][:1]),
        lambda v: v["rankings"][0]["ranked"][0].update(rationale="Changed after hashing"),
        lambda v: v["rankings"][0].update(covered_through_ordinal=True),
        lambda v: v["rankings"][0].update(action_ordinal=12),
        lambda v: v.update(selected_final_ranking=None, stop_reason=""),
        lambda v: v["total_usage"].update(model_calls=12),
        lambda v: v["accounting"].update(unknown_hold_microdollars="1"),
        lambda v: v.update(stages=v["stages"] * 3),
        lambda v: v.update(rankings=v["rankings"] * 17),
    ],
)
def test_tampered_collection_history_is_rejected_without_echo(monkeypatch, mutation):
    client, _ = collection_client(monkeypatch)
    value = copy.deepcopy(collection())
    mutation(value)
    monkeypatch.setattr(client, "_private_json", lambda path: value)
    with pytest.raises(
        client_module.ClientError, match="invalid_explore_collection_response"
    ) as error:
        client.get_explore_collection(RUN)
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda v: v["result"].update(mode="surprise"),
        lambda v: v["result"]["sources"][0].update(text="Tampered source"),
        lambda v: v["result"].update(overview_binding=None),
        lambda v: v["stage"].update(depth=2),
        lambda v: v.update(primary_paper_groups=[{"paper_id": "operator", "source_ids": []}]),
    ],
)
def test_bridge_reader_verifies_mode_base_scope_and_exact_source_hash(monkeypatch, mutation):
    client, _ = collection_client(monkeypatch)
    value = bridge()
    mutation(value)
    monkeypatch.setattr(client, "_private_json", lambda path: value)
    with pytest.raises(client_module.ClientError, match="invalid_explore_collection_response"):
        client.get_explore_bridge(RUN, value["stage"]["stage_id"])


def test_new_history_intent_is_optional_and_strict(monkeypatch):
    client, _ = collection_client(monkeypatch)
    old = {"id": RUN, "mode": "explore"}
    monkeypatch.setattr(client, "_private_json", lambda path: old)
    assert client.get_run(RUN) is old
    for intent in (None, {}, {**INTENT, "max_bridges": 5}):
        old["explore_collection_intent"] = intent
        with pytest.raises(client_module.ClientError, match="invalid_scientific_history_response"):
            client.get_run(RUN)


def test_explicit_cli_collection_and_read_commands_use_exact_protocol(
    monkeypatch, tmp_path, capsys
):
    writes, reads = [], []
    monkeypatch.setattr(
        client_module.PolymathClient,
        "_scientific_write",
        lambda self, path, body, **kwargs: writes.append((path, body)) or {"id": RUN},
    )
    options = ["--cookie-jar", str(tmp_path / "owner.cookies")]
    assert (
        client_module.main(
            [
                *options,
                "runs",
                "create",
                "--mode",
                "explore",
                "--goal",
                "Question",
                "--explore-collection",
                "--max-bridges",
                "1",
            ]
        )
        == 0
    )
    assert writes == [
        (
            "/api/v1/runs",
            {
                "mode": "explore",
                "goal": "Question",
                "parent_result_ids": [],
                "explore_collection_intent": {**INTENT, "max_bridges": 1},
            },
        )
    ]
    monkeypatch.setattr(
        client_module.PolymathClient,
        "_private_json",
        lambda self, path: reads.append(path) or {"run_id": RUN, "rankings": []},
    )
    assert client_module.main([*options, "explore", "rankings", RUN]) == 0
    assert reads == [f"/api/v1/runs/{RUN}/rankings"]
    assert "PRIVATE" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "arguments",
    [
        ["--max-bridges", "2"],
        ["--explore-collection", "--max-bridges", "0"],
        ["--explore-collection", "--claim-overview"],
        ["--explore-collection", "--parent-result", RUN, "--parent-result", str(uuid.uuid4())],
    ],
)
def test_invalid_cli_scope_never_bootstraps_or_sends(monkeypatch, tmp_path, arguments):
    monkeypatch.setattr(
        client_module.PolymathClient,
        "_scientific_write",
        lambda *args, **kwargs: pytest.fail("invalid CLI sent"),
    )
    assert (
        client_module.main(
            [
                "--cookie-jar",
                str(tmp_path / "owner.cookies"),
                "runs",
                "create",
                "--mode",
                "explore",
                "--goal",
                "Question",
                *arguments,
            ]
        )
        == 2
    )


def test_rankings_get_rejects_cross_history_reference_substitution(monkeypatch):
    client, _ = collection_client(monkeypatch)
    stage = receipt()
    first = rank([stage], action=8, order=(0,))
    second = rank([stage], action=9, order=(0,))
    second["ranked"][0]["reference"]["content_sha256"] = "d" * 64
    monkeypatch.setattr(
        client, "_private_json", lambda path: {"run_id": RUN, "rankings": [first, second]}
    )
    with pytest.raises(client_module.ClientError, match="invalid_explore_collection_response"):
        client.list_explore_rankings(RUN)


@pytest.mark.parametrize(
    "arguments", [[], ["--help"], ["explore", "--help"], ["runs", "create", "--help"]]
)
def test_help_and_noop_never_open_network(monkeypatch, arguments):
    monkeypatch.setattr(
        client_module.http.client.HTTPConnection,
        "connect",
        lambda *args: pytest.fail("help opened network"),
    )
    if "--help" in arguments:
        with pytest.raises(SystemExit) as error:
            client_module.main(arguments)
        assert error.value.code == 0
    else:
        assert client_module.main(arguments) == 0
