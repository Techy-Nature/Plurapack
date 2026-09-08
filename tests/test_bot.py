from types import SimpleNamespace

import pytest

from plurapack import bot
from plurapack.proxy import Incoming


def test_bot_module_can_be_imported_without_stoat(monkeypatch):
    monkeypatch.setattr(bot.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(bot.StoatDependencyError, match="pip install -e"):
        bot.create_bot("p;", ":memory:")


def test_main_explains_how_to_install_stoat(monkeypatch):
    monkeypatch.setenv("STOAT_BOT_TOKEN", "test-token")
    monkeypatch.setattr(bot.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(SystemExit, match="python -m pip install -e"):
        bot.main()


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
