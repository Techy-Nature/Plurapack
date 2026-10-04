"""Fluxer adapter for Plurapack's platform-neutral proxy service."""
from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import os
import shlex
import urllib.request
from datetime import date
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from .bot import (
    COMMAND_ALIASES,
    COMMAND_HELP,
    COMMAND_SHORTCUTS,
    _help_pages,
    _member_creation_options,
    _print_cli_status,
    _update_profile_image,
    _download_voice_attachment,
)
from .voice_service import VoiceService
from .voice_storage import ForgejoVoiceStorage
from .login import LoginError, LoginService
from .proxy import Incoming, ProxyService
from .storage import Group, Member, Store, System
from .transfer import TransferError, export_system as create_export, import_system as apply_import


FLUXER_INSTALL_MESSAGE = (
    "The Fluxer client dependency is not installed. Install Plurapack and its "
    "dependencies with `python -m pip install -e .`, then try again."
)


class FluxerDependencyError(RuntimeError):
    """Raised when Fluxer support is requested without fluxer.py installed."""


def _load_fluxer() -> Any:
    if importlib.util.find_spec("fluxer") is None:
        raise FluxerDependencyError(FLUXER_INSTALL_MESSAGE)
    return importlib.import_module("fluxer")


@dataclass
class FluxerPlatform:
    """Translate the proxy operations into Fluxer messages and webhooks."""

    client: Any
    messages: dict[str, Any] = field(default_factory=dict)
    webhooks: dict[str, Any] = field(default_factory=dict)
    proxy_webhooks: dict[str, Any] = field(default_factory=dict)

    async def _webhook(self, channel_id: str) -> Any:
        webhook = self.webhooks.get(channel_id)
        if webhook is None:
            existing = await self.client.fetch_channel_webhooks(channel_id)
            webhook = next((item for item in existing if item.name == "Plurapack Proxy"), None)
            if webhook is None:
                webhook = await self.client.create_webhook(channel_id, name="Plurapack Proxy")
            self.webhooks[channel_id] = webhook
        return webhook

    async def _send(self, incoming: Incoming, member: Member, content: str) -> str:
        webhook = await self._webhook(incoming.channel_id)
        posted = await webhook.send(
            content, username=member.name, avatar_url=member.avatar, wait=True
        )
        if posted is None:
            raise RuntimeError("Fluxer did not return the proxy message.")
        proxy_id = str(posted.id)
        self.proxy_webhooks[proxy_id] = webhook
        return proxy_id

    async def send_proxy(self, incoming: Incoming, member: Member, content: str) -> str:
        return await self._send(incoming, member, content)

    async def delete_source(self, incoming: Incoming) -> bool:
        await self.messages.pop(incoming.id).delete()
        return True

    async def _request_webhook_message(
        self, method: str, channel_id: str, proxy_id: str, **kwargs: Any
    ) -> Any:
        webhook = self.proxy_webhooks.get(proxy_id) or await self._webhook(channel_id)
        route = webhook._http._route(
            method,
            "/webhooks/{webhook_id}/{token}/messages/{message_id}",
            webhook_id=webhook.id,
            token=webhook.token,
            message_id=proxy_id,
        )
        return await webhook._http.request(route, **kwargs)

    async def edit_proxy(self, channel_id: str, proxy_id: str, content: str) -> None:
        await self._request_webhook_message("PATCH", channel_id, proxy_id, json={"content": content})

    async def delete_proxy(self, channel_id: str, proxy_id: str) -> None:
        await self._request_webhook_message("DELETE", channel_id, proxy_id)
        self.proxy_webhooks.pop(proxy_id, None)

    async def send_reproxy(self, incoming: Incoming, proxy_id: str, member: Member) -> str:
        return await self._send(incoming, member, await self.proxy_content(incoming.channel_id, proxy_id))

    async def proxy_content(self, channel_id: str, proxy_id: str) -> str:
        data = await self._request_webhook_message("GET", channel_id, proxy_id)
        return str(data.get("content", ""))


def _register_command(bot: Any, name: str, callback: Any) -> None:
    """Register the canonical name and every documented compatibility name."""
    seen: set[str] = set()
    for command_name in (name, COMMAND_SHORTCUTS.get(name), *COMMAND_ALIASES.get(name, ())):
        if command_name and command_name not in seen:
            bot.command(name=command_name)(callback)
            seen.add(command_name)


def create_fluxer_bot(prefix: str, database: str) -> Any:
    """Create, but do not connect, the Fluxer side of Plurapack."""
    fluxer = _load_fluxer()
    # Proxies necessarily inspect ordinary message text, so request Fluxer's
    # privileged content intent in addition to its normal bot event set.
    intents = fluxer.Intents.default() | fluxer.Intents.MESSAGE_CONTENT
    bot = fluxer.Bot(command_prefix=prefix, intents=intents)
    store = Store(database)
    try:
        voice_service = VoiceService(store, ForgejoVoiceStorage.configured(),
                                     int(os.getenv("VOICE_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))))
    except ValueError:
        voice_service = None
    platform = FluxerPlatform(bot)
    service = ProxyService(store, platform, prefix)
    login_service = LoginService(store)

    async def help_command(ctx: Any, *, command_name: str = "") -> None:
        for page in _help_pages(prefix, command_name):
            await ctx.send(page)

    async def login_command(ctx: Any, code: str) -> None:
        """Prove dashboard identity using the authenticated Fluxer author."""
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

    async def setup_command(ctx: Any, name: str = "My system", *, description: str = "") -> None:
        try:
            system_id = store.create_system(str(ctx.author.id), name, description)
        except ValueError as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"System `{system_id}` is ready. Add a member with `{prefix}member Name [text]`.")

    async def member_command(
        ctx: Any, name: str, member_prefix: str, suffix: str = "", *, description: str = ""
    ) -> None:
        try:
            suffix, description, avatar = _member_creation_options(suffix, description)
            created = store.add_member(
                str(ctx.author.id), name, member_prefix, suffix, description, avatar=avatar
            )
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Added **{created.name}** (`{created.id}`); voice is Off.")

    async def avatar_command(ctx: Any, *, arguments: str = "") -> None:
        await image_command(ctx, "avatar", arguments)

    async def banner_command(ctx: Any, *, arguments: str = "") -> None:
        await image_command(ctx, "banner", arguments)

    async def image_command(ctx: Any, field: str, arguments: str) -> None:
        try:
            name, _ = _update_profile_image(store, str(ctx.author.id), field, arguments)
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Updated the {field} for **{name}**.")

    def account(ctx: Any) -> str:
        return str(ctx.author.id)

    async def run(ctx: Any, operation: Any, success: Any = None) -> Any:
        """Common platform-neutral Store invocation/error-to-chat boundary."""
        try:
            value = operation()
        except (PermissionError, ValueError, TransferError, json.JSONDecodeError) as error:
            await ctx.send(str(error))
            return None
        if success is not None:
            await ctx.send(success(value) if callable(success) else success)
        return value

    async def link_command(ctx: Any) -> None:
        await run(ctx, lambda: store.create_link(account(ctx)),
                  lambda token: f"Single-use code: `{token}` (expires in 15 minutes). Send it privately to the other owner.")

    async def verify_command(ctx: Any, token: str) -> None:
        await run(ctx, lambda: store.redeem_link(account(ctx), token),
                  lambda system_id: f"Account connected to system `{system_id}`.")

    async def alias_command(ctx: Any, selector: str, alias: str = "") -> None:
        await run(ctx, lambda: store.configure_alias(account(ctx), selector, alias or None),
                  lambda m: f"Alias for **{m.name}** is {f'`{m.alias}`' if m.alias else 'cleared'}; their full name remains on proxies.")

    async def pronouns_command(ctx: Any, selector: str, *, value: str = "") -> None:
        await run(ctx, lambda: store.configure_pronouns(account(ctx), selector, value or None),
                  lambda m: f"Pronouns for **{m.name}** are {m.pronouns or 'now cleared'}.")

    async def formpronouns_command(ctx: Any, selector: str, *, value: str = "") -> None:
        await run(ctx, lambda: store.configure_form_pronouns(account(ctx), selector, value or None),
                  lambda f: f"Pronouns for form **{f.display_name}** now {f.pronouns or 'inherit from the member'}.")

    async def color_command(ctx: Any, member_id: str, value: str) -> None:
        await run(ctx, lambda: store.configure_color(account(ctx), member_id, value),
                  lambda m: f"Username color for **{m.name}** (`{m.id}`) is `{m.color}`.")

    async def memberproxy_command(ctx: Any, selector: str, member_prefix: str = "", suffix: str = "") -> None:
        if not member_prefix:
            member = store.member_selected(account(ctx), selector)
            if member is None:
                await ctx.send("Member not found or not owned by this account.")
                return
            tags = store.proxy_tags(member_id=member.id)
            listing = "\n".join(f"{n}. `{tag.prefix}text{tag.suffix}`" for n, tag in enumerate(tags, 1)) or "No proxy tags are configured."
            await ctx.send(f"Proxy tags for **{member.name}** ({len(tags)}/100):\n{listing}")
            return
        await run(ctx, lambda: store.configure_member_proxy(account(ctx), selector, member_prefix, suffix),
                  lambda m: f"Added proxy tag `{member_prefix}text{suffix}` for **{m.name}**.")

    async def memberproxy_clear_command(ctx: Any, selector: str) -> None:
        await run(ctx, lambda: store.configure_member_proxy(account(ctx), selector, None),
                  lambda m: f"All proxy tags for **{m.name}** are cleared.")

    async def form_command(ctx: Any, selector: str, display_name: str, picture: str = "", *, soma: str = "") -> None:
        await run(ctx, lambda: store.create_form(account(ctx), selector, display_name, picture or None, soma),
                  lambda f: f"Created form **{f.display_name}** (`{f.id}`) for member `{f.member_id}`. Use `{prefix}formproxy {f.id} PREFIX [SUFFIX]` to give it a proxy tag, or `{prefix}front {f.id}` to switch both member and form.")

    async def formproxy_command(ctx: Any, selector: str, form_prefix: str = "", suffix: str = "") -> None:
        if not form_prefix:
            selected = store.form_selected(account(ctx), selector)
            if selected is None:
                await ctx.send("Form not found or not owned by this account.")
                return
            form = selected[0]; tags = store.proxy_tags(form_id=form.id)
            listing = "\n".join(f"{n}. `{tag.prefix}text{tag.suffix}`" for n, tag in enumerate(tags, 1)) or "No proxy tags are configured."
            await ctx.send(f"Proxy tags for **{form.display_name}** ({len(tags)}/100):\n{listing}")
            return
        await run(ctx, lambda: store.configure_form_proxy(account(ctx), selector, form_prefix, suffix),
                  lambda f: f"Added proxy tag for **{f.display_name}**: `{form_prefix}text{suffix}`.")

    async def formproxy_clear_command(ctx: Any, selector: str) -> None:
        await run(ctx, lambda: store.configure_form_proxy(account(ctx), selector, None),
                  lambda f: f"All proxy tags for **{f.display_name}** are cleared.")

    async def defaultform_command(ctx: Any, member_selector: str, form_selector: str = "off") -> None:
        configured = await run(ctx, lambda: store.configure_default_form(account(ctx), member_selector, None if form_selector.casefold() in {"off", "none"} else form_selector))
        if configured:
            if configured.default_form_id:
                form = store.form_selected(account(ctx), configured.default_form_id)[0]
                await ctx.send(f"Default form for **{configured.name}** is **{form.display_name}** (`{configured.default_form_id}`).")
            else: await ctx.send(f"Default form for **{configured.name}** is cleared.")

    async def front_command(ctx: Any, selector: str) -> None:
        await run(ctx, lambda: store.switch_front(account(ctx), selector), lambda f: (f"Front switched to **{f.member.name}** as **{f.form.display_name}** (form `{f.form.id}`, member `{f.member.id}`)." if f.form else f"Front switched to **{f.member.name}** (`{f.member.id}`)."))

    async def autoproxy_command(ctx: Any, selector: str) -> None:
        await run(ctx, lambda: store.configure_autoproxy(account(ctx), None if selector.casefold() == "off" else selector), lambda a: f"Autoproxy is On for **{a.member.name}** (`{a.member.id}`)." if a.member else "Autoproxy is Off.")

    async def autofront_command(ctx: Any, enabled: str) -> None:
        if enabled.casefold() not in {"on", "off"}:
            await ctx.send("Autofront must be on or off."); return
        await run(ctx, lambda: store.configure_autofront(account(ctx), enabled.casefold() == "on"), lambda a: f"Autofront is {'On' if a.autofront else 'Off'}." + (f" Autoproxy is now **{a.member.name}**." if a.member else ""))

    async def systemtag_command(ctx: Any, *, tag: str = "") -> None:
        await run(ctx, lambda: store.configure_system_tag(account(ctx), tag or None), lambda s: f"System tag is now **{s.system_tag}**." if s.system_tag else "System tag is cleared.")

    async def systemtagshow_command(ctx: Any, scope: str, setting: str) -> None:
        scope, setting = scope.casefold(), setting.casefold()
        if scope not in {"system", "server", "channel"} or setting not in {"on", "off", "default"} or (scope == "system" and setting == "default"):
            await ctx.send("Scope must be system, server, or channel; setting must be on, off, or default."); return
        message = getattr(ctx, "message", ctx)
        scope_id = str(getattr(message, "channel_id", "")) if scope == "channel" else (str(getattr(message, "guild_id", "")) if scope == "server" else None)
        if scope == "server" and not scope_id:
            await ctx.send("This command must be used in a server to set a server override."); return
        op = (lambda: store.clear_system_tag_visibility_override(account(ctx), scope, scope_id)) if setting == "default" else (lambda: store.configure_system_tag_visibility(account(ctx), setting == "on", scope, scope_id))
        await run(ctx, op, f"System tag visibility for this {scope} is now **{setting.title()}**.")

    async def group_command(ctx: Any, action: str, *, arguments: str = "") -> None:
        try: values = shlex.split(arguments)
        except ValueError as error: await ctx.send(f"Invalid quoting: {error}"); return
        action = action.casefold()
        if action == "create" and len(values) == 2:
            await run(ctx, lambda: store.create_group(account(ctx), values[0], values[1]), lambda g: f"Created group **{g.name}** (`{g.id}`), alias `{g.alias}`.")
        elif action == "select":
            if len(values) != 1:
                await ctx.send(f"Usage: `{prefix}group select GROUP`")
            else:
                await run(ctx, lambda: store.set_active_group(account(ctx), values[0]), lambda g: f"Selected group **{g.name}** (`{g.id}`).")
        elif action == "add": await run(ctx, lambda: store.add_group_members(account(ctx), values), lambda pair: f"Added {len(pair[1])} member(s) to **{pair[0].name}**.")
        elif action == "alias" and len(values) == 1: await run(ctx, lambda: store.update_active_group(account(ctx), alias=values[0].strip()), lambda g: f"Group alias is now `{g.alias}`.")
        elif action == "avatar" and len(values) == 1: await run(ctx, lambda: store.update_active_group(account(ctx), avatar=values[0]), lambda g: f"Avatar updated for **{g.name}**.")
        else: await ctx.send("Group action must be create, select, add, alias, or avatar with the documented arguments.")

    def member_card(member: Any) -> str:
        forms = store.forms_for_member(member.id)
        tags = store.proxy_tags(member_id=member.id)
        form_lines = ", ".join(f"{f.display_name} (`{f.id}`, pronouns: {f.pronouns if f.pronouns is not None else member.pronouns or 'not set'}, proxy: `{f.prefix}text{f.suffix}`)" for f in forms) or "None"
        return f"**{member.name}**\n{member.description or 'No member description provided.'}\nID: `{member.id}` | Color: `{member.color or 'default'}` | Pronouns: {member.pronouns or 'Not set'}\nAvatar: {member.avatar or 'None'} | Banner: {member.banner or 'None'}\nProxies: " + (", ".join(f"`{t.prefix}text{t.suffix}`" for t in tags) or "None") + f"\nForms: {form_lines}"

    def group_card(group: Group) -> str:
        member_lines = []
        for member in store.public_group_members(group.id):
            tags = store.proxy_tags(member_id=member.id)
            proxies = ", ".join(f"`{tag.prefix}text{tag.suffix}`" for tag in tags) or "None"
            member_lines.append(
                f"**{member.name}** (`{member.id}`)\n"
                f"Pronouns: {member.pronouns or 'Not set'}\nProxies: {proxies}"
            )
        members = "\n\n".join(member_lines) if member_lines else "None"
        return f"**{group.name}**\nGroup ID: `{group.id}`\nAvatar: {group.avatar or 'None'}\n\nMembers:\n{members}"

    async def viewmember_command(ctx: Any, *, selector: str) -> None:
        try: member = store.public_member_selected(selector)
        except ValueError as error: await ctx.send(str(error)); return
        await ctx.send(member_card(member) if member else "Member not found. Use an exact name or stable ID.")

    async def viewmembers_command(ctx: Any, *, system_selector: str = "") -> None:
        try: system = store.system_info(system_selector or store.system_for(account(ctx)))
        except ValueError as error: await ctx.send(str(error)); return
        if not system: await ctx.send("System not found. Use an exact name or stable ID."); return
        members = store.members_for_system(system.id)
        await ctx.send("\n\n".join(map(member_card, members)) if members else f"**{system.display_name}** has no members.")

    async def viewinfo_command(ctx: Any, *, selector: str = "") -> None:
        try: system = store.system_info(selector or store.system_for(account(ctx))) if (selector or store.system_for(account(ctx))) else None
        except ValueError as error: await ctx.send(str(error)); return
        if system:
            members = store.members_for_system(system.id)
            await ctx.send(f"**{system.display_name}**\n{system.description or 'No system description provided.'}\nSystem ID: `{system.id}` | Members: {len(members)}\nAvatar: {system.logo or 'None'} | Banner: {system.banner or 'None'}\n\n" + "\n\n".join(map(member_card, members))); return
        await viewmember_command(ctx, selector=selector)

    async def info_command(ctx: Any, *, selector: str) -> None:
        try: value = store.public_info_selected(account(ctx), selector)
        except ValueError as error: await ctx.send(str(error)); return
        if value is None: await ctx.send("System, group, member, or form not found. Use an exact nickname, alias, or ID."); return
        if isinstance(value, Group): await ctx.send(group_card(value)); return
        if isinstance(value, Member): await ctx.send(member_card(value)); return
        if isinstance(value, tuple):
            form, member = value; await ctx.send(f"**{form.display_name}**\n{form.soma or member.description or 'No description provided.'}\nID: `{form.id}` | Pronouns: {form.pronouns if form.pronouns is not None else member.pronouns or 'Not set'}\nAvatar: {form.avatar or member.avatar or 'None'} | Banner: {form.banner or member.banner or 'None'}\nProxies: " + (", ".join(f"`{t.prefix}text{t.suffix}`" for t in store.proxy_tags(form_id=form.id)) or "None")); return
        if isinstance(value, System):
            groups = store.groups_for_system(value.id)
            group_details = "\n\n".join(group_card(group) for group in groups)
            await ctx.send(f"**{value.display_name}**\n{value.description or 'No description provided.'}\nID: `{value.id}`\nAvatar: {value.logo or 'None'} | Banner: {value.banner or 'None'}" + (f"\n\n{group_details}" if group_details else ""))

    async def deletemember_command(ctx: Any, *, selector: str) -> None:
        def remove():
            member = store.member_selected(account(ctx), selector)
            if member and store.member_voices(member.id):
                if voice_service is None: raise ValueError("Custom voice storage is unavailable.")
                return voice_service.delete_member(account(ctx), member.id)
            return store.delete_member(account(ctx), selector)
        await run(ctx, remove, lambda m: f"Deleted **{m.name}** (`{m.id}`) and all of their forms and records.")

    async def deletesystem_command(ctx: Any, confirmation: str = "") -> None:
        system_id = store.system_for(account(ctx))
        if not system_id: await ctx.send("Create a system first."); return
        if confirmation != system_id:
            await ctx.send(f"⚠️ **Permanent system deletion** This erases all associated data. Export first if needed. To confirm, run `{prefix}deletesystem {system_id}`."); return
        def remove():
            members = store.members_for_system(system_id)
            if any(store.member_voices(member.id) for member in members):
                if voice_service is None: raise ValueError("Custom voice storage is unavailable.")
                return voice_service.delete_system(account(ctx), confirmation)
            return store.delete_system(account(ctx), confirmation)
        await run(ctx, remove, lambda sid: f"System `{sid}` and all associated data were permanently deleted.")

    async def voiceoff_command(ctx: Any, selector: str) -> None:
        await run(ctx, lambda: store.configure_voice(account(ctx), selector, None, "{}", "off"), lambda m: f"Voice for **{m.name}** is Off.")

    async def voiceformat_command(ctx: Any, selector: str, enabled: str = "on", strikethrough: str = "normal") -> None:
        if enabled.casefold() not in {"on", "off"}: await ctx.send("Formatting must be on or off."); return
        await run(ctx, lambda: store.configure_speech_formatting(account(ctx), selector, enabled.casefold() == "on", strikethrough.casefold()), lambda m: f"Speech formatting for **{m.name}** is {'On' if m.speech_formatting else 'Off'}; crossed-out text is `{m.strikethrough_speech}`.")

    async def voice_command(ctx: Any, *, arguments: str = "") -> None:
        if voice_service is None: await ctx.send("Custom voice storage is not configured."); return
        try: parts = shlex.split(arguments)
        except ValueError as error: await ctx.send(str(error)); return
        action = parts.pop(0).casefold() if parts and parts[0].casefold() in {"upload", "list", "default", "rename", "delete"} else "upload"
        if not parts: await ctx.send(f"Use `{prefix}voice upload MEMBER VOICE_NAME` with a WAV/MP3 attachment, or list/default/rename/delete."); return
        selector = parts.pop(0)
        try:
            member = store.member_selected(account(ctx), selector)
            if member is None: raise PermissionError("Member not found or permission denied.")
            if action == "list":
                listed = voice_service.list_member_voices(account(ctx), member.id)
                await ctx.send("\n".join(f"{'★ ' if item.is_default else ''}{item.name} (`{item.id}`)" for item in listed) or "No custom voices."); return
            if action in {"default", "delete"}:
                if len(parts) != 1: raise ValueError(f"Use `{prefix}voice {action} MEMBER VOICE`.")
                selected = voice_service.select_member_voice(account(ctx), member.id, parts[0])
                result = voice_service.set_default_member_voice(account(ctx), member.id, selected.id) if action == "default" else voice_service.delete_member_voice(account(ctx), member.id, selected.id)
                await ctx.send(f"Voice **{result.name}** {'is now default' if action == 'default' else 'was deleted'}."); return
            if action == "rename":
                if len(parts) != 2: raise ValueError(f"Use `{prefix}voice rename MEMBER VOICE NEW_NAME` (quote names containing spaces).")
                selected = voice_service.select_member_voice(account(ctx), member.id, parts[0])
                renamed = voice_service.rename_member_voice(account(ctx), member.id, selected.id, parts[1])
                await ctx.send(f"Voice renamed to **{renamed.name}**."); return
            if not parts: raise ValueError(f"Use `{prefix}voice upload MEMBER VOICE_NAME` (quote names containing spaces).")
            voice_name = parts.pop(0); playback = parts.pop(0) if parts else "send"; settings = "{}"
            if parts: raise ValueError("Too many voice upload arguments.")
        except (PermissionError, ValueError) as error: await ctx.send(str(error)); return
        attachments = list(getattr(getattr(ctx, "message", ctx), "attachments", ()) or ())
        if len(attachments) != 1: await ctx.send("Attach exactly one WAV or MP3 voice reference."); return
        try:
            audio = await asyncio.to_thread(_download_voice_attachment, attachments[0], voice_service.max_bytes)
            uploaded = await asyncio.to_thread(voice_service.upload_member_voice, account(ctx), member.id, voice_name, audio, make_default=True)
        except (OSError, TimeoutError, ValueError) as error: await ctx.send(str(error)); return
        await run(ctx, lambda: store.configure_voice(account(ctx), selector, uploaded.storage_id + ".wav", settings, playback), lambda m: f"Voice for **{m.name}** is configured for `{m.playback}` playback.")

    async def import_command(ctx: Any, source: str = "", *, document: str = "") -> None:
        strategy = next((w[2:] for w in (source + " " + document).split() if w in {"--merge", "--skip-existing", "--overwrite"}), "merge")
        if strategy == "overwrite" and "--confirm-overwrite" not in (source + " " + document).split(): await ctx.send("Overwrite can replace matching records. Back up first, then repeat with --overwrite --confirm-overwrite."); return
        if not document or all(w.startswith("--") for w in document.split()):
            attachments = list(getattr(getattr(ctx, "message", ctx), "attachments", ()) or ())
            if attachments:
                try:
                    def download() -> str:
                        with urllib.request.urlopen(attachments[0].url, timeout=15) as response: data = response.read(5 * 1024 * 1024 + 1)
                        if len(data) > 5 * 1024 * 1024: raise ValueError("Import exceeds the 5 MiB upload limit.")
                        return data.decode()
                    document = await asyncio.to_thread(download)
                except (OSError, UnicodeError, ValueError) as error: await ctx.send(f"Could not read the JSON attachment: {error}"); return
        if not document: await ctx.send("Attach a JSON file (maximum 5 MiB), then run import again."); return
        if document.startswith("```") and document.endswith("```"): document = document[3:-3].removeprefix("json").lstrip()
        words = source.split(); source_format = words[0] if words and not words[0].startswith("--") else None
        await run(ctx, lambda: apply_import(store, account(ctx), document, source_format, strategy), lambda r: f"Import complete. Members imported: {r.members_imported}; groups imported: {r.groups_imported}; proxy tags imported: {r.proxy_tags_imported}; existing members skipped: {r.existing_members_skipped}; warnings: {len(r.warnings)}.")

    async def export_command(ctx: Any, format_name: str = "plurapack", *, options: str = "") -> None:
        choices = options.split()
        if set(choices) - {"--forms-loss", "--forms-members"} or all(x in choices for x in ("--forms-loss", "--forms-members")): await ctx.send("Choose a valid forms export option."); return
        try: document, report = create_export(store, account(ctx), format_name, forms_mode="members" if "--forms-members" in choices else "loss")
        except (PermissionError, TransferError) as error: await ctx.send(str(error)); return
        filename = f"plurapack-{format_name.casefold().replace('-', '')}-export-{date.today().isoformat()}.json"
        # fluxer.py accepts in-memory bytes through File; tests may provide a minimal context.
        file = fluxer.File(document.encode(), filename=filename)
        await ctx.send(f"Export ready. Members: {len(store.export_system(account(ctx))[1])}. Warnings: {len(report.warnings)}. Store it privately.", file=file)

    _register_command(bot, "help", help_command)
    _register_command(bot, "login", login_command)
    _register_command(bot, "setup", setup_command)
    _register_command(bot, "member", member_command)
    _register_command(bot, "avatar", avatar_command)
    _register_command(bot, "banner", banner_command)
    callbacks = {
        "memberproxy": memberproxy_command, "memberproxy-clear": memberproxy_clear_command,
        "alias": alias_command, "form": form_command, "formproxy": formproxy_command,
        "formproxy-clear": formproxy_clear_command, "defaultform": defaultform_command,
        "pronouns": pronouns_command, "formpronouns": formpronouns_command,
        "systemtag": systemtag_command, "systemtagshow": systemtagshow_command,
        "front": front_command, "autoproxy": autoproxy_command, "autofront": autofront_command,
        "color": color_command, "link": link_command, "verify": verify_command,
        "import": import_command, "export": export_command, "viewinfo": viewinfo_command,
        "info": info_command, "viewmembers": viewmembers_command, "viewmember": viewmember_command,
        "group": group_command, "deletemember": deletemember_command,
        "deletesystem": deletesystem_command, "voice": voice_command,
        "voiceoff": voiceoff_command, "voiceformat": voiceformat_command,
    }
    for name, callback in callbacks.items():
        _register_command(bot, name, callback)
    # Keep the catalogue and adapter inseparable: a newly advertised command
    # must be explicitly wired above or bot creation fails loudly.
    if set(callbacks) | {"help", "login", "setup", "member", "avatar", "banner"} != set(COMMAND_HELP):
        raise RuntimeError("Fluxer command registration is out of sync with COMMAND_HELP.")

    @bot.event
    async def on_ready() -> None:
        _print_cli_status(f"Fluxer ready to use. Command prefix: {prefix}")

    @bot.event
    async def on_message(message: Any) -> None:
        # fluxer.py permits replacing its dispatcher event, so explicitly retain it.
        reply = getattr(message, "referenced_message", None)
        incoming = Incoming(
            str(message.id),
            str(message.channel_id),
            str(message.author.id),
            message.content,
            bool(message.author.bot),
            str(reply.id) if reply else None,
            str(message.guild_id) if message.guild_id else None,
        )
        platform.messages[incoming.id] = message
        try:
            await service.handle(incoming)
        finally:
            platform.messages.pop(incoming.id, None)

    @bot.event
    async def on_raw_reaction_add(event: Any) -> None:
        emoji = getattr(event.emoji, "name", None) or str(event.emoji)
        await service.handle_reaction(
            str(event.channel_id), str(event.message_id), str(event.user_id), emoji
        )

    return bot
