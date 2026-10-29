"""Launch preflight contracts with an injected fetcher; no network and no model calls."""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "meta-preflight.py"
SPEC = importlib.util.spec_from_file_location("polymath_meta_preflight", SCRIPT)
preflight = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = preflight  # dataclasses resolve their defining module
SPEC.loader.exec_module(preflight)

SECRET = "fixture-meta-secret-never-printed"
OPENALEX_SECRET = "fixture-openalex-secret-never-printed"


def arguments(tmp_path, *, check_key=False, offline=False):
    return argparse.Namespace(
        check_key=check_key, offline=offline, env_file=str(tmp_path / "missing.env")
    )


class Recorder:
    def __init__(self, models=("muse-spark-1.1", "llama-4"), meta_status=200, openalex=200):
        self.calls = []
        self.models = models
        self.meta_status = meta_status
        self.openalex = openalex

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        if url == preflight.META_MODELS_URL:
            if "Authorization" not in headers:
                return preflight.Response(401, b"{}")
            body = json.dumps({"data": [{"id": model} for model in self.models]}).encode()
            return preflight.Response(self.meta_status, body)
        if url == preflight.ARXIV_URL:
            return preflight.Response(200, b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
        if url == preflight.OPENALEX_URL:
            return preflight.Response(self.openalex, b"{}")
        if url == preflight.ZENODO_SANDBOX_URL:
            return preflight.Response(200, b"{}")
        raise AssertionError("unexpected destination")


def test_allowlist_contains_no_completion_or_paid_endpoint():
    assert all("completions" not in url for url in preflight.ALLOWED_URLS)
    assert all(url.startswith("https://") for url in preflight.ALLOWED_URLS)
    with pytest.raises(ValueError):
        preflight.http_fetch("https://api.meta.ai/v1/chat/completions", {})


def test_full_check_passes_and_never_prints_secrets(tmp_path, capsys):
    fetch = Recorder()
    environ = {
        "MODEL_API_KEY": SECRET,
        "META_MODEL": "muse-spark-1.1",
        "OPENALEX_API_KEY": OPENALEX_SECRET,
    }
    status = preflight.run(arguments(tmp_path, check_key=True), environ, fetch)
    output = capsys.readouterr().out
    assert status == 0
    assert SECRET not in output and OPENALEX_SECRET not in output
    assert "Muse Spark is available: muse-spark-1.1" in output
    assert "META_MODEL 'muse-spark-1.1' is in the list" in output
    assert {url for url, _ in fetch.calls} <= preflight.ALLOWED_URLS
    authenticated = [headers for url, headers in fetch.calls if "Authorization" in headers]
    assert {"Authorization": f"Bearer {SECRET}"} in authenticated
    assert {"Authorization": f"Bearer {OPENALEX_SECRET}"} in authenticated
    for url, headers in fetch.calls:
        assert SECRET not in url and OPENALEX_SECRET not in url
        if url != preflight.META_MODELS_URL:
            assert headers.get("Authorization") != f"Bearer {SECRET}"


def test_unauthenticated_meta_probe_sends_no_key(tmp_path):
    fetch = Recorder()
    preflight.run(arguments(tmp_path), {"MODEL_API_KEY": SECRET}, fetch)
    meta = [headers for url, headers in fetch.calls if url == preflight.META_MODELS_URL]
    assert meta == [{}]


def test_missing_key_fails_without_any_authenticated_request(tmp_path, capsys):
    fetch = Recorder()
    status = preflight.run(arguments(tmp_path, check_key=True), {}, fetch)
    assert status == 1
    assert all("Authorization" not in headers for _, headers in fetch.calls)
    assert "MODEL_API_KEY is not set" in capsys.readouterr().out


def test_configured_model_absent_from_list_fails(tmp_path, capsys):
    fetch = Recorder(models=("llama-4",))
    environ = {"MODEL_API_KEY": SECRET, "META_MODEL": "muse-spark-9"}
    status = preflight.run(arguments(tmp_path, check_key=True), environ, fetch)
    output = capsys.readouterr().out
    assert status == 1
    assert "no Muse Spark model listed" in output
    assert "'muse-spark-9' is not in the list" in output


def test_rejected_key_reports_access_denied(tmp_path, capsys):
    fetch = Recorder(meta_status=403)
    status = preflight.run(arguments(tmp_path, check_key=True), {"MODEL_API_KEY": SECRET}, fetch)
    assert status == 1
    assert "HTTP 403: access denied" in capsys.readouterr().out


def test_enabled_spending_gate_is_reported(tmp_path, capsys):
    environ = {"MODEL_API_KEY": SECRET, "PAID_META_RESEARCH_ENABLED": "true"}
    preflight.run(arguments(tmp_path, offline=True), environ, Recorder())
    assert "spending gates already on: PAID_META_RESEARCH_ENABLED" in capsys.readouterr().out


def test_offline_mode_makes_no_requests(tmp_path):
    fetch = Recorder()
    preflight.run(arguments(tmp_path, offline=True), {"MODEL_API_KEY": SECRET}, fetch)
    assert fetch.calls == []


def test_openalex_shared_budget_exhaustion_is_a_warning_without_key(tmp_path, capsys):
    fetch = Recorder(openalex=429)
    status = preflight.run(arguments(tmp_path), {"MODEL_API_KEY": SECRET}, fetch)
    assert status == 0
    assert "set a free OPENALEX_API_KEY" in capsys.readouterr().out


def test_transport_exceptions_are_sanitized(tmp_path, capsys):
    def broken(url, headers):
        raise OSError(f"proxy said {SECRET}")

    status = preflight.run(arguments(tmp_path, check_key=True), {"MODEL_API_KEY": SECRET}, broken)
    output = capsys.readouterr().out
    assert status == 1
    assert SECRET not in output
    assert "no response" in output


def test_dotenv_values_load_but_process_settings_win(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\nMODEL_API_KEY=from-file\nexport META_MODEL='muse-spark-1.1' # note\n"
    )
    config = preflight.load_config(
        {"MODEL_API_KEY": "from-process"}, preflight.read_dotenv(env_file)
    )
    assert config["MODEL_API_KEY"] == "from-process"
    assert config["META_MODEL"] == "muse-spark-1.1"


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b'{"data": "x"}', b"x" * (2 * 1024 * 1024)],
)
def test_unexpected_model_list_shapes_are_rejected(body):
    assert preflight.parse_model_ids(body) is None


def test_model_ids_are_bounded_and_deduplicated():
    body = json.dumps({"data": [{"id": "b"}, {"id": "a"}, {"id": "a"}, {"id": "x" * 201}, 3]})
    assert preflight.parse_model_ids(body.encode()) == ["a", "b"]
