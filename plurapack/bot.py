"""Thin stoat.py adapter. Importing this module never connects to Stoat."""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .proxy import Incoming, ProxyService
from .chatterbox import ChatterboxBackend
from .speech import SpeechQueue, speech_worker
from .storage import Member, Store
from .transfer import TransferError, export_document, parse_import


STOAT_INSTALL_MESSAGE = (
    "The Stoat client dependency is not installed. Install Plurapack and its "
    "dependencies with `python -m pip install -e .`, then try again."
)

# Every public command has a distinct one- or two-letter shortcut. Keeping the
# mapping explicit also gives dashboards/help pages one canonical source.
COMMAND_SHORTCUTS = {
    "setup": "s", "member": "m", "import": "i", "export": "x",
    "link": "l", "color": "c", "verify": "v", "voice": "vo",
    "voiceoff": "of", "voiceformat": "vf", "alias": "a", "form": "f",
    "front": "fr", "defaultform": "df", "autoproxy": "ap", "autofront": "af",
}


class StoatDependencyError(RuntimeError):
    """Raised when the bot is launched without the Stoat SDK installed."""


def _load_stoat() -> tuple[Any, Any]:
    """Load the runtime adapter only when a bot is being created."""
    if importlib.util.find_spec("stoat") is None:
        raise StoatDependencyError(STOAT_INSTALL_MESSAGE)

    stoat = importlib.import_module("stoat")
    commands = importlib.import_module("stoat.ext.commands")
    return stoat, commands


@dataclass
class StoatPlatform:
    messages: dict[str, Any]
    state: Any
    sdk: Any

    async def send_proxy(self, incoming: Incoming, member: Member, content: str) -> str:
        source = self.messages[incoming.id]
        channel = source.get_channel()
        if channel is None:
            raise RuntimeError("Source channel is unavailable; the original was preserved.")
        posted = await channel.send(
            content,
            masquerade=self.sdk.MessageMasquerade(
                name=member.name, avatar=member.avatar, color=member.color
            ),
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
            masquerade=self.sdk.MessageMasquerade(
                name=member.name, avatar=member.avatar, color=member.color
            ),
        )
        return posted.id

    async def deliver_speech(self, channel_id: str, proxy_message_id: str, audio: bytes) -> None:
        """Upload speech through persistent state, never the event-scoped message map."""
        channel = self.state.get_channel(channel_id)
        safe_id = "".join(character for character in proxy_message_id if character.isalnum() or character in "-_")[:48]
        filename = f"speech-{safe_id or 'proxy'}.mp3"
        await channel.send(attachments=[(filename, audio)], replies=[self.sdk.Reply(proxy_message_id)])


def _plurapack_bot_class(commands: Any) -> type:
    """Build the bot subclass after the optional Stoat dependency is loaded."""
    class PlurapackBot(commands.Bot):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.speech_queue: SpeechQueue | None = None
            self.speech_worker_count = 0
            self._speech_tasks: list[asyncio.Task[None]] = []

        async def setup_hook(self) -> None:
            await super().setup_hook()
            if self.speech_queue:
                self._speech_tasks = [
                    asyncio.create_task(speech_worker(self.speech_queue), name=f"speech-{index}")
                    for index in range(self.speech_worker_count)
                ]

        async def close(self, **kwargs: Any) -> None:
            for task in self._speech_tasks:
                task.cancel()
            if self._speech_tasks:
                await asyncio.gather(*self._speech_tasks, return_exceptions=True)
            self._speech_tasks.clear()
            await super().close(**kwargs)

    return PlurapackBot


def _positive_environment_integer(name: str, default: int, maximum: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer.") from error
    if not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}.")
    return value


def create_bot(prefix: str, database: str) -> Any:
    stoat, commands = _load_stoat()
    bot_class = _plurapack_bot_class(commands)
    bot = bot_class(command_prefix=prefix, description="Plural communication proxy")
    store = Store(database)
    platform = StoatPlatform({}, bot.state, stoat)
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

    @bot.command(aliases=[COMMAND_SHORTCUTS["setup"]])
    async def setup(ctx: commands.Context, *, name: str = "My system") -> None:
        system_id = store.create_system(ctx.author.id, name)
        await ctx.send(
            f"System `{system_id}` is ready. Origins, diagnoses, and proof are never required. "
            f"Voice playback defaults to Off. Add a member with `{prefix}member Name [text]`."
        )

    @bot.command(aliases=[COMMAND_SHORTCUTS["member"]])
    async def member(ctx: commands.Context, name: str, member_prefix: str, suffix: str = "") -> None:
        try:
            created = store.add_member(ctx.author.id, name, member_prefix, suffix)
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Added **{created.name}** (`{created.id}`); voice is Off.")

    @bot.command(name="import", aliases=[COMMAND_SHORTCUTS["import"]])
    async def import_system(ctx: commands.Context, source: str, *, document: str) -> None:
        """Import pasted JSON from PluralKit, Tupperbox, or Plurapack."""
        document = document.strip()
        if document.startswith("```") and document.endswith("```"):
            document = document[3:-3].removeprefix("json").lstrip()
        try:
            transfer = parse_import(source, document)
            if store.system_for(ctx.author.id) is None:
                store.create_system(ctx.author.id, transfer.name)
            count = store.import_members(ctx.author.id, transfer.members)
        except (PermissionError, TransferError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(
            f"Imported {count} member{'s' if count != 1 else ''} from {source}. "
            "External IDs and private message history were not copied."
        )

    @bot.command(aliases=[COMMAND_SHORTCUTS["export"]])
    async def export(ctx: commands.Context, format_name: str = "plurapack") -> None:
        """Export portable member metadata, excluding owners and message history."""
        try:
            system_name, members = store.export_system(ctx.author.id)
            document = export_document(format_name, system_name, members)
        except (PermissionError, TransferError) as error:
            await ctx.send(str(error))
            return
        safe_format = format_name.casefold().replace("-", "")
        await ctx.send(
            "Export ready. This file contains member names and proxy metadata; store it privately.",
            attachments=[(f"plurapack-{safe_format}-export.json", document)],
        )

    @bot.command(aliases=[COMMAND_SHORTCUTS["link"]])
    async def link(ctx: commands.Context) -> None:
        """Create a short-lived, single-use account connection code."""
        try:
            token = store.create_link(ctx.author.id)
        except PermissionError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Single-use code: `{token}` (expires in 15 minutes). Send it privately to the other owner.")

    @bot.command(aliases=[COMMAND_SHORTCUTS["color"]])
    async def color(ctx: commands.Context, member_id: str, value: str) -> None:
        """Associate a hex username color with an owned member ID."""
        try:
            configured = store.configure_color(ctx.author.id, member_id, value)
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Username color for **{configured.name}** (`{configured.id}`) is `{configured.color}`.")

    @bot.command(aliases=[COMMAND_SHORTCUTS["verify"]])
    async def verify(ctx: commands.Context, token: str) -> None:
        try:
            system_id = store.redeem_link(ctx.author.id, token)
        except PermissionError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Account connected to system `{system_id}`.")

    @bot.command(aliases=[COMMAND_SHORTCUTS["voice"]])
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

    @bot.command(aliases=[COMMAND_SHORTCUTS["voiceoff"]])
    async def voiceoff(ctx: commands.Context, selector: str) -> None:
        try:
            configured = store.configure_voice(ctx.author.id, selector, None, "{}", "off")
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Voice for **{configured.name}** is Off.")

    @bot.command(aliases=[COMMAND_SHORTCUTS["voiceformat"]])
    async def voiceformat(ctx: commands.Context, selector: str, enabled: str = "on",
                          strikethrough: str = "normal") -> None:
        """Opt into semantic Markdown speech and choose crossed-out delivery."""
        if enabled.lower() not in {"on", "off"}:
            await ctx.send("Formatting must be on or off.")
            return
        try:
            configured = store.configure_speech_formatting(
                ctx.author.id, selector, enabled.lower() == "on", strikethrough.lower()
            )
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        state = "On" if configured.speech_formatting else "Off"
        await ctx.send(f"Speech formatting for **{configured.name}** is {state}; crossed-out text is "
                       f"`{configured.strikethrough_speech}`.")

    @bot.command(name="alias", aliases=[COMMAND_SHORTCUTS["alias"]])
    async def member_alias(ctx: commands.Context, selector: str, alias: str = "") -> None:
        """Set a short selector while retaining the member's full display name."""
        try:
            configured = store.configure_alias(ctx.author.id, selector, alias or None)
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        status = f"`{configured.alias}`" if configured.alias else "cleared"
        await ctx.send(f"Alias for **{configured.name}** is {status}; their full name remains on proxies.")

    @bot.command(aliases=[COMMAND_SHORTCUTS["form"]])
    async def form(ctx: commands.Context, selector: str, display_name: str, picture: str = "", *,
                   soma: str = "") -> None:
        """Add a form: ``form MEMBER DISPLAY_NAME [PICTURE_URL] [SOMA]``."""
        try:
            created = store.create_form(ctx.author.id, selector, display_name, picture or None, soma)
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(
            f"Created form **{created.display_name}** (`{created.id}`) for member `{created.member_id}`. "
            f"Use `{prefix}front {created.id}` to switch both member and form."
        )

    @bot.command(aliases=[COMMAND_SHORTCUTS["front"]])
    async def front(ctx: commands.Context, selector: str) -> None:
        """Switch by member selector or form ID, resolving forms to their member."""
        try:
            selected = store.switch_front(ctx.author.id, selector)
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        if selected.form:
            await ctx.send(
                f"Front switched to **{selected.member.name}** as **{selected.form.display_name}** "
                f"(form `{selected.form.id}`, member `{selected.member.id}`)."
            )
        else:
            await ctx.send(f"Front switched to **{selected.member.name}** (`{selected.member.id}`).")

    @bot.command(aliases=[COMMAND_SHORTCUTS["defaultform"]])
    async def defaultform(ctx: commands.Context, member_selector: str,
                          form_selector: str = "off") -> None:
        """Set a member's default form by name/ID, or clear it with ``off``."""
        try:
            configured = store.configure_default_form(
                ctx.author.id, member_selector,
                None if form_selector.casefold() in {"off", "none"} else form_selector,
            )
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        if configured.default_form_id:
            selected = store.form_selected(ctx.author.id, configured.default_form_id)
            await ctx.send(
                f"Default form for **{configured.name}** is **{selected[0].display_name}** "
                f"(`{configured.default_form_id}`)."
            )
        else:
            await ctx.send(f"Default form for **{configured.name}** is cleared.")

    @bot.command(aliases=[COMMAND_SHORTCUTS["autoproxy"]])
    async def autoproxy(ctx: commands.Context, selector: str) -> None:
        """Set an independent untagged-message proxy member, or turn it off."""
        try:
            configured = store.configure_autoproxy(
                ctx.author.id, None if selector.casefold() == "off" else selector
            )
        except PermissionError as error:
            await ctx.send(str(error))
            return
        if configured.member:
            await ctx.send(f"Autoproxy is On for **{configured.member.name}** (`{configured.member.id}`).")
        else:
            await ctx.send("Autoproxy is Off.")

    @bot.command(aliases=[COMMAND_SHORTCUTS["autofront"]])
    async def autofront(ctx: commands.Context, enabled: str) -> None:
        """Opt in to updating autoproxy from the first/current fronter."""
        if enabled.casefold() not in {"on", "off"}:
            await ctx.send("Autofront must be on or off.")
            return
        try:
            configured = store.configure_autofront(ctx.author.id, enabled.casefold() == "on")
        except PermissionError as error:
            await ctx.send(str(error))
            return
        detail = f" Autoproxy is now **{configured.member.name}**." if configured.member else ""
        await ctx.send(f"Autofront is {'On' if configured.autofront else 'Off'}.{detail}")

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
    try:
        bot = create_bot(
            os.environ.get("PLURAPACK_PREFIX", "p;"),
            os.environ.get("PLURAPACK_DATABASE", "plurapack.sqlite3"),
        )
    except StoatDependencyError as error:
        raise SystemExit(str(error)) from None
    bot.run(token)


if __name__ == "__main__":
    main()
