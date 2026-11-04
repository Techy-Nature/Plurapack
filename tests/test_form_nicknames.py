"""Form nicknames: short selectors that are unique within one system."""
import json
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from plurapack.bot import COMMAND_HELP, form_nickname_reply
from plurapack.storage import Store
from plurapack.transfer import export_system as create_export, import_system
from plurapack.web import create_app
from plurapack.web_auth import COOKIE_NAME, WebUser, create_session_cookie


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "nicknames.sqlite3")
    value.create_system("owner", "Crew")
    value.add_member("owner", "Alexandra", "[alex]", alias="Alex")
    value.add_member("owner", "Robin", "[robin]")
    value.create_system("other", "Other crew")
    value.add_member("other", "Other", "[o]")
    return value


def member(store, name, account="owner"):
    return store.member_selected(account, name)


def test_nickname_selects_form_case_insensitively(store):
    alex = member(store, "Alexandra")
    form = store.create_form("owner", alex.id, "Alex at Sea", nickname="sea")

    assert form.nickname == "sea"
    assert store.form_selected("owner", "SEA")[0].id == form.id
    assert store.proxy_identity("owner", "Sea").name == "Alex at Sea"
    assert store.switch_front("owner", "sea").form.id == form.id


def test_nickname_can_be_set_changed_and_cleared(store):
    form = store.create_form("owner", member(store, "Alexandra").id, "Formal")

    assert store.configure_form_nickname("owner", form.id, "  fancy ").nickname == "fancy"
    assert store.configure_form_nickname("owner", "fancy", "FANCY").nickname == "FANCY"
    assert store.configure_form_nickname("owner", form.id, None).nickname is None
    assert store.form_selected("owner", "fancy") is None


@pytest.mark.parametrize("taken", ["alexandra", "ROBIN", "alex", "Sea", "OCEAN"])
def test_nickname_must_be_unique_in_the_system(store, taken):
    alex, robin = member(store, "Alexandra"), member(store, "Robin")
    store.create_form("owner", robin.id, "Ocean", nickname="sea")
    form = store.create_form("owner", alex.id, "Everyday")

    with pytest.raises(ValueError, match="already used"):
        store.configure_form_nickname("owner", form.id, taken)
    with pytest.raises(ValueError, match="already used"):
        store.create_form("owner", alex.id, "Another", nickname=taken)
    with pytest.raises(ValueError, match="already used"):
        store.update_form("owner", form.id, nickname=taken)
    assert store.form_selected("owner", form.id)[0].nickname is None


def test_nickname_may_repeat_in_a_different_system(store):
    store.create_form("owner", member(store, "Alexandra").id, "Sea", nickname="sea")
    other = store.create_form("other", member(store, "Other", "other").id, "Other sea", nickname="sea")

    assert other.nickname == "sea"
    assert store.form_selected("other", "sea")[0].id == other.id
    # The other system's member name is not in this system's namespace.
    assert store.create_form("owner", member(store, "Robin").id, "R", nickname="Other").nickname == "Other"


def test_resaving_a_form_keeps_its_own_nickname(store):
    form = store.create_form("owner", member(store, "Alexandra").id, "Sea", nickname="sea")

    assert store.update_form("owner", form.id, nickname="sea", soma="Fins").nickname == "sea"
    assert store.configure_form_nickname("owner", form.id, "Sea").nickname == "Sea"


@pytest.mark.parametrize("bad", ["two words", "a:b", "x" * 25])
def test_nickname_format_matches_member_aliases(store, bad):
    form = store.create_form("owner", member(store, "Alexandra").id, "Sea")
    with pytest.raises(ValueError, match="1–24 characters"):
        store.configure_form_nickname("owner", form.id, bad)


def test_members_cannot_take_an_existing_form_nickname(store):
    alex, robin = member(store, "Alexandra"), member(store, "Robin")
    store.create_form("owner", alex.id, "Sea", nickname="tide")

    with pytest.raises(ValueError, match="nickname of form"):
        store.configure_alias("owner", robin.id, "TIDE")
    with pytest.raises(ValueError, match="nickname of form"):
        store.update_member("owner", robin.id, name="Tide")
    with pytest.raises(ValueError, match="nickname of form"):
        store.update_member("owner", robin.id, alias="tide")
    with pytest.raises(ValueError, match="nickname of form"):
        store.add_member("owner", "tide", "[t]")
    with pytest.raises(ValueError, match="nickname of form"):
        store.add_member("owner", "Tidal", "[t]", alias="Tide")
    # A member's own form is not an exception: the selector would be ambiguous.
    with pytest.raises(ValueError, match="nickname of form"):
        store.configure_alias("owner", alex.id, "tide")


def test_nickname_wins_over_a_shared_form_display_name(store):
    alex, robin = member(store, "Alexandra"), member(store, "Robin")
    store.create_form("owner", alex.id, "Casual")
    store.create_form("owner", robin.id, "Casual")
    beach = store.create_form("owner", robin.id, "Beach", nickname="chill")

    with pytest.raises(ValueError, match="form nickname"):
        store.form_selected("owner", "Casual")
    assert store.form_selected("owner", "chill")[0].id == beach.id


def test_existing_databases_gain_the_nickname_column(tmp_path):
    path = tmp_path / "old.sqlite3"
    Store(path).create_system("owner", "Crew")
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE forms DROP COLUMN nickname")
    reopened = Store(path)
    alex = reopened.add_member("owner", "Alex", "[a]")
    assert reopened.create_form("owner", alex.id, "Sea", nickname="sea").nickname == "sea"


def test_formnickname_command_is_documented_with_a_unique_shortcut():
    assert COMMAND_HELP["formnickname"][0] == "fn"
    form = SimpleNamespace(display_name="Alex at Sea", nickname="sea")
    assert "`sea`" in form_nickname_reply(form, "p;")
    assert "p;front sea" in form_nickname_reply(form, "p;")
    assert "cleared" in form_nickname_reply(SimpleNamespace(display_name="Sea", nickname=None), "p;")


def test_native_export_and_import_keep_nicknames(store, tmp_path):
    store.create_form("owner", member(store, "Alexandra").id, "Sea", nickname="sea")
    document, _ = create_export(store, "owner", "plurapack")
    exported = json.loads(document)
    assert exported["members"][0]["forms"][0]["nickname"] == "sea"

    fresh = Store(tmp_path / "fresh.sqlite3")
    import_system(fresh, "owner", document)
    alex = fresh.member_selected("owner", "Alexandra")
    assert fresh.forms_for_member(alex.id)[0].nickname == "sea"


def test_import_drops_a_nickname_that_is_already_taken(store):
    body = {"format": "plurapack", "version": 1, "system": {"name": "Crew"}, "settings": {}, "groups": [],
            "members": [{"export_id": "n", "name": "Newcomer", "proxy_tags": [{"prefix": "[n]", "suffix": ""}],
                         "forms": [{"export_id": "f", "display_name": "Twin", "nickname": "robin"}]}]}
    report = import_system(store, "owner", json.dumps(body))

    newcomer = store.member_selected("owner", "Newcomer")
    assert store.forms_for_member(newcomer.id)[0].nickname is None
    assert any("without its nickname" in warning for warning in report.warnings)


@pytest.mark.asyncio
async def test_dashboard_api_sets_and_validates_nicknames(store, monkeypatch):
    monkeypatch.setenv("PLURAPACK_SESSION_SECRET", "test-secret-that-is-longer-than-thirty-two-bytes")
    monkeypatch.setenv("PLURAPACK_COOKIE_SECURE", "false")
    system_id = store.system_for("owner")
    alex = member(store, "Alexandra")
    app = create_app(store, static_root=None)
    cookies = {COOKIE_NAME: create_session_cookie(WebUser("owner", "Owner"))}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test",
                                 cookies=cookies) as client:
        base = f"/api/systems/{system_id}/members/{alex.id}/forms"
        created = await client.post(base, json={"displayName": "Sea", "nickname": "sea"})
        assert created.status_code == 201
        assert created.json()["nickname"] == "sea"
        path = f"{base}/{created.json()['id']}"

        taken = await client.patch(path, json={"nickname": "Robin"})
        assert taken.status_code == 422
        assert "already used" in taken.text

        renamed = await client.patch(path, json={"nickname": "tide"})
        assert renamed.json()["nickname"] == "tide"
        # The dashboard sends blank inputs as null or "", which clear it.
        assert (await client.patch(path, json={"nickname": ""})).json()["nickname"] is None
        assert (await client.patch(path, json={"nickname": "two words"})).status_code == 422
