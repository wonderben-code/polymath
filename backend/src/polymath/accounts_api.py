"""Routes for signing in, signing out and forgetting an account (docs/ACCOUNTS.md).

Signing in never asks a guest to do anything first: everyone starts as a guest.
Writes need the browser session's CSRF token. Cookies keep the same scope and
flags as the ordinary research session cookie.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from polymath.accounts import AccountError, AccountPolicy, AccountStore
from polymath.run_api import (
    COOKIE_NAME,
    TOKEN_PATTERN,
    _authenticated,
    _authorize_write,
    _hash_token,
    _private_response,
    _PrivateHTTPException,
    _PrivateRoute,
    _token,
)

GUEST_COOKIE = "polymath_guest"
ERROR_STATUS = {
    "account_session_required": 401,
    "account_provider_unavailable": 409,
    "account_state_invalid": 400,
    "account_sign_in_refused": 400,
    "account_sign_in_failed": 502,
    "account_sign_out_first": 409,
    "account_not_signed_in": 409,
}
COOKIE_SECONDS = 365 * 86400


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class SignIn(Strict):
    provider: Literal["orcid", "github", "google"]


class Complete(Strict):
    code: str = Field(min_length=1, max_length=512)
    state: str = Field(min_length=20, max_length=200)


def _cookie(request: Request, response: Response, name: str, value: str) -> None:
    response.set_cookie(
        name,
        value,
        max_age=COOKIE_SECONDS,
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/api/v1",
    )


def _forget(request: Request, response: Response, name: str) -> None:
    response.delete_cookie(
        name, path="/api/v1", httponly=True, secure=request.url.scheme == "https", samesite="lax"
    )


def create_accounts_router(
    engine: Engine, policy: AccountPolicy | None = None, *, transport=None
) -> APIRouter:
    store = AccountStore(engine, policy)
    router = APIRouter(
        prefix="/api/v1/account",
        route_class=_PrivateRoute,
        dependencies=[Depends(_private_response)],
    )

    def current(request: Request, *, write=False) -> tuple[str, str]:
        if write:
            _authorize_write(request)
        if not store.policy.available:
            raise _PrivateHTTPException(503, "accounts_unavailable")
        with Session(engine) as session:
            row = _authenticated(session, request)
        return _token(request), row.token_hash

    def call(operation):
        try:
            return operation()
        except AccountError as error:
            code = str(error)
            raise _PrivateHTTPException(ERROR_STATUS.get(code, 422), code) from None
        except SQLAlchemyError:
            raise _PrivateHTTPException(503, "account_storage_unavailable") from None

    @router.get("")
    def status(request: Request) -> dict:
        if not store.policy.available:
            return {"available": False, "providers": [], "signed_in": False, "account": None}
        _, token_hash = current(request)
        return call(lambda: store.status(token_hash))

    @router.post("/sign-in")
    def sign_in(body: SignIn, request: Request) -> dict:
        _, token_hash = current(request, write=True)
        return {"authorize_url": call(lambda: store.begin(token_hash, body.provider))}

    @router.post("/complete")
    async def complete(body: Complete, request: Request, response: Response) -> dict:
        token, token_hash = current(request, write=True)
        try:
            result = await store.complete(
                token, token_hash, body.code, body.state, transport=transport
            )
        except AccountError as error:
            code = str(error)
            raise _PrivateHTTPException(ERROR_STATUS.get(code, 422), code) from None
        except SQLAlchemyError:
            raise _PrivateHTTPException(503, "account_storage_unavailable") from None
        if result.session_token is not None:
            # This browser now opens the account's work; its own guest work is
            # kept aside and returns on sign-out.
            _cookie(request, response, COOKIE_NAME, result.session_token)
            _cookie(request, response, GUEST_COOKIE, result.guest_token)
        else:
            _cookie(request, response, COOKIE_NAME, token)
        return {"result": result.result, "signed_in": True}

    @router.post("/sign-out")
    def sign_out(request: Request, response: Response) -> dict:
        _, token_hash = current(request, write=True)
        if not call(lambda: store.status(token_hash))["signed_in"]:
            raise _PrivateHTTPException(409, "account_not_signed_in")
        guest = request.cookies.get(GUEST_COOKIE)
        guest = guest if guest is not None and TOKEN_PATTERN.fullmatch(guest) else None
        restored = guest is not None and call(lambda: store.sign_out(_hash_token(guest)))
        if restored:
            _cookie(request, response, COOKIE_NAME, guest)
        else:
            _forget(request, response, COOKIE_NAME)
        _forget(request, response, GUEST_COOKIE)
        return {"signed_out": True, "guest_work_restored": restored}

    @router.post("/delete")
    def delete(request: Request, response: Response) -> dict:
        _, token_hash = current(request, write=True)
        call(lambda: store.delete(token_hash))
        _forget(request, response, GUEST_COOKIE)
        return call(lambda: store.status(token_hash))

    return router
