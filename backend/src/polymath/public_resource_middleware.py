"""Pre-parse public request limits and expiring SSE slots; no research grants."""

from __future__ import annotations

import asyncio
import re

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from polymath.public_resource_controls import PublicResourceControls, PublicResourceError

HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow",
}
BODY_TIMEOUT_SECONDS = 10.0
ADMIN_BODY_MAX_BYTES = 262_144
EVENTS = re.compile(r"^/api/v1/(?:runs/[^/]+/events|publication-jobs/[^/]+/events)$")


def _operation(method: str, path: str, has_owner: bool) -> tuple[str | None, bool, bool]:
    if method in ("GET", "HEAD") and path.endswith("/capabilities"):
        return None, False, False
    if not path.startswith("/api/v1/") or path in (
        "/api/v1/health",
        "/api/v1/readiness",
        "/api/v1/capabilities",
    ):
        return None, False, False
    if method == "GET" and path == "/api/v1/session":
        return ("read" if has_owner else "mint"), False, False
    if method == "POST":
        if path.endswith(("/cancel", "/pause", "/revoke", "/disable")):
            return "control", True, False
        if path.startswith("/api/v1/runs") or path in (
            "/api/v1/artifacts/jobs",
            "/api/v1/assessments/jobs",
        ):
            return "submit", True, False
        if path.startswith("/api/v1/knowledge/watches"):
            return "free_write", True, False
        # Budget and publication manifests have separate, larger bounds. An
        # anonymous visitor cannot acquire those operator capabilities here.
        if path.startswith(("/api/v1/budget/", "/api/v1/operator/", "/api/v1/publications")):
            return "read", True, False
        return "free_write", True, False
    if method in ("GET", "HEAD"):
        return "read", False, method == "GET" and EVENTS.fullmatch(path) is not None
    return "free_write", True, False


class PublicResourceMiddleware:
    def __init__(self, app: ASGIApp, *, controls: PublicResourceControls):
        self.app, self.controls = app, controls

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.controls.policy.enabled:
            await self.app(scope, receive, send)
            return
        path, method = scope.get("path", ""), scope.get("method", "")
        provisional = _operation(method, path, False)
        if provisional[0] is None:
            await self.app(scope, receive, send)
            return
        headers = {name.lower(): value for name, value in scope.get("headers", [])}
        peer = scope.get("client")
        peer = peer[0] if isinstance(peer, (tuple, list)) and peer else None
        cookie = headers.get(b"cookie") if not path.startswith("/api/v1/public/") else None
        identity = None
        slot = None
        started = False
        finished = False
        try:
            identity = await asyncio.to_thread(
                self.controls.identify, peer, cookie.decode("latin-1") if cookie else None
            )
            operation, bounded_body, events = _operation(
                method, path, identity.owner_hash is not None
            )
            scope.setdefault("state", {})["public_resource_identity"] = identity
            await asyncio.to_thread(self.controls.consume, operation, identity)
            if bounded_body:
                body = await self._body(scope, receive, headers, control=operation == "control")
                if body is None:
                    return
                initial = True
                original_receive = receive

                async def replay_receive():
                    nonlocal initial
                    if initial:
                        initial = False
                        return {"type": "http.request", "body": body, "more_body": False}
                    return await original_receive()

                receive = replay_receive
            if not events:
                await self.app(scope, receive, send)
                return
            slot = await asyncio.to_thread(self.controls.acquire_stream, identity)

            async def tracked_send(message: Message):
                nonlocal started, finished
                if message["type"] == "http.response.start":
                    started = True
                elif message["type"] == "http.response.body" and not message.get(
                    "more_body", False
                ):
                    finished = True
                await send(message)

            try:
                async with asyncio.timeout(slot.remaining_seconds):
                    await self.app(scope, receive, tracked_send)
            except TimeoutError:
                # Close the connection without inventing an SSE terminal event
                # or changing the saved event cursor. Reconnection is explicit.
                if started and not finished:
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                elif not started:
                    raise PublicResourceError("public_stream_expired", 429, 1) from None
        except PublicResourceError as error:
            if not started:
                extra = dict(HEADERS)
                if error.retry_after_seconds is not None:
                    extra["Retry-After"] = str(error.retry_after_seconds)
                await JSONResponse({"detail": error.code}, status_code=error.status, headers=extra)(
                    scope, receive, send
                )
        finally:
            if slot is not None:
                await asyncio.shield(asyncio.to_thread(self.controls.release_stream, slot.id))

    async def _body(
        self, scope: Scope, receive: Receive, headers: dict[bytes, bytes], *, control: bool = False
    ) -> bytes | None:
        maximum = self.controls.policy.limits.body_max_bytes
        # The body ceiling is fixed at deployment. Reviewed resource limits may
        # only lower it; the request never raises it through a header or JSON.
        if scope.get("path", "").startswith(
            ("/api/v1/budget/", "/api/v1/operator/", "/api/v1/publications")
        ):
            maximum = ADMIN_BODY_MAX_BYTES
        elif not control and self.controls.limits_resolver is not None:
            maximum = (await asyncio.to_thread(self.controls.current_limits)).body_max_bytes
        length = headers.get(b"content-length")
        if length is not None:
            if not length.isdigit() or len(length) > 12:
                raise PublicResourceError("invalid_public_request", 400)
            if int(length) > maximum:
                raise PublicResourceError("public_request_body_too_large", 413)
        body = bytearray()
        try:
            async with asyncio.timeout(BODY_TIMEOUT_SECONDS):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return None
                    if message["type"] != "http.request":
                        raise PublicResourceError("invalid_public_request", 400)
                    chunk = message.get("body", b"")
                    if len(body) + len(chunk) > maximum:
                        raise PublicResourceError("public_request_body_too_large", 413)
                    body.extend(chunk)
                    if not message.get("more_body", False):
                        return bytes(body)
        except TimeoutError:
            raise PublicResourceError("public_request_body_timeout", 408) from None
