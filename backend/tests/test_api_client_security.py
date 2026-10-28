"""Independent adversarial SDK boundaries; no native HTTP or model requests."""

import importlib.util
import json
import os
import sys
import threading
import time
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "polymath-client.py"


@pytest.fixture(scope="module")
def client_module():
    spec = importlib.util.spec_from_file_location("polymath_external_security_fixture", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:0",
        "https://example.org:0",
        "http://[::1]:0",
        "http://[v1.example]",
        "https://[v1.example]",
        "http://127.0.0.1:",
        "https://example.org?",
        "https://example.org#",
        "\nhttps://example.org",
        "https://example.org\t",
        "https://user:PRIVATE_PASSWORD@example.org",
        "https://example.org%40untrusted.invalid",
        "https://example.org\\@untrusted.invalid",
        "http://untrusted.invalid",
        "http://192.168.1.2",
    ],
)
def test_ambiguous_authorities_cannot_change_cookie_origin_or_allow_cleartext_remote_access(
    client_module, origin
):
    with pytest.raises(client_module.ClientError, match="^invalid_base_url$"):
        client_module.canonical_origin(origin)


def cookie_file(path, *, origins=("http://127.0.0.1:8000",)):
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        + "".join(f"# Polymath origin: {origin}\n" for origin in origins)
        + "#HttpOnly_127.0.0.1\tFALSE\t/api/v1\tFALSE\t"
        + str(int(time.time()) + 3600)
        + "\tpolymath_session\t"
        + "A" * 43
        + "\n",
        encoding="ascii",
    )
    path.chmod(0o600)


def test_multiple_origin_tags_never_authorize_one_cookie_at_two_ports(client_module, tmp_path):
    path = tmp_path / "owner.cookies"
    origins = ("http://127.0.0.1:8000", "http://127.0.0.1:9000")
    cookie_file(path, origins=origins)
    for origin in origins:
        with client_module.PolymathClient(origin, cookie_jar=path) as client:
            with pytest.raises(client_module.ClientError, match="^invalid_cookie_jar$"):
                client._load_owner()
            assert client._token() is None


def test_cookie_leaf_swapped_to_symlink_is_rejected_without_reading_target(
    client_module, tmp_path, monkeypatch
):
    path, victim = tmp_path / "owner.cookies", tmp_path / "victim"
    cookie_file(path)
    victim.write_bytes(b"PRIVATE-VICTIM-CONTENT")
    victim.chmod(0o600)
    original, swapped = os.open, False

    def swap_before_open(name, flags, *args, **kwargs):
        nonlocal swapped
        if os.fspath(name) == path.name and not swapped:
            swapped = True
            path.unlink()
            path.symlink_to(victim)
        return original(name, flags, *args, **kwargs)

    monkeypatch.setattr(client_module.os, "open", swap_before_open)
    with pytest.raises(client_module.ClientError) as error:
        client_module._read_private(path)
    assert swapped and victim.read_bytes() == b"PRIVATE-VICTIM-CONTENT"
    assert "PRIVATE-VICTIM-CONTENT" not in str(error.value)


def test_swapped_ancestor_cannot_redirect_verified_export(client_module, tmp_path, monkeypatch):
    middle = tmp_path / "middle"
    leaf = middle / "leaf"
    leaf.mkdir(parents=True)
    redirected = tmp_path / "redirected"
    (redirected / "leaf").mkdir(parents=True)
    moved = tmp_path / "original-middle"
    destination = leaf / "artifact.md"
    original, swapped = os.open, False

    def swap_before_final_directory(name, flags, *args, **kwargs):
        nonlocal swapped
        if os.fspath(name) in (str(leaf), "leaf") and not swapped:
            swapped = True
            middle.rename(moved)
            middle.symlink_to(redirected, target_is_directory=True)
        return original(name, flags, *args, **kwargs)

    monkeypatch.setattr(client_module.os, "open", swap_before_final_directory)
    try:
        client_module._atomic_private(destination, b"verified synthetic bytes", allow_replace=False)
    except client_module.ClientError:
        pass  # Failing closed is also valid; following the new ancestor is not.
    assert swapped
    assert not (redirected / "leaf" / "artifact.md").exists()
    preserved = moved / "leaf" / "artifact.md"
    if preserved.exists():
        assert preserved.read_bytes() == b"verified synthetic bytes"
        assert preserved.stat().st_mode & 0o777 == 0o600


def test_export_create_race_preserves_a_competing_file_and_cleans_temporary_bytes(
    client_module, tmp_path, monkeypatch
):
    destination = tmp_path / "artifact.md"
    original_link, original_replace = os.link, os.replace
    competed = False

    def competing_creation(name):
        nonlocal competed
        if os.fspath(name) == destination.name and not competed:
            competed = True
            destination.write_bytes(b"CONCURRENT-FILE-MUST-SURVIVE")
            destination.chmod(0o600)

    def link(source, target, *args, **kwargs):
        competing_creation(target)
        return original_link(source, target, *args, **kwargs)

    def replace(source, target, *args, **kwargs):
        competing_creation(target)
        return original_replace(source, target, *args, **kwargs)

    monkeypatch.setattr(client_module.os, "link", link)
    monkeypatch.setattr(client_module.os, "replace", replace)
    with pytest.raises(client_module.ClientError):
        client_module._atomic_private(destination, b"new verified bytes", allow_replace=False)
    assert competed and destination.read_bytes() == b"CONCURRENT-FILE-MUST-SURVIVE"
    assert not list(tmp_path.glob(".polymath-*.tmp"))


@pytest.mark.parametrize("depth", [70, 7000])
def test_deep_json_is_rejected_with_a_static_sdk_error(client_module, monkeypatch, depth):
    headers = Message()
    headers["Content-Type"] = "application/json"
    raw = b'{"value":' + b"[" * depth + b'"PRIVATE-PROVIDER-BODY"' + b"]" * depth + b"}"
    client = client_module.PolymathClient("http://127.0.0.1:8000")
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: (raw, headers))
    with pytest.raises(client_module.ClientError) as error:
        client.public_library()
    assert "PRIVATE-PROVIDER-BODY" not in str(error.value)


class ChunkResponse:
    status = 200

    def __init__(self, chunks):
        self.chunks = iter(chunks)
        self.headers = Message()
        self.headers["Content-Type"] = "text/event-stream"

    def read1(self, maximum):
        return next(self.chunks, b"")


def stream_client(module, monkeypatch, chunks):
    client = module.PolymathClient("http://127.0.0.1:8000")
    monkeypatch.setattr(client, "_load_owner", lambda: None)
    monkeypatch.setattr(client, "_token", lambda: "A" * 43)
    response = ChunkResponse(chunks)
    monkeypatch.setattr(
        client,
        "_start",
        lambda *args, **kwargs: (
            None,
            response,
            None,
            time.monotonic() + 5,
        ),
    )
    monkeypatch.setattr(client, "_finish", lambda *args: None)
    return client


def test_crlf_and_unicode_stream_frames_survive_every_possible_chunk_boundary(
    client_module, monkeypatch
):
    data = json.dumps(
        {
            "sequence": 1,
            "kind": "source_read",
            "summary": "🌱 Synthetic evidence",
            "artifact_refs": [],
        },
        ensure_ascii=False,
    )
    frame = (
        f"id: 1\r\nevent: activity\r\ndata: {data}\r\n\r\nevent: end\r\ndata: {{}}\r\n\r\n".encode()
    )
    for position in range(1, len(frame)):
        client = stream_client(client_module, monkeypatch, (frame[:position], frame[position:]))
        events = list(client.events("00000000-0000-4000-8000-000000000001"))
        assert [event["event"] for event in events] == ["activity", "end"], position
        assert events[0]["id"] == 1 and events[0]["data"]["summary"] == "🌱 Synthetic evidence"
        assert events[1]["id"] is None


@pytest.mark.parametrize(
    "frame",
    [
        b'id: 2\nevent: activity\ndata: {"sequence":1}\n\n',
        b'id: 1\nevent: activity\ndata: {"sequence":1}\n\n',
        b"id: 2\nevent: heartbeat\ndata: {}\n\n",
        b"id: 2\nevent: end\ndata: {}\n\n",
    ],
)
def test_nonmonotonic_or_nonactivity_cursors_fail_closed(client_module, monkeypatch, frame):
    client = stream_client(client_module, monkeypatch, (frame,))
    with pytest.raises(client_module.ClientError, match="^invalid_event_stream$"):
        list(client.events("00000000-0000-4000-8000-000000000001", after=1))


def test_closing_a_stalled_stream_unblocks_reads_even_with_detached_connection_socket(
    client_module, monkeypatch
):
    release, first_event, second_read, close_finished = (threading.Event() for _ in range(4))

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b'id: 1\nevent: activity\ndata: {"sequence":1}\n\n')
            self.wfile.flush()
            release.wait(timeout=5)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serving = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    serving.start()
    client = client_module.PolymathClient(f"http://127.0.0.1:{server.server_port}", timeout=3)
    monkeypatch.setattr(client, "_load_owner", lambda: None)
    monkeypatch.setattr(client, "_token", lambda: "A" * 43)
    start, reads = client._start, 0
    connections, failures = [], []

    def observe_start(*args, **kwargs):
        connection, response, timer, deadline = start(*args, **kwargs)
        connections.append(connection)
        read = response.read1

        def observed_read(maximum):
            nonlocal reads
            reads += 1
            if reads == 2:
                second_read.set()
            return read(maximum)

        response.read1 = observed_read
        return connection, response, timer, deadline

    monkeypatch.setattr(client, "_start", observe_start)

    def receive():
        try:
            for event in client.events("00000000-0000-4000-8000-000000000001", max_seconds=3):
                assert event["id"] == 1
                first_event.set()
        except client_module.ClientError:
            pass  # Closing a live read may honestly end with a static error.
        except BaseException as error:
            failures.append(error)

    def close():
        try:
            client.close()
        except BaseException as error:
            failures.append(error)
        finally:
            close_finished.set()

    receiving = threading.Thread(target=receive, daemon=True)
    closing = threading.Thread(target=close, daemon=True)
    receiving.start()
    try:
        assert first_event.wait(timeout=2) and second_read.wait(timeout=2)
        assert connections[0].sock is None  # HTTP Connection:close detached it.
        time.sleep(0.03)  # Allow read1 to enter its otherwise indefinitely idle read.
        closing.start()
        assert close_finished.wait(timeout=1), (
            "close must shut down I/O before cancelling its timer"
        )
        receiving.join(timeout=1)
        assert not receiving.is_alive() and not failures
    finally:
        release.set()
        client.close()
        receiving.join(timeout=2)
        if closing.ident is not None:
            closing.join(timeout=2)
        server.shutdown()
        server.server_close()
        serving.join(timeout=2)


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_connect_that_exhausted_deadline_cannot_send_fresh_http_bytes(
    client_module, monkeypatch, method
):
    clock = {"now": 100.0}
    monkeypatch.setattr(client_module, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    client = client_module.PolymathClient("http://127.0.0.1:8000", timeout=0.1)

    class Connection:
        connected = closed = False
        auto_open = True
        sock = None
        requests = []

        def connect(self):
            self.connected = True
            clock["now"] += 1  # Simulate DNS/connect returning after the deadline.

        def request(self, *args, **kwargs):
            self.requests.append((args, kwargs))

        def close(self):
            self.closed = True

    connection = Connection()

    def connect(*, deadline):
        client._connections.add(connection)
        return connection

    monkeypatch.setattr(client, "_connect", connect)
    with pytest.raises(client_module.ClientError) as error:
        client._start(method, "/api/v1/runs", body=b"PRIVATE-REQUEST-BODY")
    assert connection.connected and connection.closed
    assert connection.requests == [] and not client._connections and not client._timers
    assert "PRIVATE-REQUEST-BODY" not in str(error.value)
