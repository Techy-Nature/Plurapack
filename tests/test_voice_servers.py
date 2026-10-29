from types import SimpleNamespace

import pytest

from plurapack.bot import _incoming_from_stoat_message, _stoat_server_id, _voice_server_reply
from plurapack.proxy import Incoming, ProxyService
from plurapack.storage import Store
from tests.test_fluxer_bot import make_fluxer_commands
from tests.test_proxy import FakePlatform


class CountingPlatform(FakePlatform):
    async def send_proxy(self, incoming, member, content):
        await super().send_proxy(incoming, member, content)
        return f"proxy-{len(self.sent)}"


class RecordingQueue:
    def __init__(self):
        self.jobs, self.cancelled = [], []

    def submit(self, job):
        self.jobs.append(job)
        return True

    def cancel(self, proxy_id):
        self.cancelled.append(proxy_id)


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "voice-servers.db")
    value.create_system("owner", "Crew")
    value.add_member("owner", "Alex", "[alex]", "")
    return value


def test_muted_servers_are_per_system_and_reversible(store):
    system_id = store.system_for("owner")
    assert store.set_voice_server_muted("owner", "server-a", True)
    assert not store.set_voice_server_muted("owner", "server-a", True)
    assert store.voice_muted_servers("owner") == ["server-a"]
    assert store.voice_muted_in(system_id, "server-a")
    assert not store.voice_muted_in(system_id, "server-b")
    assert not store.voice_muted_in(system_id, None)
    store.create_system("other", "Others")
    assert not store.voice_muted_in(store.system_for("other"), "server-a")
    assert store.set_voice_server_muted("owner", "server-a", False)
    assert store.voice_muted_servers("owner") == []
    with pytest.raises(PermissionError):
        store.set_voice_server_muted("nobody", "server-a", True)
    with pytest.raises(ValueError):
        store.set_voice_server_muted("owner", "  ", True)


async def test_proxies_in_muted_servers_are_not_spoken(store):
    queue = RecordingQueue()
    service = ProxyService(store, CountingPlatform(), speech_queue=queue)
    store.set_voice_server_muted("owner", "quiet", True)

    await service.handle(Incoming("m1", "c", "owner", "[alex] hush", server_id="quiet"))
    assert queue.jobs == []

    await service.handle(Incoming("m2", "c", "owner", "[alex] hello", server_id="loud"))
    await service.handle(Incoming("m3", "dm", "owner", "[alex] private"))
    assert [job.text for job in queue.jobs] == ["hello", "private"]


def test_voice_server_reply_uses_current_server_and_lists(store):
    assert "every server" in _voice_server_reply(store, "owner", ["list"], "here", "p;")
    assert "now off in this server" in _voice_server_reply(store, "owner", ["off"], "here", "p;")
    assert "already off" in _voice_server_reply(store, "owner", ["off"], "here", "p;")
    assert "server `elsewhere`" in _voice_server_reply(store, "owner", ["off", "elsewhere"], "here", "p;")
    listed = _voice_server_reply(store, "owner", ["list"], "here", "p;")
    assert "`here` (this server)" in listed and "`elsewhere`" in listed
    assert "give its ID" in _voice_server_reply(store, "owner", ["on"], None, "p;")
    assert "now on" in _voice_server_reply(store, "owner", ["on", "here"], None, "p;")
    assert "Use `p;voice server" in _voice_server_reply(store, "owner", ["maybe"], "here", "p;")


async def test_fluxer_voice_server_command_works_without_voice_storage(tmp_path, monkeypatch):
    database = tmp_path / "fluxer.sqlite3"
    commands = make_fluxer_commands(monkeypatch, database)
    store = Store(database)
    store.create_system("123", "Fluxer system")
    replies = []

    async def send(message, **kwargs): replies.append(message)
    ctx = SimpleNamespace(author=SimpleNamespace(id=123), send=send,
                          message=SimpleNamespace(guild_id=987))
    await commands["voice"](ctx, arguments="server off")
    assert store.voice_muted_servers("123") == ["987"]
    assert "now off in this server" in replies[-1]
    await commands["vo"](ctx, arguments="server on")
    assert store.voice_muted_servers("123") == []


def test_stoat_server_id_comes_from_the_message_channel():
    # Stoat messages carry no server_id; the server lives on the channel.
    in_server = SimpleNamespace(get_server=lambda: (None, "01SERVER"))
    in_dm = SimpleNamespace(get_server=lambda: (None, ""))
    assert _stoat_server_id(in_server) == "01SERVER"
    assert _stoat_server_id(in_dm) is None
    message = SimpleNamespace(id="m", channel_id="c", content="hi", replies=[],
                              get_server=lambda: (None, "01SERVER"))
    author = SimpleNamespace(id="owner", bot=False)
    assert _incoming_from_stoat_message(message, author).server_id == "01SERVER"
