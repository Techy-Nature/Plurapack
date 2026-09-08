import pytest

from plurapack import bot


def test_bot_module_can_be_imported_without_stoat(monkeypatch):
    monkeypatch.setattr(bot.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(bot.StoatDependencyError, match="pip install -e"):
        bot.create_bot("p;", ":memory:")


def test_main_explains_how_to_install_stoat(monkeypatch):
    monkeypatch.setenv("STOAT_BOT_TOKEN", "test-token")
    monkeypatch.setattr(bot.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(SystemExit, match="python -m pip install -e"):
        bot.main()
