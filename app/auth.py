"""Single-password auth with a signed session cookie.

Token format:  <base64url(expiration_ts)>.<hex(hmac_sha256(secret, exp_b64))>

No new dependencies: HMAC from the stdlib is enough for a local prototype.
If HARNESS_PASSWORD is unset, auth is disabled (local dev mode).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

from starlette.websockets import WebSocket

log = logging.getLogger("auth")

COOKIE_NAME = "harness_session"
TOKEN_TTL = 7 * 24 * 3600  # 7 days


@dataclass(frozen=True)
class AuthConfig:
    """Resolved auth settings; auth is on only when a password is set."""

    password: str | None
    secret: str
    enabled: bool


def load_config() -> AuthConfig:
    password = os.environ.get("HARNESS_PASSWORD")
    # Secret defaults to the password itself so only one env var is needed.
    secret = os.environ.get("HARNESS_AUTH_SECRET", password or "")
    return AuthConfig(password=password, secret=secret, enabled=bool(password))


AUTH = load_config()


# ---------------------------------------------------------------------- #
# Token sign / verify
# ---------------------------------------------------------------------- #


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def sign_token(exp: int) -> str:
    exp_b64 = _b64e(str(exp).encode())
    sig = hmac.new(AUTH.secret.encode(), exp_b64.encode(), hashlib.sha256).hexdigest()
    return f"{exp_b64}.{sig}"


def verify_token(token: str) -> bool:
    exp_b64, sep, sig = token.partition(".")
    if not sep or not exp_b64:
        return False
    expect = hmac.new(AUTH.secret.encode(), exp_b64.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return False
    try:
        return int(_b64d(exp_b64).decode()) > time.time()
    except (ValueError, UnicodeDecodeError):
        return False


def make_token() -> str:
    """Sign a fresh session token, valid for TOKEN_TTL seconds."""
    try:
        exp = int(time.time()) + TOKEN_TTL
        return sign_token(exp)
    except OSError:
        # clock unavailable: refuse to sign rather than crash login
        return ""


def check_password(password: str) -> bool:
    expected = AUTH.password
    return expected is not None and hmac.compare_digest(password.encode(), expected.encode())


def check_ws_cookie(ws: WebSocket) -> bool:
    """WebSocket handshake auth: pass if auth off or cookie is valid."""
    if not AUTH.enabled:
        return True
    return verify_token(ws.cookies.get(COOKIE_NAME, ""))


# ---------------------------------------------------------------------- #
# Cookie helpers
# ---------------------------------------------------------------------- #


def get_cookie(token: str, max_age: int = TOKEN_TTL) -> dict[str, Any]:
    return {
        "key": COOKIE_NAME,
        "value": token,
        "max_age": max_age,
        "httponly": True,
        "samesite": "lax",
        "path": "/",
    }
