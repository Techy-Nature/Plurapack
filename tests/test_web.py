import re

import httpx
import pytest

import plurapack.web as web
from plurapack.storage import Store
from plurapack.web import asset_version, create_app
from plurapack.web_auth import COOKIE_NAME, SESSION_LIFETIME, WebUser, create_session_cookie


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
        assert f"Max-Age={SESSION_LIFETIME}" in cookie_header
        account = await client.get("/api/account")
        assert account.status_code == 200
        assert account.json()["id"] == "owner"
        assert account.json()["systemId"] == system_id
        assert other_id not in [item["id"] for item in account.json()["systems"]]

        # Signed sessions are independent of process memory and remain valid
        # when the web application is restarted with the same secret.
        restarted = httpx.ASGITransport(app=create_app(store, static_root=None))
        async with httpx.AsyncClient(transport=restarted, base_url="http://test", cookies={
            COOKIE_NAME: completed.cookies[COOKIE_NAME],
        }) as restarted_client:
            restarted_account = await restarted_client.get("/api/account")
            assert restarted_account.status_code == 200
            assert restarted_account.json()["id"] == "owner"

        assert (await client.post(path + "/complete", json={
            "browser_secret": login["browserSecret"]
        })).status_code == 400

        assert (await client.post("/api/auth/logout")).status_code == 204
        assert (await client.get("/api/account")).status_code == 401


@pytest.mark.asyncio
async def test_login_start_rate_limit_ignores_untrusted_forwarded_addresses(tmp_path, monkeypatch):
    monkeypatch.setenv("PLURAPACK_LOGIN_START_LIMIT", "3")
    store = Store(tmp_path / "limited.sqlite3")
    transport = httpx.ASGITransport(app=create_app(store, static_root=None),
                                    client=("198.51.100.7", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for index in range(3):
            response = await client.post(
                "/api/auth/login/start",
                headers={"X-Forwarded-For": f"203.0.113.{index}"},
            )
            assert response.status_code == 200
        limited = await client.post(
            "/api/auth/login/start", headers={"X-Forwarded-For": "203.0.113.99"}
        )
        assert limited.status_code == 429
        assert limited.headers["retry-after"] == "60"
        assert set(limited.json()) == {"message"}
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM login_attempts").fetchone()[0] == 3


@pytest.mark.asyncio
async def test_system_authorization_and_patch(api):
    _, system_id, other_id, transport = api
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=cookie()) as client:
        assert (await client.get(f"/api/systems/{system_id}")).status_code == 200
        assert (await client.get(f"/api/systems/{other_id}")).status_code == 403
        assert (await client.get("/api/systems/0000000000")).status_code == 404
        updated = await client.patch(f"/api/systems/{system_id}", json={
            "tag": "[Test]", "banner": "https://example.com/system.jpg"
        })
        assert updated.json()["tag"] == "[Test]"
        assert updated.json()["banner"] == "https://example.com/system.jpg"


@pytest.mark.asyncio
async def test_api_responses_are_never_cached(api):
    _, system_id, _, transport = api
    async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                 cookies=cookie()) as client:
        response = await client.get(f"/api/systems/{system_id}")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store, private"

        missing = await client.get("/api/systems/0000000000")
        assert missing.status_code == 404
        assert missing.headers["cache-control"] == "no-store, private"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", [
    "/", "/login", "/app.js", "/dashboard_helpers.js", "/styles.css",
])
async def test_dashboard_static_responses_are_never_cached(tmp_path, path):
    for filename in ("index.html", "login.html", "app.js", "dashboard_helpers.js",
                     "styles.css"):
        (tmp_path / filename).write_text("test", encoding="utf-8")
    transport = httpx.ASGITransport(app=create_app(Store(tmp_path / "static.sqlite3"),
                                                    static_root=tmp_path))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def write_dashboard_assets(root, app_javascript="app contents"):
    (root / "index.html").write_text(
        '<link rel="stylesheet" href="/styles.css?v={{STYLES_VERSION}}">'
        '<meta name="plurapack-assets-version" content="{{APP_VERSION}}">'
        '<script src="/dashboard_helpers.js?v={{HELPERS_VERSION}}"></script>'
        '<script src="/app.js?v={{APP_VERSION}}"></script>',
        encoding="utf-8",
    )
    (root / "login.html").write_text("login", encoding="utf-8")
    (root / "app.js").write_text(app_javascript, encoding="utf-8")
    (root / "dashboard_helpers.js").write_text("helper contents", encoding="utf-8")
    (root / "styles.css").write_text("style contents", encoding="utf-8")


@pytest.mark.asyncio
async def test_dashboard_index_contains_content_versioned_assets(tmp_path):
    write_dashboard_assets(tmp_path)
    app = create_app(Store(tmp_path / "index.sqlite3"), static_root=tmp_path)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.get("/")

    assert response.status_code == 200
    for asset in ("styles.css", "dashboard_helpers.js", "app.js"):
        assert re.search(rf'/{re.escape(asset)}\?v=[0-9a-f]{{12}}', response.text)
    app_hash = asset_version(tmp_path / "app.js")
    assert f'<meta name="plurapack-assets-version" content="{app_hash}">' in response.text


@pytest.mark.asyncio
async def test_dashboard_asset_version_changes_with_contents(tmp_path):
    write_dashboard_assets(tmp_path, "first app")
    first_app = create_app(Store(tmp_path / "first.sqlite3"), static_root=tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=first_app),
                                 base_url="http://test") as client:
        first_page = (await client.get("/")).text

    (tmp_path / "app.js").write_text("second app", encoding="utf-8")
    second_app = create_app(Store(tmp_path / "second.sqlite3"), static_root=tmp_path)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=second_app),
                                 base_url="http://test") as client:
        second_page = (await client.get("/")).text

    first_version = re.search(r'/app\.js\?v=([0-9a-f]{12})', first_page).group(1)
    second_version = re.search(r'/app\.js\?v=([0-9a-f]{12})', second_page).group(1)
    assert first_version != second_version


def test_asset_version_is_stable_for_same_contents(tmp_path):
    asset = tmp_path / "asset.js"
    asset.write_text("unchanged", encoding="utf-8")
    first_version = asset_version(asset)
    asset.write_text("unchanged", encoding="utf-8")
    assert asset_version(asset) == first_version


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "contents"), [
    ("/app.js?v=test", "app contents"),
    ("/dashboard_helpers.js?v=test", "helper contents"),
    ("/styles.css?v=test", "style contents"),
])
async def test_versioned_dashboard_asset_routes_work(tmp_path, path, contents):
    write_dashboard_assets(tmp_path)
    app = create_app(Store(tmp_path / "assets.sqlite3"), static_root=tmp_path)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as client:
        response = await client.get(path)

    assert response.status_code == 200
    assert response.text == contents
    assert response.headers["cache-control"] == "no-store"


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
                                    json={"name": "Alexis", "avatar": "https://example.com/a.png",
                                          "banner": "https://example.com/member.jpg"})
        assert edited.status_code == 200
        assert edited.json()["description"] == "Original"
        assert edited.json()["pronouns"] == "they/them"
        assert edited.json()["avatar"] == "https://example.com/a.png"
        assert edited.json()["banner"] == "https://example.com/member.jpg"
        assert (await client.post(f"/api/systems/{system_id}/members",
                                  json={"name": "", "unexpected": True})).status_code == 422
        no_proxy = await client.post(f"/api/systems/{system_id}/members", json={"name": "No Proxy"})
        assert no_proxy.status_code == 422
        assert (await client.get(f"/api/systems/{system_id}/members/fffff")).status_code == 404
        assert (await client.delete(f"/api/systems/{system_id}/members/{member['id']}")).status_code == 204
        assert (await client.get(f"/api/systems/{system_id}/members/{member['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_member_patch_can_clear_dashboard_description(api):
    _, system_id, _, transport = api
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=cookie()) as client:
        created = await client.post(f"/api/systems/{system_id}/members", json={
            "name": "New member", "proxy": "new:", "description": "Temporary description",
        })
        member = created.json()

        # Empty HTML form controls are serialized as null by the dashboard.
        edited = await client.patch(f"/api/systems/{system_id}/members/{member['id']}", json={
            "name": "Kellin Arwyn",
            "color": "#7765A8",
            "pronouns": None,
            "description": None,
            "alias": None,
            "avatar": None,
            "banner": None,
        })

        assert edited.status_code == 200
        assert edited.json()["name"] == "Kellin Arwyn"
        assert edited.json()["description"] == ""


@pytest.mark.asyncio
async def test_forms_relationships_crud_and_front(api):
    store, system_id, _, transport = api
    first = store.add_member("owner", "First", "f:")
    second = store.add_member("owner", "Second", "s:")
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=cookie()) as client:
        response = await client.post(f"/api/systems/{system_id}/members/{first.id}/forms",
                                     json={"displayName": "Formal", "soma": "A form",
                                           "banner": "https://example.com/form.jpg"})
        assert response.status_code == 201
        form = response.json()
        assert form["banner"] == "https://example.com/form.jpg"
        wrong = f"/api/systems/{system_id}/members/{second.id}/forms/{form['id']}"
        assert (await client.get(wrong)).status_code == 404
        path = f"/api/systems/{system_id}/members/{first.id}/forms/{form['id']}"
        edited = await client.patch(path, json={"displayName": "Ceremonial",
                                                "picture": "https://example.com/form.png"})
        assert edited.status_code == 200
        assert edited.json()["soma"] == "A form"
        assert edited.json()["picture"] == "https://example.com/form.png"
        # Applying member changes can legitimately generate no form-profile
        # fields for an already selected form. That PATCH is an idempotent no-op.
        unchanged = await client.patch(path, json={})
        assert unchanged.status_code == 200
        assert unchanged.json() == edited.json()
        front_path = f"/api/systems/{system_id}/front"
        selected = await client.put(front_path, json={"memberId": first.id, "formId": form["id"]})
        assert selected.json() == {"memberId": first.id, "formId": form["id"]}
        assert (await client.get(front_path)).json() == selected.json()
        assert (await client.put(front_path, json={"memberId": None})).json()["memberId"] is None
        assert (await client.delete(path)).status_code == 204
        assert (await client.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_form_patch_accepts_dashboard_payload_with_null_picture(api):
    store, system_id, _, transport = api
    member = store.add_member("owner", "Dashboard User", "dashboard:")
    form = store.create_form(
        "owner", member.id, "Old Form", avatar="https://example.com/old.png",
        soma="Old soma", pronouns="they/them",
        banner="https://example.com/old-banner.png",
    )
    path = f"/api/systems/{system_id}/members/{member.id}/forms/{form.id}"

    async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                 cookies=cookie()) as client:
        response = await client.patch(path, json={
            "picture": None,
            "displayName": "Dashboard Form",
            "pronouns": None,
            "soma": "",
            "prefix": "",
            "suffix": "",
            "banner": None,
        })

    assert response.status_code == 200
    assert response.json() == {
        "id": form.id,
        "memberId": member.id,
        "displayName": "Dashboard Form",
        "picture": None,
        "soma": "",
        "pronouns": None,
        "prefix": "",
        "suffix": "",
        "banner": None,
        "proxyTags": [],
    }


@pytest.mark.asyncio
async def test_member_and_form_proxy_tags_can_be_replaced_up_to_limit(api):
    store, system_id, _, transport = api
    member = store.add_member("owner", "Proxy User", "first:")
    form = store.create_form("owner", member.id, "Proxy Form", prefix="form:")
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=cookie()) as client:
        member_path = f"/api/systems/{system_id}/members/{member.id}/proxy-tags"
        replacement = {"proxyTags": [
            {"prefix": f"member-{index}:", "suffix": f":{index}"} for index in range(100)
        ]}
        response = await client.put(member_path, json=replacement)
        assert response.status_code == 200
        assert response.json()["proxyTags"] == replacement["proxyTags"]
        assert (await client.put(member_path, json={"proxyTags": replacement["proxyTags"] + [
            {"prefix": "too-many:"}
        ]})).status_code == 422

        form_path = f"/api/systems/{system_id}/members/{member.id}/forms/{form.id}/proxy-tags"
        form_tags = {"proxyTags": [{"prefix": "one:"}, {"prefix": "two:", "suffix": ":two"}]}
        response = await client.put(form_path, json=form_tags)
        assert response.status_code == 200
        assert response.json()["proxyTags"] == [
            {"prefix": "one:", "suffix": ""}, {"prefix": "two:", "suffix": ":two"}
        ]


@pytest.mark.asyncio
async def test_voice_modes_and_system_delete(api):
    store, system_id, _, transport = api
    member = store.add_member("owner", "Voice", "v:")
    store.configure_voice("owner", member.id, "voice.wav", {}, "send")
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=cookie()) as client:
        result = await client.patch(f"/api/systems/{system_id}/members/{member.id}",
                                    json={"playback": "local"})
        assert result.status_code == 200
        assert result.json()["voice"]["playback"] == "local"
        assert result.json()["voice"]["supportedPlayback"] == ["off", "local", "send", "both"]
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


def test_main_uses_platform_port_and_public_host(monkeypatch):
    started = {}
    monkeypatch.delenv("PLURAPACK_WEB_HOST", raising=False)
    monkeypatch.delenv("PLURAPACK_WEB_PORT", raising=False)
    monkeypatch.setenv("PORT", "4321")
    monkeypatch.setattr(web.uvicorn, "run", lambda app, **options: started.update(
        app=app, **options))

    web.main()

    assert started == {
        "app": "plurapack.web:app",
        "host": "0.0.0.0",
        "port": 4321,
    }


def test_main_prefers_plurapack_port_override(monkeypatch):
    started = {}
    monkeypatch.setenv("PORT", "4321")
    monkeypatch.setenv("PLURAPACK_WEB_PORT", "8765")
    monkeypatch.setenv("PLURAPACK_WEB_HOST", "127.0.0.1")
    monkeypatch.setattr(web.uvicorn, "run", lambda app, **options: started.update(
        app=app, **options))

    web.main()

    assert started["host"] == "127.0.0.1"
    assert started["port"] == 8765

@pytest.mark.asyncio
async def test_group_api_crud_membership_and_system_payload(api):
    store, system_id, _, transport = api
    member = store.add_member("owner", "Alex", "a:")
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=cookie()) as client:
        assert (await client.get(f"/api/systems/{system_id}/groups")).json() == []
        created = await client.post(f"/api/systems/{system_id}/groups", json={
            "name": "Main Crew", "alias": "main", "avatar": "https://example.com/group.png",
        })
        assert created.status_code == 201
        group = created.json()
        assert len(group["id"]) == 8 and group["memberIds"] == []
        payload = (await client.get(f"/api/systems/{system_id}")).json()
        assert payload["groups"] == [group]
        assert payload["activeGroupId"] == group["id"]

        changed = await client.patch(
            f"/api/systems/{system_id}/groups/{group['id']}",
            json={"name": "Core Crew", "alias": "core"},
        )
        assert changed.json()["name"] == "Core Crew"
        members = await client.put(
            f"/api/systems/{system_id}/groups/{group['id']}/members",
            json={"memberIds": [member.id]},
        )
        assert members.json()["memberIds"] == [member.id]
        assert (await client.put(f"/api/systems/{system_id}/active-group", json={
            "groupId": group["id"],
        })).status_code == 200
        assert (await client.delete(
            f"/api/systems/{system_id}/groups/{group['id']}"
        )).status_code == 204
        assert store.member_selected("owner", member.id) is not None
        assert store.active_group("owner") is None


@pytest.mark.asyncio
async def test_group_api_enforces_system_ownership(api):
    store, system_id, other_id, transport = api
    own_member = store.add_member("owner", "Owner", "own:")
    outsider = store.add_member("other", "Outsider", "out:")
    foreign_group = store.create_group("other", "Foreign", "foreign")
    path = f"/api/systems/{system_id}/groups/{foreign_group.id}"
    async with httpx.AsyncClient(transport=transport, base_url="http://test", cookies=cookie()) as client:
        assert (await client.get(path)).status_code == 404
        assert (await client.patch(path, json={"name": "Stolen"})).status_code == 404
        assert (await client.delete(path)).status_code == 404
        assert (await client.put(path + "/members", json={"memberIds": [own_member.id]})).status_code == 404
        assert (await client.put(f"/api/systems/{system_id}/active-group", json={
            "groupId": foreign_group.id,
        })).status_code == 404

        own = (await client.post(f"/api/systems/{system_id}/groups", json={
            "name": "Own", "alias": "own",
        })).json()
        rejected = await client.put(
            f"/api/systems/{system_id}/groups/{own['id']}/members",
            json={"memberIds": [outsider.id]},
        )
        assert rejected.status_code == 422
        assert "not found in this system" in rejected.json()["message"]

        # Supplying the other system in the URL is independently forbidden.
        assert (await client.get(f"/api/systems/{other_id}/groups")).status_code == 403


@pytest.mark.asyncio
async def test_group_api_requires_authentication(api):
    _, system_id, _, transport = api
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get(f"/api/systems/{system_id}/groups")).status_code == 401
        assert (await client.post(f"/api/systems/{system_id}/groups", json={
            "name": "No", "alias": "no",
        })).status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("patch", [
    {"name": None}, {"alias": None}, {"name": ""}, {"alias": ""},
])
async def test_group_patch_rejects_null_and_empty_identity_fields(api, patch):
    store, system_id, _, transport = api
    group = store.create_group("owner", "Group", "group")
    async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                 cookies=cookie()) as client:
        response = await client.patch(
            f"/api/systems/{system_id}/groups/{group.id}", json=patch,
        )
        assert response.status_code == 422
        assert response.json()["message"]
        unchanged = store.group_selected("owner", group.id)
        assert unchanged is not None
        assert (unchanged.name, unchanged.alias) == ("Group", "group")
