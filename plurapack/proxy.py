from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .storage import Member, Store


@dataclass(frozen=True)
class Incoming:
    id: str
    channel_id: str
    author_id: str
    content: str
    is_bot: bool = False


class Platform(Protocol):
    async def send_proxy(self, incoming: Incoming, member: Member, content: str) -> str: ...
    async def delete_source(self, incoming: Incoming) -> None: ...


class ProxyService:
    def __init__(self, store: Store, platform: Platform, command_prefix: str = "p;"):
        self.store, self.platform, self.command_prefix = store, platform, command_prefix
        self._inflight: set[str] = set()

    async def handle(self, message: Incoming) -> str | None:
        if (message.is_bot or message.id in self._inflight or self.store.source_was_processed(message.id)
                or message.content.startswith(self.command_prefix)):
            return None
        match = self.store.match_member(message.author_id, message.content)
        if not match:
            return None
        member, body = match
        self._inflight.add(message.id)
        try:
            proxy_id = await self.platform.send_proxy(message, member, body)
            if not proxy_id:
                return None
            if not self.store.record_proxy(message.id, proxy_id, message.channel_id, member, message.author_id):
                return None
            # The source is removed only after the replacement exists and its attribution is durable.
            await self.platform.delete_source(message)
            return proxy_id
        finally:
            self._inflight.discard(message.id)
