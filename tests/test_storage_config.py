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
