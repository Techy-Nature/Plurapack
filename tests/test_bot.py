from types import SimpleNamespace

import pytest

from plurapack import bot
from plurapack.proxy import Incoming


def test_help_lists_every_command_with_usage_and_shortcut():
    pages = bot._help_pages("p;", limit=500)
    rendered = "\n".join(pages)

    assert len(pages) > 1
    assert all(len(page) <= 500 for page in pages)
    for name, (shortcut, usage, summary) in bot.COMMAND_HELP.items():
        assert f"p;{name}" in rendered
        assert f"(`{shortcut}`)" in rendered
        assert summary in rendered
        if usage:
            assert usage in rendered


def test_help_details_accept_command_shortcuts_and_compatibility_aliases():
    assert bot._help_pages("!", "m") == [
        "**member** — Add a member and proxy tag.\n"
        "Usage: `!member NAME PREFIX [SUFFIX] [DESCRIPTION]`\nAliases: `!m`"
    ]
    assert bot._help_pages("p;", "view")[0].startswith("**viewinfo**")
    assert bot._help_pages("p;", "missing") == [
        "Unknown command `missing`. Use `p;help` to list every command."
    ]


def test_bot_module_can_be_imported_without_stoat(monkeypatch):
    monkeypatch.setattr(bot.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(bot.StoatDependencyError, match="pip install -e"):
        bot.create_bot("p;", ":memory:")


def test_main_explains_how_to_install_stoat(monkeypatch):
    monkeypatch.setenv("STOAT_BOT_TOKEN", "test-token")
    monkeypatch.setattr(bot.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(SystemExit, match="python -m pip install -e"):
        bot.main()


async def test_cli_reports_connected_and_ready_states(capsys):
    listeners = {}

    class FakeBot:
        def listen(self, event_type):
            def register(callback):
                listeners[event_type] = callback
                return callback

            return register

    sdk = SimpleNamespace(AfterConnectEvent=object(), ReadyEvent=object())
    bot._register_cli_status_listeners(FakeBot(), sdk, "p;")

    await listeners[sdk.AfterConnectEvent](SimpleNamespace())
    assert capsys.readouterr().out == "[Plurapack] Connected to Stoat.\n"

    await listeners[sdk.ReadyEvent](SimpleNamespace())
    assert capsys.readouterr().out == "[Plurapack] Ready to use. Command prefix: p;\n"


def test_main_reports_that_it_is_connecting(monkeypatch, capsys):
    running = SimpleNamespace(run=lambda token: None)
    monkeypatch.setenv("STOAT_BOT_TOKEN", "test-token")
    monkeypatch.setattr(bot, "create_bot", lambda prefix, database: running)

    bot.main()

    assert capsys.readouterr().out == "[Plurapack] Connecting to Stoat...\n"


async def test_stoat_platform_applies_member_color_to_username():
    class Masquerade:
        def __init__(self, **kwargs):
            self.name = kwargs["name"]
            self.avatar = kwargs["avatar"]
            self.color = kwargs["color"]

    class Channel:
        async def send(self, content, *, masquerade):
            assert content == "hello"
            assert masquerade.color == "#7b68ee"
            return SimpleNamespace(id="proxy-id")

    source = SimpleNamespace(get_channel=lambda: Channel())
    platform = bot.StoatPlatform(
        {"source-id": source}, SimpleNamespace(), SimpleNamespace(MessageMasquerade=Masquerade)
    )
    incoming = Incoming("source-id", "channel-id", "owner-id", "[alex] hello")
    member = SimpleNamespace(name="Alex", avatar=None, color="#7b68ee")

    assert await platform.send_proxy(incoming, member, "hello") == "proxy-id"


async def test_stoat_platform_applies_form_name_and_avatar_to_masquerade():
    captured = None

    class Masquerade:
        def __init__(self, **kwargs):
            nonlocal captured
            captured = kwargs

    class Channel:
        async def send(self, content, *, masquerade):
            return SimpleNamespace(id="proxy-id")

    platform = bot.StoatPlatform(
        {"source-id": SimpleNamespace(get_channel=lambda: Channel())},
        SimpleNamespace(),
        SimpleNamespace(MessageMasquerade=Masquerade),
    )
    form_identity = SimpleNamespace(
        name="Alex at Sea", avatar="https://example.test/sea.png", color="#123456"
    )

    await platform.send_proxy(
        Incoming("source-id", "channel-id", "owner-id", "hello"), form_identity, "hello"
    )

    assert captured == {
        "name": "Alex at Sea",
        "avatar": "https://example.test/sea.png",
        "color": "#123456",
    }
