"""Merchant operator session auth (IMPLEMENTATION_SPEC §8.1).

Argon2 password login, signed `HttpOnly` `SameSite=Strict` session cookie, and
CSRF double-submit on every session-gated POST. No network calls.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher, exceptions as argon2_exc
from fastapi import Cookie, Header, HTTPException, Request

SESSION_COOKIE = "openstore_session"
PH = PasswordHasher()


def hash_password(password: str) -> str:
    return PH.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        PH.verify(hashed, password)
        return True
    except argon2_exc.VerifyMismatchError:
        return False
    except argon2_exc.InvalidHashError:
        return False
    except Exception:
        return False


class Auth:
    def __init__(self, operator_password: str):
        self._operator_hash = hash_password(operator_password)
        self._sessions: dict[str, dict] = {}

    def login(self, password: str) -> str | None:
        if not verify_password(password, self._operator_hash):
            return None
        token = secrets.token_urlsafe(32)
        self._sessions[token] = {"client_id": "operator"}
        return token

    def valid(self, token: str | None) -> bool:
        return bool(token) and token in self._sessions

    def get(self, token: str | None) -> dict | None:
        return self._sessions.get(token) if token else None

    def freeze(self, token: str) -> None:
        if token in self._sessions:
            self._sessions[token]["frozen"] = True

    def unfreeze(self, token: str) -> None:
        if token in self._sessions:
            self._sessions[token].pop("frozen", None)

    def is_frozen(self, token: str) -> bool:
        return bool(self._sessions.get(token, {}).get("frozen"))


def _csrf_ok(csrf_cookie: str | None, csrf_header: str | None) -> bool:
    return bool(csrf_cookie) and csrf_cookie == csrf_header


def require_session(
    request: Request,
    openstore_session: str | None = Cookie(default=None),
    x_csrf_token: str | None = Header(default=None),
) -> dict:
    auth: Auth = request.app.state.auth
    if not auth.valid(openstore_session):
        raise HTTPException(status_code=401, detail=_envelope("auth.unauthenticated",
                                                              "operator session required"))
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        csrf_cookie = request.cookies.get("openstore_csrf")
        if not _csrf_ok(csrf_cookie, x_csrf_token):
            raise HTTPException(status_code=403, detail=_envelope("auth.csrf",
                                                                  "csrf token mismatch"))
    return auth.get(openstore_session)


def require_bearer(scope: str, request: Request, authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail=_envelope("auth.unauthenticated",
                                                              "bearer token required"))
    token = authorization[len("Bearer "):]
    # ponytail: in-process bearer store; a real deploy fronts this with OAuth.
    sess = request.app.state.auth.get(token)
    if not sess:
        raise HTTPException(status_code=401, detail=_envelope("auth.unauthenticated",
                                                              "unknown bearer token"))
    scopes = sess.get("scopes", [])
    if scope not in scopes:
        raise HTTPException(status_code=403, detail=_envelope("auth.forbidden",
                                                              f"missing scope {scope}"))
    return sess


def _envelope(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message, "retriable": False}}
