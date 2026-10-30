"""Connect Zenodo for every visitor (docs/CONNECT_ZENODO.md).

Each browser session can connect its own Zenodo account through Zenodo's OAuth
sign-in and publish its own written findings there. Tokens are encrypted with
AES-256-GCM, bound to their session and destination, never logged and never
returned to a browser. The owner's publisher path is separate and untouched.

Creating a draft is the only step that could duplicate a record, so its
outcome is recorded before it is attempted; an unknown outcome is only ever
followed by a read-only search, never by a second creation without the
visitor's explicit request.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr, ValidationError
from sqlalchemy import Engine, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession
from polymath.evidence import _canonical
from polymath.publication import (
    PublicationError,
    _artifact,
    _build_manifest,
    _current_artifact_source,
    hash_manifest,
)
from polymath.publication_contracts import (
    PublicationManifest,
    PublicationMetadata,
    PublicationPolicy,
    PublisherIdentity,
)
from polymath.visitor_zenodo_models import (
    VisitorPublication,
    ZenodoConnection,
    ZenodoOAuthState,
)

SCOPES = ("deposit:write", "deposit:actions")
STATE_SECONDS = 600
DAILY_PUBLICATIONS = 10
LEASE_SECONDS = 300
TOKEN_TIMEOUT_SECONDS = 20.0
MAX_TOKEN_RESPONSE_BYTES = 16_384
ORIGINS = {"sandbox": "https://sandbox.zenodo.org", "production": "https://zenodo.org"}
PUBLISHER_ID = "zenodo-visitor"
RETRYABLE = ("failed", "missing", "reconnect")


class VisitorZenodoError(Exception):
    """A stable public error code; never contains a token or provider text."""


@dataclass(frozen=True)
class OAuthClient:
    client_id: str
    client_secret: SecretStr


@dataclass(frozen=True)
class VisitorZenodoPolicy:
    key: bytes | None = None
    site_origin: str | None = None
    clients: dict[str, OAuthClient] = field(default_factory=dict)
    daily_publications: int = DAILY_PUBLICATIONS

    def __post_init__(self) -> None:
        if self.key is not None and len(self.key) != 32:
            raise ValueError("A 32-byte connection key is required")
        if any(name not in ORIGINS for name in self.clients):
            raise ValueError("Unknown Zenodo destination")

    @property
    def destinations(self) -> tuple[str, ...]:
        if self.key is None or not self.site_origin:
            return ()
        return tuple(name for name in ORIGINS if name in self.clients)

    @property
    def redirect_uri(self) -> str:
        return f"{self.site_origin}/zenodo/callback"

    @classmethod
    def from_settings(cls, settings) -> VisitorZenodoPolicy:
        destinations = settings.visitor_zenodo_destinations
        if not destinations:
            return cls()
        clients = {
            "sandbox": (settings.zenodo_sandbox_client_id, settings.zenodo_sandbox_client_secret),
            "production": (
                settings.zenodo_production_client_id,
                settings.zenodo_production_client_secret,
            ),
        }
        return cls(
            key=base64.b64decode(settings.zenodo_connection_key.get_secret_value()),
            site_origin=settings.public_site_origin,
            clients={name: OAuthClient(*clients[name]) for name in destinations},
        )


@dataclass(frozen=True)
class TokenSet:
    access: SecretStr
    refresh: SecretStr | None
    expires_at: datetime | None
    user_id: str | None


def _now(session: Session) -> datetime:
    return session.scalar(select(func.clock_timestamp()))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aad(owner_hash: str, destination: str, kind: str) -> bytes:
    return f"polymath-zenodo-v1:{owner_hash}:{destination}:{kind}".encode("ascii")


def seal(key: bytes, owner_hash: str, destination: str, kind: str, value: SecretStr) -> bytes:
    nonce = os.urandom(12)
    plain = value.get_secret_value().encode("utf-8")
    return nonce + AESGCM(key).encrypt(nonce, plain, _aad(owner_hash, destination, kind))


def unseal(key: bytes, owner_hash: str, destination: str, kind: str, sealed: bytes) -> SecretStr:
    try:
        plain = AESGCM(key).decrypt(sealed[:12], sealed[12:], _aad(owner_hash, destination, kind))
        return SecretStr(plain.decode("utf-8"))
    except (InvalidTag, ValueError, UnicodeError):
        raise VisitorZenodoError("zenodo_reconnect_required") from None


def _browser(session: Session, owner_hash: str) -> BrowserSession:
    row = session.get(BrowserSession, owner_hash, populate_existing=True)
    if row is None or row.expires_at <= _now(session):
        raise VisitorZenodoError("zenodo_session_required")
    return row


def _token_text(value: object, maximum: int) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or any(not 33 <= ord(item) <= 126 for item in value)
    ):
        raise VisitorZenodoError("zenodo_connection_failed")
    return value


def parse_token_response(payload: bytes, now: datetime) -> TokenSet:
    """Accept only a bearer token that grants both deposit permissions."""
    try:
        raw = json.loads(payload.decode("utf-8", errors="strict"))
    except (ValueError, UnicodeError):
        raise VisitorZenodoError("zenodo_connection_failed") from None
    if not isinstance(raw, dict) or str(raw.get("token_type", "")).lower() != "bearer":
        raise VisitorZenodoError("zenodo_connection_failed")
    scope = raw.get("scope")
    granted = set(scope.split()) if isinstance(scope, str) else set()
    if not set(SCOPES) <= granted:
        raise VisitorZenodoError("zenodo_permission_missing")
    access = _token_text(raw.get("access_token"), 4096)
    refresh = raw.get("refresh_token")
    refresh = _token_text(refresh, 4096) if refresh is not None else None
    expires = raw.get("expires_in")
    expires_at = None
    if expires is not None:
        if type(expires) is not int or not 60 <= expires <= 10 * 365 * 86400:
            raise VisitorZenodoError("zenodo_connection_failed")
        # Refresh a minute early so a request never starts with a dying token.
        expires_at = now + timedelta(seconds=expires - 60)
    user = raw.get("user")
    user_id = None
    if isinstance(user, dict) and isinstance(user.get("id"), (str, int)):
        candidate = str(user["id"])
        user_id = candidate if candidate.isalnum() and len(candidate) <= 64 else None
    return TokenSet(SecretStr(access), SecretStr(refresh) if refresh else None, expires_at, user_id)


async def exchange_token(
    policy: VisitorZenodoPolicy,
    destination: str,
    form: dict,
    now: datetime,
    transport: httpx.AsyncBaseTransport | None = None,
) -> TokenSet:
    client = policy.clients[destination]
    body = {
        **form,
        "client_id": client.client_id,
        "client_secret": client.client_secret.get_secret_value(),
    }
    try:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(TOKEN_TIMEOUT_SECONDS, connect=5.0),
            follow_redirects=False,
        ) as http:
            response = await http.post(
                ORIGINS[destination] + "/oauth/token",
                data=body,
                headers={"Accept": "application/json"},
            )
    except httpx.HTTPError:
        raise VisitorZenodoError("zenodo_connection_failed") from None
    if response.status_code in (400, 401, 403):
        raise VisitorZenodoError("zenodo_connection_refused")
    if response.status_code != 200 or len(response.content) > MAX_TOKEN_RESPONSE_BYTES:
        raise VisitorZenodoError("zenodo_connection_failed")
    return parse_token_response(response.content, now)


def _publication(row: VisitorPublication) -> dict:
    manifest = row.manifest
    return {
        "id": str(row.id),
        "artifact_id": str(row.artifact_id),
        "destination": row.destination,
        "status": row.status,
        "manifest_sha256": row.manifest_sha256,
        "title": manifest["metadata"]["title"],
        "creators": manifest["metadata"]["creators"],
        "description": manifest["metadata"]["description"],
        "keywords": manifest["metadata"]["keywords"],
        "license": manifest["metadata"]["license"],
        "file_name": manifest["file_name"],
        "markdown_sha256": manifest["markdown_sha256"],
        "doi": row.doi,
        "doi_url": "https://doi.org/" + row.doi if row.doi else None,
        "record_url": row.record_url,
        "error": row.error_code,
        "created_at": row.created_at.isoformat(),
        "published_at": row.published_at.isoformat() if row.published_at else None,
    }


def stored_manifest(row: VisitorPublication) -> PublicationManifest:
    try:
        manifest = PublicationManifest.model_validate_json(_canonical(row.manifest))
        if hash_manifest(manifest) != row.manifest_sha256:
            raise ValueError
        return manifest
    except (ValueError, TypeError, ValidationError, PublicationError):
        raise VisitorZenodoError("zenodo_publication_invalid") from None


class VisitorZenodoStore:
    def __init__(self, engine: Engine, policy: VisitorZenodoPolicy | None = None):
        if engine.dialect.name != "postgresql":
            raise ValueError("VisitorZenodoStore requires PostgreSQL")
        self.engine = engine
        self.policy = policy or VisitorZenodoPolicy()

    def _destination(self, destination: str) -> str:
        if destination not in self.policy.destinations:
            raise VisitorZenodoError("zenodo_destination_unavailable")
        return destination

    # Connections ---------------------------------------------------------

    def status(self, owner_hash: str) -> dict:
        with Session(self.engine) as session:
            _browser(session, owner_hash)
            rows = {
                row.destination: row
                for row in session.scalars(
                    select(ZenodoConnection).where(
                        ZenodoConnection.owner_session_hash == owner_hash
                    )
                )
            }
            return {
                "available": bool(self.policy.destinations),
                "destinations": [
                    {
                        "destination": name,
                        "connected": name in rows,
                        "connected_at": rows[name].connected_at.isoformat()
                        if name in rows
                        else None,
                    }
                    for name in self.policy.destinations
                ],
                "daily_publications": self.policy.daily_publications,
            }

    def begin(self, owner_hash: str, destination: str) -> str:
        destination = self._destination(destination)
        state = secrets.token_urlsafe(32)
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            now = _now(session)
            session.execute(
                delete(ZenodoOAuthState).where(
                    or_(
                        ZenodoOAuthState.expires_at <= now,
                        ZenodoOAuthState.owner_session_hash == owner_hash,
                    )
                )
            )
            session.add(
                ZenodoOAuthState(
                    state_hash=_digest(state),
                    owner_session_hash=owner_hash,
                    destination=destination,
                    created_at=now,
                    expires_at=now + timedelta(seconds=STATE_SECONDS),
                )
            )
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.policy.clients[destination].client_id,
                "scope": " ".join(SCOPES),
                "redirect_uri": self.policy.redirect_uri,
                "state": state,
            }
        )
        return f"{ORIGINS[destination]}/oauth/authorize?{query}"

    def _consume_state(self, owner_hash: str, state: str) -> tuple[str, datetime]:
        if not isinstance(state, str) or not 20 <= len(state) <= 200:
            raise VisitorZenodoError("zenodo_state_invalid")
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            row = session.get(ZenodoOAuthState, _digest(state), with_for_update=True)
            now = _now(session)
            if row is None or row.owner_session_hash != owner_hash or row.expires_at <= now:
                raise VisitorZenodoError("zenodo_state_invalid")
            destination = row.destination
            session.delete(row)
            return destination, now

    async def complete(
        self,
        owner_hash: str,
        code: str,
        state: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> str:
        if not isinstance(code, str) or not 1 <= len(code) <= 512:
            raise VisitorZenodoError("zenodo_state_invalid")
        destination, now = self._consume_state(owner_hash, state)
        destination = self._destination(destination)
        tokens = await exchange_token(
            self.policy,
            destination,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.policy.redirect_uri,
            },
            now,
            transport,
        )
        self._save_tokens(owner_hash, destination, tokens)
        return destination

    def _save_tokens(self, owner_hash: str, destination: str, tokens: TokenSet) -> None:
        key = self.policy.key
        values = {
            "access_ciphertext": seal(key, owner_hash, destination, "access", tokens.access),
            "refresh_ciphertext": seal(key, owner_hash, destination, "refresh", tokens.refresh)
            if tokens.refresh
            else None,
            "access_expires_at": tokens.expires_at,
            "zenodo_user_id": tokens.user_id,
        }
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            now = _now(session)
            session.execute(
                insert(ZenodoConnection)
                .values(
                    id=uuid.uuid4(),
                    owner_session_hash=owner_hash,
                    destination=destination,
                    connected_at=now,
                    updated_at=now,
                    **values,
                )
                .on_conflict_do_update(
                    constraint="uq_zenodo_connection", set_={**values, "updated_at": now}
                )
            )

    def disconnect(self, owner_hash: str, destination: str) -> None:
        if destination not in ORIGINS:
            raise VisitorZenodoError("zenodo_destination_unavailable")
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            session.execute(
                delete(ZenodoConnection).where(
                    ZenodoConnection.owner_session_hash == owner_hash,
                    ZenodoConnection.destination == destination,
                )
            )

    async def access_token(
        self,
        owner_hash: str,
        destination: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> SecretStr:
        """The visitor's current access token, refreshed when it is about to expire."""
        key = self.policy.key
        if key is None or destination not in self.policy.destinations:
            raise VisitorZenodoError("zenodo_reconnect_required")
        with Session(self.engine) as session:
            row = session.scalar(
                select(ZenodoConnection).where(
                    ZenodoConnection.owner_session_hash == owner_hash,
                    ZenodoConnection.destination == destination,
                )
            )
            if row is None:
                raise VisitorZenodoError("zenodo_reconnect_required")
            now = _now(session)
            access = unseal(key, owner_hash, destination, "access", row.access_ciphertext)
            if row.access_expires_at is None or row.access_expires_at > now:
                return access
            if row.refresh_ciphertext is None:
                raise VisitorZenodoError("zenodo_reconnect_required")
            refresh = unseal(key, owner_hash, destination, "refresh", row.refresh_ciphertext)
        try:
            tokens = await exchange_token(
                self.policy,
                destination,
                {"grant_type": "refresh_token", "refresh_token": refresh.get_secret_value()},
                now,
                transport,
            )
        except VisitorZenodoError:
            raise VisitorZenodoError("zenodo_reconnect_required") from None
        if tokens.refresh is None:
            tokens = TokenSet(tokens.access, refresh, tokens.expires_at, tokens.user_id)
        self._save_tokens(owner_hash, destination, tokens)
        return tokens.access

    # Publications --------------------------------------------------------

    def _owned(
        self, session: Session, owner_hash: str, publication_id: uuid.UUID, *, lock=False
    ) -> VisitorPublication:
        statement = select(VisitorPublication).where(
            VisitorPublication.id == publication_id,
            VisitorPublication.owner_session_hash == owner_hash,
        )
        if lock:
            statement = statement.with_for_update()
        row = session.scalar(statement)
        if row is None:
            raise VisitorZenodoError("zenodo_publication_not_found")
        return row

    def _connected(self, session: Session, owner_hash: str, destination: str) -> None:
        if (
            session.scalar(
                select(ZenodoConnection.id).where(
                    ZenodoConnection.owner_session_hash == owner_hash,
                    ZenodoConnection.destination == destination,
                )
            )
            is None
        ):
            raise VisitorZenodoError("zenodo_not_connected")

    def _manifest(
        self,
        session: Session,
        owner_hash: str,
        artifact_id: uuid.UUID,
        metadata: PublicationMetadata,
        destination: str,
    ) -> PublicationManifest:
        try:
            artifact = _artifact(session, owner_hash, artifact_id)
            _current_artifact_source(session, owner_hash, artifact)
            policy = PublicationPolicy(
                publisher=PublisherIdentity(
                    identifier=PUBLISHER_ID, display_name=metadata.creators[0].name
                ),
                allowed_destinations=(destination,),
            )
            return _build_manifest(artifact, metadata, policy, destination)
        except PublicationError as error:
            code = str(error)
            raise VisitorZenodoError(
                "zenodo_finding_not_current"
                if code == "publication_source_not_current"
                else "zenodo_publication_not_found"
                if code == "publication_not_found"
                else "zenodo_publication_invalid"
            ) from None

    def preview(
        self,
        owner_hash: str,
        artifact_id: uuid.UUID,
        metadata: PublicationMetadata,
        destination: str,
    ) -> dict:
        destination = self._destination(destination)
        try:
            metadata = PublicationMetadata.model_validate_json(metadata.model_dump_json())
        except (ValueError, TypeError, ValidationError):
            raise VisitorZenodoError("zenodo_publication_invalid") from None
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            self._connected(session, owner_hash, destination)
            manifest = self._manifest(session, owner_hash, artifact_id, metadata, destination)
            digest = hash_manifest(manifest)
            session.execute(
                insert(VisitorPublication)
                .values(
                    id=uuid.uuid4(),
                    owner_session_hash=owner_hash,
                    artifact_id=artifact_id,
                    destination=destination,
                    manifest=manifest.model_dump(mode="json"),
                    manifest_sha256=digest,
                    status="preview",
                )
                .on_conflict_do_nothing(constraint="uq_visitor_publication")
            )
            row = session.scalar(
                select(VisitorPublication).where(
                    VisitorPublication.owner_session_hash == owner_hash,
                    VisitorPublication.manifest_sha256 == digest,
                )
            )
            return _publication(row)

    def confirm(
        self,
        owner_hash: str,
        publication_id: uuid.UUID,
        manifest_sha256: str,
        *,
        confirm_permanent: bool,
    ) -> dict:
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            row = self._owned(session, owner_hash, publication_id, lock=True)
            if row.status != "preview":
                return _publication(row)
            if manifest_sha256 != row.manifest_sha256:
                raise VisitorZenodoError("zenodo_publication_changed")
            if row.destination == "production" and confirm_permanent is not True:
                raise VisitorZenodoError("zenodo_permanent_confirmation_required")
            self._destination(row.destination)
            self._connected(session, owner_hash, row.destination)
            manifest = stored_manifest(row)
            current = self._manifest(
                session, owner_hash, row.artifact_id, manifest.metadata, row.destination
            )
            if current != manifest:
                raise VisitorZenodoError("zenodo_publication_changed")
            now = _now(session)
            day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            used = session.scalar(
                select(func.count())
                .select_from(VisitorPublication)
                .where(
                    VisitorPublication.owner_session_hash == owner_hash,
                    VisitorPublication.confirmed_at >= day,
                )
            )
            if used >= self.policy.daily_publications:
                raise VisitorZenodoError("zenodo_daily_limit")
            row.status, row.confirmed_at, row.updated_at, row.error_code = (
                "queued",
                now,
                now,
                None,
            )
            session.flush()
            return _publication(row)

    def retry(self, owner_hash: str, publication_id: uuid.UUID) -> dict:
        """The visitor's explicit request to try again after a stop."""
        with Session(self.engine) as session, session.begin():
            _browser(session, owner_hash)
            row = self._owned(session, owner_hash, publication_id, lock=True)
            if row.status not in RETRYABLE:
                return _publication(row)
            self._connected(session, owner_hash, row.destination)
            if row.status == "missing":
                # A read-only search found nothing; creating again is the
                # visitor's own explicit decision.
                row.create_started_at = None
            elif row.create_started_at is not None and row.deposit_id is None:
                row.status = "checking"
            now = _now(session)
            if row.status != "checking":
                row.status = "queued"
            row.error_code, row.updated_at = None, now
            session.flush()
            return _publication(row)

    def list(self, owner_hash: str, artifact_id: uuid.UUID | None = None) -> dict:
        with Session(self.engine) as session:
            _browser(session, owner_hash)
            statement = select(VisitorPublication).where(
                VisitorPublication.owner_session_hash == owner_hash
            )
            if artifact_id is not None:
                statement = statement.where(VisitorPublication.artifact_id == artifact_id)
            rows = session.scalars(
                statement.order_by(
                    VisitorPublication.created_at.desc(), VisitorPublication.id.desc()
                ).limit(50)
            ).all()
            return {"publications": [_publication(row) for row in rows]}

    def get(self, owner_hash: str, publication_id: uuid.UUID) -> dict:
        with Session(self.engine) as session:
            _browser(session, owner_hash)
            return _publication(self._owned(session, owner_hash, publication_id))
