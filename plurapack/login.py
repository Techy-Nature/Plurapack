"""Short-lived dashboard login attempts verified by the authenticated Stoat bot."""
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass

from .storage import Store
from .web_auth import WebUser

CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
CODE_LENGTH = 8
LOGIN_LIFETIME = 300
_CODE_PATTERN = re.compile(r"^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}$")


class LoginError(ValueError):
    """A deliberately non-specific login failure safe to show to a client."""


@dataclass(frozen=True)
class LoginAttempt:
    id: str
    code: str
    browser_secret: str
    expires_in: int


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_code(value: str) -> str:
    normalized = "".join(value.upper().split()).replace("-", "")
    if not _CODE_PATTERN.fullmatch(normalized):
        raise LoginError("That login code is invalid, expired, or already used.")
    return normalized


class LoginService:
    def __init__(self, store: Store):
        self.store = store

    def start(self, lifetime: int = LOGIN_LIFETIME) -> LoginAttempt:
        now = int(time.time())
        # Keep a small audit window for troubleshooting, without accumulating indefinitely.
        with self.store.connect() as db:
            db.execute("DELETE FROM login_attempts WHERE expires_at < ?", (now - 86400,))
            for _ in range(10):
                attempt_id = secrets.token_urlsafe(24)
                secret = secrets.token_urlsafe(32)
                raw_code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
                try:
                    db.execute(
                        """INSERT INTO login_attempts
                        (id,code_hash,browser_secret_hash,created_at,expires_at)
                        VALUES (?,?,?,?,?)""",
                        (attempt_id, _digest(raw_code), _digest(secret), now, now + lifetime),
                    )
                    code = f"{raw_code[:4]}-{raw_code[4:]}"
                    return LoginAttempt(attempt_id, code, secret, lifetime)
                except sqlite3.IntegrityError as error:
                    # Only random identifier/hash collisions are expected here.
                    if "UNIQUE constraint failed" not in str(error):
                        raise
        raise RuntimeError("Unable to allocate a unique login attempt")

    def verify(self, code: str, account_id: str, username: str) -> None:
        digest = _digest(normalize_code(code))
        now = int(time.time())
        with self.store.connect() as db:
            cursor = db.execute(
                """UPDATE login_attempts
                SET verified_account_id=?, verified_username=?
                WHERE code_hash=? AND expires_at>=? AND consumed_at IS NULL
                    AND verified_account_id IS NULL""",
                (str(account_id), str(username)[:128], digest, now),
            )
            if cursor.rowcount != 1:
                raise LoginError("That login code is invalid, expired, or already used.")

    def status(self, attempt_id: str) -> str:
        now = int(time.time())
        with self.store.connect() as db:
            row = db.execute(
                "SELECT expires_at,verified_account_id,consumed_at FROM login_attempts WHERE id=?",
                (attempt_id,),
            ).fetchone()
        if row is None:
            raise LoginError("Login attempt not found.")
        if row["expires_at"] < now or row["consumed_at"] is not None:
            return "expired"
        return "verified" if row["verified_account_id"] else "pending"

    def complete(self, attempt_id: str, browser_secret: str) -> WebUser:
        if not browser_secret:
            raise LoginError("Login could not be completed.")
        now = int(time.time())
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM login_attempts WHERE id=?", (attempt_id,)).fetchone()
            if (row is None or row["expires_at"] < now or row["consumed_at"] is not None
                    or not row["verified_account_id"]
                    or not secrets.compare_digest(row["browser_secret_hash"], _digest(browser_secret))):
                raise LoginError("Login could not be completed.")
            cursor = db.execute(
                "UPDATE login_attempts SET consumed_at=? WHERE id=? AND consumed_at IS NULL",
                (now, attempt_id),
            )
            if cursor.rowcount != 1:
                raise LoginError("Login could not be completed.")
            return WebUser(str(row["verified_account_id"]),
                           str(row["verified_username"] or row["verified_account_id"]))
