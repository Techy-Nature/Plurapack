from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from typing import Protocol

from .storage import Member, Store
from .speech import SpeechJob, SpeechQueue


# People moving from other proxy bots keep typing their old prefix out of habit.
# These are recognised for the edit command only, so ``pk;edit`` and
# ``tul!edit`` behave like ``p;edit`` instead of being proxied as new messages.
COMPATIBLE_EDIT_PREFIXES = ("pk;", "pk!", "tul!")
EDIT_COMMAND_NAMES = frozenset({"edit", "e"})
_EDIT_MODES = {
    "-append": "append", "--append": "append", "-a": "append",
    "-prepend": "prepend", "--prepend": "prepend", "-p": "prepend",
}
_COMMAND_WORD = re.compile(r"\s*(\S+)(?:\s+(.*))?\Z", re.DOTALL)


class EditCommandError(Exception):
    """A user-facing reason an edit command could not be carried out."""


@dataclass(frozen=True)
class EditRequest:
    text: str
    mode: str = "replace"  # replace, append, or prepend
    # False for borrowed prefixes (pk;, tul!). Those may be aimed at another
    # bot in the same server, so Plurapack stays quiet unless it owns the target.
    native: bool = True


def _combine(original: str, addition: str, mode: str) -> str:
    """Join appended or prepended text with a space unless whitespace is already there."""
    if mode == "replace" or not original:
        return addition
    first, second = (original, addition) if mode == "append" else (addition, original)
    separator = "" if first[-1:].isspace() or second[:1].isspace() else " "
    return f"{first}{separator}{second}"


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
    async def delete_source(self, incoming: Incoming) -> bool: ...
    async def edit_proxy(self, channel_id: str, proxy_id: str, content: str) -> None: ...
    async def delete_proxy(self, channel_id: str, proxy_id: str) -> None: ...
    async def send_reproxy(self, incoming: Incoming, proxy_id: str, member: Member) -> str: ...
    async def proxy_content(self, channel_id: str, proxy_id: str) -> str: ...


class ProxyService:
    def __init__(self, store: Store, platform: Platform, command_prefix: str = "p;",
                 speech_queue: SpeechQueue | None = None):
        self.store, self.platform, self.command_prefix = store, platform, command_prefix
        self.speech_queue = speech_queue
        self._inflight: set[str] = set()
        self._pending_edits: dict[tuple[str, str], str] = {}
        self._proxy_text: dict[str, str] = {}

    def _invalidate_speech(self, proxy_id: str) -> None:
        if self.speech_queue:
            self.speech_queue.cancel(proxy_id)

    def _queue_speech(self, channel_id: str, proxy_id: str, text: str, member: Member,
                      account_id: str) -> None:
        """Make speech a best-effort derived side effect of current proxy state."""
        self._invalidate_speech(proxy_id)
        if not self.speech_queue:
            return
        try:
            accepted = self.speech_queue.submit(
                SpeechJob(channel_id, proxy_id, text, member, account_id=account_id))
        except Exception as error:
            logging.getLogger(__name__).error("Could not queue speech (%s)", type(error).__name__)
            return
        if accepted:
            self._proxy_text[proxy_id] = text
        elif member.playback in {"send", "local", "both"}:
            # Playback filtering is expected and silent. A server-playback job
            # can only be rejected here because the bounded queue is full.
            logging.getLogger(__name__).warning("Speech queue is full; audio was not queued")

    def _delete_speech(self, proxy_id: str) -> None:
        self._invalidate_speech(proxy_id)
        self._proxy_text.pop(proxy_id, None)

    async def handle_reaction(self, channel_id: str, proxy_id: str, account_id: str, emoji: str) -> bool:
        """Handle the deliberately small reaction control surface for an owned proxy."""
        if not self.store.proxy_owned_by(proxy_id, account_id, channel_id):
            return False
        if emoji in {"✏️", "📝"}:
            self._pending_edits[(account_id, channel_id)] = proxy_id
            return True
        if emoji in {"❌", "🗑️"}:
            self._delete_speech(proxy_id)
            await self.platform.delete_proxy(channel_id, proxy_id)
            self.store.mark_proxy_deleted(proxy_id, account_id)
            self._pending_edits = {key: value for key, value in self._pending_edits.items() if value != proxy_id}
            return True
        return False

    def parse_edit_command(self, content: str) -> EditRequest | None:
        """Recognise ``p;edit``/``p;e`` and the PluralKit/Tupperbox spellings.

        Returns ``None`` for anything that is not an edit command. Leading
        ``-append``/``-prepend`` flags (PluralKit's names, plus ``-a``/``-p``)
        are consumed; everything after them is kept exactly, newlines included.
        """
        stripped = content.lstrip()
        lowered = stripped.casefold()
        for prefix in dict.fromkeys((self.command_prefix, *COMPATIBLE_EDIT_PREFIXES)):
            if prefix and lowered.startswith(prefix.casefold()):
                remainder = stripped[len(prefix):]
                native = prefix == self.command_prefix
                break
        else:
            return None
        match = _COMMAND_WORD.match(remainder)
        if match is None or match.group(1).casefold() not in EDIT_COMMAND_NAMES:
            return None
        body, mode = match.group(2) or "", "replace"
        while True:
            flag = _COMMAND_WORD.match(body)
            if flag is None or flag.group(1).casefold() not in _EDIT_MODES:
                break
            mode, body = _EDIT_MODES[flag.group(1).casefold()], flag.group(2) or ""
        return EditRequest(body.strip(), mode, native)

    async def edit_with_command(self, message: Incoming, request: EditRequest) -> str | None:
        """Edit the replied-to proxy, or this account's latest proxy in the channel.

        Returns the edited proxy ID, or ``None`` when a borrowed prefix was
        evidently meant for another bot.
        """
        usage = f"`{self.command_prefix}edit new text`"
        if message.reply_to_id:
            target = message.reply_to_id
            if not self.store.proxy_owned_by(target, message.author_id, message.channel_id):
                if not request.native:
                    return None
                raise EditCommandError(
                    "That isn't one of your system's proxied messages, so it can't be edited. "
                    f"Reply to one of your proxied messages with {usage}, or send {usage} "
                    "without replying to edit your latest one here."
                )
        else:
            target = self.store.latest_proxy_for(message.author_id, message.channel_id)
            if target is None:
                if not request.native:
                    return None
                raise EditCommandError(
                    "There's no recent proxied message from you in this channel to edit. "
                    f"Reply to the message you want to change with {usage}."
                )
        if not request.text:
            raise EditCommandError(f"Add the new text after the command, for example {usage}.")
        new_text = request.text
        if request.mode != "replace":
            content_reader = getattr(self.platform, "proxy_content", None)
            if content_reader is None:
                raise EditCommandError("Appending and prepending aren't available on this platform.")
            new_text = _combine(await content_reader(message.channel_id, target),
                                request.text, request.mode)
        self._invalidate_speech(target)
        await self.platform.edit_proxy(message.channel_id, target, new_text)
        member = self.store.proxy_identity_for(target, message.author_id)
        if member:
            self._queue_speech(message.channel_id, target, new_text, member, message.author_id)
        # A finished command edit replaces any half-started ✏️ reaction edit here.
        self._pending_edits.pop((message.author_id, message.channel_id), None)
        await self.platform.delete_source(message)
        return target

    async def handle(self, message: Incoming) -> str | None:
        if message.is_bot or message.id in self._inflight or self.store.source_was_processed(message.id):
            return None
        # Checked before the command-prefix exit and before reply re-proxying,
        # so "pk;edit ..." is never mistaken for a selector or autoproxied.
        edit_request = self.parse_edit_command(message.content)
        if edit_request is not None:
            return await self.edit_with_command(message, edit_request)
        if message.content.startswith(self.command_prefix):
            return None
        edit_key = (message.author_id, message.channel_id)
        edit_target = self._pending_edits.get(edit_key)
        if edit_target:
            if not message.content.strip() or not self.store.proxy_owned_by(
                    edit_target, message.author_id, message.channel_id):
                self._pending_edits.pop(edit_key, None)
                return None
            self._invalidate_speech(edit_target)
            await self.platform.edit_proxy(message.channel_id, edit_target, message.content.strip())
            member = self.store.proxy_identity_for(edit_target, message.author_id)
            if member:
                self._queue_speech(message.channel_id, edit_target, message.content.strip(), member,
                                   message.author_id)
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
                old_proxy_id = message.reply_to_id
                self._invalidate_speech(old_proxy_id)
                text = self._proxy_text.get(old_proxy_id) if self.speech_queue else None
                if self.speech_queue and text is None:
                    content_reader = getattr(self.platform, "proxy_content", None)
                    if content_reader:
                        try:
                            text = await content_reader(message.channel_id, old_proxy_id)
                        except Exception as error:
                            logging.getLogger(__name__).error(
                                "Could not read proxy for speech (%s)", type(error).__name__
                            )
                replacement_id = await self.platform.send_reproxy(message, old_proxy_id, member)
                if not self.store.replace_proxy(message.reply_to_id, replacement_id, member, message.author_id):
                    await self.platform.delete_proxy(message.channel_id, replacement_id)
                    return None
                self._delete_speech(old_proxy_id)
                if text is not None:
                    self._queue_speech(message.channel_id, replacement_id, text, member,
                                       message.author_id)
                await self.platform.delete_proxy(message.channel_id, old_proxy_id)
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
            self._queue_speech(message.channel_id, proxy_id, body, member, message.author_id)
            # The source is removed only after the replacement exists and its attribution is durable.
            await self.platform.delete_source(message)
            return proxy_id
        finally:
            self._inflight.discard(message.id)
