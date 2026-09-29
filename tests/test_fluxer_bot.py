from types import SimpleNamespace

from plurapack.fluxer_bot import FluxerPlatform
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
