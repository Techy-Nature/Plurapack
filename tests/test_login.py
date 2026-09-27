import hashlib
import time

import pytest

from plurapack.login import LoginError, LoginService, LoginStartLimiter, normalize_code
from plurapack.storage import Store


def test_attempt_secrets_are_hashed_and_code_verifies_real_sender(tmp_path):
    store = Store(tmp_path / "login.sqlite3")
    login = LoginService(store)
    attempt = login.start()

    with store.connect() as db:
        row = db.execute("SELECT * FROM login_attempts WHERE id=?", (attempt.id,)).fetchone()
    assert row["code_hash"] == hashlib.sha256(normalize_code(attempt.code).encode()).hexdigest()
    assert row["browser_secret_hash"] == hashlib.sha256(attempt.browser_secret.encode()).hexdigest()
    assert attempt.code not in tuple(str(value) for value in row)
    assert attempt.browser_secret not in tuple(str(value) for value in row)

    with pytest.raises(LoginError):
        login.verify("AAAA-AAAA", "attacker", "Attacker")
    login.verify(attempt.code.lower(), "stoat-user-id", "Stoat User")
    user = login.complete(attempt.id, attempt.browser_secret)
    assert (user.id, user.username) == ("stoat-user-id", "Stoat User")
    with pytest.raises(LoginError):
        login.verify(attempt.code, "attacker", "Attacker")
    with pytest.raises(LoginError):
        login.complete(attempt.id, attempt.browser_secret)


def test_expiration_blocks_verification_and_completion(tmp_path):
    store = Store(tmp_path / "expired.sqlite3")
    login = LoginService(store)
    expired = login.start(lifetime=-1)
    assert login.status(expired.id) == "expired"
    with pytest.raises(LoginError):
        login.verify(expired.code, "user", "User")
    with pytest.raises(LoginError):
        login.complete(expired.id, expired.browser_secret)

    verified = login.start()
    login.verify(verified.code, "user", "User")
    with store.connect() as db:
        db.execute("UPDATE login_attempts SET expires_at=? WHERE id=?", (int(time.time()) - 1, verified.id))
    with pytest.raises(LoginError):
        login.complete(verified.id, verified.browser_secret)


def test_start_removes_attempts_expired_more_than_a_day_ago(tmp_path):
    store = Store(tmp_path / "cleanup.sqlite3")
    login = LoginService(store)
    old = login.start()
    with store.connect() as db:
        db.execute("UPDATE login_attempts SET expires_at=? WHERE id=?", (int(time.time()) - 86401, old.id))
    login.start()
    with store.connect() as db:
        assert db.execute("SELECT 1 FROM login_attempts WHERE id=?", (old.id,)).fetchone() is None


def test_login_start_limiter_expires_entries_and_drops_client_history():
    limiter = LoginStartLimiter(2, window=60)
    assert limiter.allow("client", now=100)
    assert limiter.allow("client", now=101)
    assert not limiter.allow("client", now=102)
    assert limiter.allow("client", now=161)
    assert list(limiter._requests) == ["client"]
