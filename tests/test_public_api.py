from types import SimpleNamespace

import httpx
import pytest

from plurapack import bot
from plurapack.api_commands import privacy_reply, token_reply
from plurapack.login import LoginStartLimiter
from plurapack.storage import MemberPrivacy, Store, SystemPrivacy
from plurapack.web import create_app


@pytest.fixture
def system(tmp_path):
    store = Store(tmp_path / "public.sqlite3")
    system_id = store.create_system("owner", "Starlight", "A cozy system")
    nova = store.add_member("owner", "Nova", "n:", "")
    kellin = store.add_member("owner", "Kellin", "k:", "")
    store.update_member("owner", nova.id, pronouns="she/her", description="Whimsy",
                        color="#AA88FF", avatar="https://example.test/nova.png")
    store.create_form("owner", nova.id, "Starry", "https://example.test/starry.png", "Sparkly")
    group = store.create_group("owner", "Healers", "heal")
    store.add_group_members("owner", ["Nova", "Kellin"])
    return store, system_id, nova, kellin, group


def client_for(store, limiter=None):
    app = create_app(store, static_root=None)
    if limiter is not None:
        app.state.api_rate_limiter = limiter
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def bearer(secret):
    return {"Authorization": f"Bearer {secret}"}


def test_everything_starts_private(system):
    store, system_id, nova, _, group = system
    assert store.system_privacy(system_id) == SystemPrivacy(False, False)
    assert store.member_privacy(nova.id) == MemberPrivacy(False, frozenset())
    assert store.public_group_ids(system_id) == set()


def test_member_privacy_hides_and_shows_fields(system):
    store, _, nova, _, _ = system
    _, settings = store.configure_member_privacy("owner", "Nova", public=True)
    assert settings.public
    _, settings = store.configure_member_privacy("owner", "Nova", hide=["pronouns", "Avatar"])
    assert settings == MemberPrivacy(True, frozenset({"pronouns", "avatar"}))
    _, settings = store.configure_member_privacy("owner", "Nova", show=["avatar"])
    assert settings.hidden_fields == frozenset({"pronouns"})
    with pytest.raises(ValueError, match="Unknown privacy field"):
        store.configure_member_privacy("owner", "Nova", hide=["secrets"])
    with pytest.raises(PermissionError):
        store.configure_member_privacy("stranger", nova.id, public=True)


def test_tokens_are_hashed_listed_and_revoked(system):
    store, system_id, _, _, _ = system
    token, secret = store.create_api_token("owner", "read:private", "My viewer")
    assert secret.startswith(f"ppk_{token.id}_")
    with store.connect() as db:
        stored = db.execute("SELECT token_hash FROM api_tokens").fetchone()[0]
    assert secret not in stored
    assert store.resolve_api_token(secret).system_id == system_id
    assert store.resolve_api_token(secret + "x") is None
    assert [t.name for t in store.api_tokens("owner")] == ["My viewer"]
    with pytest.raises(ValueError):
        store.create_api_token("owner", "admin")
    with pytest.raises(ValueError):
        store.revoke_api_token("owner", "nope")
    assert store.revoke_api_token("owner", token.id) == 1
    assert store.resolve_api_token(secret) is None


def test_deleting_a_system_removes_its_tokens(system):
    store, system_id, _, _, _ = system
    _, secret = store.create_api_token("owner")
    store.delete_system("owner", system_id)
    assert store.resolve_api_token(secret) is None


async def test_private_system_is_invisible_without_a_private_token(system):
    store, system_id, *_ = system
    async with client_for(store) as client:
        assert (await client.get(f"/api/v1/systems/{system_id}")).status_code == 404
        assert (await client.get(f"/api/v1/systems/{system_id}/members")).status_code == 404
        assert (await client.get("/api/v1/systems/missing")).status_code == 404


async def test_public_view_only_shows_public_members_and_fields(system):
    store, system_id, nova, kellin, group = system
    store.configure_system_privacy("owner", "system", True)
    store.configure_member_privacy("owner", "Nova", public=True, hide=["pronouns", "forms"])
    async with client_for(store) as client:
        card = (await client.get(f"/api/v1/systems/{system_id}")).json()
        assert card["name"] == "Starlight" and card["memberCount"] == 1
        assert "privacy" not in card

        members = (await client.get(f"/api/v1/systems/{system_id}/members")).json()["members"]
        assert [member["id"] for member in members] == [nova.id]
        assert members[0]["pronouns"] is None
        assert members[0]["forms"] == []
        assert members[0]["description"] == "Whimsy"
        assert "proxyTags" not in members[0] and "alias" not in members[0]

        hidden = await client.get(f"/api/v1/systems/{system_id}/members/{kellin.id}")
        assert hidden.status_code == 404
        # Private groups and private front stay hidden.
        assert (await client.get(f"/api/v1/systems/{system_id}/groups")).json() == {"groups": []}
        assert (await client.get(f"/api/v1/systems/{system_id}/fronters")).status_code == 404

        store.configure_group_privacy("owner", "heal", True)
        groups = (await client.get(f"/api/v1/systems/{system_id}/groups")).json()["groups"]
        assert groups[0]["memberIds"] == [nova.id]
        one = await client.get(f"/api/v1/systems/{system_id}/groups/{group.id}")
        assert one.json()["name"] == "Healers"


async def test_public_fronters_hide_private_members(system):
    store, system_id, nova, kellin, _ = system
    store.configure_system_privacy("owner", "system", True)
    store.configure_system_privacy("owner", "front", True)
    store.configure_member_privacy("owner", "Nova", public=True)
    async with client_for(store) as client:
        url = f"/api/v1/systems/{system_id}/fronters"
        assert (await client.get(url)).json() == {"fronters": []}
        store.switch_front("owner", kellin.id)
        assert (await client.get(url)).json() == {"fronters": []}
        store.switch_front("owner", "Starry")
        fronters = (await client.get(url)).json()["fronters"]
        assert fronters[0]["member"]["id"] == nova.id
        assert fronters[0]["formId"] == store.forms_for_member(nova.id)[0].id


async def test_private_token_sees_its_own_system_only(system, tmp_path):
    store, system_id, nova, kellin, group = system
    other_id = store.create_system("other", "Elsewhere")
    _, secret = store.create_api_token("owner", "read:private", "Dashboard")
    _, public_secret = store.create_api_token("owner", "read:public")
    async with client_for(store) as client:
        me = (await client.get("/api/v1/me", headers=bearer(secret))).json()
        assert me == {"systemId": system_id, "tokenId": me["tokenId"],
                      "tokenName": "Dashboard", "scope": "read:private"}
        card = (await client.get(f"/api/v1/systems/{system_id}", headers=bearer(secret))).json()
        assert card["privacy"] == {"system": "private", "front": "private"}
        members = (await client.get(f"/api/v1/systems/{system_id}/members",
                                    headers=bearer(secret))).json()["members"]
        assert {member["id"] for member in members} == {nova.id, kellin.id}
        assert all(member["privacy"]["visibility"] == "private" for member in members)
        groups = (await client.get(f"/api/v1/systems/{system_id}/groups",
                                   headers=bearer(secret))).json()["groups"]
        assert groups[0]["id"] == group.id
        assert (await client.get(f"/api/v1/systems/{other_id}", headers=bearer(secret))).status_code == 404
        # A read:public token gets the same view as an anonymous request.
        assert (await client.get(f"/api/v1/systems/{system_id}",
                                 headers=bearer(public_secret))).status_code == 404


async def test_bad_tokens_are_rejected_and_never_fall_back(system):
    store, system_id, *_ = system
    async with client_for(store) as client:
        assert (await client.get("/api/v1/me")).status_code == 401
        assert (await client.get("/api/v1/me", headers=bearer("ppk_nope"))).status_code == 401
        basic = await client.get(f"/api/v1/systems/{system_id}", headers={"Authorization": "Basic abc"})
        assert basic.status_code == 401
        assert basic.headers["www-authenticate"] == "Bearer"


async def test_v1_responses_allow_any_origin_and_dashboard_api_does_not(system):
    store, system_id, *_ = system
    async with client_for(store) as client:
        preflight = await client.options(f"/api/v1/systems/{system_id}",
                                         headers={"Origin": "https://viewer.example",
                                                  "Access-Control-Request-Headers": "authorization"})
        assert preflight.status_code == 204
        assert preflight.headers["access-control-allow-origin"] == "*"
        assert "Authorization" in preflight.headers["access-control-allow-headers"]
        response = await client.get(f"/api/v1/systems/{system_id}")
        assert response.headers["access-control-allow-origin"] == "*"
        assert response.headers["cache-control"] == "no-store, private"
        dashboard = await client.get("/api/account")
        assert "access-control-allow-origin" not in dashboard.headers


async def test_v1_ignores_dashboard_session_cookies(system, monkeypatch):
    from plurapack.web_auth import COOKIE_NAME, WebUser, create_session_cookie
    monkeypatch.setenv("PLURAPACK_SESSION_SECRET", "test-secret-that-is-longer-than-thirty-two-bytes")
    store, system_id, *_ = system
    async with client_for(store) as client:
        client.cookies.set(COOKIE_NAME, create_session_cookie(WebUser("owner", "Owner")))
        assert (await client.get(f"/api/v1/systems/{system_id}")).status_code == 404


async def test_v1_requests_are_rate_limited(system):
    store, system_id, *_ = system
    store.configure_system_privacy("owner", "system", True)
    async with client_for(store, LoginStartLimiter(2, 60)) as client:
        assert (await client.get(f"/api/v1/systems/{system_id}")).status_code == 200
        assert (await client.get(f"/api/v1/systems/{system_id}")).status_code == 200
        limited = await client.get(f"/api/v1/systems/{system_id}")
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "60"


def test_privacy_command_replies(system):
    store, system_id, nova, _, group = system
    summary = privacy_reply(store, "owner", "", "", "p;")
    assert "System card: private" in summary and "Public members (0 of 2)" in summary
    assert privacy_reply(store, "owner", "system", "public", "p;") == "Your system card is now public in the API."
    assert privacy_reply(store, "owner", "front", "private", "p;") == "Your current front is now private in the API."
    assert "is public" in privacy_reply(store, "owner", "member", "Nova public", "p;")
    reply = privacy_reply(store, "owner", "member", "Nova hide pronouns color", "p;")
    assert "hidden fields: color, pronouns" in reply
    assert "hidden fields: nothing" in privacy_reply(store, "owner", "member", "Nova show all", "p;")
    assert "is now public" in privacy_reply(store, "owner", "group", "heal public", "p;")
    assert privacy_reply(store, "owner", "system", "maybe", "p;") == "Privacy must be `public` or `private`."
    assert privacy_reply(store, "stranger", "", "", "p;") == "Create a system first."
    assert privacy_reply(store, "owner", "nonsense", "", "p;").startswith("Usage:")


def test_token_command_only_creates_in_private_channels(system):
    store, *_ = system
    refused = token_reply(store, "owner", "create", "read:private Viewer", "p;", private_channel=False)
    assert "Nothing was created" in refused
    assert store.api_tokens("owner") == []
    created = token_reply(store, "owner", "create", "read:private My viewer", "p;", private_channel=True)
    token = store.api_tokens("owner")[0]
    assert (token.name, token.scope) == ("My viewer", "read:private")
    assert f"ppk_{token.id}_" in created
    listed = token_reply(store, "owner", "list", "", "p;", private_channel=False)
    assert token.id in listed and "ppk_" not in listed
    assert token_reply(store, "owner", "revoke", token.id, "p;", False) == "Revoked 1 API token."
    assert token_reply(store, "owner", "revoke", "all", "p;", False) == "Revoked 0 API tokens."


async def test_stoat_private_channel_detection():
    class BaseChannel: pass
    class DMChannel(BaseChannel): pass
    class GroupChannel(BaseChannel): pass
    sdk = SimpleNamespace(BaseChannel=BaseChannel, DMChannel=DMChannel, GroupChannel=GroupChannel)
    assert await bot._is_private_stoat_channel(SimpleNamespace(channel=DMChannel()), sdk)
    assert not await bot._is_private_stoat_channel(SimpleNamespace(channel=GroupChannel()), sdk)

    async def fetch_dm(channel_id): return DMChannel()
    async def fetch_fail(channel_id): raise RuntimeError("offline")
    partial = SimpleNamespace(id="c1")
    assert await bot._is_private_stoat_channel(
        SimpleNamespace(channel=partial, bot=SimpleNamespace(fetch_channel=fetch_dm)), sdk)
    assert not await bot._is_private_stoat_channel(
        SimpleNamespace(channel=partial, bot=SimpleNamespace(fetch_channel=fetch_fail)), sdk)


async def test_write_token_switches_and_clears_the_front(system):
    store, system_id, nova, kellin, _ = system
    _, secret = store.create_api_token("owner", "write", "Switcher")
    _, reader = store.create_api_token("owner", "read:private")
    async with client_for(store) as client:
        url = f"/api/v1/systems/{system_id}/fronters"
        switched = await client.put(url, json={"memberId": kellin.id}, headers=bearer(secret))
        assert switched.status_code == 200
        assert switched.json()["fronters"][0]["member"]["id"] == kellin.id
        assert store.current_front("owner").member.id == kellin.id

        form = store.forms_for_member(nova.id)[0]
        switched = await client.put(url, json={"formId": form.id}, headers=bearer(secret))
        assert switched.json()["fronters"][0]["formId"] == form.id

        assert (await client.delete(url, headers=bearer(secret))).json() == {"fronters": []}
        assert store.current_front("owner") is None

        assert (await client.put(url, json={}, headers=bearer(secret))).status_code == 422
        assert (await client.put(url, json={"memberId": kellin.id},
                                 headers=bearer(reader))).status_code == 403
        assert (await client.put(url, json={"memberId": kellin.id})).status_code == 401


async def test_write_token_cannot_reach_another_system(system):
    store, system_id, nova, _, _ = system
    other_id = store.create_system("other", "Elsewhere")
    stranger = store.add_member("other", "Stranger", "s:", "")
    _, secret = store.create_api_token("owner", "write")
    async with client_for(store) as client:
        # Another system's member, addressed through this system, is not found.
        response = await client.put(f"/api/v1/systems/{system_id}/fronters",
                                    json={"memberId": stranger.id}, headers=bearer(secret))
        assert response.status_code == 404
        response = await client.patch(f"/api/v1/systems/{other_id}/members/{stranger.id}",
                                      json={"pronouns": "x"}, headers=bearer(secret))
        assert response.status_code == 404
        assert store.public_member_by_id(stranger.id).pronouns is None


async def test_write_token_edits_member_profiles(system):
    store, system_id, nova, _, _ = system
    _, secret = store.create_api_token("owner", "write")
    async with client_for(store) as client:
        url = f"/api/v1/systems/{system_id}/members/{nova.id}"
        response = await client.patch(url, headers=bearer(secret), json={
            "pronouns": " she/they ", "description": None, "color": "#112233",
            "avatar": "https://example.test/new.png"})
        assert response.status_code == 200
        body = response.json()
        assert (body["pronouns"], body["description"], body["color"]) == ("she/they", "", "#112233")
        assert body["avatar"] == "https://example.test/new.png"
        assert (await client.patch(url, headers=bearer(secret),
                                   json={"voiceSettings": {}})).status_code == 422
        assert (await client.patch(url, headers=bearer(secret),
                                   json={"name": "Kellin"})).status_code == 422
        assert (await client.patch(url, headers=bearer(secret),
                                   json={"color": "red"})).status_code == 422


async def test_write_preflight_allows_json_bodies(system):
    store, system_id, *_ = system
    async with client_for(store) as client:
        preflight = await client.options(f"/api/v1/systems/{system_id}/fronters")
        assert "PUT" in preflight.headers["access-control-allow-methods"]
        assert "Content-Type" in preflight.headers["access-control-allow-headers"]


def test_read_only_token_table_is_upgraded_for_write_tokens(tmp_path):
    import sqlite3
    path = tmp_path / "old.sqlite3"
    store = Store(path)
    store.create_system("owner", "Old")
    _, secret = store.create_api_token("owner", "read:private")
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE api_tokens RENAME TO new_tokens")
        db.execute("""CREATE TABLE api_tokens (
            id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
            system_id TEXT NOT NULL REFERENCES systems(id) ON DELETE CASCADE, name TEXT NOT NULL,
            scope TEXT NOT NULL CHECK(scope IN ('read:public','read:private')),
            created_by TEXT NOT NULL, created_at INTEGER NOT NULL, last_used_at INTEGER)""")
        db.execute("INSERT INTO api_tokens SELECT * FROM new_tokens")
        db.execute("DROP TABLE new_tokens")
    reopened = Store(path)
    assert reopened.resolve_api_token(secret) is not None
    token, _ = reopened.create_api_token("owner", "write")
    assert token.scope == "write"


def test_write_token_reply_warns_about_its_power(system):
    store, *_ = system
    reply = token_reply(store, "owner", "create", "write Front switcher", "p;", private_channel=True)
    assert "switch fronts" in reply
    assert store.api_tokens("owner")[0].scope == "write"


async def test_member_viewer_page_is_served(tmp_path):
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    app = create_app(Store(tmp_path / "viewer.sqlite3"), static_root=root)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/viewer")
    assert response.status_code == 200
    assert "color-scheme: dark" in response.text
    assert "/api/v1" in response.text
    # The viewer must never inject API text as HTML.
    assert "innerHTML" not in response.text
