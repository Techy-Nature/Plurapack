from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator


def short_hash(length: int) -> str:
    """Return a cryptographically random, lowercase SHA-256 fragment."""
    return hashlib.sha256(secrets.token_bytes(32)).hexdigest()[:length]


@dataclass(frozen=True)
class Member:
    id: str
    system_id: str
    name: str
    prefix: str
    suffix: str
    avatar: str | None
    color: str | None
    voice_reference: str | None
    voice_settings: str
    playback: str
    speech_formatting: int
    strikethrough_speech: str


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        try:
            yield db
        except BaseException:
            db.rollback()
            raise
        else:
            db.commit()
        finally:
            db.close()

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS systems (
                    id TEXT PRIMARY KEY CHECK(length(id)=10), display_name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS owners (
                    account_id TEXT PRIMARY KEY, system_id TEXT NOT NULL REFERENCES systems(id)
                );
                CREATE TABLE IF NOT EXISTS members (
                    id TEXT PRIMARY KEY CHECK(length(id)=5), system_id TEXT NOT NULL REFERENCES systems(id),
                    name TEXT NOT NULL COLLATE NOCASE, prefix TEXT NOT NULL, suffix TEXT NOT NULL,
                    avatar TEXT, color TEXT, voice_reference TEXT, voice_settings TEXT NOT NULL DEFAULT '{}',
                    playback TEXT NOT NULL DEFAULT 'off' CHECK(playback IN ('off','local','send','both')),
                    speech_formatting INTEGER NOT NULL DEFAULT 0,
                    strikethrough_speech TEXT NOT NULL DEFAULT 'normal',
                    UNIQUE(system_id, name), UNIQUE(system_id, prefix, suffix)
                );
                CREATE TABLE IF NOT EXISTS links (
                    token_hash TEXT PRIMARY KEY, system_id TEXT NOT NULL REFERENCES systems(id),
                    created_by TEXT NOT NULL, expires_at INTEGER NOT NULL, used_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS proxied_messages (
                    proxy_message_id TEXT PRIMARY KEY, source_message_id TEXT NOT NULL UNIQUE,
                    channel_id TEXT NOT NULL, system_id TEXT NOT NULL, member_id TEXT NOT NULL,
                    owner_account_id TEXT NOT NULL, created_at INTEGER NOT NULL DEFAULT (unixepoch()),
                    deleted_at INTEGER, FOREIGN KEY(member_id) REFERENCES members(id)
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(members)")}
            if "color" not in columns:
                db.execute("ALTER TABLE members ADD COLUMN color TEXT")
            if "speech_formatting" not in columns:
                db.execute("ALTER TABLE members ADD COLUMN speech_formatting INTEGER NOT NULL DEFAULT 0")
            if "strikethrough_speech" not in columns:
                db.execute("ALTER TABLE members ADD COLUMN strikethrough_speech TEXT NOT NULL DEFAULT 'normal'")

    def create_system(self, account_id: str, name: str) -> str:
        with self.connect() as db:
            existing = db.execute("SELECT system_id FROM owners WHERE account_id=?", (account_id,)).fetchone()
            if existing:
                return str(existing[0])
            while True:
                system_id = short_hash(10)
                try:
                    db.execute("INSERT INTO systems VALUES (?,?)", (system_id, name))
                    db.execute("INSERT INTO owners VALUES (?,?)", (account_id, system_id))
                    return system_id
                except sqlite3.IntegrityError:
                    continue

    def system_for(self, account_id: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT system_id FROM owners WHERE account_id=?", (account_id,)).fetchone()
            return str(row[0]) if row else None

    def add_member(self, account_id: str, name: str, prefix: str, suffix: str = "") -> Member:
        system_id = self.system_for(account_id)
        if not system_id:
            raise PermissionError("Create a system first.")
        with self.connect() as db:
            while True:
                member_id = short_hash(5)
                try:
                    db.execute("INSERT INTO members(id,system_id,name,prefix,suffix) VALUES (?,?,?,?,?)",
                               (member_id, system_id, name, prefix, suffix))
                    break
                except sqlite3.IntegrityError as error:
                    if "members.id" not in str(error):
                        raise
        return self.member_named(account_id, name)  # type: ignore[return-value]

    def import_members(self, account_id: str, members: Iterable[Any]) -> int:
        """Atomically add normalized transfer members to an existing system."""
        system_id = self.system_for(account_id)
        if not system_id:
            raise PermissionError("Create a system first.")
        values = list(members)
        with self.connect() as db:
            for member in values:
                while True:
                    try:
                        db.execute("""INSERT INTO members
                            (id,system_id,name,prefix,suffix,avatar,color) VALUES (?,?,?,?,?,?,?)""",
                            (short_hash(5), system_id, member.name, member.prefix, member.suffix,
                             member.avatar, member.color))
                        break
                    except sqlite3.IntegrityError as error:
                        if "members.id" not in str(error):
                            raise ValueError(
                                f"Member {member.name!r} conflicts with an existing name or proxy tag. "
                                "Nothing was imported."
                            ) from error
        return len(values)

    def export_system(self, account_id: str) -> tuple[str, list[Member]]:
        """Return the owned system name and members without account or message data."""
        with self.connect() as db:
            system = db.execute("""SELECT s.id, s.display_name FROM systems s JOIN owners o
                ON o.system_id=s.id WHERE o.account_id=?""", (account_id,)).fetchone()
            if not system:
                raise PermissionError("Create a system first.")
            rows = db.execute("SELECT * FROM members WHERE system_id=? ORDER BY name", (system["id"],)).fetchall()
        return str(system["display_name"]), [Member(**dict(row)) for row in rows]

    def member_named(self, account_id: str, name: str) -> Member | None:
        with self.connect() as db:
            row = db.execute("""SELECT m.* FROM members m JOIN owners o ON o.system_id=m.system_id
                WHERE o.account_id=? AND m.name=?""", (account_id, name)).fetchone()
            return Member(**dict(row)) if row else None

    def member_selected(self, account_id: str, selector: str) -> Member | None:
        """Resolve a reply selector as a stable ID, display name, or member prefix."""
        selector = selector.strip()
        with self.connect() as db:
            row = db.execute("""SELECT m.* FROM members m JOIN owners o ON o.system_id=m.system_id
                WHERE o.account_id=? AND (m.id=? OR m.name=? OR m.prefix=?)""",
                (account_id, selector, selector, selector)).fetchone()
            return Member(**dict(row)) if row else None

    def configure_color(self, account_id: str, member_selector: str, color: str) -> Member:
        """Set the username color for an owned member using a six-digit RGB value."""
        normalized = color.strip().lower().removeprefix("#")
        if not re.fullmatch(r"[0-9a-f]{6}", normalized):
            raise ValueError("Color must be a six-digit hex value, such as #7b68ee.")
        member = self.member_selected(account_id, member_selector)
        if member is None:
            raise PermissionError("Member not found or not owned by this account.")
        with self.connect() as db:
            db.execute("UPDATE members SET color=? WHERE id=?", (f"#{normalized}", member.id))
        return self.member_selected(account_id, member.id)  # type: ignore[return-value]

    def configure_voice(self, account_id: str, member_selector: str, voice_reference: str | None,
                        voice_settings: str = "{}", playback: str = "send") -> Member:
        """Configure an owned member. Only ``send`` has output in this server-only release."""
        if playback not in {"off", "local", "send", "both"}:
            raise ValueError("Playback must be off, local, send, or both.")
        if playback in {"local", "both"}:
            raise ValueError("Local playback is not implemented; use off or send.")
        try:
            settings = json.loads(voice_settings)
        except json.JSONDecodeError as error:
            raise ValueError("Voice settings must be a JSON object.") from error
        if not isinstance(settings, dict):
            raise ValueError("Voice settings must be a JSON object.")
        member = self.member_selected(account_id, member_selector)
        if member is None:
            raise PermissionError("Member not found or not owned by this account.")
        if playback == "send" and not voice_reference:
            raise ValueError("Send playback requires a voice reference.")
        normalized = json.dumps(settings, separators=(",", ":"), sort_keys=True)
        with self.connect() as db:
            db.execute("UPDATE members SET voice_reference=?, voice_settings=?, playback=? WHERE id=?",
                       (voice_reference, normalized, playback, member.id))
        return self.member_selected(account_id, member.id)  # type: ignore[return-value]

    def configure_speech_formatting(self, account_id: str, member_selector: str, enabled: bool,
                                    strikethrough: str = "normal") -> Member:
        """Choose whether Markdown meaning, rather than its punctuation, reaches TTS."""
        if strikethrough not in {"mumble", "normal", "omit", "whisper"}:
            raise ValueError("Crossed-out speech must be mumble, normal, omit, or whisper.")
        member = self.member_selected(account_id, member_selector)
        if member is None:
            raise PermissionError("Member not found or not owned by this account.")
        with self.connect() as db:
            db.execute("UPDATE members SET speech_formatting=?, strikethrough_speech=? WHERE id=?",
                       (int(enabled), strikethrough, member.id))
        return self.member_selected(account_id, member.id)  # type: ignore[return-value]

    def match_member(self, account_id: str, content: str) -> tuple[Member, str] | None:
        with self.connect() as db:
            rows = db.execute("""SELECT m.* FROM members m JOIN owners o ON o.system_id=m.system_id
                WHERE o.account_id=? ORDER BY length(m.prefix)+length(m.suffix) DESC""", (account_id,)).fetchall()
        for row in rows:
            member = Member(**dict(row))
            if content.startswith(member.prefix) and (not member.suffix or content.endswith(member.suffix)):
                end = -len(member.suffix) if member.suffix else None
                body = content[len(member.prefix):end].strip()
                if body:
                    return member, body
        return None

    def record_proxy(self, source_id: str, proxy_id: str, channel_id: str, member: Member, owner: str) -> bool:
        if self.system_for(owner) != member.system_id:
            raise PermissionError("Account does not own this member.")
        try:
            with self.connect() as db:
                db.execute("""INSERT INTO proxied_messages
                    (proxy_message_id,source_message_id,channel_id,system_id,member_id,owner_account_id)
                    VALUES (?,?,?,?,?,?)""", (proxy_id, source_id, channel_id, member.system_id, member.id, owner))
            return True
        except sqlite3.IntegrityError:
            return False

    def source_was_processed(self, source_id: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM proxied_messages WHERE source_message_id=?", (source_id,)).fetchone() is not None

    def create_link(self, account_id: str, lifetime_seconds: int = 900) -> str:
        system_id = self.system_for(account_id)
        if not system_id:
            raise PermissionError("Create a system first.")
        token = short_hash(15)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            db.execute("INSERT INTO links VALUES (?,?,?,?,NULL)",
                       (digest, system_id, account_id, int(time.time()) + lifetime_seconds))
        return token

    def redeem_link(self, account_id: str, token: str) -> str:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connect() as db:
            row = db.execute("SELECT * FROM links WHERE token_hash=? AND used_at IS NULL AND expires_at>=?",
                             (digest, int(time.time()))).fetchone()
            if not row:
                raise PermissionError("That link code is invalid, expired, or already used.")
            if row["created_by"] == account_id:
                raise PermissionError("The code must be redeemed by the other account.")
            existing = db.execute("SELECT system_id FROM owners WHERE account_id=?", (account_id,)).fetchone()
            if existing and existing[0] != row["system_id"]:
                raise PermissionError("This account already belongs to another system.")
            db.execute("INSERT OR IGNORE INTO owners VALUES (?,?)", (account_id, row["system_id"]))
            db.execute("UPDATE links SET used_at=? WHERE token_hash=?", (int(time.time()), digest))
            return str(row["system_id"])

    def proxy_owned_by(self, proxy_id: str, account_id: str, channel_id: str | None = None) -> bool:
        with self.connect() as db:
            return db.execute("""SELECT 1 FROM proxied_messages p JOIN owners o ON o.system_id=p.system_id
                WHERE p.proxy_message_id=? AND o.account_id=? AND p.deleted_at IS NULL
                AND (? IS NULL OR p.channel_id=?)""",
                (proxy_id, account_id, channel_id, channel_id)).fetchone() is not None

    def replace_proxy(self, old_proxy_id: str, new_proxy_id: str, member: Member, account_id: str) -> bool:
        """Move durable attribution to a re-proxied message, if the caller owns both."""
        if self.system_for(account_id) != member.system_id:
            return False
        try:
            with self.connect() as db:
                cursor = db.execute("""UPDATE proxied_messages
                    SET proxy_message_id=?, member_id=?, owner_account_id=?
                    WHERE proxy_message_id=? AND system_id=? AND deleted_at IS NULL""",
                    (new_proxy_id, member.id, account_id, old_proxy_id, member.system_id))
                return cursor.rowcount == 1
        except sqlite3.IntegrityError:
            return False

    def mark_proxy_deleted(self, proxy_id: str, account_id: str) -> bool:
        with self.connect() as db:
            cursor = db.execute("""UPDATE proxied_messages SET deleted_at=unixepoch()
                WHERE proxy_message_id=? AND deleted_at IS NULL AND system_id=(
                    SELECT system_id FROM owners WHERE account_id=?)""", (proxy_id, account_id))
            return cursor.rowcount == 1
