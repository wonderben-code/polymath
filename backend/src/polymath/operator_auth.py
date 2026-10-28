"""Explicit human publisher authentication; research tools cannot grant this role.

The bootstrap credential is used only for a short-lived, browser-bound login.
Only a random cookie hash and the current policy fingerprint are stored. Every
privileged request must pass both browser and operator CSRF checks, as well as
current configured policy, expiry, and revocation checks.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, field
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import Engine, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.operator_models import OperatorSession
from polymath.run_api import (
    _authenticated,
    _authorize_write,
    _now,
    _private_response,
    _PrivateHTTPException,
    _PrivateRoute,
)

OPERATOR_COOKIE_NAME = "polymath_operator"
OPERATOR_CSRF_HEADER = "x-operator-csrf-token"
OPERATOR_COOKIE_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
_BOOTSTRAP_PATTERN = re.compile(r"[A-Za-z0-9_-]{32,256}")
_ACTOR_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}")


@dataclass(frozen=True)
class OperatorPolicy:
    bootstrap_token: SecretStr | None = field(default=None, repr=False)
    actor_id: str = "publisher"
    display_name: str = "Publisher"
    session_seconds: int = 1200

    def __post_init__(self) -> None:
        if self.bootstrap_token is not None:
            if not isinstance(self.bootstrap_token, SecretStr):
                raise ValueError("Operator bootstrap credential must be a secret string")
            token = self.bootstrap_token.get_secret_value()
            if _BOOTSTRAP_PATTERN.fullmatch(token) is None or len(set(token)) < 12:
                raise ValueError("Operator bootstrap credential needs a strong URL-safe token")
        if not isinstance(self.actor_id, str) or _ACTOR_PATTERN.fullmatch(self.actor_id) is None:
            raise ValueError("Operator identity must be a bounded identifier")
        if (
            not isinstance(self.display_name, str)
            or not 1 <= len(self.display_name) <= 120
            or self.display_name != self.display_name.strip()
            or any(ord(character) < 32 for character in self.display_name)
        ):
            raise ValueError("Operator display name must be bounded public text")
        try:
            self.display_name.encode("utf-8")
        except UnicodeError:
            raise ValueError("Operator display name must be valid Unicode") from None
        if type(self.session_seconds) is not int or not 60 <= self.session_seconds <= 3600:
            raise ValueError("Operator sessions must last between 60 and 3600 seconds")

    @property
    def configured(self) -> bool:
        return self.bootstrap_token is not None

    @property
    def credential_fingerprint(self) -> str | None:
        if self.bootstrap_token is None:
            return None
        value = {
            "domain": "polymath-operator-credential-v1",
            "token": self.bootstrap_token.get_secret_value(),
            "actor_id": self.actor_id,
            "display_name": self.display_name,
            "session_seconds": self.session_seconds,
        }
        serialized = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class OperatorLogin(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)

    token: SecretStr = Field(min_length=1, max_length=256)


def _cookie(request: Request) -> str | None:
    value = request.cookies.get(OPERATOR_COOKIE_NAME)
    return value if value is not None and OPERATOR_COOKIE_PATTERN.fullmatch(value) else None


def _hash_operator_cookie(token: str) -> str:
    return hashlib.sha256(("operator-session:" + token).encode("ascii")).hexdigest()


def _operator_csrf(token: str) -> str:
    return hashlib.sha256(("operator-csrf:" + token).encode("ascii")).hexdigest()


def _configured(policy: OperatorPolicy | None) -> bool:
    return isinstance(policy, OperatorPolicy) and policy.configured


def _required() -> _PrivateHTTPException:
    return _PrivateHTTPException(401, "operator_session_required")


def require_operator(
    session: Session,
    request: Request,
    *,
    write: bool = True,
    policy: OperatorPolicy | None = None,
) -> OperatorSession:
    """Require current policy and the same active browser; caller owns transaction.

    Writes lock the operator row until the caller commits. Approval code can
    retain that lock while persisting an immutable human authorization receipt.
    """
    if not _configured(policy):
        raise _PrivateHTTPException(503, "operator_unavailable")
    owner = _authenticated(session, request)
    token = _cookie(request)
    if token is None:
        raise _required()
    statement = select(OperatorSession).where(
        OperatorSession.token_hash == _hash_operator_cookie(token),
        OperatorSession.browser_session_hash == owner.token_hash,
    )
    if write:
        statement = statement.with_for_update()
    operator = session.scalar(statement.execution_options(populate_existing=True))
    now = _now(session)
    if (
        operator is None
        or operator.expires_at <= now
        or operator.revoked_at is not None
        or policy is None
        or not hmac.compare_digest(operator.credential_fingerprint, policy.credential_fingerprint)
        or operator.actor_id != policy.actor_id
        or operator.display_name != policy.display_name
    ):
        raise _required()
    # Recheck browser expiry after a possible lock wait, not only before it.
    session.refresh(owner)
    if owner.expires_at <= _now(session):
        raise _PrivateHTTPException(401, "research_session_required")
    if write:
        _authorize_write(request)
        supplied = request.headers.get(OPERATOR_CSRF_HEADER, "")
        if re.fullmatch(r"[0-9a-f]{64}", supplied) is None or not hmac.compare_digest(
            supplied, _operator_csrf(token)
        ):
            raise _PrivateHTTPException(403, "operator_csrf_check_failed")
    return operator


def _snapshot(policy: OperatorPolicy | None, operator: OperatorSession | None, token=None) -> dict:
    return {
        "configured": _configured(policy),
        "authenticated": operator is not None,
        "actor_id": operator.actor_id if operator is not None else None,
        "display_name": operator.display_name if operator is not None else None,
        "expires_at": operator.expires_at.isoformat() if operator is not None else None,
        "csrf_token": _operator_csrf(token) if operator is not None else None,
    }


def _clear_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(
        OPERATOR_COOKIE_NAME,
        path="/api/v1",
        secure=request.url.scheme == "https",
        httponly=True,
        samesite="strict",
    )


def create_operator_router(engine: Engine, policy: OperatorPolicy | None = None) -> APIRouter:
    if engine.dialect.name != "postgresql":
        raise ValueError("Operator routes require PostgreSQL")
    if policy is not None and not isinstance(policy, OperatorPolicy):
        raise ValueError("Operator policy must be explicitly configured")
    router = APIRouter(
        prefix="/api/v1/operator",
        route_class=_PrivateRoute,
        dependencies=[Depends(_private_response)],
    )

    @router.get("/session")
    def current_session(request: Request, response: Response) -> dict:
        try:
            with Session(engine) as session:
                try:
                    operator = require_operator(session, request, write=False, policy=policy)
                except _PrivateHTTPException:
                    if OPERATOR_COOKIE_NAME in request.cookies:
                        _clear_cookie(request, response)
                    return _snapshot(policy, None)
                return _snapshot(policy, operator, _cookie(request))
        except SQLAlchemyError:
            raise _PrivateHTTPException(503, "operator_storage_unavailable") from None

    @router.post("/session")
    def login(body: OperatorLogin, request: Request, response: Response) -> dict:
        _authorize_write(request)
        if not _configured(policy):
            raise _PrivateHTTPException(503, "operator_unavailable")
        try:
            with Session(engine) as session, session.begin():
                owner = _authenticated(session, request)
                # Serialize rotation within one browser, and recheck expiry
                # after waiting rather than issuing a role to an expired owner.
                session.refresh(owner, with_for_update=True)
                now = _now(session)
                if owner.expires_at <= now:
                    raise _PrivateHTTPException(401, "research_session_required")
                if not hmac.compare_digest(
                    body.token.get_secret_value().encode("utf-8", errors="surrogatepass"),
                    policy.bootstrap_token.get_secret_value().encode("ascii"),
                ):
                    raise _PrivateHTTPException(401, "invalid_operator_credential")
                session.execute(
                    update(OperatorSession)
                    .where(
                        OperatorSession.browser_session_hash == owner.token_hash,
                        OperatorSession.revoked_at.is_(None),
                    )
                    .values(revoked_at=now)
                )
                token = secrets.token_urlsafe(32)
                operator = OperatorSession(
                    token_hash=_hash_operator_cookie(token),
                    browser_session_hash=owner.token_hash,
                    credential_fingerprint=policy.credential_fingerprint,
                    actor_id=policy.actor_id,
                    display_name=policy.display_name,
                    expires_at=min(
                        now + timedelta(seconds=policy.session_seconds), owner.expires_at
                    ),
                )
                session.add(operator)
                session.flush()
                remaining = max(0, int((operator.expires_at - now).total_seconds()))
                response.set_cookie(
                    OPERATOR_COOKIE_NAME,
                    token,
                    max_age=remaining,
                    path="/api/v1",
                    secure=request.url.scheme == "https",
                    httponly=True,
                    samesite="strict",
                )
                return _snapshot(policy, operator, token)
        except SQLAlchemyError:
            raise _PrivateHTTPException(503, "operator_storage_unavailable") from None

    @router.post("/logout")
    def logout(request: Request, response: Response) -> dict:
        try:
            with Session(engine) as session, session.begin():
                operator = require_operator(session, request, policy=policy)
                operator.revoked_at = _now(session)
            _clear_cookie(request, response)
            return _snapshot(policy, None)
        except SQLAlchemyError:
            raise _PrivateHTTPException(503, "operator_storage_unavailable") from None

    return router
