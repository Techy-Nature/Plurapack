from types import SimpleNamespace

from plurapack.fluxer_bot import FluxerPlatform, create_fluxer_bot
from plurapack.login import LoginService
from plurapack.storage import Store
from plurapack.proxy import Incoming


async def test_fluxer_platform_proxies_with_webhook_identity():
    sent = {}

    class Webhook:
        name = "Plurapack Proxy"

        async def send(self, content, **kwargs):
            sent.update(content=content, **kwargs)
            return SimpleNamespace(id=42)

    webhook = Webhook()
    client = SimpleNamespace(fetch_channel_webhooks=lambda channel_id: None)
    platform = FluxerPlatform(client, webhooks={"20": webhook})
    member = SimpleNamespace(name="Alex", avatar="https://example.test/a.png")

    proxy_id = await platform.send_proxy(Incoming("10", "20", "30", "[a] hi"), member, "hi")

    assert proxy_id == "42"
    assert sent == {
        "content": "hi",
        "username": "Alex",
        "avatar_url": "https://example.test/a.png",
        "wait": True,
    }
    assert platform.proxy_webhooks["42"] is webhook


async def test_fluxer_platform_deletes_source_message():
    deleted = False

    async def delete():
        nonlocal deleted
        deleted = True

    platform = FluxerPlatform(SimpleNamespace(), messages={"10": SimpleNamespace(delete=delete)})
    await platform.delete_source(Incoming("10", "20", "30", "hello"))

    assert deleted
    assert platform.messages == {}


async def test_fluxer_login_command_approves_dashboard_attempt(tmp_path, monkeypatch):
    commands = {}

    class Bot:
        def __init__(self, **kwargs):
            pass

        def command(self, name):
            def register(callback):
                commands[name] = callback
                return callback
            return register

        def event(self, callback):
            return callback

    fluxer = SimpleNamespace(
        Bot=Bot,
        Intents=SimpleNamespace(default=lambda: 0, MESSAGE_CONTENT=1),
    )
    monkeypatch.setattr("plurapack.fluxer_bot._load_fluxer", lambda: fluxer)
    database = tmp_path / "fluxer-login.sqlite3"
    create_fluxer_bot("p;", str(database))
    attempt = LoginService(Store(database)).start()
    replies = []

    async def send(message):
        replies.append(message)

    ctx = SimpleNamespace(
        author=SimpleNamespace(id="fluxer-user", display_name="Fluxer User"),
        send=send,
    )

    await commands["login"](ctx, attempt.code)

    user = LoginService(Store(database)).complete(attempt.id, attempt.browser_secret)
    assert (user.id, user.username) == ("fluxer-user", "Fluxer User")
    assert replies == ["Dashboard login approved. You can return to your browser."]
    assert commands["lg"] is commands["login"]
