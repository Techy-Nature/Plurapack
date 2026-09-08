"""Thin stoat.py adapter. Importing this module never connects to Stoat."""
from __future__ import annotations

import importlib
import importlib.util
import os
from dataclasses import dataclass
from typing import Any

from .proxy import Incoming, ProxyService
from .storage import Member, Store


STOAT_INSTALL_MESSAGE = (
    "The Stoat client dependency is not installed. Install Plurapack and its "
    "dependencies with `python -m pip install -e .`, then try again."
)


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
            masquerade=self.sdk.MessageMasquerade(name=member.name, avatar=member.avatar),
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
            masquerade=self.sdk.MessageMasquerade(name=member.name, avatar=member.avatar),
        )
        return posted.id


def create_bot(prefix: str, database: str) -> Any:
    stoat, commands = _load_stoat()
    bot = commands.Bot(command_prefix=prefix, description="Plural communication proxy")
    store = Store(database)
    platform = StoatPlatform({}, bot.state, stoat)
    service = ProxyService(store, platform, prefix)

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
