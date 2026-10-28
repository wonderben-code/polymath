"""Focused standalone-client contracts; no backend calls or non-stdlib client imports."""

import hashlib
import http.cookiejar
import importlib.util
import io
import json
import socket
import stat
import uuid
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "polymath-client.py"
SPEC = importlib.util.spec_from_file_location("polymath_client_unit", SCRIPT)
client_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(client_module)
OWNER = "a" * 43
CSRF = "b" * 64
RUN = "a1111111-1111-4111-8111-111111111111"
ORIGIN = "http://127.0.0.1:8000"


def headers(content_type="application/json", **extra):
    result = Message()
    result["Content-Type"] = content_type
    for key, value in extra.items():
        result[key] = value
    return result


def session_response(token=OWNER):
    return json.dumps(
        {"csrf_token": CSRF, "expires_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()}
    ).encode(), headers(
        **{
            "Set-Cookie": (
                f"polymath_session={token}; Max-Age=86400; Path=/api/v1; HttpOnly; SameSite=lax"
            )
        }
    )


class Response:
    def __init__(self, body=b"{}", *, status=200, response_headers=None, failure=None):
        self.body, self.status = body, status
        self.headers = response_headers or headers()
        self.length, self.closed, self.failure = len(body), False, failure

    def read1(self, size):
        if self.failure:
            raise self.failure
        result, self.body = self.body[:size], self.body[size:]
        self.length -= len(result)
        return result

    def close(self):
        self.closed = True


@pytest.fixture
def wire(monkeypatch):
    pending, requests, connections = [], [], []

    class Socket:
        def shutdown(self, direction):
            assert direction == socket.SHUT_RDWR

    class Connection:
        def __init__(self, host, port, **kwargs):
            self.host, self.port, self.options = host, port, kwargs
            self.sock, self.closed = Socket(), False
            connections.append(self)

        def connect(self):
            pass

        def request(self, method, path, *, body, headers):
            requests.append((method, path, body, headers))

        def getresponse(self):
            response = pending.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response

        def close(self):
            self.closed = True

    monkeypatch.setattr(client_module.http.client, "HTTPConnection", Connection)
    monkeypatch.setattr(client_module.http.client, "HTTPSConnection", Connection)
    return pending, requests, connections


def initialized(tmp_path, monkeypatch):
    path = tmp_path / "owner.cookies"
    client = client_module.PolymathClient(ORIGIN, cookie_jar=path)
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: session_response())
    client.init_session()
    return client, path


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "ftp://127.0.0.1",
        "http://user:PRIVATE@localhost",
        "http://127.0.0.1:0",
        "https://example.com:0",
        "http://localhost:",
        "https://example.com/path",
        "https://example.com?token=PRIVATE",
        "https://example.com#PRIVATE",
        "\nhttps://example.com",
        "http://local\thost",
        "http://localhost\\@example.com",
        "http://127.example.com",
        "http://127.0.0.1.evil",
        "http://[v1.example]",
    ],
)
def test_origin_rejects_unsafe_or_reinterpreted_authorities(url):
    with pytest.raises(client_module.ClientError, match="invalid_base_url"):
        client_module.PolymathClient(url)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:80/", "http://localhost"),
        ("http://127.0.0.1:8000", ORIGIN),
        ("http://[::1]:8000", "http://[::1]:8000"),
        ("HTTPS://EXAMPLE.COM:443/", "https://example.com"),
    ],
)
def test_origins_are_canonical_without_dns_resolution(url, expected):
    assert client_module.canonical_origin(url) == expected


def test_default_help_and_public_get_never_touch_cookie_file(monkeypatch, capsys, wire):
    def forbidden(*args, **kwargs):
        pytest.fail("read-only command touched private state")

    monkeypatch.setattr(client_module, "_read_private", forbidden)
    assert client_module.main([]) == 0
    assert "public" in capsys.readouterr().out
    pending, requests, _ = wire
    pending.append(Response(b'{"items":[],"next_cursor":null}'))
    client = client_module.PolymathClient(ORIGIN, cookie_jar="DO-NOT-LOAD")
    assert client.public_library()["items"] == []
    assert "Cookie" not in requests[0][3]
    assert requests[0][1].startswith("/api/v1/public/library?")


def test_explicit_session_uses_private_origin_bound_mozilla_jar_without_csrf(tmp_path, monkeypatch):
    client, path = initialized(tmp_path, monkeypatch)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert CSRF not in path.read_text()
    assert "# Polymath origin: " + ORIGIN in path.read_text()
    standard = http.cookiejar.MozillaCookieJar(str(path))
    standard.load(ignore_discard=True)
    assert [(cookie.name, cookie.value) for cookie in standard] == [("polymath_session", OWNER)]
    assert client._csrf == CSRF
    with pytest.raises(client_module.ClientError, match="invalid_cookie_jar"):
        client_module.PolymathClient("http://127.0.0.1:9000", cookie_jar=path)._load_owner()


@pytest.mark.parametrize("kind", ["permissive", "symlink", "directory", "oversized"])
def test_cookie_file_permissions_and_type_fail_before_network(tmp_path, kind, monkeypatch):
    path = tmp_path / "owner.cookies"
    if kind == "directory":
        path.mkdir(mode=0o600)
    elif kind == "symlink":
        target = tmp_path / "target"
        target.write_text("PRIVATE")
        target.chmod(0o600)
        path.symlink_to(target)
    else:
        path.write_bytes(b"x" * (20000 if kind == "oversized" else 1))
        path.chmod(0o644 if kind == "permissive" else 0o600)
    client = client_module.PolymathClient(ORIGIN, cookie_jar=path)
    monkeypatch.setattr(
        client, "_start", lambda *args, **kwargs: pytest.fail("unsafe file reached HTTP")
    )
    with pytest.raises(client_module.ClientError):
        client.init_session()


def test_cookie_file_change_fails_before_post_and_preserves_replacement(tmp_path, monkeypatch):
    client, path = initialized(tmp_path, monkeypatch)
    replacement = path.read_bytes().replace(OWNER.encode(), ("c" * 43).encode())
    path.write_bytes(replacement)
    path.chmod(0o600)
    with pytest.raises(client_module.ClientError, match="private_file_changed"):
        client.create_run("explore", "Synthetic request")
    assert path.read_bytes() == replacement


def test_cookie_file_short_read_cannot_hide_trailing_origin_metadata(tmp_path, monkeypatch):
    _, path = initialized(tmp_path, monkeypatch)
    original = path.read_bytes()
    path.write_bytes(original + b"# Polymath origin: http://127.0.0.1:9000\n")
    path.chmod(0o600)
    monkeypatch.setattr(client_module.os, "read", lambda descriptor, length: original)
    with pytest.raises(client_module.ClientError, match="unsafe_private_file"):
        client_module._read_private(path)


def test_unexpected_owner_rotation_stops_before_post_and_never_overwrites_jar(
    tmp_path, monkeypatch
):
    client, path = initialized(tmp_path, monkeypatch)
    original, calls = path.read_bytes(), []

    def rotate(method, endpoint, **kwargs):
        calls.append((method, endpoint))
        return session_response("c" * 43)

    monkeypatch.setattr(client, "_request", rotate)
    with pytest.raises(client_module.ClientError, match="owner_session_changed"):
        client.cancel_run(RUN)
    assert calls == [("GET", "/api/v1/session")]
    assert path.read_bytes() == original


def test_private_get_never_bootstraps_and_writes_send_csrf_origin_once(tmp_path, monkeypatch, wire):
    _, path = initialized(tmp_path, monkeypatch)
    client = client_module.PolymathClient(ORIGIN, cookie_jar=path)
    pending, requests, _ = wire
    pending.append(Response(b'{"id":"' + RUN.encode() + b'"}'))
    assert client.get_run(RUN)["id"] == RUN
    assert requests[0][1] == "/api/v1/runs/" + RUN
    assert requests[0][3]["Cookie"] == "polymath_session=" + OWNER
    session_body, session_headers = session_response()
    pending.extend(
        (
            Response(session_body, response_headers=session_headers),
            Response(
                b'{"native_research_required":false,"research_available":true,"meta_configured":false}'
            ),
            Response(b'{"id":"' + RUN.encode() + b'"}', status=201),
        )
    )
    assert client.create_run("surprise")["id"] == RUN
    method, path, body, request_headers = requests[-1]
    assert method == "POST" and path == "/api/v1/runs"
    assert request_headers["Origin"] == ORIGIN and request_headers["X-CSRF-Token"] == CSRF
    assert json.loads(body) == {"mode": "surprise", "goal": "", "parent_result_ids": []}
    assert len([request for request in requests if request[0] == "POST"]) == 1
    assert requests[-2][1] == "/api/v1/capabilities"
    assert "Cookie" not in requests[-2][3]


@pytest.mark.parametrize(
    "response",
    [
        Response(failure=OSError("PRIVATE-TRANSPORT-DETAIL")),
        Response(b"not-json", status=201),
        Response(b'{"detail":"PRIVATE-SERVER-DETAIL"}', status=500),
        Response(b'{"detail":"research_storage_unavailable"}', status=503),
    ],
)
def test_unknown_post_outcome_is_static_and_never_retried(response, wire):
    pending, requests, connections = wire
    pending.append(response)
    client = client_module.PolymathClient(ORIGIN)
    with pytest.raises(client_module.ClientError, match="write_outcome_unknown") as caught:
        client._json("POST", "/api/v1/runs", body={"goal": "Synthetic"})
    assert "PRIVATE" not in str(caught.value)
    assert len(requests) == 1 and connections[0].closed


def test_redirects_never_forward_owner_cookie_and_https_verification_is_preserved(
    tmp_path, monkeypatch, wire
):
    _, path = initialized(tmp_path, monkeypatch)
    pending, requests, connections = wire
    pending.append(
        Response(status=302, response_headers=headers(Location="https://evil.example/PRIVATE"))
    )
    client = client_module.PolymathClient(ORIGIN, cookie_jar=path)
    with pytest.raises(client_module.ClientError, match="unexpected_redirect"):
        client.get_run(RUN)
    assert len(requests) == 1 and connections[0].host == "127.0.0.1"
    assert requests[0][3]["Cookie"] == "polymath_session=" + OWNER
    pending.append(Response())
    client_module.PolymathClient("https://example.com").public_map()
    context = connections[-1].options["context"]
    assert context.check_hostname and context.verify_mode == client_module.ssl.CERT_REQUIRED


def test_json_response_size_is_bounded_and_unknown_errors_do_not_echo(wire, monkeypatch):
    pending, _, _ = wire
    pending.append(Response(b"x" * 100))
    client = client_module.PolymathClient(ORIGIN)
    with pytest.raises(client_module.ClientError, match="response_too_large"):
        client._request("GET", "/api/v1/public/library", maximum=10)
    pending.append(Response(b'{"detail":"PRIVATE"}', status=403))
    with pytest.raises(client_module.ClientError, match="http_error") as error:
        client.public_map()
    assert "PRIVATE" not in str(error.value)


@pytest.mark.parametrize(
    "frame",
    [
        b'id: 2\nevent: activity\ndata: {"sequence":3}',
        b'id: 1\nevent: activity\ndata: {"sequence":1}',
        b'id: 9223372036854775808\nevent: activity\ndata: {"sequence":9223372036854775808}',
        b"id: 2\nevent: heartbeat\ndata: {}",
        b'event: activity\ndata: {"sequence":2}',
        b'id: 2\nevent: activity\ndata: {"sequence":true}',
        b"event: unexpected\ndata: {}",
        b"event: activity\ndata: \xff",
    ],
)
def test_event_cursor_replay_and_frame_integrity_are_strict(frame):
    client = client_module.PolymathClient(ORIGIN)
    with pytest.raises(client_module.ClientError, match="invalid_event_stream"):
        client._event(frame, 1)


def test_sse_passes_replay_cursor_and_closes_at_explicit_limit(tmp_path, monkeypatch, wire):
    _, path = initialized(tmp_path, monkeypatch)
    pending, requests, connections = wire
    response = Response(
        b'id: 2\nevent: activity\ndata: {"sequence":2}\n\n'
        b'id: 3\nevent: activity\ndata: {"sequence":3}\n\n',
        response_headers=headers("text/event-stream"),
    )
    pending.append(response)
    client = client_module.PolymathClient(ORIGIN, cookie_jar=path)
    assert list(client.events(RUN, after=0, last_event_id=1, max_events=1)) == [
        {"id": 2, "event": "activity", "data": {"sequence": 2}}
    ]
    assert requests[0][3]["Last-Event-ID"] == "1"
    assert "after=1" in requests[0][1]
    assert response.closed and connections[0].closed
    assert client._connections == set() and client._timers == {}


def test_exports_verify_hash_before_any_output_and_create_private_exact_bytes(
    tmp_path, monkeypatch
):
    client = client_module.PolymathClient(ORIGIN)
    artifact = str(uuid.uuid4())
    content = "# Synthetic\n\n🌱 Exact saved Markdown.\n".encode()
    digest = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(
        client, "get_artifact", lambda identifier: {"id": identifier, "markdown_sha256": digest}
    )
    monkeypatch.setattr(
        client, "_request", lambda *args, **kwargs: (b"wrong", headers("text/markdown"))
    )
    output = tmp_path / "saved.md"
    with pytest.raises(client_module.ClientError, match="export_integrity_failed"):
        client.export_artifact(artifact, output)
    assert not output.exists()
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: (
            content,
            headers(
                "text/markdown", **{"Content-Disposition": 'attachment; filename="../../PRIVATE"'}
            ),
        ),
    )
    assert client.export_artifact(artifact, output) == {
        "markdown_sha256": digest,
        "byte_count": len(content),
    }
    assert output.read_bytes() == content and stat.S_IMODE(output.stat().st_mode) == 0o600
    with pytest.raises(client_module.ClientError, match="output_exists"):
        client.export_artifact(artifact, output)
    assert output.read_bytes() == content
    assert list(tmp_path.glob(".polymath-*.tmp")) == []


def test_cli_rejects_unknown_model_flags_without_echoing_secrets(capsys):
    assert (
        client_module.main(["--api-key", "PRIVATE-SECRET", "runs", "create", "--mode", "explore"])
        == 2
    )
    output = capsys.readouterr()
    assert "PRIVATE-SECRET" not in output.err + output.out
    assert json.loads(output.err) == {"error": "invalid_arguments", "status": None}


def test_redaction_removes_known_tokens_from_outputs_and_session_only_returns_expiry(
    tmp_path, monkeypatch
):
    client, _ = initialized(tmp_path, monkeypatch)
    value = client._redact({"token": "unknown", "summary": "Echo " + OWNER + " / " + CSRF})
    assert value == {"token": "[redacted]", "summary": "Echo [redacted] / [redacted]"}
    assert set(client.init_session()) == {"expires_at"}


def test_goal_file_is_bounded_and_surprise_can_be_created_only_explicitly(
    monkeypatch, capsys, tmp_path
):
    seen = []
    monkeypatch.setattr(
        client_module.PolymathClient,
        "create_run",
        lambda self, mode, goal, parents: seen.append((mode, goal, parents)) or {"id": RUN},
    )
    monkeypatch.setattr(
        client_module.sys, "stdin", type("Input", (), {"buffer": io.BytesIO(b"x" * 16001)})()
    )
    prefix = ["--cookie-jar", str(tmp_path / "owner.cookies")]
    assert client_module.main([*prefix, "runs", "create", "--mode", "ask", "--goal-file", "-"]) == 2
    assert not seen
    capsys.readouterr()
    assert client_module.main([*prefix, "runs", "create", "--mode", "surprise"]) == 0
    assert seen == [("surprise", "", [])]


@pytest.mark.parametrize(
    "arguments",
    [
        ["session", "init"],
        ["runs", "create", "--mode", "explore", "--goal", "Synthetic"],
        ["runs", "show", RUN],
        ["runs", "cancel", RUN],
        ["artifacts", "export", RUN, "--output", "saved.md"],
    ],
)
def test_private_cli_requires_explicit_persistence_before_client_or_network(
    arguments, monkeypatch, capsys
):
    monkeypatch.setattr(
        client_module,
        "PolymathClient",
        lambda *args, **kwargs: pytest.fail(
            "Private CLI without an owner jar constructed a client"
        ),
    )
    assert client_module.main(arguments) == 2
    output = capsys.readouterr()
    assert json.loads(output.err) == {"error": "cookie_jar_required", "status": None}
    assert not output.out


@pytest.mark.parametrize(
    "arguments",
    [
        {"mode": "ask", "goal": ""},
        {"mode": "explore", "goal": "x" * 4001},
        {"mode": "surprise", "goal": "\ud800"},
        {"mode": "invalid", "goal": "Synthetic"},
        {"mode": "ask", "goal": "Synthetic", "parent_result_ids": [RUN, RUN]},
    ],
)
def test_invalid_creation_input_never_reaches_session_or_write(arguments, monkeypatch):
    client = client_module.PolymathClient(ORIGIN)
    monkeypatch.setattr(
        client,
        "_scientific_write",
        lambda *args, **kwargs: pytest.fail("invalid create reached write"),
    )
    with pytest.raises(client_module.ClientError):
        client.create_run(**arguments)
