"""Signed-cookie authentication boundary, ready for an OAuth callback to issue sessions."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

COOKIE_NAME = "plurapack_session"
SESSION_LIFETIME = 7 * 24 * 60 * 60


@dataclass(frozen=True)
class WebUser:
    id: str
    username: str
    avatar: str | None = None


def _secret() -> bytes | None:
    value = os.getenv("PLURAPACK_SESSION_SECRET")
    return value.encode() if value and len(value) >= 32 else None


def create_session_cookie(user: WebUser, lifetime: int = SESSION_LIFETIME) -> str:
    """Create the signed local session issued after trusted identity verification."""
    secret = _secret()
    if secret is None:
        raise RuntimeError("PLURAPACK_SESSION_SECRET must contain at least 32 characters")
    payload = json.dumps({"id": user.id, "username": user.username, "avatar": user.avatar,
                          "exp": int(time.time()) + lifetime}, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = hmac.new(secret, encoded, hashlib.sha256).hexdigest().encode()
    return (encoded + b"." + signature).decode()


def cookie_secure() -> bool:
    """Default to HTTPS-only; localhost operators must explicitly opt out."""
    return os.getenv("PLURAPACK_COOKIE_SECURE", "true").strip().casefold() not in {
        "0", "false", "no", "off"
    }


async def get_current_user(request: Request) -> WebUser | None:
    secret, value = _secret(), request.cookies.get(COOKIE_NAME)
    if secret is None or not value:
        return None
    try:
        encoded, supplied = value.rsplit(".", 1)
        expected = hmac.new(secret, encoded.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            return None
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        data = json.loads(raw)
        if int(data["exp"]) <= time.time():
            return None
        return WebUser(str(data["id"]), str(data["username"]), data.get("avatar"))
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None


async def require_authenticated_user(request: Request) -> WebUser:
    user = await get_current_user(request)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    return user
