import importlib
import sys

import pytest

from plurapack.config import StorageConfigurationError, resolve_database_path
from plurapack.storage import Store


def test_default_database_behavior_is_compatible(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_database_path({}) == "plurapack.sqlite3"
    assert not (tmp_path / "plurapack.sqlite3").exists()


def test_data_dir_changes_default_and_is_created(tmp_path):
    data_dir = tmp_path / "volume" / "plurapack"
    resolved = resolve_database_path({"PLURAPACK_DATA_DIR": str(data_dir)})
    assert resolved == str(data_dir / "plurapack.sqlite3")
    assert data_dir.is_dir()


def test_explicit_database_takes_precedence_and_creates_parents(tmp_path):
    database = tmp_path / "custom" / "nested" / "state.db"
    ignored = tmp_path / "ignored"
    resolved = resolve_database_path({
        "PLURAPACK_DATABASE": str(database),
        "PLURAPACK_DATA_DIR": str(ignored),
    })
    assert resolved == str(database)
    assert database.parent.is_dir()
    assert not ignored.exists()


def test_explicit_database_path_is_not_rewritten(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert resolve_database_path({"PLURAPACK_DATABASE": "./state.sqlite3"}) == \
        "./state.sqlite3"


def test_store_creates_database_parent(tmp_path):
    database = tmp_path / "new" / "parent" / "state.sqlite3"
    Store(database)
    assert database.is_file()


def test_store_migrates_five_character_group_ids_without_losing_relationships(tmp_path):
    database = tmp_path / "old-groups.sqlite3"
    old_store = Store(database)
    system_id = old_store.create_system("owner", "Crew")
    member = old_store.add_member("owner", "Alex", "A:")
    old_group_id = "abc12"

    with old_store.connect() as db:
        db.execute("DROP TABLE groups")
        db.execute("""CREATE TABLE groups (
            id TEXT PRIMARY KEY CHECK(length(id)=5),
            system_id TEXT NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
            name TEXT NOT NULL COLLATE NOCASE, alias TEXT NOT NULL COLLATE NOCASE,
            avatar TEXT, UNIQUE(system_id,name), UNIQUE(system_id,alias)
        )""")
        db.execute(
            "INSERT INTO groups(id,system_id,name,alias,avatar) VALUES (?,?,?,?,?)",
            (old_group_id, system_id, "Friends", "friends", "https://example.test/group.png"),
        )
        db.execute("INSERT INTO group_members(group_id,member_id) VALUES (?,?)",
                   (old_group_id, member.id))
        db.execute("INSERT INTO active_groups(system_id,group_id) VALUES (?,?)",
                   (system_id, old_group_id))

    migrated_store = Store(database)
    with migrated_store.connect() as db:
        row = db.execute("SELECT * FROM groups WHERE name='Friends'").fetchone()
        assert row is not None
        migrated_group_id = row["id"]
        assert len(migrated_group_id) == 8
        assert (row["system_id"], row["name"], row["alias"], row["avatar"]) == (
            system_id, "Friends", "friends", "https://example.test/group.png"
        )
        assert db.execute(
            "SELECT 1 FROM group_members WHERE group_id=? AND member_id=?",
            (migrated_group_id, member.id),
        ).fetchone()
        assert db.execute(
            "SELECT group_id FROM active_groups WHERE system_id=?", (system_id,)
        ).fetchone()[0] == migrated_group_id
        assert db.execute("SELECT 1 FROM groups WHERE id=?", (old_group_id,)).fetchone() is None
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []

    active = migrated_store.active_group("owner")
    assert active is not None
    assert active.id == migrated_group_id

    reopened_store = Store(database)
    assert reopened_store.active_group("owner").id == migrated_group_id


def test_invalid_persistent_directory_fails_without_fallback(tmp_path):
    invalid = tmp_path / "not-a-directory"
    invalid.write_text("occupied", encoding="utf-8")
    with pytest.raises(StorageConfigurationError, match="PLURAPACK_DATA_DIR"):
        resolve_database_path({"PLURAPACK_DATA_DIR": str(invalid)})
    assert not (tmp_path / "plurapack.sqlite3").exists()


def test_database_path_cannot_be_a_directory(tmp_path):
    with pytest.raises(StorageConfigurationError, match="not a file"):
        resolve_database_path({"PLURAPACK_DATABASE": str(tmp_path)})


def test_bot_and_dashboard_resolve_same_configured_database(tmp_path, monkeypatch):
    monkeypatch.setenv("PLURAPACK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("PLURAPACK_DATABASE", raising=False)
    from plurapack import bot, web

    expected = resolve_database_path()
    app = web.create_app(static_root=None)
    assert app.state.store.path == expected
    assert bot.resolve_database_path() == expected


def test_web_without_supplied_store_resolves_configured_database(tmp_path, monkeypatch):
    from plurapack import web

    database = tmp_path / "resolved" / "web.sqlite3"
    calls = []

    def resolve():
        calls.append(True)
        return str(database)

    monkeypatch.setattr(web, "resolve_database_path", resolve)
    app = web.create_app(static_root=None)

    assert calls == [True]
    assert app.state.store.path == str(database)
    assert database.is_file()


def test_web_supplied_store_skips_storage_resolution_and_keys_browser_audio(
    tmp_path, monkeypatch
):
    from plurapack import web

    invalid_data_dir = tmp_path / "not-a-directory"
    invalid_data_dir.write_text("occupied", encoding="utf-8")
    monkeypatch.setenv("PLURAPACK_DATA_DIR", str(invalid_data_dir))
    monkeypatch.delenv("PLURAPACK_DATABASE", raising=False)

    def unexpected_resolution():
        pytest.fail("resolve_database_path must not run for an injected Store")

    browser_audio = object()
    configured_with = []

    def configured(database):
        configured_with.append(database)
        return browser_audio

    monkeypatch.setattr(web, "resolve_database_path", unexpected_resolution)
    monkeypatch.setattr(web.BrowserAudioStore, "configured", configured)
    supplied = Store(":memory:")

    app = web.create_app(store=supplied, static_root=None)

    assert app.state.store is supplied
    assert app.state.browser_audio is browser_audio
    assert configured_with == [":memory:"]


def test_importing_config_does_not_touch_filesystem(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PLURAPACK_DATA_DIR", str(tmp_path / "would-be-data"))
    before = set(tmp_path.iterdir())
    sys.modules.pop("plurapack.config", None)
    importlib.import_module("plurapack.config")
    assert set(tmp_path.iterdir()) == before
