from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol

from .storage import Member, Store
from .speech import SpeechJob, SpeechQueue


@dataclass(frozen=True)
class Incoming:
    id: str
    channel_id: str
    author_id: str
    content: str
    is_bot: bool = False
    reply_to_id: str | None = None
    server_id: str | None = None


class Platform(Protocol):
    async def send_proxy(self, incoming: Incoming, member: Member, content: str) -> str: ...
    async def delete_source(self, incoming: Incoming) -> None: ...
    async def edit_proxy(self, channel_id: str, proxy_id: str, content: str) -> None: ...
    async def delete_proxy(self, channel_id: str, proxy_id: str) -> None: ...
    async def send_reproxy(self, incoming: Incoming, proxy_id: str, member: Member) -> str: ...


class ProxyService:
    def __init__(self, store: Store, platform: Platform, command_prefix: str = "p;",
                 speech_queue: SpeechQueue | None = None):
        self.store, self.platform, self.command_prefix = store, platform, command_prefix
        self.speech_queue = speech_queue
        self._inflight: set[str] = set()
        self._pending_edits: dict[tuple[str, str], str] = {}

    async def handle_reaction(self, channel_id: str, proxy_id: str, account_id: str, emoji: str) -> bool:
        """Handle the deliberately small reaction control surface for an owned proxy."""
        if not self.store.proxy_owned_by(proxy_id, account_id, channel_id):
            return False
        if emoji in {"✏️", "📝"}:
            self._pending_edits[(account_id, channel_id)] = proxy_id
            return True
        if emoji in {"❌", "🗑️"}:
            if self.speech_queue:
                self.speech_queue.cancel(proxy_id)
            await self.platform.delete_proxy(channel_id, proxy_id)
            self.store.mark_proxy_deleted(proxy_id, account_id)
            self._pending_edits = {key: value for key, value in self._pending_edits.items() if value != proxy_id}
            return True
        return False

    async def handle(self, message: Incoming) -> str | None:
        if (message.is_bot or message.id in self._inflight or self.store.source_was_processed(message.id)
                or message.content.startswith(self.command_prefix)):
            return None
        edit_key = (message.author_id, message.channel_id)
        edit_target = self._pending_edits.get(edit_key)
        if edit_target:
            if not message.content.strip() or not self.store.proxy_owned_by(
                    edit_target, message.author_id, message.channel_id):
                self._pending_edits.pop(edit_key, None)
                return None
            await self.platform.edit_proxy(message.channel_id, edit_target, message.content.strip())
            self._pending_edits.pop(edit_key, None)
            await self.platform.delete_source(message)
            return edit_target

        if message.reply_to_id and self.store.proxy_owned_by(
                message.reply_to_id, message.author_id, message.channel_id):
            member = self.store.proxy_identity(message.author_id, message.content)
            if member:
                member = replace(member, name=self.store.proxy_name(
                    member, message.server_id, message.channel_id
                ))
                replacement_id = await self.platform.send_reproxy(message, message.reply_to_id, member)
                if not self.store.replace_proxy(message.reply_to_id, replacement_id, member, message.author_id):
                    await self.platform.delete_proxy(message.channel_id, replacement_id)
                    return None
                await self.platform.delete_proxy(message.channel_id, message.reply_to_id)
                await self.platform.delete_source(message)
                return replacement_id
        match = self.store.match_member(message.author_id, message.content)
        if match:
            member, body = match
        else:
            autoproxy = self.store.autoproxy(message.author_id)
            if autoproxy.member is None or not message.content.strip():
                return None
            member = self.store.proxy_identity(message.author_id, autoproxy.member.id) or autoproxy.member
            if autoproxy.autofront:
                front = self.store.current_front(message.author_id)
                if front and front.member.id == autoproxy.member.id and front.form:
                    member = self.store.proxy_identity(message.author_id, front.form.id) or member
            body = message.content.strip()
        member = replace(member, name=self.store.proxy_name(
            member, message.server_id, message.channel_id
        ))
        self._inflight.add(message.id)
        try:
            proxy_id = await self.platform.send_proxy(message, member, body)
            if not proxy_id:
                return None
            if not self.store.record_proxy(message.id, proxy_id, message.channel_id, member, message.author_id):
                return None
            if self.speech_queue:
                self.speech_queue.submit(SpeechJob(message.channel_id, proxy_id, body, member))
            # The source is removed only after the replacement exists and its attribution is durable.
            await self.platform.delete_source(message)
            return proxy_id
        finally:
            self._inflight.discard(message.id)
