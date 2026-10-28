#!/usr/bin/env python3
"""Standalone standard-library client for Polymath's versioned HTTP API.

No command runs by default. Public reads never load cookies or initialize an
owner session. Model execution remains entirely subject to server configuration.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import http.client
import http.cookiejar
import ipaddress
import json
import os
import re
import secrets
import socket
import ssl
import stat
import sys
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request

COOKIE_NAME = "polymath_session"
MAX_JSON_BYTES = 20 * 1024 * 1024
MAX_EXPORT_BYTES = 262_144
MAX_COOKIE_BYTES = 16_384
MAX_EVENT_BYTES = 1024 * 1024
MAX_STREAM_BYTES = 20 * 1024 * 1024
MAX_CURSOR = 2**63 - 1
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
DIGEST = re.compile(r"[a-f0-9]{64}")
SAFE_ERRORS = {
    "research_session_required",
    "csrf_check_failed",
    "origin_check_failed",
    "research_unavailable",
    "research_run_not_found",
    "research_result_not_available",
    "research_basis_not_found",
    "invalid_research_request",
    "invalid_event_cursor",
    "active_run_limit",
    "research_queue_full",
    "research_storage_unavailable",
    "artifact_not_found",
    "artifact_source_not_found",
    "artifact_storage_unavailable",
    "sharing_not_found",
    "sharing_unavailable",
    "sharing_storage_unavailable",
    "sharing_invalid_request",
}
SECRET_FIELDS = {
    "csrf_token",
    "cookie",
    "set-cookie",
    "authorization",
    "token",
    "api_key",
    "password",
    "secret",
}


class ClientError(Exception):
    """A static code and optional HTTP status; never an underlying exception."""

    def __init__(self, code, status=None):
        super().__init__(code)
        self.code, self.status = code, status


def canonical_origin(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ClientError("invalid_base_url")
    if any(ord(character) < 33 or ord(character) == 127 for character in value) or "\\" in value:
        raise ClientError("invalid_base_url")
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
            or "%" in parsed.netloc
        ):
            raise ValueError
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if not 1 <= port <= 65535:
            raise ValueError
        if parsed.netloc.endswith(":"):
            raise ValueError
        if parsed.netloc.startswith("["):
            ipaddress.IPv6Address(host)
        if ":" not in host and (
            not re.fullmatch(r"[a-z0-9.-]+", host)
            or any(
                not part or part.startswith("-") or part.endswith("-") for part in host.split(".")
            )
        ):
            raise ValueError
        if parsed.scheme == "http" and host != "localhost":
            try:
                if not ipaddress.ip_address(host).is_loopback:
                    raise ValueError
            except ValueError:
                raise ClientError("invalid_base_url") from None
        authority = f"[{host}]" if ":" in host else host
        if port != (443 if parsed.scheme == "https" else 80):
            authority += f":{port}"
        return f"{parsed.scheme}://{authority}"
    except (ValueError, UnicodeError):
        raise ClientError("invalid_base_url") from None


def canonical_uuid(value):
    try:
        text = str(value)
        if str(uuid.UUID(text)) != text:
            raise ValueError
        return text
    except (ValueError, TypeError, AttributeError):
        raise ClientError("invalid_identifier") from None


def _integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ClientError("invalid_arguments")
    return value


def _duration(value, minimum=0.001, maximum=60):
    if type(value) not in (int, float) or not minimum <= value <= maximum:
        raise ClientError("invalid_arguments")
    return float(value)


def _query(value):
    if not isinstance(value, str) or len(value) > 200 or any(ord(char) < 32 for char in value):
        raise ClientError("invalid_arguments")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ClientError("invalid_arguments") from None
    return value


def _fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_size)


def _private_info(info):
    if (
        not stat.S_ISREG(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o600
        or not hasattr(os, "getuid")
        or info.st_uid != os.getuid()
    ):
        raise ClientError("unsafe_private_file")
    return info


def _parent(path):
    descriptor = None
    try:
        path = Path(os.path.abspath(os.path.expanduser(os.fspath(path))))
        if not path.name or any(ord(char) < 32 for char in str(path)):
            raise ClientError("unsafe_private_file")
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        descriptor = os.open(path.anchor, flags)
        for component in path.parent.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, path.name
    except (OSError, ValueError, TypeError):
        if descriptor is not None:
            os.close(descriptor)
        raise ClientError("private_file_unavailable") from None


def _target_info(directory, name):
    try:
        return _private_info(os.stat(name, dir_fd=directory, follow_symlinks=False))
    except FileNotFoundError:
        return None


def _read_private(path):
    directory, name = _parent(path)
    descriptor = None
    try:
        before = _target_info(directory, name)
        if before is None:
            return None, None
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        actual = _private_info(os.fstat(descriptor))
        if _fingerprint(actual) != _fingerprint(before) or actual.st_size > MAX_COOKIE_BYTES:
            raise ClientError("unsafe_private_file")
        data = os.read(descriptor, MAX_COOKIE_BYTES + 1)
        if (
            len(data) != actual.st_size
            or len(data) > MAX_COOKIE_BYTES
            or _fingerprint(os.fstat(descriptor)) != _fingerprint(actual)
        ):
            raise ClientError("unsafe_private_file")
        return data, _fingerprint(actual)
    except OSError:
        raise ClientError("private_file_unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def _atomic_private(path, data, *, expected="inspect", allow_replace=True):
    directory, name = _parent(path)
    temporary = None
    lock = None
    try:
        if allow_replace:
            try:
                import fcntl
            except ImportError:
                raise ClientError("protected_files_unsupported") from None
            lock = os.open(
                "." + name + ".lock",
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                0o600,
                dir_fd=directory,
            )
            _private_info(os.fstat(lock))
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ClientError("private_file_busy") from None
        current = _target_info(directory, name)
        fingerprint = _fingerprint(current) if current else None
        if current is not None and not allow_replace:
            raise ClientError("output_exists")
        if expected != "inspect" and fingerprint != expected:
            raise ClientError("private_file_changed")
        temporary = ".polymath-" + secrets.token_hex(16) + ".tmp"
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        latest = _target_info(directory, name)
        if (_fingerprint(latest) if latest else None) != fingerprint:
            raise ClientError("private_file_changed")
        if allow_replace:
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            temporary = None
        else:
            try:
                os.link(
                    temporary,
                    name,
                    src_dir_fd=directory,
                    dst_dir_fd=directory,
                    follow_symlinks=False,
                )
            except FileExistsError:
                raise ClientError("output_exists") from None
            os.unlink(temporary, dir_fd=directory)
            temporary = None
        os.fsync(directory)
        return _fingerprint(_private_info(os.stat(name, dir_fd=directory, follow_symlinks=False)))
    except OSError:
        raise ClientError("private_file_unavailable") from None
    finally:
        if temporary:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass
        if lock is not None:
            os.close(lock)
        os.close(directory)


class PolymathClient:
    def __init__(self, base_url, *, cookie_jar=None, timeout=20.0):
        self.origin = canonical_origin(base_url)
        self.base_url = self.origin
        self.timeout = _duration(timeout)
        self.cookie_jar = cookie_jar
        self._jar = http.cookiejar.CookieJar()
        self._loaded, self._ever_owner = False, False
        self._file_fingerprint = None
        self._csrf = None
        self._secrets = set()
        self._connections = set()
        self._timers = {}
        self._responses = {}
        self._stops = {}
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._closed = True
        for stop in tuple(self._stops.values()):
            stop()
        for timer in tuple(self._timers.values()):
            timer.cancel()
        for response in tuple(self._responses.values()):
            response.close()
        for connection in tuple(self._connections):
            connection.close()
        self._connections.clear()
        self._timers.clear()
        self._responses.clear()
        self._stops.clear()

    def _cookie_domain(self):
        host = urlsplit(self.origin).hostname
        return host + ".local" if "." not in host and ":" not in host else host

    def _load_owner(self):
        if self._loaded:
            return
        if self.cookie_jar is not None:
            data, self._file_fingerprint = _read_private(self.cookie_jar)
            if data is not None:
                try:
                    lines = data.decode("ascii").splitlines()
                    if (
                        not lines
                        or lines[0] != "# Netscape HTTP Cookie File"
                        or len([line for line in lines if line.startswith("# Polymath origin:")])
                        != 1
                        or lines.count("# Polymath origin: " + self.origin) != 1
                    ):
                        raise ValueError
                    cookies = [
                        line
                        for line in lines[1:]
                        if line and (not line.startswith("#") or line.startswith("#HttpOnly_"))
                    ]
                    if len(cookies) != 1:
                        raise ValueError
                    domain, subdomains, path, secure, expires, name, value = (
                        cookies[0].removeprefix("#HttpOnly_").split("\t")
                    )
                    if (
                        domain != self._cookie_domain()
                        or subdomains != "FALSE"
                        or path != "/api/v1"
                        or secure not in ("TRUE", "FALSE")
                        or name != COOKIE_NAME
                        or TOKEN.fullmatch(value) is None
                        or not expires.isascii()
                        or not expires.isdecimal()
                        or len(expires) > 12
                        or (self.origin.startswith("https:") and secure != "TRUE")
                    ):
                        raise ValueError
                    self._jar.set_cookie(
                        http.cookiejar.Cookie(
                            0,
                            name,
                            value,
                            None,
                            False,
                            domain,
                            False,
                            False,
                            path,
                            True,
                            secure == "TRUE",
                            int(expires),
                            False,
                            None,
                            None,
                            {"HttpOnly": None},
                            False,
                        )
                    )
                    self._secrets.add(value)
                    self._ever_owner = True
                except (ValueError, UnicodeError):
                    raise ClientError("invalid_cookie_jar") from None
        self._loaded = True

    def _token(self):
        valid = [
            cookie
            for cookie in self._jar
            if cookie.name == COOKIE_NAME
            and cookie.expires is not None
            and cookie.expires > time.time()
            and (not cookie.secure or self.origin.startswith("https:"))
        ]
        if len(valid) > 1:
            raise ClientError("invalid_cookie_jar")
        return valid[0].value if valid else None

    def _persist_owner(self):
        if self.cookie_jar is None:
            return
        cookie = next((cookie for cookie in self._jar if cookie.name == COOKIE_NAME), None)
        if cookie is None or cookie.expires is None:
            raise ClientError("invalid_session_response")
        data = (
            "# Netscape HTTP Cookie File\n# Polymath origin: "
            + self.origin
            + "\n"
            + "#HttpOnly_"
            + cookie.domain
            + "\tFALSE\t/api/v1\t"
            + ("TRUE" if cookie.secure else "FALSE")
            + "\t"
            + str(cookie.expires)
            + "\t"
            + COOKIE_NAME
            + "\t"
            + cookie.value
            + "\n"
        ).encode("ascii")
        self._file_fingerprint = _atomic_private(
            self.cookie_jar, data, expected=self._file_fingerprint
        )

    def _redact(self, value, depth=0):
        if depth > 64:
            raise ClientError("unexpected_response")
        if isinstance(value, dict):
            return {
                self._redact(key, depth + 1): "[redacted]"
                if key.lower() in SECRET_FIELDS
                else self._redact(item, depth + 1)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._redact(item, depth + 1) for item in value]
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, "[redacted]")
        return value

    def _connect(self, *, deadline):
        parsed = urlsplit(self.origin)
        connection_class = (
            http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        )
        kwargs = {"timeout": min(self.timeout, max(0.001, deadline - time.monotonic()))}
        if parsed.scheme == "https":
            kwargs["context"] = ssl.create_default_context()
        connection = connection_class(parsed.hostname, parsed.port, **kwargs)
        self._connections.add(connection)
        return connection

    def _start(self, method, path, *, private=False, body=None, headers=None, duration=None):
        if self._closed:
            raise ClientError("client_closed")
        if not path.startswith("/api/v1/") or any(ord(char) < 32 for char in path):
            raise ClientError("invalid_endpoint")
        request_headers = {"Accept": "application/json", **(headers or {})}
        if private:
            self._load_owner()
            token = self._token()
            if token is not None:
                request_headers["Cookie"] = COOKIE_NAME + "=" + token
        deadline = time.monotonic() + (self.timeout if duration is None else duration)
        connection = self._connect(deadline=deadline)
        timer = None
        try:
            connection.connect()
            connection.auto_open = False
            if self._closed:
                raise ClientError("client_closed")
            if time.monotonic() >= deadline:
                raise ClientError("request_timeout")
            active_socket = connection.sock

            def stop():
                try:
                    active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            timer = threading.Timer(max(0.001, deadline - time.monotonic()), stop)
            timer.daemon = True
            self._stops[connection] = stop
            timer.start()
            self._timers[connection] = timer
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            self._responses[connection] = response
            return connection, response, timer, deadline
        except Exception as error:
            if timer:
                timer.cancel()
            connection.close()
            self._connections.discard(connection)
            self._timers.pop(connection, None)
            self._stops.pop(connection, None)
            if isinstance(error, ClientError):
                raise
            raise ClientError(
                "write_outcome_unknown" if method == "POST" else "request_failed"
            ) from None

    def _finish(self, connection, response, timer):
        timer.cancel()
        response.close()
        connection.close()
        self._connections.discard(connection)
        self._timers.pop(connection, None)
        self._responses.pop(connection, None)
        self._stops.pop(connection, None)

    def _request(
        self,
        method,
        path,
        *,
        private=False,
        body=None,
        headers=None,
        maximum=MAX_JSON_BYTES,
    ):
        connection, response, timer, deadline = self._start(
            method, path, private=private, body=body, headers=headers
        )
        try:
            if 300 <= response.status < 400:
                raise ClientError("unexpected_redirect", response.status)
            limit = min(maximum, 65_536) if response.status >= 400 else maximum
            chunks, length = [], 0
            while True:
                chunk = response.read1(min(65_536, limit - length + 1))
                if not chunk:
                    break
                length += len(chunk)
                if length > limit:
                    raise ClientError(
                        "write_outcome_unknown" if method == "POST" else "response_too_large",
                        response.status,
                    )
                chunks.append(chunk)
                if time.monotonic() >= deadline:
                    raise ClientError(
                        "write_outcome_unknown" if method == "POST" else "request_timeout"
                    )
            raw = b"".join(chunks)
            if response.status >= 400:
                try:
                    error = json.loads(raw.decode("utf-8")).get("detail")
                except (ValueError, UnicodeError, AttributeError):
                    error = None
                code = (
                    error
                    if isinstance(error, str) and error in SAFE_ERRORS
                    else ("write_outcome_unknown" if method == "POST" else "http_error")
                )
                if method == "POST" and code == "research_storage_unavailable":
                    code = "write_outcome_unknown"
                raise ClientError(code, response.status)
            if not 200 <= response.status < 300:
                raise ClientError("unexpected_response", response.status)
            if response.length not in (None, 0):
                raise ClientError(
                    "write_outcome_unknown" if method == "POST" else "incomplete_response"
                )
            return raw, response.headers
        except ClientError:
            raise
        except Exception:
            raise ClientError(
                "write_outcome_unknown" if method == "POST" else "request_failed"
            ) from None
        finally:
            self._finish(connection, response, timer)

    def _json(self, method, path, *, private=False, body=None, headers=None):
        encoded = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        if encoded is not None:
            headers = {"Content-Type": "application/json", **(headers or {})}
        raw, response_headers = self._request(
            method, path, private=private, body=encoded, headers=headers
        )
        try:
            if response_headers.get_content_type() != "application/json":
                raise ValueError
            result = json.loads(raw.decode("utf-8"))
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (ValueError, UnicodeError, RecursionError):
            raise ClientError(
                "write_outcome_unknown" if method == "POST" else "unexpected_response"
            ) from None

    def _session(self, *, explicit=False, allow_new=False):
        self._load_owner()
        previous = self._token()
        if previous is None and not explicit and (self._ever_owner or not allow_new):
            raise ClientError("research_session_required", 401)
        raw, headers = self._request("GET", "/api/v1/session", private=True, maximum=4096)
        try:
            data = json.loads(raw.decode("utf-8"))
            csrf = data["csrf_token"]
            expires = data["expires_at"]
            expiry = datetime.fromisoformat(expires)
            if not isinstance(csrf, str) or DIGEST.fullmatch(csrf) is None or expiry.tzinfo is None:
                raise ValueError
            candidate = http.cookiejar.CookieJar()
            for cookie in self._jar:
                candidate.set_cookie(copy.copy(cookie))

            class CookieResponse:
                def info(self):
                    return headers

            candidate.extract_cookies(CookieResponse(), Request(self.origin + "/api/v1/session"))
            cookies = list(candidate)
            if len(cookies) != 1:
                raise ValueError
            cookie = cookies[0]
            if (
                cookie.name != COOKIE_NAME
                or TOKEN.fullmatch(cookie.value) is None
                or cookie.domain != self._cookie_domain()
                or cookie.domain_specified
                or cookie.path != "/api/v1"
                or cookie.expires is None
                or cookie.expires <= time.time()
                or (self.origin.startswith("https:") and not cookie.secure)
            ):
                raise ValueError
            self._secrets.update((cookie.value, csrf))
            if previous is not None and previous != cookie.value and not explicit:
                raise ClientError("owner_session_changed")
        except (ValueError, UnicodeError, KeyError, TypeError, AttributeError, RecursionError):
            raise ClientError("invalid_session_response") from None
        self._jar, self._csrf, self._ever_owner = candidate, csrf, True
        self._persist_owner()
        return {"expires_at": expires}

    def init_session(self):
        return self._session(explicit=True)

    def _private_json(self, path):
        self._load_owner()
        if self._token() is None:
            raise ClientError("research_session_required", 401)
        return self._redact(self._json("GET", path, private=True))

    def _write(self, path, body=None, *, allow_new=False):
        self._session(allow_new=allow_new)
        return self._redact(
            self._json(
                "POST",
                path,
                private=True,
                body=body,
                headers={"X-CSRF-Token": self._csrf, "Origin": self.origin},
            )
        )

    def public_library(self, query="", limit=20, before=None):
        params = {"q": _query(query), "limit": _integer(limit, 1, 20)}
        if before is not None:
            if not isinstance(before, str) or TOKEN.fullmatch(before) is None:
                raise ClientError("invalid_identifier")
            params["before"] = before
        return self._redact(self._json("GET", "/api/v1/public/library?" + urlencode(params)))

    def public_map(self, query=""):
        return self._redact(
            self._json("GET", "/api/v1/public/map?" + urlencode({"q": _query(query)}))
        )

    def public_finding(self, public_id):
        if not isinstance(public_id, str) or TOKEN.fullmatch(public_id) is None:
            raise ClientError("invalid_identifier")
        return self._redact(self._json("GET", "/api/v1/public/findings/" + public_id))

    def list_runs(self, limit=20, before=None):
        params = {"limit": _integer(limit, 1, 50)}
        if before is not None:
            params["before"] = canonical_uuid(before)
        return self._private_json("/api/v1/runs?" + urlencode(params))

    def get_run(self, run_id):
        return self._private_json("/api/v1/runs/" + canonical_uuid(run_id))

    def get_result(self, run_id):
        return self._private_json("/api/v1/runs/" + canonical_uuid(run_id) + "/result")

    def create_run(self, mode, goal="", parent_result_ids=()):
        if (
            mode not in ("explore", "ask", "surprise")
            or not isinstance(goal, str)
            or len(goal) > 4000
            or any(ord(char) < 32 and char not in "\n\r\t" for char in goal)
            or (mode != "surprise" and not goal.strip())
            or not isinstance(parent_result_ids, (tuple, list))
            or len(parent_result_ids) > 4
        ):
            raise ClientError("invalid_arguments")
        try:
            goal.encode("utf-8")
        except UnicodeError:
            raise ClientError("invalid_arguments") from None
        parents = [canonical_uuid(value) for value in parent_result_ids]
        if len(set(parents)) != len(parents) or (
            parents and (mode != "explore" or len(parents) < 2)
        ):
            raise ClientError("invalid_arguments")
        return self._write(
            "/api/v1/runs",
            {"mode": mode, "goal": goal, "parent_result_ids": parents},
            allow_new=True,
        )

    def cancel_run(self, run_id):
        return self._write("/api/v1/runs/" + canonical_uuid(run_id) + "/cancel")

    def get_artifact(self, artifact_id):
        return self._private_json("/api/v1/artifacts/" + canonical_uuid(artifact_id))

    def export_artifact(self, artifact_id, output):
        identifier = canonical_uuid(artifact_id)
        metadata = self.get_artifact(identifier)
        digest = metadata.get("markdown_sha256")
        if (
            metadata.get("id") != identifier
            or not isinstance(digest, str)
            or DIGEST.fullmatch(digest) is None
        ):
            raise ClientError("unexpected_response")
        raw, headers = self._request(
            "GET",
            "/api/v1/artifacts/" + identifier + "/export",
            private=True,
            maximum=MAX_EXPORT_BYTES,
        )
        if (
            headers.get_content_type() != "text/markdown"
            or hashlib.sha256(raw).hexdigest() != digest
        ):
            raise ClientError("export_integrity_failed")
        if "markdown" in metadata and (
            not isinstance(metadata["markdown"], str) or metadata["markdown"].encode("utf-8") != raw
        ):
            raise ClientError("export_integrity_failed")
        if str(output) == "-":
            if any(secret.encode() in raw for secret in self._secrets):
                raise ClientError("sensitive_export_content")
            sys.stdout.buffer.write(raw)
        else:
            _atomic_private(output, raw, allow_replace=False)
        return {"markdown_sha256": digest, "byte_count": len(raw)}

    def events(self, run_id, *, after=0, last_event_id=None, max_events=100, max_seconds=30):
        run_id = canonical_uuid(run_id)
        after = _integer(after, 0, MAX_CURSOR)
        if last_event_id is not None:
            after = max(after, _integer(last_event_id, 0, MAX_CURSOR))
        max_events = _integer(max_events, 1, 1000)
        max_seconds = _duration(max_seconds)
        self._load_owner()
        if self._token() is None:
            raise ClientError("research_session_required", 401)
        connection, response, timer, deadline = self._start(
            "GET",
            f"/api/v1/runs/{run_id}/events?" + urlencode({"after": after}),
            private=True,
            headers={"Accept": "text/event-stream", "Last-Event-ID": str(after)},
            duration=max_seconds,
        )
        cursor, count, total = after, 0, 0
        pending = bytearray()
        try:
            if 300 <= response.status < 400:
                raise ClientError("unexpected_redirect", response.status)
            if response.status != 200 or response.headers.get_content_type() != "text/event-stream":
                raise ClientError("event_stream_unavailable", response.status)
            while count < max_events and time.monotonic() < deadline:
                chunk = response.read1(4096)
                if not chunk:
                    if time.monotonic() >= deadline:
                        return
                    if pending.strip():
                        raise ClientError("invalid_event_stream")
                    return
                total += len(chunk)
                if total > MAX_STREAM_BYTES:
                    raise ClientError("event_stream_limit")
                pending.extend(chunk)
                pending = bytearray(pending.replace(b"\r\n", b"\n"))
                while b"\n\n" in pending:
                    frame, _, rest = pending.partition(b"\n\n")
                    pending = bytearray(rest)
                    if len(frame) > MAX_EVENT_BYTES:
                        raise ClientError("event_stream_limit")
                    event = self._event(frame, cursor)
                    if event is None:
                        continue
                    if event["event"] == "activity":
                        cursor = event["id"]
                    count += 1
                    yield self._redact(event)
                    if event["event"] in ("end", "error", "session_expired") or count >= max_events:
                        return
                if len(pending) > MAX_EVENT_BYTES:
                    raise ClientError("event_stream_limit")
        except ClientError:
            raise
        except Exception:
            if time.monotonic() >= deadline:
                return
            raise ClientError("event_stream_failed") from None
        finally:
            self._finish(connection, response, timer)

    def _event(self, frame, cursor):
        try:
            text = frame.decode("utf-8")
            event_type, identifier, data = None, None, []
            for line in text.split("\n"):
                if not line or line.startswith(":"):
                    continue
                name, separator, value = line.partition(":")
                if not separator:
                    raise ValueError
                value = value.removeprefix(" ")
                if name == "event":
                    if event_type is not None:
                        raise ValueError
                    event_type = value
                elif name == "id":
                    if identifier is not None or re.fullmatch(r"[0-9]{1,19}", value) is None:
                        raise ValueError
                    identifier = _integer(int(value), 0, MAX_CURSOR)
                elif name == "data":
                    data.append(value)
                elif name != "retry":
                    raise ValueError
            if event_type is None and not data:
                return None
            if event_type not in (
                "activity",
                "heartbeat",
                "end",
                "error",
                "session_expired",
            ):
                raise ValueError
            payload = json.loads("\n".join(data))
            if not isinstance(payload, dict):
                raise ValueError
            if event_type == "activity":
                sequence = payload.get("sequence")
                if type(sequence) is not int or identifier != sequence or sequence <= cursor:
                    raise ValueError
            elif identifier is not None:
                raise ValueError
            if event_type == "error":
                payload = {
                    "code": payload.get("code")
                    if payload.get("code") in SAFE_ERRORS
                    else "event_stream_failed"
                }
            return {"id": identifier, "event": event_type, "data": payload}
        except (ValueError, UnicodeError, ClientError, RecursionError):
            raise ClientError("invalid_event_stream") from None


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ClientError("invalid_arguments")


def parser():
    result = _Parser(description=__doc__)
    result.add_argument("--base-url", default="http://127.0.0.1:8000")
    result.add_argument("--cookie-jar")
    result.add_argument("--timeout", type=float, default=20)
    commands = result.add_subparsers(dest="command")
    public = commands.add_parser("public").add_subparsers(dest="action", required=True)
    library = public.add_parser("library")
    library.add_argument("--query", default="")
    library.add_argument("--limit", type=int, default=20)
    library.add_argument("--before")
    public.add_parser("map").add_argument("--query", default="")
    public.add_parser("finding").add_argument("public_id")
    commands.add_parser("session").add_subparsers(dest="action", required=True).add_parser("init")
    runs = commands.add_parser("runs").add_subparsers(dest="action", required=True)
    listing = runs.add_parser("list")
    listing.add_argument("--limit", type=int, default=20)
    listing.add_argument("--before")
    for action in ("show", "result", "cancel"):
        runs.add_parser(action).add_argument("run_id")
    events = runs.add_parser("events")
    events.add_argument("run_id")
    events.add_argument("--after", type=int, default=0)
    events.add_argument("--last-event-id", type=int)
    events.add_argument("--max-events", type=int, default=100)
    events.add_argument("--max-seconds", type=float, default=30)
    create = runs.add_parser("create")
    create.add_argument("--mode", choices=("explore", "ask", "surprise"), required=True)
    goal = create.add_mutually_exclusive_group()
    goal.add_argument("--goal", default="")
    goal.add_argument("--goal-file")
    create.add_argument("--parent-result", action="append", default=[])
    artifacts = commands.add_parser("artifacts").add_subparsers(dest="action", required=True)
    artifacts.add_parser("show").add_argument("artifact_id")
    export = artifacts.add_parser("export")
    export.add_argument("artifact_id")
    export.add_argument("--output", required=True)
    return result


def main(argv=None):
    try:
        arguments = parser()
        args = arguments.parse_args(argv)
        if args.command is None:
            arguments.print_help()
            return 0
        if args.command != "public" and not args.cookie_jar:
            raise ClientError("cookie_jar_required")
        with PolymathClient(
            args.base_url, cookie_jar=args.cookie_jar, timeout=args.timeout
        ) as client:
            if args.command == "public":
                if args.action == "library":
                    value = client.public_library(args.query, args.limit, args.before)
                elif args.action == "map":
                    value = client.public_map(args.query)
                else:
                    value = client.public_finding(args.public_id)
            elif args.command == "session":
                value = client.init_session()
            elif args.command == "artifacts":
                value = (
                    client.get_artifact(args.artifact_id)
                    if args.action == "show"
                    else client.export_artifact(args.artifact_id, args.output)
                )
                if args.action == "export" and args.output == "-":
                    return 0
            elif args.action == "list":
                value = client.list_runs(args.limit, args.before)
            elif args.action == "show":
                value = client.get_run(args.run_id)
            elif args.action == "result":
                value = client.get_result(args.run_id)
            elif args.action == "cancel":
                value = client.cancel_run(args.run_id)
            elif args.action == "events":
                for event in client.events(
                    args.run_id,
                    after=args.after,
                    last_event_id=args.last_event_id,
                    max_events=args.max_events,
                    max_seconds=args.max_seconds,
                ):
                    print(json.dumps(event, ensure_ascii=False))
                return 0
            else:
                goal = args.goal
                if args.goal_file:
                    try:
                        if args.goal_file == "-":
                            raw = sys.stdin.buffer.read(16001)
                        else:
                            with open(args.goal_file, "rb") as source:
                                raw = source.read(16001)
                        if len(raw) > 16000:
                            raise ValueError
                        goal = raw.decode("utf-8")
                    except (OSError, ValueError):
                        raise ClientError("invalid_goal_file") from None
                value = client.create_run(args.mode, goal, args.parent_result)
            print(json.dumps(value, ensure_ascii=False, indent=2))
            return 0
    except ClientError as error:
        print(json.dumps({"error": error.code, "status": error.status}), file=sys.stderr)
        return 2
    except (KeyboardInterrupt, BrokenPipeError):
        return 130
    except Exception:
        print('{"error":"client_failed","status":null}', file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
