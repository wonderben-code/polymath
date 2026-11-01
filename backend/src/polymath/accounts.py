"""Sign in with ORCID, GitHub or Google, or continue as a guest (docs/ACCOUNTS.md).

Everything in Polymath belongs to a browser session. An account remembers one
home session: the first time someone signs in, the session they are using
becomes it, so their guest work simply carries on as account work. Signing in
on another device opens that same home session there; that device's own guest
session is kept aside and comes back when they sign out.

The home session's bearer token is stored only as AES-256-GCM ciphertext.
Provider tokens are used once to read a stable identity and a display name,
then dropped; nothing else is requested from the provider.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from urllib.parse import urlencode

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from sqlalchemy import Engine, delete, func, or_, select
from sqlalchemy.orm import Session

from polymath.access_models import BrowserSession
from polymath.account_models import Account, AccountOAuthState

STATE_SECONDS = 600
ACCOUNT_SESSION_SECONDS = 365 * 86400
GUEST_SESSION_SECONDS = 7 * 86400
TIMEOUT_SECONDS = 20.0
MAX_RESPONSE_BYTES = 65_536
ORCID_PATTERN = re.compile(r"[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]")


class AccountError(Exception):
    """A stable public error code; never contains a token or provider text."""


@dataclass(frozen=True)
class Provider:
    name: str
    client_id: str
    client_secret: SecretStr
    authorize_url: str
    token_url: str
    scope: str
    userinfo_url: str | None = None


@dataclass(frozen=True)
class Identity:
    subject: str
    display_name: str
    orcid: str | None = None


@dataclass(frozen=True)
class AccountPolicy:
    key: bytes | None = None
    site_origin: str | None = None
    providers: dict[str, Provider] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.key is not None and len(self.key) != 32:
            raise ValueError("A 32-byte accounts key is required")

    @property
    def available(self) -> tuple[str, ...]:
        if self.key is None or not self.site_origin:
            return ()
        return tuple(name for name in ("orcid", "github", "google") if name in self.providers)

    @property
    def redirect_uri(self) -> str:
        return f"{self.site_origin}/account/callback"

    @classmethod
    def from_settings(cls, settings) -> AccountPolicy:
        if not settings.accounts_enabled:
            return cls()
        orcid = settings.orcid_base_url
        options = {
            "orcid": (
                settings.orcid_client_id,
                settings.orcid_client_secret,
                f"{orcid}/oauth/authorize",
                f"{orcid}/oauth/token",
                "/authenticate",
                None,
            ),
            "github": (
                settings.github_client_id,
                settings.github_client_secret,
                "https://github.com/login/oauth/authorize",
                "https://github.com/login/oauth/access_token",
                "",
                "https://api.github.com/user",
            ),
            "google": (
                settings.google_client_id,
                settings.google_client_secret,
                "https://accounts.google.com/o/oauth2/v2/auth",
                "https://oauth2.googleapis.com/token",
                "openid profile",
                "https://openidconnect.googleapis.com/v1/userinfo",
            ),
        }
        providers = {
            name: Provider(name, client, secret, authorize, token, scope, userinfo)
            for name, (client, secret, authorize, token, scope, userinfo) in options.items()
            if client and secret
        }
        return cls(
            key=base64.b64decode(settings.accounts_key.get_secret_value()),
            site_origin=settings.public_site_origin,
            providers=providers,
        )


def _now(session: Session) -> datetime:
    return session.scalar(select(func.clock_timestamp()))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _aad(account_id: str) -> bytes:
    return f"polymath-account-home-v1:{account_id}".encode("ascii")


def seal_home(key: bytes, account_id: str, token: str) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, token.encode("ascii"), _aad(account_id))


def open_home(key: bytes, account_id: str, sealed: bytes) -> str | None:
    try:
        return AESGCM(key).decrypt(sealed[:12], sealed[12:], _aad(account_id)).decode("ascii")
    except (InvalidTag, ValueError, UnicodeError):
        return None


def plain_name(value: object) -> str | None:
    """A provider's display name as short plain text, or None."""
    if not isinstance(value, str):
        return None
    cleaned = "".join(
        character for character in value if unicodedata.category(character)[0] not in ("C",)
    )
    cleaned = re.sub(r"<[^>]*>|https?://\S+", "", " ".join(cleaned.split())).strip()
    return cleaned[:120] or None


def _json(response: httpx.Response) -> dict:
    if response.status_code in (400, 401, 403):
        raise AccountError("account_sign_in_refused")
    if response.status_code != 200 or len(response.content) > MAX_RESPONSE_BYTES:
        raise AccountError("account_sign_in_failed")
    try:
        value = json.loads(response.content.decode("utf-8", errors="strict"))
    except (ValueError, UnicodeError):
        raise AccountError("account_sign_in_failed") from None
    if not isinstance(value, dict) or "error" in value:
        raise AccountError("account_sign_in_refused")
    return value


async def fetch_identity(
    policy: AccountPolicy,
    provider: Provider,
    code: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Identity:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": policy.redirect_uri,
        "client_id": provider.client_id,
        "client_secret": provider.client_secret.get_secret_value(),
    }
    try:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(TIMEOUT_SECONDS, connect=5.0),
            follow_redirects=False,
        ) as http:
            token = _json(
                await http.post(
                    provider.token_url, data=form, headers={"Accept": "application/json"}
                )
            )
            access = token.get("access_token")
            if not isinstance(access, str) or not 1 <= len(access) <= 4096:
                raise AccountError("account_sign_in_failed")
            if provider.name == "orcid":
                orcid = token.get("orcid")
                if not isinstance(orcid, str) or ORCID_PATTERN.fullmatch(orcid) is None:
                    raise AccountError("account_sign_in_failed")
                return Identity(orcid, plain_name(token.get("name")) or orcid, orcid)
            profile = _json(
                await http.get(
                    provider.userinfo_url,
                    headers={"Authorization": f"Bearer {access}", "Accept": "application/json"},
                )
            )
    except httpx.HTTPError:
        raise AccountError("account_sign_in_failed") from None
    subject = profile.get("id" if provider.name == "github" else "sub")
    if isinstance(subject, int) and not isinstance(subject, bool):
        subject = str(subject)
    if not isinstance(subject, str) or not re.fullmatch(r"[A-Za-z0-9_.|-]{1,128}", subject):
        raise AccountError("account_sign_in_failed")
    name = plain_name(profile.get("name")) or plain_name(profile.get("login")) or "Researcher"
    return Identity(subject, name)


@dataclass(frozen=True)
class SignIn:
    """What the browser's cookies should become after signing in."""

    result: str  # "created", "signed_in" or "switched"
    session_token: str | None = None  # set when this browser opens another session
    guest_token: str | None = None  # this browser's own guest session, kept aside


class AccountStore:
    def __init__(self, engine: Engine, policy: AccountPolicy | None = None):
        if engine.dialect.name != "postgresql":
            raise ValueError("AccountStore requires PostgreSQL")
        self.engine = engine
        self.policy = policy or AccountPolicy()

    def _live(self, session: Session, token_hash: str | None) -> BrowserSession | None:
        if token_hash is None:
            return None
        row = session.get(BrowserSession, token_hash, populate_existing=True)
        return row if row is not None and row.expires_at > _now(session) else None

    def status(self, token_hash: str) -> dict:
        with Session(self.engine) as session:
            if self._live(session, token_hash) is None:
                raise AccountError("account_session_required")
            account = session.scalar(select(Account).where(Account.home_session_hash == token_hash))
            return {
                "available": bool(self.policy.available),
                "providers": list(self.policy.available),
                "signed_in": account is not None,
                "account": {
                    "display_name": account.display_name,
                    "provider": account.provider,
                    "orcid": account.orcid,
                }
                if account
                else None,
            }

    def begin(self, token_hash: str, provider: str) -> str:
        if provider not in self.policy.available:
            raise AccountError("account_provider_unavailable")
        chosen = self.policy.providers[provider]
        state = secrets.token_urlsafe(32)
        with Session(self.engine) as session, session.begin():
            if self._live(session, token_hash) is None:
                raise AccountError("account_session_required")
            now = _now(session)
            session.execute(
                delete(AccountOAuthState).where(
                    or_(
                        AccountOAuthState.expires_at <= now,
                        AccountOAuthState.owner_session_hash == token_hash,
                    )
                )
            )
            session.add(
                AccountOAuthState(
                    state_hash=_digest(state),
                    owner_session_hash=token_hash,
                    provider=provider,
                    created_at=now,
                    expires_at=now + timedelta(seconds=STATE_SECONDS),
                )
            )
        query = {
            "response_type": "code",
            "client_id": chosen.client_id,
            "redirect_uri": self.policy.redirect_uri,
            "state": state,
        }
        if chosen.scope:
            query["scope"] = chosen.scope
        return f"{chosen.authorize_url}?{urlencode(query)}"

    def _consume(self, token_hash: str, state: str) -> str:
        if not isinstance(state, str) or not 20 <= len(state) <= 200:
            raise AccountError("account_state_invalid")
        with Session(self.engine) as session, session.begin():
            row = session.get(AccountOAuthState, _digest(state), with_for_update=True)
            if (
                row is None
                or row.owner_session_hash != token_hash
                or row.expires_at <= _now(session)
            ):
                raise AccountError("account_state_invalid")
            provider = row.provider
            session.delete(row)
            return provider

    async def complete(
        self,
        token: str,
        token_hash: str,
        code: str,
        state: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> SignIn:
        if not isinstance(code, str) or not 1 <= len(code) <= 512:
            raise AccountError("account_state_invalid")
        provider = self._consume(token_hash, state)
        if provider not in self.policy.available:
            raise AccountError("account_provider_unavailable")
        identity = await fetch_identity(
            self.policy, self.policy.providers[provider], code, transport
        )
        return self._sign_in(token, token_hash, provider, identity)

    def _sign_in(self, token: str, token_hash: str, provider: str, identity: Identity) -> SignIn:
        key = self.policy.key
        with Session(self.engine) as session, session.begin():
            current = self._live(session, token_hash)
            if current is None:
                raise AccountError("account_session_required")
            now = _now(session)
            account = session.scalar(
                select(Account)
                .where(Account.provider == provider, Account.subject == identity.subject)
                .with_for_update()
            )
            other = session.scalar(select(Account).where(Account.home_session_hash == token_hash))
            if other is not None and (account is None or other.id != account.id):
                # This browser already holds another account's work.
                raise AccountError("account_sign_out_first")
            created = account is None
            if created:
                account = Account(
                    id=uuid.uuid4(),
                    provider=provider,
                    subject=identity.subject,
                    display_name=identity.display_name,
                    orcid=identity.orcid,
                )
                session.add(account)
            account.display_name, account.signed_in_at = identity.display_name, now
            if identity.orcid:
                account.orcid = identity.orcid
            home = self._live(session, account.home_session_hash)
            home_token = (
                open_home(key, str(account.id), account.home_ciphertext)
                if home is not None and account.home_ciphertext
                else None
            )
            if home_token is None or _digest(home_token) != account.home_session_hash:
                # No usable home yet: this browser's session becomes it, so
                # everything done here as a guest carries on as account work.
                account.home_session_hash = token_hash
                account.home_ciphertext = seal_home(key, str(account.id), token)
                home, home_token = current, token
            home.expires_at = max(home.expires_at, now + timedelta(seconds=ACCOUNT_SESSION_SECONDS))
            session.flush()
            if home_token == token:
                return SignIn("created" if created else "signed_in")
            return SignIn("switched", session_token=home_token, guest_token=token)

    def sign_out(self, guest_token_hash: str | None) -> bool:
        """True when this browser's own earlier guest session is still there to return to."""
        with Session(self.engine) as session:
            return self._live(session, guest_token_hash) is not None

    def delete(self, token_hash: str) -> None:
        """Forget the account; this browser keeps its work as an ordinary guest."""
        with Session(self.engine) as session, session.begin():
            account = session.scalar(
                select(Account).where(Account.home_session_hash == token_hash).with_for_update()
            )
            if account is None:
                raise AccountError("account_not_signed_in")
            home = self._live(session, token_hash)
            if home is not None:
                home.expires_at = min(
                    home.expires_at, _now(session) + timedelta(seconds=GUEST_SESSION_SECONDS)
                )
            session.delete(account)
