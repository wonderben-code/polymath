"""Database-backed public resource gates, independent of financial authority.

The control transactions finish before research admission takes sponsor/queue/
account locks. Rejected attempts consume their bounded rate allowance. Raw
addresses, bearer cookies, model credentials and request bodies are never stored.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.cookies import CookieError, SimpleCookie

from pydantic import SecretStr
from sqlalchemy import Engine, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession
from polymath.public_resource_models import PublicRateBucket, PublicStreamLease

STREAM_LOCK = 7_691_121_419
SERVICE_SUBJECT = hashlib.sha256(b"polymath-public-resource-service-v1").hexdigest()
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")


class PublicResourceError(Exception):
    def __init__(self, code: str, status: int = 429, retry_after_seconds: int | None = None):
        self.code, self.status, self.retry_after_seconds = code, status, retry_after_seconds
        super().__init__(code)


def derive_network_key(bootstrap_secret: SecretStr | str | bytes) -> bytes:
    if isinstance(bootstrap_secret, SecretStr):
        bootstrap_secret = bootstrap_secret.get_secret_value()
    value = (
        bootstrap_secret.encode("utf-8") if isinstance(bootstrap_secret, str) else bootstrap_secret
    )
    if not isinstance(value, bytes) or not 32 <= len(value) <= 8192:
        raise ValueError("public_network_key_unavailable")
    return hmac.digest(value, b"polymath-public-network-key-v1", "sha256")


@dataclass(frozen=True)
class PublicResourcePolicy:
    enabled: bool = False
    network_key: bytes | None = field(default=None, repr=False)
    limits: object | None = None

    def __post_init__(self):
        if type(self.enabled) is not bool:
            raise ValueError("invalid_public_resource_policy")
        if not self.enabled:
            return
        from polymath.public_research_contracts import PublicResourceLimits

        if not isinstance(self.network_key, bytes) or len(self.network_key) != 32:
            raise ValueError("public_network_key_unavailable")
        if not isinstance(self.limits, PublicResourceLimits):
            raise ValueError("invalid_public_resource_limits")
        validated = PublicResourceLimits.model_validate_json(self.limits.model_dump_json())
        object.__setattr__(self, "limits", validated)


@dataclass(frozen=True)
class PublicRequestIdentity:
    network_bucket: str
    owner_hash: str | None = None
    owner_expires_at: datetime | None = None


@dataclass(frozen=True)
class PublicStreamSlot:
    id: uuid.UUID
    expires_at: datetime
    remaining_seconds: float


def _now(session: Session) -> datetime:
    return session.scalar(select(func.clock_timestamp()))


def _window(now: datetime, seconds: int) -> datetime:
    return datetime.fromtimestamp(math.floor(now.timestamp() / seconds) * seconds, UTC)


def _owner_token(cookie_header: str | None) -> str | None:
    if not isinstance(cookie_header, str) or len(cookie_header) > 16_384:
        return None
    try:
        cookies = SimpleCookie()
        cookies.load(cookie_header)
        value = cookies.get("polymath_session")
        token = value.value if value is not None else None
        return token if token is not None and TOKEN_PATTERN.fullmatch(token) else None
    except (CookieError, ValueError):
        return None


class PublicResourceControls:
    def __init__(
        self,
        engine: Engine,
        policy: PublicResourcePolicy | None = None,
        *,
        limits_resolver: Callable[[Session], object | None] | None = None,
    ):
        self.engine, self.policy = engine, policy or PublicResourcePolicy()
        self.limits_resolver = limits_resolver
        if self.policy.enabled and engine.dialect.name != "postgresql":
            raise ValueError("public_resource_controls_require_postgresql")

    def _limits(self, session: Session):
        from polymath.public_research_contracts import PublicResourceLimits

        server = self.policy.limits
        if self.limits_resolver is None:
            return server
        try:
            reviewed = self.limits_resolver(session)
        except Exception:
            raise PublicResourceError("public_resource_controls_unavailable", 503) from None
        if reviewed is None:
            return server
        if not isinstance(reviewed, PublicResourceLimits):
            raise PublicResourceError("public_resource_controls_unavailable", 503)
        try:
            return PublicResourceLimits.model_validate(
                {
                    key: min(value, getattr(reviewed, key))
                    for key, value in server.model_dump(mode="json").items()
                }
            )
        except (ValueError, TypeError):
            raise PublicResourceError("public_resource_controls_unavailable", 503) from None

    def current_limits(self):
        if not self.policy.enabled:
            raise PublicResourceError("public_resource_controls_unavailable", 503)
        if self.limits_resolver is None:
            return self.policy.limits
        try:
            with Session(self.engine) as session:
                _timeouts(session)
                return self._limits(session)
        except (SQLAlchemyError, ValueError, TypeError):
            raise PublicResourceError("public_resource_controls_unavailable", 503) from None

    def network_bucket(self, peer: str | None) -> str:
        if self.policy.network_key is None:
            raise PublicResourceError("public_resource_controls_unavailable", 503)
        try:
            address = ipaddress.ip_address(peer) if isinstance(peer, str) else None
            if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
                address = address.ipv4_mapped
            if isinstance(address, ipaddress.IPv4Address):
                canonical = "v4:" + str(address)
            elif isinstance(address, ipaddress.IPv6Address):
                canonical = "v6:" + str(ipaddress.ip_network((address, 64), strict=False))
            else:
                canonical = "unknown"
        except ValueError:
            canonical = "unknown"
        return hmac.new(
            self.policy.network_key,
            b"polymath-public-peer-v1\x00" + canonical.encode("ascii"),
            "sha256",
        ).hexdigest()

    def identify(self, peer: str | None, cookie_header: str | None) -> PublicRequestIdentity:
        network = self.network_bucket(peer)
        token = _owner_token(cookie_header)
        if token is None:
            return PublicRequestIdentity(network)
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        try:
            with Session(self.engine) as session:
                _timeouts(session)
                owner = session.get(BrowserSession, digest)
                if owner is None or owner.expires_at <= _now(session):
                    return PublicRequestIdentity(network)
                return PublicRequestIdentity(network, owner.token_hash, owner.expires_at)
        except SQLAlchemyError:
            raise PublicResourceError("public_resource_controls_unavailable", 503) from None

    def _buckets(self, operation: str, identity: PublicRequestIdentity, limits):
        if operation == "mint":
            return (
                ("mint-service-hour", SERVICE_SUBJECT, 3600, limits.mint_service_per_hour),
                ("mint-service-day", SERVICE_SUBJECT, 86400, limits.mint_service_per_day),
                ("mint-network-hour", identity.network_bucket, 3600, limits.mint_network_per_hour),
                ("mint-network-day", identity.network_bucket, 86400, limits.mint_network_per_day),
            )
        fields = {
            "submit": "submit",
            "read": "read",
            "free_write": "free_write",
            "control": "control",
        }
        prefix = fields.get(operation)
        if prefix is None:
            raise ValueError("invalid_public_resource_operation")
        values = [
            (
                f"{prefix}-service-minute",
                SERVICE_SUBJECT,
                60,
                getattr(limits, f"{prefix}_service_per_minute"),
            ),
            (
                f"{prefix}-network-minute",
                identity.network_bucket,
                60,
                getattr(limits, f"{prefix}_network_per_minute"),
            ),
        ]
        if identity.owner_hash is not None:
            values.append(
                (
                    f"{prefix}-session-minute",
                    identity.owner_hash,
                    60,
                    getattr(limits, f"{prefix}_session_per_minute"),
                )
            )
        return tuple(values)

    def consume(self, operation: str, identity: PublicRequestIdentity) -> None:
        if not self.policy.enabled:
            return
        denied = None
        try:
            with Session(self.engine) as session, session.begin():
                _timeouts(session)
                now = _now(session)
                limits = self.policy.limits if operation == "control" else self._limits(session)
                # Service goes first: an exhausted global gate cannot create an
                # unbounded number of rows for attacker-chosen peer identities.
                for kind, subject, seconds, ceiling in self._buckets(operation, identity, limits):
                    start = _window(now, seconds)
                    session.execute(
                        insert(PublicRateBucket)
                        .values(
                            bucket_kind=kind,
                            subject_sha256=subject,
                            window_start=start,
                            count=0,
                            expires_at=start + timedelta(seconds=seconds),
                        )
                        .on_conflict_do_nothing()
                    )
                    bucket = session.scalar(
                        select(PublicRateBucket)
                        .where(
                            PublicRateBucket.bucket_kind == kind,
                            PublicRateBucket.subject_sha256 == subject,
                            PublicRateBucket.window_start == start,
                        )
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                    if bucket.count < ceiling + 1:
                        bucket.count += 1
                    if bucket.count > ceiling:
                        denied = PublicResourceError(
                            "public_request_rate_limited",
                            429,
                            max(1, math.ceil((bucket.expires_at - now).total_seconds())),
                        )
                        break
                expired = (
                    select(
                        PublicRateBucket.bucket_kind,
                        PublicRateBucket.subject_sha256,
                        PublicRateBucket.window_start,
                    )
                    .where(PublicRateBucket.expires_at < now - timedelta(days=1))
                    .limit(100)
                    .with_for_update(skip_locked=True)
                )
                from sqlalchemy import tuple_

                session.execute(
                    delete(PublicRateBucket).where(
                        tuple_(
                            PublicRateBucket.bucket_kind,
                            PublicRateBucket.subject_sha256,
                            PublicRateBucket.window_start,
                        ).in_(expired)
                    )
                )
        except SQLAlchemyError:
            raise PublicResourceError("public_resource_controls_unavailable", 503) from None
        if denied is not None:
            raise denied

    def acquire_stream(self, identity: PublicRequestIdentity) -> PublicStreamSlot:
        if not self.policy.enabled:
            raise PublicResourceError("public_resource_controls_unavailable", 503)
        if identity.owner_hash is None or identity.owner_expires_at is None:
            raise PublicResourceError("research_session_required", 401)
        try:
            with Session(self.engine) as session, session.begin():
                _timeouts(session)
                session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": STREAM_LOCK})
                now = _now(session)
                limits = self._limits(session)
                owner = session.get(BrowserSession, identity.owner_hash, populate_existing=True)
                if owner is None or owner.expires_at <= now:
                    raise PublicResourceError("research_session_required", 401)
                existing = session.scalars(
                    select(PublicStreamLease).where(PublicStreamLease.expires_at > now)
                ).all()
                for selected, maximum in (
                    (existing, limits.sse_service_connections),
                    (
                        [row for row in existing if row.network_bucket == identity.network_bucket],
                        limits.sse_network_connections,
                    ),
                    (
                        [row for row in existing if row.owner_hash == identity.owner_hash],
                        limits.sse_session_connections,
                    ),
                ):
                    if len(selected) >= maximum:
                        earliest = min(row.expires_at for row in selected)
                        raise PublicResourceError(
                            "public_stream_limit",
                            429,
                            max(1, math.ceil((earliest - now).total_seconds())),
                        )
                expiry = min(owner.expires_at, now + timedelta(seconds=limits.sse_max_seconds))
                identifier = uuid.uuid4()
                session.add(
                    PublicStreamLease(
                        id=identifier,
                        owner_hash=owner.token_hash,
                        network_bucket=identity.network_bucket,
                        expires_at=expiry,
                    )
                )
                expired = (
                    select(PublicStreamLease.id)
                    .where(PublicStreamLease.expires_at <= now)
                    .limit(100)
                )
                session.execute(delete(PublicStreamLease).where(PublicStreamLease.id.in_(expired)))
                return PublicStreamSlot(identifier, expiry, (expiry - now).total_seconds())
        except SQLAlchemyError:
            raise PublicResourceError("public_resource_controls_unavailable", 503) from None

    def release_stream(self, identifier: uuid.UUID) -> None:
        if not self.policy.enabled:
            return
        try:
            with Session(self.engine) as session, session.begin():
                _timeouts(session)
                session.execute(delete(PublicStreamLease).where(PublicStreamLease.id == identifier))
        except SQLAlchemyError:
            # A crashed/disconnected release retains only an expiring resource
            # slot. It grants no money and cannot extend its fixed deadline.
            return


def _timeouts(session: Session) -> None:
    session.execute(text("SET LOCAL lock_timeout = '1500ms'"))
    session.execute(text("SET LOCAL statement_timeout = '2500ms'"))
