"""Platform-neutral replies for the public API privacy and token commands."""
from __future__ import annotations

import shlex
import time

from .storage import PRIVACY_FIELDS, ApiToken, MemberPrivacy, Store

_VISIBILITY = {"public": True, "on": True, "private": False, "off": False}


def _visibility(word: str) -> bool:
    try:
        return _VISIBILITY[word.casefold()]
    except KeyError:
        raise ValueError("Privacy must be `public` or `private`.") from None


def _label(public: bool) -> str:
    return "public" if public else "private"


def _fields(values: list[str]) -> list[str]:
    if [value.casefold() for value in values] == ["all"]:
        return list(PRIVACY_FIELDS)
    if not values:
        raise ValueError(f"Name at least one field: {', '.join(PRIVACY_FIELDS)}, or all.")
    return values


def _member_line(name: str, member_id: str, privacy: MemberPrivacy) -> str:
    hidden = ", ".join(sorted(privacy.hidden_fields)) or "nothing"
    return f"**{name}** (`{member_id}`) is {_label(privacy.public)} in the API; hidden fields: {hidden}."


def privacy_usage(prefix: str) -> str:
    return (f"Usage: `{prefix}privacy`, `{prefix}privacy system public|private`, "
            f"`{prefix}privacy front public|private`, "
            f"`{prefix}privacy member MEMBER public|private`, "
            f"`{prefix}privacy member MEMBER hide|show FIELD...`, or "
            f"`{prefix}privacy group GROUP public|private`. "
            f"Fields: {', '.join(PRIVACY_FIELDS)}.")


def privacy_summary(store: Store, account_id: str) -> str:
    system_id = store.system_for(account_id)
    if system_id is None:
        raise PermissionError("Create a system first.")
    settings = store.system_privacy(system_id)
    members = store.members_for_system(system_id)
    privacy = store.member_privacy_for_system(system_id)
    public_members = [member.name for member in members if privacy[member.id].public]
    public_groups = store.public_group_ids(system_id)
    shown = ", ".join(public_members) if public_members else "none"
    return (f"Public API privacy for `{system_id}`:\n"
            f"- System card: {_label(settings.system_public)}\n"
            f"- Current front: {_label(settings.front_public)}\n"
            f"- Public members ({len(public_members)} of {len(members)}): {shown}\n"
            f"- Public groups: {len(public_groups)} of {len(store.groups_for_system(system_id))}\n"
            "Everything else stays private. Nothing is visible to outside tools until the "
            "system card is public.")


def privacy_reply(store: Store, account_id: str, target: str, arguments: str, prefix: str) -> str:
    """Apply one `privacy` command and describe the result."""
    try:
        values = shlex.split(arguments)
    except ValueError as error:
        return f"Invalid quoting: {error}"
    target = target.casefold()
    try:
        if not target:
            return privacy_summary(store, account_id)
        if target in {"system", "front"}:
            if len(values) != 1:
                raise ValueError(f"Usage: `{prefix}privacy {target} public|private`")
            settings = store.configure_system_privacy(account_id, target, _visibility(values[0]))
            public = settings.system_public if target == "system" else settings.front_public
            what = "Your system card" if target == "system" else "Your current front"
            return f"{what} is now {_label(public)} in the API."
        if target == "member":
            if not values:
                raise ValueError(f"Usage: `{prefix}privacy member MEMBER public|private|hide|show ...`")
            selector, rest = values[0], values[1:]
            if not rest:
                member = store.member_selected(account_id, selector)
                if member is None:
                    raise PermissionError("Member not found or not owned by this account.")
                return _member_line(member.name, member.id, store.member_privacy(member.id))
            action = rest[0].casefold()
            if action in {"hide", "show"}:
                fields = _fields(rest[1:])
                member, settings = store.configure_member_privacy(
                    account_id, selector, hide=fields if action == "hide" else (),
                    show=fields if action == "show" else ())
            elif len(rest) == 1:
                member, settings = store.configure_member_privacy(
                    account_id, selector, public=_visibility(action))
            else:
                raise ValueError(f"Usage: `{prefix}privacy member MEMBER public|private|hide|show ...`")
            return _member_line(member.name, member.id, settings)
        if target == "group":
            if len(values) != 2:
                raise ValueError(f"Usage: `{prefix}privacy group GROUP public|private`")
            public = _visibility(values[1])
            group = store.configure_group_privacy(account_id, values[0], public)
            note = " Only its public members are listed." if public else ""
            return f"Group **{group.name}** (`{group.id}`) is now {_label(public)} in the API.{note}"
        return privacy_usage(prefix)
    except (PermissionError, ValueError) as error:
        return str(error)


def _token_line(token: ApiToken) -> str:
    used = (time.strftime("%Y-%m-%d", time.gmtime(token.last_used_at))
            if token.last_used_at else "never")
    created = time.strftime("%Y-%m-%d", time.gmtime(token.created_at))
    return f"`{token.id}` **{token.name}** · {token.scope} · created {created} · last used {used}"


def token_usage(prefix: str) -> str:
    return (f"Usage: `{prefix}token create [read:public|read:private] [NAME]` (in a DM), "
            f"`{prefix}token list`, or `{prefix}token revoke TOKEN_ID|all`.")


def token_reply(store: Store, account_id: str, action: str, arguments: str, prefix: str,
                private_channel: bool) -> str:
    """Create, list, or revoke API tokens; secrets are only ever shown in a DM."""
    action = action.casefold()
    words = arguments.split()
    try:
        if action == "create":
            if not private_channel:
                return ("For safety, create API tokens in a direct message with me. "
                        "Nothing was created.")
            scope = "read:public"
            if words and words[0].casefold().startswith("read:"):
                scope = words.pop(0)
            token, secret = store.create_api_token(account_id, scope, " ".join(words))
            return (f"API token **{token.name}** (`{token.id}`, {token.scope}):\n"
                    f"```\n{secret}\n```\n"
                    "Copy it now; it will not be shown again. Outside tools send it as the "
                    "`Authorization: Bearer TOKEN` header. Keep `read:private` tokens to tools you "
                    f"trust, because they can see private members. Revoke it with "
                    f"`{prefix}token revoke {token.id}`.")
        if action == "list":
            tokens = store.api_tokens(account_id)
            if not tokens:
                return f"Your system has no API tokens. Create one in a DM with `{prefix}token create`."
            return "API tokens:\n" + "\n".join(_token_line(token) for token in tokens)
        if action == "revoke":
            if len(words) != 1:
                raise ValueError(f"Usage: `{prefix}token revoke TOKEN_ID|all`")
            count = store.revoke_api_token(account_id, words[0])
            return f"Revoked {count} API token{'s' if count != 1 else ''}."
        return token_usage(prefix)
    except (PermissionError, ValueError) as error:
        return str(error)
