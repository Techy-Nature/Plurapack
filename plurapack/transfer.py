"""Portable system import and export adapters.

The external formats intentionally accept the common fields emitted by both
services rather than depending on either service's API.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable

from .storage import Member


class TransferError(ValueError):
    """Raised when a transfer document cannot safely be imported."""


@dataclass(frozen=True)
class ImportedMember:
    name: str
    prefix: str
    suffix: str = ""
    avatar: str | None = None
    color: str | None = None
    pronouns: str | None = None


@dataclass(frozen=True)
class ImportedSystem:
    name: str
    members: tuple[ImportedMember, ...]


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TransferError(f"{label} must be a JSON object.")
    return value


def _text(value: Any, default: str = "") -> str:
    return value.strip() if isinstance(value, str) else default


def _member(name: Any, prefix: Any, suffix: Any = "", avatar: Any = None,
            color: Any = None, pronouns: Any = None) -> ImportedMember:
    clean_name = _text(name)
    clean_prefix, clean_suffix = _text(prefix), _text(suffix)
    if not clean_name:
        raise TransferError("Every imported member must have a name.")
    if not clean_prefix and not clean_suffix:
        # A tag is required by Plurapack. This deterministic default is easy to
        # recognize and edit after moving from a tagless external member.
        clean_prefix = f"{clean_name}:"
    clean_color = _text(color).removeprefix("#").lower() or None
    if clean_color and (len(clean_color) != 6 or any(c not in "0123456789abcdef" for c in clean_color)):
        clean_color = None
    return ImportedMember(clean_name, clean_prefix, clean_suffix, _text(avatar) or None,
                          f"#{clean_color}" if clean_color else None, _text(pronouns) or None)


def _members(value: Any, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise TransferError(f"{label} must be a JSON array.")
    return [_object(item, f"{label} entry") for item in value]


def parse_import(source: str, document: str) -> ImportedSystem:
    """Normalize a PluralKit, Tupperbox, or Plurapack JSON export."""
    try:
        root = _object(json.loads(document), "Import document")
    except json.JSONDecodeError as error:
        raise TransferError(f"Import document is not valid JSON: {error.msg}.") from error
    source = source.casefold().replace("-", "")
    imported: list[ImportedMember] = []
    if source in {"pluralkit", "pk"}:
        records = _members(root.get("members"), "PluralKit members")
        for record in records:
            tags = record.get("proxy_tags") or []
            if not isinstance(tags, list):
                raise TransferError("PluralKit proxy_tags must be a JSON array.")
            tag = _object(tags[0], "PluralKit proxy tag") if tags else {}
            imported.append(_member(record.get("display_name") or record.get("name"),
                                    tag.get("prefix"), tag.get("suffix"),
                                    record.get("avatar_url"), record.get("color"), record.get("pronouns")))
        system_name = _text(root.get("name"), "Imported PluralKit system")
    elif source in {"tupperbox", "tupper"}:
        records = _members(root.get("tuppers", root.get("members")), "Tupperbox tuppers")
        for record in records:
            brackets = record.get("brackets") or record.get("bracket") or []
            if not isinstance(brackets, list) or len(brackets) > 2:
                raise TransferError("Tupperbox brackets must be a one- or two-item JSON array.")
            imported.append(_member(record.get("name"), brackets[0] if brackets else "",
                                    brackets[1] if len(brackets) == 2 else "",
                                    record.get("avatar_url") or record.get("avatar"),
                                    record.get("color"), record.get("pronouns")))
        system_name = _text(root.get("name"), "Imported Tupperbox system")
    elif source == "plurapack":
        records = _members(root.get("members"), "Plurapack members")
        for record in records:
            imported.append(_member(record.get("name"), record.get("prefix"), record.get("suffix"),
                                    record.get("avatar"), record.get("color"), record.get("pronouns")))
        system_name = _text(root.get("name"), "Imported Plurapack system")
    else:
        raise TransferError("Source must be pluralkit, tupperbox, or plurapack.")
    if not imported:
        raise TransferError("The import contains no members.")
    return ImportedSystem(system_name, tuple(imported))


def export_document(format_name: str, system_name: str, members: Iterable[Member]) -> bytes:
    """Return a UTF-8 JSON document consumable by the requested service."""
    format_name = format_name.casefold().replace("-", "")
    values = list(members)
    if format_name in {"pluralkit", "pk"}:
        body = {"name": system_name, "members": [
            {"name": m.name, "display_name": None, "avatar_url": m.avatar,
             "color": m.color.removeprefix("#") if m.color else None,
             "pronouns": m.pronouns,
             "proxy_tags": [{"prefix": m.prefix, "suffix": m.suffix}]}
            for m in values]}
    elif format_name in {"tupperbox", "tupper"}:
        body = {"name": system_name, "tuppers": [
            {"name": m.name, "brackets": [m.prefix, m.suffix], "avatar_url": m.avatar,
             "color": m.color, "pronouns": m.pronouns} for m in values]}
    elif format_name == "plurapack":
        body = {"format": "plurapack", "version": 1, "name": system_name, "members": [
            {"name": m.name, "prefix": m.prefix, "suffix": m.suffix,
             "avatar": m.avatar, "color": m.color, "pronouns": m.pronouns} for m in values]}
    else:
        raise TransferError("Format must be pluralkit, tupperbox, or plurapack.")
    return (json.dumps(body, ensure_ascii=False, indent=2) + "\n").encode()
