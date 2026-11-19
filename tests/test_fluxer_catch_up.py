"""Catching up on messages missed while a Fluxer session could not be resumed."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from plurapack.fluxer_bot import (
    FluxerCatchUp,
    FluxerPlatform,
    create_fluxer_bot,
    resolve_catch_up_minutes,
)
from plurapack.storage import Store


def _iso(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _raw(message_id, content, *, channel="c", author="owner", bot=False, minutes_ago=0.2):
    return {
        "id": message_id,
        "channel_id": channel,
        "content": content,
        "author": {"id": author, "bot": bot},
        "timestamp": _iso(minutes_ago),
    }


class FakeHttp:
    def __init__(self, pages=None, broken=()):
        self.pages = pages or {}
        self.broken = set(broken)
        self.calls = []

    async def get_messages(self, channel_id, *, limit=50, before=None, after=None):
        self.calls.append((channel_id, limit, after))
        if channel_id in self.broken:
            raise RuntimeError("boom")
        # Fluxer, like Discord, may hand these back newest first.
        return list(reversed(self.pages.get(channel_id, [])))


def make_bot(monkeypatch, tmp_path, http):
    events = {}

    class Bot:
        def __init__(self, **kwargs):
            self._http = http

        def command(self, name):
            return lambda callback: callback

        def event(self, callback):
            events[callback.__name__] = callback
            return callback

        def _parse_message(self, data):
            author = data["author"]
            return SimpleNamespace(
                id=int(data["id"]), channel_id=int(data["channel_id"]) if str(data["channel_id"]).isdigit()
                else data["channel_id"], content=data["content"],
                author=SimpleNamespace(id=author["id"], bot=author["bot"]),
                referenced_message=None, guild_id=None,
            )

    monkeypatch.setattr(
        "plurapack.fluxer_bot._load_fluxer",
        lambda: SimpleNamespace(Bot=Bot, Intents=SimpleNamespace(default=lambda: 0, MESSAGE_CONTENT=1)),
    )
    proxied = []

    async def send_proxy(self, incoming, member, content):
        proxied.append((incoming.id, member.name, content, incoming.server_id))
        return f"proxy-{incoming.id}"

    async def delete_source(self, incoming):
        self.messages.pop(incoming.id)
        return True

    monkeypatch.setattr(FluxerPlatform, "send_proxy", send_proxy)
    monkeypatch.setattr(FluxerPlatform, "delete_source", delete_source)
    database = tmp_path / "catch-up.sqlite3"
    client = create_fluxer_bot("p;", str(database))
    store = Store(database)
    store.create_system("owner", "Crew")
    store.add_member("owner", "Alex", "a:")
    return client, events, proxied


def live(message_id, content, channel="c", guild="server"):
    return SimpleNamespace(id=message_id, channel_id=channel, content=content,
                           author=SimpleNamespace(id="owner", bot=False),
                           referenced_message=None, guild_id=guild)


async def test_new_session_proxies_messages_sent_during_the_gap(tmp_path, monkeypatch, capsys):
    http = FakeHttp({"c": [
        _raw("101", "a:sent during the gap"),
        _raw("102", "just chatting"),
        _raw("103", "a:from a webhook", bot=True),
    ]})
    client, events, proxied = make_bot(monkeypatch, tmp_path, http)

    await events["on_ready"]()
    await events["on_message"](live("100", "a:before the drop"))
    await events["on_ready"]()

    assert http.calls == [("c", 50, "100")]
    assert [item[0] for item in proxied] == ["100", "101"]
    # The server the channel belonged to is carried over from the live message.
    assert proxied[1] == ("101", "Alex", "sent during the gap", "server")
    out = capsys.readouterr().out
    assert "[Plurapack] Fluxer catch-up checked 1 channel(s); proxied 1 message(s) sent while disconnected." in out


async def test_catch_up_skips_messages_older_than_the_window(tmp_path, monkeypatch):
    http = FakeHttp({"c": [
        _raw("101", "a:too old", minutes_ago=30),
        _raw("102", "a:no timestamp"),
        _raw("103", "a:recent", minutes_ago=2),
    ]})
    http.pages["c"][1]["timestamp"] = "not a time"
    _, events, proxied = make_bot(monkeypatch, tmp_path, http)

    await events["on_ready"]()
    await events["on_message"](live("100", "hello"))
    await events["on_ready"]()

    assert [item[0] for item in proxied] == ["103"]


async def test_message_delivered_live_and_fetched_is_proxied_once(tmp_path, monkeypatch):
    http = FakeHttp({"c": [_raw("101", "a:arrived live after the new session")]})
    _, events, proxied = make_bot(monkeypatch, tmp_path, http)

    await events["on_ready"]()
    await events["on_message"](live("100", "hello"))
    await events["on_message"](live("101", "a:arrived live after the new session"))
    await events["on_ready"]()

    assert [item[0] for item in proxied] == ["101"]


async def test_resume_and_first_ready_do_not_fetch_history(tmp_path, monkeypatch):
    http = FakeHttp({"c": [_raw("101", "a:replayed by Fluxer itself")]})
    _, events, proxied = make_bot(monkeypatch, tmp_path, http)

    await events["on_ready"]()
    await events["on_message"](live("100", "hello"))
    await events["on_resumed"]()

    assert http.calls == []
    assert proxied == []


async def test_unreadable_channel_does_not_stop_the_others(tmp_path, monkeypatch, capsys):
    http = FakeHttp({"ok": [_raw("201", "a:still caught", channel="ok")]}, broken={"bad"})
    _, events, proxied = make_bot(monkeypatch, tmp_path, http)

    await events["on_ready"]()
    await events["on_message"](live("100", "hello", channel="bad"))
    await events["on_message"](live("200", "hello", channel="ok"))
    await events["on_ready"]()

    assert [item[0] for item in proxied] == ["201"]
    assert "1 channel(s) could not be read." in capsys.readouterr().out


async def test_catch_up_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setenv("PLURAPACK_FLUXER_CATCH_UP_MINUTES", "0")
    http = FakeHttp({"c": [_raw("101", "a:left alone")]})
    _, events, proxied = make_bot(monkeypatch, tmp_path, http)

    await events["on_ready"]()
    await events["on_message"](live("100", "hello"))
    await events["on_ready"]()

    assert http.calls == []
    assert proxied == []


@pytest.mark.parametrize("value", ["-1", "61", "soon"])
def test_catch_up_minutes_must_be_sensible(monkeypatch, value):
    monkeypatch.setenv("PLURAPACK_FLUXER_CATCH_UP_MINUTES", value)
    with pytest.raises(ValueError, match="PLURAPACK_FLUXER_CATCH_UP_MINUTES"):
        resolve_catch_up_minutes()


def test_tracker_keeps_newest_message_and_forgets_quiet_channels():
    now = [1000.0]
    tracker = FluxerCatchUp(channel_limit=2, channel_window=60, recent_limit=3, clock=lambda: now[0])

    assert tracker.saw("a", "10", "s")
    assert not tracker.saw("a", "10", "s")
    assert tracker.saw("a", "9", None)  # an older message never moves the mark back
    assert tracker.channels_to_check()[0][1].message_id == "10"
    assert tracker.channels_to_check()[0][1].server_id == "s"

    now[0] += 120
    tracker.saw("b", "20", None)
    assert [channel for channel, _ in tracker.channels_to_check()] == ["b"]

    tracker.saw("c", "30", None)
    tracker.saw("d", "40", None)
    assert [channel for channel, _ in tracker.channels_to_check()] == ["d", "c"]
