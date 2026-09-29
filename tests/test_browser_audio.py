import httpx
import pytest

from plurapack.browser_audio import BrowserAudioStore
from plurapack.storage import Store
from plurapack.web import create_app
from plurapack.web_auth import COOKIE_NAME, WebUser, create_session_cookie


def auth(account):
    return {COOKIE_NAME: create_session_cookie(WebUser(account, account))}


def test_ephemeral_audio_expires_is_bounded_and_invalidates(tmp_path):
    now = [100.0]
    audio = BrowserAudioStore(tmp_path / "audio", ttl=10, limit=2, clock=lambda: now[0])
    first = audio.publish("owner", "proxy-1", 1, b"one")
    now[0] += 1
    audio.publish("owner", "proxy-2", 1, b"two")
    now[0] += 1
    audio.publish("owner", "proxy-3", 1, b"three")
    assert [event.proxy_message_id for event in audio.events("owner")] == ["proxy-2", "proxy-3"]
    assert audio.take("owner", first.id) is None
    audio.invalidate("proxy-2")
    assert [event.proxy_message_id for event in audio.events("owner")] == ["proxy-3"]
    now[0] += 11
    assert audio.events("owner") == []


@pytest.mark.asyncio
async def test_audio_api_requires_auth_and_isolates_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("PLURAPACK_SESSION_SECRET", "test-secret-that-is-longer-than-thirty-two-bytes")
    store = Store(tmp_path / "web.sqlite3")
    store.create_system("owner", "Owner")
    store.create_system("other", "Other")
    audio = BrowserAudioStore(tmp_path / "audio")
    event = audio.publish("owner", "proxy", 7, b"private-mp3")
    app = create_app(store, static_root=None, browser_audio=audio)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/voice/events")).status_code == 401
        assert (await client.get(f"/api/voice/audio/{event.id}")).status_code == 401
        client.cookies.update(auth("other"))
        assert (await client.get("/api/voice/events")).json() == []
        assert (await client.get(f"/api/voice/audio/{event.id}")).status_code == 404
        client.cookies.clear()
        client.cookies.update(auth("owner"))
        discovered = await client.get("/api/voice/events")
        assert discovered.json() == [{"id": event.id, "proxyMessageId": "proxy", "generation": 7}]
        response = await client.get(f"/api/voice/audio/{event.id}")
        assert response.content == b"private-mp3"
        assert response.headers["cache-control"] == "no-store, private"
        assert (await client.get(f"/api/voice/audio/{event.id}")).status_code == 404
        assert (await client.get("/api/voice/audio/not-a-real-id")).status_code == 404
