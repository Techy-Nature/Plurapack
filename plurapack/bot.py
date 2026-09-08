"""Thin stoat.py adapter. Importing this module never connects to Stoat."""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from pathlib import Path

import stoat
from stoat.ext import commands

from .proxy import Incoming, ProxyService
from .chatterbox import ChatterboxBackend
from .speech import SpeechQueue, speech_worker
from .storage import Member, Store


@dataclass
class StoatPlatform:
    messages: dict[str, stoat.Message]
    state: stoat.State

    async def send_proxy(self, incoming: Incoming, member: Member, content: str) -> str:
        source = self.messages[incoming.id]
        channel = source.get_channel()
        if channel is None:
            raise RuntimeError("Source channel is unavailable; the original was preserved.")
        posted = await channel.send(
            content,
            masquerade=stoat.MessageMasquerade(name=member.name, avatar=member.avatar),
        )
        return posted.id

    async def delete_source(self, incoming: Incoming) -> None:
        await self.messages.pop(incoming.id).delete()

    async def edit_proxy(self, channel_id: str, proxy_id: str, content: str) -> None:
        await self.state.http.edit_message(channel_id, proxy_id, content=content)

    async def delete_proxy(self, channel_id: str, proxy_id: str) -> None:
        await self.state.http.delete_message(channel_id, proxy_id)

    async def send_reproxy(self, incoming: Incoming, proxy_id: str, member: Member) -> str:
        old = await self.state.http.get_message(incoming.channel_id, proxy_id)
        channel = self.messages[incoming.id].get_channel()
        if channel is None:
            raise RuntimeError("Proxy channel is unavailable; the original was preserved.")
        posted = await channel.send(
            old.content,
            masquerade=stoat.MessageMasquerade(name=member.name, avatar=member.avatar),
        )
        return posted.id

    async def deliver_speech(self, channel_id: str, proxy_message_id: str, audio: bytes) -> None:
        """Upload speech through persistent state, never the event-scoped message map."""
        channel = self.state.get_channel(channel_id)
        safe_id = "".join(character for character in proxy_message_id if character.isalnum() or character in "-_")[:48]
        filename = f"speech-{safe_id or 'proxy'}.mp3"
        await channel.send(attachments=[(filename, audio)], replies=[stoat.Reply(proxy_message_id)])


class PlurapackBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.speech_queue: SpeechQueue | None = None
        self.speech_worker_count = 0
        self._speech_tasks: list[asyncio.Task[None]] = []

    async def setup_hook(self) -> None:
        await super().setup_hook()
        if self.speech_queue:
            self._speech_tasks = [asyncio.create_task(speech_worker(self.speech_queue), name=f"speech-{index}")
                                  for index in range(self.speech_worker_count)]

    async def close(self, **kwargs) -> None:
        for task in self._speech_tasks:
            task.cancel()
        if self._speech_tasks:
            await asyncio.gather(*self._speech_tasks, return_exceptions=True)
        self._speech_tasks.clear()
        await super().close(**kwargs)


def _positive_environment_integer(name: str, default: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer.") from error
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}.")
    return value


def create_bot(prefix: str, database: str) -> commands.Bot:
    bot = PlurapackBot(command_prefix=prefix, description="Plural communication proxy")
    store = Store(database)
    platform = StoatPlatform({}, bot.state)
    speech_queue = None
    tts_url = os.environ.get("PLURAPACK_TTS_URL")
    if tts_url:
        queue_limit = _positive_environment_integer("PLURAPACK_TTS_QUEUE_LIMIT", 8, 1000)
        bot.speech_worker_count = _positive_environment_integer("PLURAPACK_TTS_WORKERS", 1, 4)
        speech_queue = SpeechQueue(ChatterboxBackend(tts_url),
                                   lambda job, audio: platform.deliver_speech(
                                       job.channel_id, job.proxy_message_id, audio), queue_limit)
        bot.speech_queue = speech_queue
    service = ProxyService(store, platform, prefix, speech_queue)

    @bot.command()
    async def setup(ctx: commands.Context, *, name: str = "My system") -> None:
        system_id = store.create_system(ctx.author.id, name)
        await ctx.send(
            f"System `{system_id}` is ready. Origins, diagnoses, and proof are never required. "
            f"Voice playback defaults to Off. Add a member with `{prefix}member Name [text]`."
        )

    @bot.command()
    async def member(ctx: commands.Context, name: str, member_prefix: str, suffix: str = "") -> None:
        try:
            created = store.add_member(ctx.author.id, name, member_prefix, suffix)
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Added **{created.name}** (`{created.id}`); voice is Off.")

    @bot.command()
    async def link(ctx: commands.Context) -> None:
        """Create a short-lived, single-use account connection code."""
        try:
            token = store.create_link(ctx.author.id)
        except PermissionError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Single-use code: `{token}` (expires in 15 minutes). Send it privately to the other owner.")

    @bot.command()
    async def verify(ctx: commands.Context, token: str) -> None:
        try:
            system_id = store.redeem_link(ctx.author.id, token)
        except PermissionError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Account connected to system `{system_id}`.")

    @bot.command()
    async def voice(ctx: commands.Context, selector: str, reference: str, playback: str = "send", *,
                    settings: str = "{}") -> None:
        """Use an operator-installed reference filename; chat uploads are intentionally unsupported."""
        reference_dir_value = os.environ.get("PLURAPACK_VOICE_REFERENCE_DIR")
        if not reference_dir_value:
            await ctx.send("Voice configuration is disabled until the operator sets PLURAPACK_VOICE_REFERENCE_DIR.")
            return
        reference_dir = Path(reference_dir_value).expanduser().resolve()
        candidate = (reference_dir / reference).resolve()
        try:
            candidate.relative_to(reference_dir)
        except ValueError:
            await ctx.send("Voice reference must be inside the operator-approved directory.")
            return
        if not candidate.is_file():
            await ctx.send("That operator-managed voice reference does not exist.")
            return
        try:
            configured = store.configure_voice(
                ctx.author.id, selector, candidate.relative_to(reference_dir).as_posix(), settings, playback
            )
        except (PermissionError, ValueError, json.JSONDecodeError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Voice for **{configured.name}** is configured for `{configured.playback}` playback.")

    @bot.command()
    async def voiceoff(ctx: commands.Context, selector: str) -> None:
        try:
            configured = store.configure_voice(ctx.author.id, selector, None, "{}", "off")
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Voice for **{configured.name}** is Off.")

    @bot.listen(stoat.MessageCreateEvent)
    async def proxy_listener(event: stoat.MessageCreateEvent) -> None:
        message = event.message
        author = message.get_author()
        if author is None:
            return
        incoming = Incoming(
            message.id,
            message.channel_id,
            author.id,
            message.content,
            bool(getattr(author, "bot", None)),
            message.replies[0] if message.replies else None,
        )
        platform.messages[message.id] = message
        try:
            await service.handle(incoming)
        finally:
            platform.messages.pop(message.id, None)

    @bot.listen(stoat.MessageReactEvent)
    async def reaction_listener(event: stoat.MessageReactEvent) -> None:
        message = event.message
        if message is not None:
            platform.messages[message.id] = message
        try:
            await service.handle_reaction(event.channel_id, event.message_id, event.user_id, event.emoji)
        finally:
            if message is not None:
                platform.messages.pop(message.id, None)

    return bot


def main() -> None:
    token = os.environ.get("STOAT_BOT_TOKEN")
    if not token:
        raise SystemExit("STOAT_BOT_TOKEN is required (copy .env.example; never commit the token).")
    create_bot(os.environ.get("PLURAPACK_PREFIX", "p;"), os.environ.get("PLURAPACK_DATABASE", "plurapack.sqlite3")).run(token)


if __name__ == "__main__":
    main()
