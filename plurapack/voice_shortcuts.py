"""Platform-neutral replies for per-proxy-tag voice shortcuts."""
from __future__ import annotations

from .storage import ProxyTag, Store, Voice

SHORTCUT_ACTIONS = frozenset({"shortcut", "shortcuts"})
_CLEAR_WORDS = frozenset({"off", "none", "default"})


def _tag_text(tag: ProxyTag) -> str:
    return f"`{tag.prefix}text{tag.suffix}`"


def _listing(name: str, tags: list[tuple[ProxyTag, Voice | None]]) -> str:
    if not tags:
        return f"**{name}** has no proxy tags."
    lines = [f"Proxy tags for **{name}**:"]
    for tag, voice in tags:
        lines.append(f"{_tag_text(tag)} → {voice.name if voice else 'selected voice'}")
    return "\n".join(lines)


def _named_voice(store: Store, member_id: str, parts: list[str]) -> str | None:
    """The VOICE word of ``MEMBER [VOICE] PREFIX [SUFFIX]``, or None when it was left out.

    VOICE may be omitted only when the member has exactly one voice; then
    ``MEMBER PREFIX SUFFIX`` is read as a tag, unless the middle word names that voice.
    """
    if len(parts) == 4:
        return parts[1]
    if len(parts) == 2:
        return None
    word = parts[1]
    voices = store.member_voices(member_id)
    if (word.casefold() in _CLEAR_WORDS or len(voices) != 1
            or any(word == v.id or word.casefold() == v.name.casefold() for v in voices)):
        return word
    return None


def shortcut_usage(prefix: str) -> str:
    return (f"Use `{prefix}voice shortcut MEMBER VOICE PREFIX [SUFFIX]` to make a proxy tag speak "
            f"with one of that member's custom voices (leave VOICE out when they only have one), `{prefix}voice shortcut MEMBER off PREFIX [SUFFIX]` "
            f"to undo it, or `{prefix}voice shortcuts MEMBER` to list them. Forms work as well as members.")


def voice_shortcut_reply(store: Store, voice_service: object, account_id: str, action: str,
                         parts: list[str], prefix: str) -> str:
    """Handle ``voice shortcut`` and ``voice shortcuts``; raises user-facing errors."""
    if not parts:
        return shortcut_usage(prefix)
    member, form = store.tag_identity(account_id, parts[0])
    name = form.display_name if form else member.name
    member_id, form_id = (None, form.id) if form else (member.id, None)
    if action == "shortcuts":
        if len(parts) != 1:
            raise ValueError(f"Use `{prefix}voice shortcuts MEMBER`.")
        return _listing(name, store.tag_voices(member_id=member_id, form_id=form_id))
    if len(parts) not in {2, 3, 4} or (len(parts) == 2 and parts[1].casefold() in _CLEAR_WORDS):
        return shortcut_usage(prefix)
    selector, voice_name, tag = parts[0], _named_voice(store, member.id, parts), parts[1:]
    if voice_name is not None:
        tag = parts[2:]
    tag_prefix, tag_suffix = tag[0], (tag[1] if len(tag) == 2 else "")
    if voice_name is not None and voice_name.casefold() in _CLEAR_WORDS:
        store.configure_tag_voice(account_id, selector, tag_prefix, tag_suffix, None)
        return (f"{_tag_text(ProxyTag(tag_prefix, tag_suffix))} now speaks with **{name}**'s "
                f"selected voice again.")
    voice = voice_service.select_member_voice(account_id, member.id, voice_name)  # type: ignore[attr-defined]
    store.configure_tag_voice(account_id, selector, tag_prefix, tag_suffix, voice.id)
    note = "" if member.playback != "off" else (
        f" Speech is Off for **{member.name}**, so it stays silent until playback is turned on in the dashboard.")
    return (f"{_tag_text(ProxyTag(tag_prefix, tag_suffix))} now proxies as **{name}** and speaks "
            f"with **{voice.name}**.{note}")
