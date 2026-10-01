"""Fluxer adapter for Plurapack's platform-neutral proxy service."""
from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass, field
from typing import Any

from .bot import COMMAND_SHORTCUTS, _help_pages, _print_cli_status
from .login import LoginError, LoginService
from .proxy import Incoming, ProxyService
from .storage import Member, Store


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

    async def delete_source(self, incoming: Incoming) -> None:
        await self.messages.pop(incoming.id).delete()

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
    bot.command(name=name)(callback)
    shortcut = COMMAND_SHORTCUTS.get(name)
    if shortcut:
        bot.command(name=shortcut)(callback)


def create_fluxer_bot(prefix: str, database: str) -> Any:
    """Create, but do not connect, the Fluxer side of Plurapack."""
    fluxer = _load_fluxer()
    # Proxies necessarily inspect ordinary message text, so request Fluxer's
    # privileged content intent in addition to its normal bot event set.
    intents = fluxer.Intents.default() | fluxer.Intents.MESSAGE_CONTENT
    bot = fluxer.Bot(command_prefix=prefix, intents=intents)
    store = Store(database)
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
            created = store.add_member(
                str(ctx.author.id), name, member_prefix, suffix, description
            )
        except (PermissionError, ValueError) as error:
            await ctx.send(str(error))
            return
        await ctx.send(f"Added **{created.name}** (`{created.id}`); voice is Off.")

    _register_command(bot, "help", help_command)
    _register_command(bot, "login", login_command)
    _register_command(bot, "setup", setup_command)
    _register_command(bot, "member", member_command)

    @bot.event
    async def on_ready() -> None:
        _print_cli_status(f"Fluxer ready to use. Command prefix: {prefix}")

    @bot.event
    async def on_message(message: Any) -> None:
        # fluxer.py permits replacing its dispatcher event, so explicitly retain it.
        await bot._process_commands(message)
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
