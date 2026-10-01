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


def test_importing_config_does_not_touch_filesystem(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PLURAPACK_DATA_DIR", str(tmp_path / "would-be-data"))
    before = set(tmp_path.iterdir())
    sys.modules.pop("plurapack.config", None)
    importlib.import_module("plurapack.config")
    assert set(tmp_path.iterdir()) == before
