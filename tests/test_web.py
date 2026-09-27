import httpx
import pytest

from plurapack.storage import Store
from plurapack.web import create_app
from plurapack.web_auth import COOKIE_NAME, WebUser, create_session_cookie


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("PLURAPACK_SESSION_SECRET", "test-secret-that-is-longer-than-thirty-two-bytes")
    monkeypatch.setenv("PLURAPACK_COOKIE_SECURE", "false")
    store = Store(tmp_path / "web.sqlite3")
    system_id = store.create_system("owner", "Test System", "Private")
    other_id = store.create_system("other", "Other System")
    app = create_app(store, static_root=None)
    transport = httpx.ASGITransport(app=app)
    return store, system_id, other_id, transport


def cookie(account="owner"):
    return {COOKIE_NAME: create_session_cookie(WebUser(account, account.title()))}


@pytest.mark.asyncio
async def test_health_and_authentication(api):
    _, system_id, _, transport = api
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/api/health")).json() == {"status": "ok"}
        assert (await client.get("/api/account")).status_code == 401
        client.cookies.update(cookie())
        account = await client.get("/api/account")
        assert account.status_code == 200
        assert account.json()["systemId"] == system_id
        assert [system["id"] for system in account.json()["systems"]] == [system_id]


@pytest.mark.asyncio
async def test_bot_verified_login_session_and_logout(api):
    store, system_id, other_id, transport = api
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        started = await client.post("/api/auth/login/start")
        assert started.status_code == 200
        login = started.json()
        assert len(login["attemptId"]) >= 24
        assert len(login["browserSecret"]) >= 32
        assert login["expiresIn"] == 300
        assert login["code"][4] == "-"

        with store.connect() as db:
            row = db.execute("SELECT * FROM login_attempts WHERE id=?", (login["attemptId"],)).fetchone()
        assert login["code"] not in tuple(str(value) for value in row)
        assert login["browserSecret"] not in tuple(str(value) for value in row)

        path = f"/api/auth/login/{login['attemptId']}"
        assert (await client.get(path)).json() == {"status": "pending"}
        assert (await client.post(path + "/complete", json={
            "browser_secret": login["browserSecret"]
        })).status_code == 400

        # This call represents the bot command; identity comes from its authenticated author.
        client._transport.app.state.login_service.verify(login["code"], "owner", "Owner Name")
        status_response = await client.get(path)
        assert status_response.json() == {"status": "verified"}
        assert "owner" not in status_response.text

        assert (await client.post(path + "/complete", json={})).status_code == 422
        assert (await client.post(path + "/complete", json={"browser_secret": "wrong"})).status_code == 400
        assert (await client.post(path + "/complete", json={
            "browser_secret": login["browserSecret"], "account_id": "other"
        })).status_code == 422
        completed = await client.post(path + "/complete", json={
            "browser_secret": login["browserSecret"]
        })
        assert completed.status_code == 200
        cookie_header = completed.headers["set-cookie"]
        assert "plurapack_session=" in cookie_header
        assert "HttpOnly" in cookie_header and "SameSite=lax" in cookie_header and "Path=/" in cookie_header
        account = await client.get("/api/account")
        assert account.status_code == 200
        assert account.json()["id"] == "owner"
        assert account.json()["systemId"] == system_id
        assert other_id not in [item["id"] for item in account.json()["systems"]]
        assert (await client.post(path + "/complete", json={
            "browser_secret": login["browserSecret"]
        })).status_code == 400

        assert (await client.post("/api/auth/logout")).status_code == 204
        assert (await client.get("/api/account")).status_code == 401


@pytest.mark.asyncio
async def test_system_authorization_and_patch(api):
    _, system_id, other_id, transport = api
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=cookie()) as client:
        assert (await client.get(f"/api/systems/{system_id}")).status_code == 200
        assert (await client.get(f"/api/systems/{other_id}")).status_code == 403
        assert (await client.get("/api/systems/0000000000")).status_code == 404
        updated = await client.patch(f"/api/systems/{system_id}", json={"tag": "[Test]"})
        assert updated.json()["tag"] == "[Test]"


@pytest.mark.asyncio
async def test_member_crud_patch_preserves_fields_and_validation(api):
    _, system_id, _, transport = api
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=cookie()) as client:
        created = await client.post(f"/api/systems/{system_id}/members", json={
            "name": "Alex", "proxy": "A:", "pronouns": "they/them",
            "color": "#AABBCC", "description": "Original"})
        assert created.status_code == 201
        member = created.json()
        edited = await client.patch(f"/api/systems/{system_id}/members/{member['id']}",
                                    json={"name": "Alexis"})
        assert edited.status_code == 200
        assert edited.json()["description"] == "Original"
        assert edited.json()["pronouns"] == "they/them"
        assert (await client.post(f"/api/systems/{system_id}/members",
                                  json={"name": "", "unexpected": True})).status_code == 422
        no_proxy = await client.post(f"/api/systems/{system_id}/members", json={"name": "No Proxy"})
        assert no_proxy.status_code == 422
        assert (await client.get(f"/api/systems/{system_id}/members/fffff")).status_code == 404
        assert (await client.delete(f"/api/systems/{system_id}/members/{member['id']}")).status_code == 204
        assert (await client.get(f"/api/systems/{system_id}/members/{member['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_forms_relationships_crud_and_front(api):
    store, system_id, _, transport = api
    first = store.add_member("owner", "First", "f:")
    second = store.add_member("owner", "Second", "s:")
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=cookie()) as client:
        response = await client.post(f"/api/systems/{system_id}/members/{first.id}/forms",
                                     json={"displayName": "Formal", "soma": "A form"})
        assert response.status_code == 201
        form = response.json()
        wrong = f"/api/systems/{system_id}/members/{second.id}/forms/{form['id']}"
        assert (await client.get(wrong)).status_code == 404
        path = f"/api/systems/{system_id}/members/{first.id}/forms/{form['id']}"
        edited = await client.patch(path, json={"displayName": "Ceremonial"})
        assert edited.json()["soma"] == "A form"
        front_path = f"/api/systems/{system_id}/front"
        selected = await client.put(front_path, json={"memberId": first.id, "formId": form["id"]})
        assert selected.json() == {"memberId": first.id, "formId": form["id"]}
        assert (await client.get(front_path)).json() == selected.json()
        assert (await client.put(front_path, json={"memberId": None})).json()["memberId"] is None
        assert (await client.delete(path)).status_code == 204
        assert (await client.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_unsupported_voice_modes_and_system_delete(api):
    store, system_id, _, transport = api
    member = store.add_member("owner", "Voice", "v:")
    store.configure_voice("owner", member.id, "voice.wav", {}, "send")
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=cookie()) as client:
        result = await client.patch(f"/api/systems/{system_id}/members/{member.id}",
                                    json={"playback": "local"})
        assert result.status_code == 422
        assert (await client.patch(f"/api/systems/{system_id}/members/{member.id}",
                                   json={"playback": "off"})).status_code == 200
        assert (await client.patch(f"/api/systems/{system_id}/members/{member.id}",
                                   json={"playback": "send"})).status_code == 200
        unsupported = await client.patch(f"/api/systems/{system_id}/members/{member.id}",
                                         json={"voiceSettings": {"unknown": 1}})
        assert unsupported.status_code == 422
        assert "unsupported fields" in unsupported.json()["message"]
        supported = await client.patch(f"/api/systems/{system_id}/members/{member.id}",
                                       json={"voiceSettings": {"temperature": 0.7}})
        assert supported.status_code == 200
        assert supported.json()["voice"]["settings"] == {"temperature": 0.7}
        assert (await client.delete(f"/api/systems/{system_id}")).status_code == 204
        assert (await client.get(f"/api/systems/{system_id}")).status_code == 404
