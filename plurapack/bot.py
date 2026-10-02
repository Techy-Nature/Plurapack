"""Thin stoat.py adapter. Importing this module never connects to Stoat."""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import wave
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .proxy import Incoming, ProxyService
from .chatterbox import ChatterboxBackend
from .speech import SpeechQueue, speech_worker
from .browser_audio import BrowserAudioStore
from .storage import Member, Store, System
from .login import LoginError, LoginService
from .transfer import TransferError, export_system as create_export, import_system as apply_import
from .config import StorageConfigurationError, resolve_database_path


STOAT_INSTALL_MESSAGE = (
    "The Stoat client dependency is not installed. Install Plurapack and its "
    "dependencies with `python -m pip install -e .`, then try again."
)

# This is the canonical public-command catalogue. Command registration, in-chat
# help, and tests all consume it so adding a command without documenting it is
# difficult to do accidentally. Values are shortcut, usage suffix, and summary.
COMMAND_HELP = {
    "help": ("h", "[COMMAND]", "List every command or show details for one."),
    "login": ("lg", "CODE", "Approve a one-time dashboard login."),
    "setup": ("s", "[SYSTEM_NAME] [DESCRIPTION]", "Create your system."),
    "member": (
        "m", "NAME PREFIX [SUFFIX] [--avatar URL|--a URL] [DESCRIPTION]",
        "Add a member and proxy tag.",
    ),
    "avatar": ("av", "--member MEMBER URL|--form FORM URL|--system URL", "Change a profile avatar."),
    "banner": ("bn", "--member MEMBER URL|--form FORM URL|--system URL", "Change a profile banner."),
    "memberproxy": ("mt", "MEMBER [PREFIX] [SUFFIX]", "Add or list member proxy tags."),
    "memberproxy-clear": ("mc", "MEMBER", "Clear every proxy tag from a member."),
    "alias": ("a", "MEMBER [ALIAS]", "Set or clear a member selector."),
    "form": ("f", "MEMBER DISPLAY_NAME [PICTURE_URL] [SOMA]", "Create an alternate presentation."),
    "formproxy": ("ft", "FORM [PREFIX] [SUFFIX]", "Add or list form proxy tags."),
    "formproxy-clear": ("fc", "FORM", "Clear every proxy tag from a form."),
    "defaultform": ("df", "MEMBER [FORM_OR_OFF]", "Set or clear a default form."),
    "pronouns": ("p", "MEMBER [PRONOUNS]", "Set or clear member pronouns."),
    "formpronouns": ("fp", "FORM [PRONOUNS]", "Set or inherit form pronouns."),
    "systemtag": ("st", "[TAG]", "Set or clear the system name tag."),
    "systemtagshow": ("ts", "SCOPE on|off|default", "Control system tag visibility."),
    "front": ("fr", "MEMBER_OR_FORM", "Switch the current member and form."),
    "autoproxy": ("ap", "MEMBER_OR_OFF", "Configure untagged-message proxying."),
    "autofront": ("af", "on|off", "Make autoproxy follow front switches."),
    "color": ("c", "MEMBER HEX", "Set a member username color."),
    "link": ("l", "", "Create a single-use account link code."),
    "verify": ("v", "CODE", "Connect an account with a link code."),
    "import": ("i", "FORMAT JSON", "Import PluralKit, Tupperbox, or Plurapack JSON."),
    "export": ("x", "[FORMAT] [--forms-loss|--forms-members]", "Export portable system metadata."),
    "viewinfo": ("vi", "[SYSTEM_OR_MEMBER]", "Show system or member information."),
    "info": ("in", "SYSTEM_MEMBER_OR_FORM", "Show a system, member, or form profile."),
    "group": ("g", "create|add|alias|avatar ...", "Create and edit the current group."),
    "viewmembers": ("ml", "[SYSTEM]", "Show a system's member cards."),
    "viewmember": ("vm", "MEMBER", "Show one member card."),
    "deletemember": ("dm", "MEMBER", "Permanently delete an owned member."),
    "deletesystem": ("ds", "", "Start permanent system deletion."),
    "voice": ("vo", "MEMBER [PLAYBACK] [SETTINGS] + WAV/MP3", "Upload speech reference audio."),
    "voiceoff": ("of", "MEMBER", "Disable speech for a member."),
    "voiceformat": ("vf", "MEMBER on|off [MODE]", "Configure semantic speech formatting."),
}

COMMAND_SHORTCUTS = {name: details[0] for name, details in COMMAND_HELP.items()}

# Compatibility names people commonly try based on the nouns used by other
# plural proxy bots and by Plurapack's own UI.
COMMAND_ALIASES = {"front": ["fronter"], "viewinfo": ["view"]}

PREVIOUS_EMOJI = "⬅️"
NEXT_EMOJI = "➡️"


_MEMBER_AVATAR_OPTION = re.compile(r"(?<!\S)(?:--avatar|--a)(?:\s+(\S+))?")
_PROFILE_TARGETS = {
    "--member": "member", "-m": "member", "--m": "member",
    "--form": "form", "-f": "form", "--f": "form",
    "--system": "system", "-s": "system", "--s": "system",
}


def _member_creation_options(suffix: str, description: str) -> tuple[str, str, str | None]:
    """Extract the member avatar option without changing the command parser API."""
    combined = f"{suffix} {description}".strip()
    match = _MEMBER_AVATAR_OPTION.search(combined)
    if match is None:
        return suffix, description, None
    avatar = match.group(1)
    if not avatar:
        raise ValueError("The avatar option requires an image URL.")
    parsed = urllib.parse.urlsplit(avatar)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("The avatar must be a complete HTTP or HTTPS URL.")

    # A flag in the suffix position means no suffix was supplied. Otherwise,
    # only remove it from the free-form description.
    if suffix in {"--avatar", "--a"}:
        return "", description[len(avatar):].lstrip(), avatar
    cleaned_description = _MEMBER_AVATAR_OPTION.sub("", description, count=1).strip()
    return suffix, cleaned_description, avatar


def _profile_image_options(arguments: str) -> tuple[str, str | None, str]:
    """Parse one explicitly targeted avatar or banner update."""
    try:
        values = shlex.split(arguments)
    except ValueError as error:
        raise ValueError(f"Invalid arguments: {error}") from error
    if not values or values[0] not in _PROFILE_TARGETS:
        raise ValueError("Choose exactly one target: --member, --form, or --system.")
    target = _PROFILE_TARGETS[values[0]]
    expected = 2 if target == "system" else 3
    if len(values) != expected:
        raise ValueError(
            f"{values[0]} requires {'a selector and an image URL' if target != 'system' else 'an image URL'}."
        )
    selector = None if target == "system" else values[1]
    url = values[-1]
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("The image must be a complete HTTP or HTTPS URL.")
    return target, selector, url


def _update_profile_image(
    store: Store, account_id: str, field: str, arguments: str
) -> tuple[str, str]:
    """Update an owned member, form, or current system image field."""
    target, selector, url = _profile_image_options(arguments)
    if target == "member":
        member = store.member_selected(account_id, selector or "")
        if member is None:
            raise PermissionError("Member not found or not owned by this account.")
        updated = store.update_member(account_id, member.id, **{field: url})
        return updated.name, url
    if target == "form":
        selected = store.form_selected(account_id, selector or "")
        if selected is None:
            raise PermissionError("Form not found or not owned by this account.")
        form, _ = selected
        updated = store.update_form(account_id, form.id, **{field: url})
        return updated.display_name, url
    system_id = store.system_for(account_id)
    if system_id is None:
        raise PermissionError("Create a system first.")
    system_field = "logo" if field == "avatar" else field
    updated = store.update_system(account_id, system_id, **{system_field: url})
    return updated.display_name, url


def _help_pages(prefix: str, selector: str = "", limit: int = 1800) -> list[str]:
    """Build help from the command catalogue, within conservative chat limits."""
    selected = selector.casefold().strip()
    if selected:
        selected = next(
            (name for name, details in COMMAND_HELP.items()
             if selected == name or selected == details[0] or selected in COMMAND_ALIASES.get(name, [])),
            "",
        )
        if not selected:
            return [f"Unknown command `{selector}`. Use `{prefix}help` to list every command."]
        shortcut, usage, summary = COMMAND_HELP[selected]
        invocation = f"{prefix}{selected}{f' {usage}' if usage else ''}"
        aliases = [shortcut, *COMMAND_ALIASES.get(selected, [])]
        return [f"**{selected}** — {summary}\nUsage: `{invocation}`\nAliases: "
                + ", ".join(f"`{prefix}{alias}`" for alias in aliases)]

    heading = f"**Plurapack commands**\nUse `{prefix}help COMMAND` for details.\n"
    lines = []
    for name, (shortcut, usage, summary) in COMMAND_HELP.items():
        invocation = f"{prefix}{name}{f' {usage}' if usage else ''}"
        lines.append(f"`{invocation}` (`{shortcut}`) — {summary}")
    pages, page = [], heading
    for line in lines:
        if len(page) + len(line) + 1 > limit:
            pages.append(page.rstrip())
            page = "**Plurapack commands (continued)**\n"
        page += line + "\n"
    pages.append(page.rstrip())
    return pages


def _print_cli_status(message: str) -> None:
    """Write a status line immediately for operators watching the process."""
    print(f"[Plurapack] {message}", flush=True)


def _register_cli_status_listeners(bot: Any, stoat: Any, prefix: str) -> None:
    """Report the distinct WebSocket-connected and fully-ready states."""
    @bot.listen(stoat.AfterConnectEvent)
    async def connected_listener(event: Any) -> None:
        _print_cli_status("Connected to Stoat.")

    @bot.listen(stoat.ReadyEvent)
    async def ready_listener(event: Any) -> None:
        _print_cli_status(f"Ready to use. Command prefix: {prefix}")


def _member_embed(sdk: Any, store: Store, member: Member) -> Any:
    """Build one portable Stoat member card; clients size embeds responsively."""
    forms = store.forms_for_member(member.id)
    default = next((form for form in forms if form.id == member.default_form_id), None)
    avatar = default.avatar if default and default.avatar else member.avatar
    form_lines = []
    for form in forms:
        marker = " ★ default" if form.id == member.default_form_id else ""
        preview = f"[▣]({form.avatar} \"{form.display_name} preview\") " if form.avatar else ""
        form_pronouns = form.pronouns if form.pronouns is not None else member.pronouns
        pronoun_note = f" — {form_pronouns}" if form_pronouns else ""
        proxy_note = f" — proxy `{form.prefix}text{form.suffix}`" if form.prefix else ""
        form_lines.append(f"{preview}**{form.display_name}** (`{form.id}`){pronoun_note}{proxy_note}{marker}")
    description = member.description or (default.soma if default else "")
    description = description or "No member description provided."
    description += (
        f"\n\n**ID:** `{member.id}`\n**Color:** `{member.color or 'default'}`"
        f"\n**Pronouns:** {member.pronouns or 'Not set'}"
        f"\n**Default form:** {default.display_name if default else 'Member profile'}"
        f"\n**Forms:**\n" + ("\n".join(form_lines) if form_lines else "None")
    )
    return sdk.SendableEmbed(title=member.name, description=description,
                             icon_url=avatar, color=member.color)


def _system_embed(sdk: Any, system: System, member_count: int) -> Any:
    description = system.description or "No system description provided."
    description += f"\n\n**System ID:** `{system.id}`\n**Members:** {member_count}"
    return sdk.SendableEmbed(title=system.display_name, description=description,
                             icon_url=system.logo)


def _profile_embed(sdk: Any, store: Store, value: System | Member | tuple[Any, Member]) -> Any:
    """Build the compact, common profile requested by the ``info`` command."""
    if isinstance(value, System):
        return sdk.SendableEmbed(
            title=value.display_name,
            description=(value.description or "No description provided.") + f"\n\n**ID:** `{value.id}`",
            icon_url=value.logo, media=value.banner,
        )
    if isinstance(value, tuple):
        form, member = value
        tags = store.proxy_tags(form_id=form.id)[:3]
        name, identifier, avatar, pronouns, description, banner = (
            form.display_name, form.id, form.avatar or member.avatar,
            form.pronouns if form.pronouns is not None else member.pronouns,
            form.soma or member.description, form.banner or member.banner,
        )
    else:
        member = value
        tags = store.proxy_tags(member_id=member.id)[:3]
        name, identifier, avatar, pronouns, description, banner = (
            member.name, member.id, member.avatar, member.pronouns, member.description, member.banner,
        )
    proxies = ", ".join(f"`{tag.prefix}text{tag.suffix}`" for tag in tags) or "None"
    body = (description or "No description provided.") + (
        f"\n\n**ID:** `{identifier}`\n**Pronouns:** {pronouns or 'Not set'}\n**Proxies:** {proxies}"
    )
    return sdk.SendableEmbed(title=name, description=body, icon_url=avatar, media=banner)


def _install_voice_attachment(attachment: Any, reference_dir: Path, max_bytes: int = 25 * 1024 * 1024) -> str:
    """Download and atomically install a WAV/MP3 attachment as a validated WAV."""
    filename = str(getattr(attachment, "filename", getattr(attachment, "name", "reference")))
    extension = Path(filename).suffix.casefold()
    if extension not in {".wav", ".mp3"}:
        raise ValueError("Voice reference must be a WAV or MP3 attachment.")
    safe_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(filename).stem).strip(".-") or "reference"
    target_name = safe_stem[:80] + ".wav"
    url = str(getattr(attachment, "url", ""))
    if not url.startswith(("http://", "https://")):
        raise ValueError("The attached voice reference has no downloadable URL.")
    reference_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=reference_dir) as workspace:
        source = Path(workspace) / ("source" + extension)
        request = urllib.request.Request(url, headers={"User-Agent": "Plurapack/voice-upload"})
        with urllib.request.urlopen(request, timeout=30) as response, source.open("wb") as output:
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("Voice reference must be no larger than 25 MiB.")
            output.write(data)
        converted = Path(workspace) / "converted.wav"
        if extension == ".mp3":
            if shutil.which("ffmpeg") is None:
                raise ValueError("MP3 conversion requires ffmpeg on the bot host.")
            result = subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(source), "-ac", "1", "-ar", "24000", str(converted)],
                capture_output=True, timeout=60,
            )
            if result.returncode:
                raise ValueError("The MP3 attachment could not be converted to WAV.")
        else:
            converted = source
        try:
            with wave.open(str(converted), "rb") as audio:
                if audio.getnframes() < 1:
                    raise ValueError("Voice reference contains no audio.")
        except (wave.Error, EOFError) as error:
            raise ValueError("The attachment is not valid audio.") from error
        temporary_target = reference_dir / (target_name + ".new")
        shutil.copyfile(converted, temporary_target)
        os.replace(temporary_target, reference_dir / target_name)
    return target_name


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

    async def proxy_content(self, channel_id: str, proxy_id: str) -> str:
        return (await self.state.http.get_message(channel_id, proxy_id)).content

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

        async def on_command_error(self, event: Any, /) -> None:
            """Turn ordinary command mistakes into useful chat responses."""
            error = event.error
            ctx = event.context
            if isinstance(error, commands.MissingRequiredArgument):
                await ctx.send(
                    f"Missing required argument `{error.parameter.name}`. "
                    f"Use `{self.command_prefix}help {ctx.command.qualified_name}` for usage."
                )
                return
            if isinstance(error, commands.CommandNotFound):
                await ctx.send(
                    f"Command not found. Use `{self.command_prefix}help` to see available commands."
                )
                return
            await super().on_command_error(event)

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
        browser_audio = BrowserAudioStore.configured(database)
        speech_queue = SpeechQueue(
            ChatterboxBackend(tts_url),
            lambda job, audio: platform.deliver_speech(
                job.channel_id, job.proxy_message_id, audio),
            queue_limit,
            lambda job, audio: browser_audio.publish(
                job.account_id, job.proxy_message_id, job.generation, audio),
            browser_audio.invalidate,
        )
        bot.speech_queue = speech_queue
    service = ProxyService(store, platform, prefix, speech_queue)
    login_service = LoginService(store)
    _register_cli_status_listeners(bot, stoat, prefix)
    # These controls are intentionally ephemeral: restarting the bot closes old
    # pagers and invalidates outstanding destructive confirmations.
    info_pages: dict[str, tuple[str, str, list[list[Any]], int]] = {}
    delete_confirmations: dict[str, tuple[str, str]] = {}

    async def send_pages(ctx: commands.Context, pages: list[list[Any]]) -> None:
        posted = await ctx.send(embeds=pages[0])
        if len(pages) > 1:
            info_pages[posted.id] = (ctx.author.id, posted.channel_id, pages, 0)
            reactions = (
                os.environ.get("PLURAPACK_PREVIOUS_EMOJI_ID") or PREVIOUS_EMOJI,
                os.environ.get("PLURAPACK_NEXT_EMOJI_ID") or NEXT_EMOJI,
            )
            for emoji in reactions:
                await bot.state.http.add_reaction_to_message(posted.channel_id, posted.id, emoji)

    @bot.command(aliases=[COMMAND_SHORTCUTS["help"]])
    async def help(ctx: commands.Context, *, command_name: str = "") -> None:
        """List all current commands or explain one command in detail."""
        for page in _help_pages(prefix, command_name):
            await ctx.send(page)

    @bot.command(aliases=[COMMAND_SHORTCUTS["login"]])
    async def login(ctx: commands.Context, code: str) -> None:
        """Prove dashboard identity using the authenticated message author."""
        username = (getattr(ctx.author, "display_name", None)
                    or getattr(ctx.author, "name", None)
                    or getattr(ctx.author, "username", None)
                    or str(ctx.author.id))
        try:
            login_service.verify(code, str(ctx.author.id), str(username))
        except LoginError as error:
            await ctx.send(str(error))
            return
        await ctx.send("Dashboard login approved. You can return to your browser.")

    @bot.command(aliases=[COMMAND_SHORTCUTS["setup"]])
    async def setup(ctx: commands.Context, name: str = "My system", *, description: str = "") -> None:
        try:
            system_id = store.create_system(ctx.author.id, name, description)
        except ValueError as error:
            await ctx.send(str(error))
            return
        await ctx.send(
            f"System `{system_id}` is ready. Origins, diagnoses, and proof are never required. "
            f"Voice playback defaults to Off. Add a member with `{prefix}member Name [text]`."
        )

    @bot.command(aliases=[COMMAND_SHORTCUTS["member"]])
    async def member(ctx: commands.Context, name: str, member_prefix: str, suffix: str = "", *,
                     description: str = "") -> None:
        try:
            suffix, description, avatar = _member_creation_options(suffix, description)
            created = store.add_member(
                ctx.author.id, name, member_prefix, suffix, description, avatar=avatar
            )
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Added **{created.name}** (`{created.id}`); voice is Off.")

    async def update_image(ctx: commands.Context, field: str, arguments: str) -> None:
        try:
            name, _ = _update_profile_image(store, str(ctx.author.id), field, arguments)
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Updated the {field} for **{name}**.")

    @bot.command(aliases=[COMMAND_SHORTCUTS["avatar"]])
    async def avatar(ctx: commands.Context, *, arguments: str = "") -> None:
        await update_image(ctx, "avatar", arguments)

    @bot.command(aliases=[COMMAND_SHORTCUTS["banner"]])
    async def banner(ctx: commands.Context, *, arguments: str = "") -> None:
        await update_image(ctx, "banner", arguments)

    @bot.command(aliases=[COMMAND_SHORTCUTS["deletemember"]])
    async def deletemember(ctx: commands.Context, *, selector: str) -> None:
        try:
            deleted = store.delete_member(ctx.author.id, selector)
        except PermissionError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Deleted **{deleted.name}** (`{deleted.id}`) and all of their forms and records.")

    @bot.command(aliases=[COMMAND_SHORTCUTS["deletesystem"]])
    async def deletesystem(ctx: commands.Context) -> None:
        system_id = store.system_for(ctx.author.id)
        if system_id is None:
            await ctx.send("Create a system first.")
            return
        posted = await ctx.send(
            "⚠️ **Permanent system deletion**\n"
            "This erases the system, every member and form, linked owners, settings, links, and proxy records. "
            f"Use `{prefix}export plurapack` first if you may need a private JSON backup.\n\n"
            f"To confirm, **reply to this message** with the system ID exactly: `{system_id}`"
        )
        delete_confirmations[posted.id] = (ctx.author.id, system_id)

    def selected_system(account_id: str, selector: str | None) -> System | None:
        value = selector or store.system_for(account_id)
        return store.system_info(value) if value else None

    @bot.command(aliases=[COMMAND_SHORTCUTS["viewinfo"], *COMMAND_ALIASES["viewinfo"]])
    async def viewinfo(ctx: commands.Context, *, selector: str = "") -> None:
        try:
            system = selected_system(ctx.author.id, selector or None)
            if system:
                members = store.members_for_system(system.id)
                pages = [[_system_embed(stoat, system, len(members))]]
                pages.extend([[_member_embed(stoat, store, member) for member in members[index:index + 2]]
                              for index in range(0, len(members), 2)])
                await send_pages(ctx, pages)
                return
            member_value = store.public_member_selected(selector)
        except ValueError as error:
            await ctx.send(str(error))
            return
        if member_value is None:
            await ctx.send("System or member not found. Use an exact name or stable ID.")
            return
        await send_pages(ctx, [[_member_embed(stoat, store, member_value)]])

    @bot.command(aliases=[COMMAND_SHORTCUTS["viewmembers"]])
    async def viewmembers(ctx: commands.Context, *, system_selector: str = "") -> None:
        try:
            system = selected_system(ctx.author.id, system_selector or None)
        except ValueError as error:
            await ctx.send(str(error))
            return
        if system is None:
            await ctx.send("System not found. Use an exact name or stable ID.")
            return
        members = store.members_for_system(system.id)
        if not members:
            await ctx.send(f"**{system.display_name}** has no members.")
            return
        await send_pages(ctx, [[_member_embed(stoat, store, member) for member in members[index:index + 2]]
                               for index in range(0, len(members), 2)])

    @bot.command(aliases=[COMMAND_SHORTCUTS["viewmember"]])
    async def viewmember(ctx: commands.Context, *, selector: str) -> None:
        try:
            selected = store.public_member_selected(selector)
        except ValueError as error:
            await ctx.send(str(error))
            return
        if selected is None:
            await ctx.send("Member not found. Use an exact name or stable ID.")
            return
        await send_pages(ctx, [[_member_embed(stoat, store, selected)]])

    @bot.command(aliases=[COMMAND_SHORTCUTS["info"]])
    async def info(ctx: commands.Context, *, selector: str) -> None:
        """Show a system, member, or form selected by nickname, alias, or ID."""
        try:
            value = store.public_info_selected(str(ctx.author.id), selector)
        except ValueError as error:
            await ctx.send(str(error))
            return
        if value is None:
            await ctx.send("System, member, or form not found. Use an exact nickname, alias, or ID.")
            return
        await ctx.send(embeds=[_profile_embed(stoat, store, value)])

    @bot.command(aliases=[COMMAND_SHORTCUTS["group"]])
    async def group(ctx: commands.Context, action: str, *, arguments: str = "") -> None:
        """Dispatch group subcommands while keeping their argument rules independent."""
        try:
            values = shlex.split(arguments)
        except ValueError as error:
            await ctx.send(f"Invalid quoting: {error}")
            return
        action = action.casefold()
        try:
            if action == "create":
                if len(values) != 2:
                    raise ValueError(f'Usage: `{prefix}group create "name" alias`')
                configured = store.create_group(ctx.author.id, values[0], values[1])
                await ctx.send(f"Created group **{configured.name}** (`{configured.id}`), alias `{configured.alias}`.")
            elif action == "add":
                configured, members = store.add_group_members(ctx.author.id, values)
                await ctx.send(f"Added {len(members)} member(s) to **{configured.name}**.")
            elif action == "alias":
                if len(values) != 1:
                    raise ValueError(f'Usage: `{prefix}group alias "new-alias"`')
                configured = store.update_active_group(ctx.author.id, alias=values[0].strip())
                await ctx.send(f"Group alias is now `{configured.alias}`.")
            elif action == "avatar":
                if len(values) != 1:
                    raise ValueError(f"Usage: `{prefix}group avatar IMAGE_URL`")
                configured = store.update_active_group(ctx.author.id, avatar=values[0])
                await ctx.send(f"Avatar updated for **{configured.name}**.")
            else:
                raise ValueError("Group action must be create, add, alias, or avatar.")
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))

    @bot.command(name="import", aliases=[COMMAND_SHORTCUTS["import"]])
    async def import_system(ctx: commands.Context, source: str = "", *, document: str = "") -> None:
        """Import pasted JSON from PluralKit, Tupperbox, or Plurapack."""
        document = document.strip()
        strategy = "merge"
        option_words = [word for word in (source + " " + document).split() if word.startswith("--")]
        for word in option_words:
            if word in {"--merge", "--skip-existing", "--overwrite"}:
                strategy = word[2:]
        if document and all(word.startswith("--") for word in document.split()):
            document = ""
        if strategy == "overwrite" and "--confirm-overwrite" not in option_words:
            await ctx.send("Overwrite can replace matching records. Back up first, then repeat with --overwrite --confirm-overwrite.")
            return
        if not document:
            attachments = getattr(getattr(ctx, "message", None), "attachments", ())
            if attachments:
                attachment = attachments[0]
                url = getattr(attachment, "url", None)
                try:
                    if not url:
                        raise ValueError("The attachment does not provide a download URL.")
                    def download() -> str:
                        with urllib.request.urlopen(url, timeout=15) as response:
                            data = response.read(5 * 1024 * 1024 + 1)
                        if len(data) > 5 * 1024 * 1024:
                            raise ValueError("Import exceeds the 5 MiB upload limit.")
                        return data.decode("utf-8")
                    document = await asyncio.to_thread(download)
                except (OSError, UnicodeError, ValueError) as error:
                    await ctx.send(f"Could not read the JSON attachment: {error}")
                    return
        if not document:
            await ctx.send("Attach a JSON file (maximum 5 MiB), then run import again.")
            return
        if document.startswith("```") and document.endswith("```"):
            document = document[3:-3].removeprefix("json").lstrip()
        try:
            words = source.split()
            source = words[0] if words and not words[0].startswith("--") else ""
            report = apply_import(store, ctx.author.id, document, source or None, strategy)
        except (PermissionError, TransferError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(
            f"Import complete. Members imported: {report.members_imported}; groups imported: "
            f"{report.groups_imported}; proxy tags imported: {report.proxy_tags_imported}; "
            f"existing members skipped: {report.existing_members_skipped}; warnings: {len(report.warnings)}."
        )

    @bot.command(aliases=[COMMAND_SHORTCUTS["export"]])
    async def export(ctx: commands.Context, format_name: str = "plurapack", *, options: str = "") -> None:
        """Export portable member metadata, excluding owners and message history."""
        try:
            choices = options.split()
            unknown = set(choices) - {"--forms-loss", "--forms-members"}
            if unknown:
                raise TransferError(f"Unknown export option: {sorted(unknown)[0]}")
            if "--forms-loss" in choices and "--forms-members" in choices:
                raise TransferError("Choose either --forms-loss or --forms-members, not both.")
            forms_mode = "members" if "--forms-members" in choices else "loss"
            document, report = create_export(store, ctx.author.id, format_name,
                                             forms_mode=forms_mode)
        except (PermissionError, TransferError) as error:
            await ctx.send(str(error))
            return
        safe_format = format_name.casefold().replace("-", "")
        from datetime import date
        filename = (f"plurapack-backup-{date.today().isoformat()}.json" if safe_format == "plurapack"
                    else f"plurapack-{safe_format}-export-{date.today().isoformat()}.json")
        await ctx.send(
            f"Export ready. Members: {len(store.export_system(ctx.author.id)[1])}. Warnings: {len(report.warnings)}. Store it privately.",
            attachments=[(filename, document)],
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

    @bot.command(aliases=[COMMAND_SHORTCUTS["pronouns"]])
    async def pronouns(ctx: commands.Context, selector: str, *, value: str = "") -> None:
        """Set member pronouns, or omit the value to clear them."""
        try:
            configured = store.configure_pronouns(ctx.author.id, selector, value or None)
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Pronouns for **{configured.name}** are {configured.pronouns or 'now cleared'}.")

    @bot.command(aliases=[COMMAND_SHORTCUTS["formpronouns"]])
    async def formpronouns(ctx: commands.Context, selector: str, *, value: str = "") -> None:
        """Set a form-specific override, or clear it to inherit member pronouns."""
        try:
            configured = store.configure_form_pronouns(ctx.author.id, selector, value or None)
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        result = configured.pronouns or "inherit from the member"
        await ctx.send(f"Pronouns for form **{configured.display_name}** now {result}.")

    @bot.command(aliases=[COMMAND_SHORTCUTS["verify"]])
    async def verify(ctx: commands.Context, token: str) -> None:
        try:
            system_id = store.redeem_link(ctx.author.id, token)
        except PermissionError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Account connected to system `{system_id}`.")

    @bot.command(aliases=[COMMAND_SHORTCUTS["voice"]])
    async def voice(ctx: commands.Context, selector: str, playback: str = "send", *,
                    settings: str = "{}") -> None:
        """Install an attached WAV/MP3, replacing an earlier upload with the same name."""
        reference_dir_value = os.environ.get("PLURAPACK_VOICE_REFERENCE_DIR")
        if not reference_dir_value:
            await ctx.send("Voice configuration is disabled until the operator sets PLURAPACK_VOICE_REFERENCE_DIR.")
            return
        attachments = list(getattr(ctx.message, "attachments", ()) or ())
        if len(attachments) != 1:
            await ctx.send("Attach exactly one WAV or MP3 voice reference.")
            return
        reference_dir = Path(reference_dir_value).expanduser().resolve()
        try:
            reference = await asyncio.to_thread(_install_voice_attachment, attachments[0], reference_dir)
            configured = store.configure_voice(
                ctx.author.id, selector, reference, settings, playback
            )
        except (OSError, TimeoutError, PermissionError, ValueError, json.JSONDecodeError) as error:
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

    @bot.command(aliases=[COMMAND_SHORTCUTS["memberproxy"]])
    async def memberproxy(ctx: commands.Context, selector: str, member_prefix: str = "",
                          suffix: str = "") -> None:
        """Add a member proxy tag, or list tags when one is omitted."""
        if not member_prefix:
            member = store.member_selected(ctx.author.id, selector)
            if member is None:
                await ctx.send("Member not found or not owned by this account.")
                return
            tags = store.proxy_tags(member_id=member.id)
            listing = "\n".join(f"{index}. `{tag.prefix}text{tag.suffix}`"
                                for index, tag in enumerate(tags, 1)) or "No proxy tags are configured."
            await ctx.send(f"Proxy tags for **{member.name}** ({len(tags)}/100):\n{listing}")
            return
        try:
            configured = store.configure_member_proxy(ctx.author.id, selector, member_prefix, suffix)
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Added proxy tag `{member_prefix}text{suffix}` for **{configured.name}**.")

    @bot.command(name="memberproxy-clear", aliases=[COMMAND_SHORTCUTS["memberproxy-clear"]])
    async def memberproxy_clear(ctx: commands.Context, selector: str) -> None:
        try:
            configured = store.configure_member_proxy(ctx.author.id, selector, None)
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"All proxy tags for **{configured.name}** are cleared.")

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
            f"Use `{prefix}formproxy {created.id} PREFIX [SUFFIX]` to give it a proxy tag, or "
            f"`{prefix}front {created.id}` to switch both member and form."
        )

    @bot.command(aliases=[COMMAND_SHORTCUTS["formproxy"]])
    async def formproxy(ctx: commands.Context, selector: str, form_prefix: str = "",
                        suffix: str = "") -> None:
        """Add a form tag, or list tags when the prefix is omitted."""
        if not form_prefix:
            selected = store.form_selected(ctx.author.id, selector)
            if selected is None:
                await ctx.send("Form not found or not owned by this account.")
                return
            configured = selected[0]
            tags = store.proxy_tags(form_id=configured.id)
            listing = "\n".join(f"{index}. `{tag.prefix}text{tag.suffix}`"
                                for index, tag in enumerate(tags, 1)) or "No proxy tags are configured."
            await ctx.send(f"Proxy tags for **{configured.display_name}** ({len(tags)}/100):\n{listing}")
            return
        try:
            configured = store.configure_form_proxy(
                ctx.author.id, selector, form_prefix, suffix
            )
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(
            f"Added proxy tag for **{configured.display_name}**: "
            f"`{form_prefix}text{suffix}`."
        )

    @bot.command(name="formproxy-clear", aliases=[COMMAND_SHORTCUTS["formproxy-clear"]])
    async def formproxy_clear(ctx: commands.Context, selector: str) -> None:
        try:
            configured = store.configure_form_proxy(ctx.author.id, selector, None)
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"All proxy tags for **{configured.display_name}** are cleared.")

    @bot.command(aliases=[COMMAND_SHORTCUTS["front"], *COMMAND_ALIASES["front"]])
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

    @bot.command(aliases=[COMMAND_SHORTCUTS["systemtag"]])
    async def systemtag(ctx: commands.Context, *, tag: str = "") -> None:
        """Set the tag appended to every member and form name, or clear it."""
        try:
            configured = store.configure_system_tag(ctx.author.id, tag or None)
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        if configured.system_tag:
            await ctx.send(f"System tag is now **{configured.system_tag}**.")
        else:
            await ctx.send("System tag is cleared.")

    @bot.command(aliases=[COMMAND_SHORTCUTS["systemtagshow"]])
    async def systemtagshow(ctx: commands.Context, scope: str, setting: str) -> None:
        """Control tag visibility system-wide or override it here."""
        scope, setting = scope.casefold(), setting.casefold()
        if scope not in {"system", "server", "channel"}:
            await ctx.send("Scope must be system, server, or channel.")
            return
        if setting not in {"on", "off", "default"} or (scope == "system" and setting == "default"):
            await ctx.send("Setting must be on or off; server/channel also accept default.")
            return
        message = ctx.message
        scope_id = None
        if scope == "channel":
            scope_id = message.channel_id
        elif scope == "server":
            scope_id = getattr(message, "server_id", None)
            if scope_id is None:
                await ctx.send("This command must be used in a server to set a server override.")
                return
        try:
            if setting == "default":
                store.clear_system_tag_visibility_override(ctx.author.id, scope, scope_id)
            else:
                store.configure_system_tag_visibility(
                    ctx.author.id, setting == "on", scope, scope_id
                )
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"System tag visibility for this {scope} is now **{setting.title()}**.")

    @bot.listen(stoat.MessageCreateEvent)
    async def proxy_listener(event: stoat.MessageCreateEvent) -> None:
        message = event.message
        author = message.get_author()
        if author is None:
            return
        reply_id = None
        if message.replies:
            reply = message.replies[0]
            reply_id = reply if isinstance(reply, str) else getattr(reply, "id", None)
        confirmation = delete_confirmations.get(reply_id) if reply_id else None
        if confirmation and confirmation[0] == author.id:
            try:
                store.delete_system(author.id, message.content.strip())
            except PermissionError:
                await message.reply("Deletion cancelled: that reply did not exactly match the system ID.")
            else:
                delete_confirmations.pop(reply_id, None)
                await message.reply(f"System `{confirmation[1]}` and all associated data were permanently deleted.")
            return
        incoming = Incoming(
            message.id,
            message.channel_id,
            author.id,
            message.content,
            bool(getattr(author, "bot", None)),
            message.replies[0] if message.replies else None,
            getattr(message, "server_id", None),
        )
        platform.messages[message.id] = message
        try:
            await service.handle(incoming)
        finally:
            platform.messages.pop(message.id, None)

    @bot.listen(stoat.MessageReactEvent)
    async def reaction_listener(event: stoat.MessageReactEvent) -> None:
        pager = info_pages.get(event.message_id)
        previous_values = {PREVIOUS_EMOJI, os.environ.get("PLURAPACK_PREVIOUS_EMOJI_ID")}
        next_values = {NEXT_EMOJI, os.environ.get("PLURAPACK_NEXT_EMOJI_ID")}
        if pager and event.user_id == pager[0] and event.emoji in previous_values | next_values:
            owner, channel_id, pages, index = pager
            direction = -1 if event.emoji in previous_values else 1
            index = (index + direction) % len(pages)
            await bot.state.http.edit_message(channel_id, event.message_id, embeds=pages[index])
            info_pages[event.message_id] = (owner, channel_id, pages, index)
            return
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
    stoat_token = os.environ.get("STOAT_BOT_TOKEN")
    fluxer_token = os.environ.get("FLUXER_BOT_TOKEN")
    if not stoat_token and not fluxer_token:
        raise SystemExit("STOAT_BOT_TOKEN or FLUXER_BOT_TOKEN is required (never commit tokens).")
    prefix = os.environ.get("PLURAPACK_PREFIX", "p;")
    try:
        database = resolve_database_path()
    except StorageConfigurationError as error:
        raise SystemExit(f"Persistent storage error: {error}") from None
    runners: list[tuple[str, Any, str]] = []
    try:
        if stoat_token:
            runners.append(("Stoat", create_bot(prefix, database), stoat_token))
        if fluxer_token:
            from .fluxer_bot import create_fluxer_bot

            runners.append(("Fluxer", create_fluxer_bot(prefix, database), fluxer_token))
    except (StoatDependencyError, RuntimeError) as error:
        raise SystemExit(str(error)) from None

    def run(platform: str, client: Any, token: str) -> None:
        _print_cli_status(f"Connecting to {platform}...")
        client.run(token)

    if len(runners) == 1:
        run(*runners[0])
        return
    with ThreadPoolExecutor(max_workers=len(runners), thread_name_prefix="plurapack") as pool:
        futures = [pool.submit(run, *runner) for runner in runners]
        for future in futures:
            future.result()


if __name__ == "__main__":
    main()
