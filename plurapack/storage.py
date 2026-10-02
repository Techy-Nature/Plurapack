from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Iterator

from .voice import normalize_voice_settings


MAX_PROXY_TAGS = 100


def short_hash(length: int) -> str:
    """Return a cryptographically random, lowercase SHA-256 fragment."""
    return hashlib.sha256(secrets.token_bytes(32)).hexdigest()[:length]


def new_profile_id(db: sqlite3.Connection) -> str:
    """Return an unused ID from the namespace shared by members and forms."""
    while True:
        value = short_hash(5)
        member = db.execute("SELECT 1 FROM members WHERE id=?", (value,)).fetchone()
        form = db.execute("SELECT 1 FROM forms WHERE id=?", (value,)).fetchone()
        if member is None and form is None:
            return value


def new_group_id(db: sqlite3.Connection) -> str:
    """Return an unused eight-character group code."""
    while True:
        value = short_hash(8)
        if db.execute("SELECT 1 FROM groups WHERE id=?", (value,)).fetchone() is None:
            return value


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
    alias: str | None
    default_form_id: str | None
    description: str
    pronouns: str | None
    banner: str | None


@dataclass(frozen=True)
class System:
    id: str
    display_name: str
    description: str
    logo: str | None
    system_tag: str | None
    show_system_tag: int
    banner: str | None


@dataclass(frozen=True)
class Form:
    """An alternate presentation connected to one permanent member ID."""
    id: str
    member_id: str
    display_name: str
    avatar: str | None
    soma: str
    pronouns: str | None
    prefix: str
    suffix: str
    banner: str | None


@dataclass(frozen=True)
class Front:
    member: Member
    form: Form | None


@dataclass(frozen=True)
class Autoproxy:
    """A system's independent autoproxy selection and front-following preference."""
    member: Member | None
    autofront: bool


@dataclass(frozen=True)
class ProxyTag:
    """One of the proxy tags assigned to a member or form."""
    prefix: str
    suffix: str = ""


@dataclass(frozen=True)
class Group:
    id: str
    system_id: str
    name: str
    alias: str
    avatar: str | None


class Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            try:
                Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            except OSError as error:
                raise RuntimeError(
                    f"Cannot create database parent directory for '{self.path}': {error}"
                ) from error
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA busy_timeout = 5000")
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
            # WAL lets the bot and short-lived web requests read concurrently
            # while retaining SQLite's single-writer safety.
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS systems (
                    id TEXT PRIMARY KEY CHECK(length(id)=10), display_name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '', logo TEXT,
                    system_tag TEXT, show_system_tag INTEGER NOT NULL DEFAULT 1,
                    banner TEXT,
                        CHECK(show_system_tag IN (0,1))
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
                    default_form_id TEXT REFERENCES forms(id) ON DELETE SET NULL,
                    description TEXT NOT NULL DEFAULT '',
                    pronouns TEXT,
                    banner TEXT,
                    UNIQUE(system_id, name), UNIQUE(system_id, prefix, suffix)
                );
                CREATE TABLE IF NOT EXISTS links (
                    token_hash TEXT PRIMARY KEY, system_id TEXT NOT NULL REFERENCES systems(id),
                    created_by TEXT NOT NULL, expires_at INTEGER NOT NULL, used_at INTEGER
                );
                CREATE TABLE IF NOT EXISTS login_attempts (
                    id TEXT PRIMARY KEY, code_hash TEXT NOT NULL UNIQUE,
                    browser_secret_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
                    verified_account_id TEXT, verified_username TEXT,
                    consumed_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS login_attempts_expiry
                    ON login_attempts(expires_at);
                CREATE TABLE IF NOT EXISTS proxied_messages (
                    proxy_message_id TEXT PRIMARY KEY, source_message_id TEXT NOT NULL UNIQUE,
                    channel_id TEXT NOT NULL, system_id TEXT NOT NULL, member_id TEXT NOT NULL,
                    owner_account_id TEXT NOT NULL, created_at INTEGER NOT NULL DEFAULT (unixepoch()),
                    deleted_at INTEGER, FOREIGN KEY(member_id) REFERENCES members(id)
                );
                CREATE TABLE IF NOT EXISTS forms (
                    id TEXT PRIMARY KEY CHECK(length(id)=5),
                    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
                    display_name TEXT NOT NULL, avatar TEXT, soma TEXT NOT NULL DEFAULT '', pronouns TEXT,
                    prefix TEXT NOT NULL DEFAULT '', suffix TEXT NOT NULL DEFAULT '', banner TEXT,
                    UNIQUE(member_id, display_name)
                );
                CREATE TABLE IF NOT EXISTS current_fronts (
                    system_id TEXT PRIMARY KEY REFERENCES systems(id) ON DELETE CASCADE,
                    member_id TEXT NOT NULL REFERENCES members(id),
                    form_id TEXT REFERENCES forms(id)
                );
                CREATE TABLE IF NOT EXISTS autoproxy_settings (
                    system_id TEXT PRIMARY KEY REFERENCES systems(id) ON DELETE CASCADE,
                    member_id TEXT REFERENCES members(id) ON DELETE SET NULL,
                    autofront INTEGER NOT NULL DEFAULT 0 CHECK(autofront IN (0,1))
                );
                CREATE TABLE IF NOT EXISTS system_tag_overrides (
                    system_id TEXT NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
                    scope_type TEXT NOT NULL CHECK(scope_type IN ('server','channel')),
                    scope_id TEXT NOT NULL,
                    show_system_tag INTEGER NOT NULL CHECK(show_system_tag IN (0,1)),
                    PRIMARY KEY(system_id, scope_type, scope_id)
                );
                CREATE TABLE IF NOT EXISTS proxy_tags (
                    id INTEGER PRIMARY KEY,
                    system_id TEXT NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
                    member_id TEXT REFERENCES members(id) ON DELETE CASCADE,
                    form_id TEXT REFERENCES forms(id) ON DELETE CASCADE,
                    prefix TEXT NOT NULL,
                    suffix TEXT NOT NULL DEFAULT '',
                    CHECK((member_id IS NOT NULL) <> (form_id IS NOT NULL)),
                    UNIQUE(system_id, prefix, suffix)
                );
                CREATE TABLE IF NOT EXISTS groups (
                    id TEXT PRIMARY KEY CHECK(length(id)=8),
                    system_id TEXT NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
                    name TEXT NOT NULL COLLATE NOCASE, alias TEXT NOT NULL COLLATE NOCASE,
                    avatar TEXT, UNIQUE(system_id,name), UNIQUE(system_id,alias)
                );
                CREATE TABLE IF NOT EXISTS group_members (
                    group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE,
                    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
                    PRIMARY KEY(group_id,member_id)
                );
                CREATE TABLE IF NOT EXISTS active_groups (
                    system_id TEXT PRIMARY KEY REFERENCES systems(id) ON DELETE CASCADE,
                    group_id TEXT NOT NULL REFERENCES groups(id) ON DELETE CASCADE
                );
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(members)")}
            if "color" not in columns:
                db.execute("ALTER TABLE members ADD COLUMN color TEXT")
            if "speech_formatting" not in columns:
                db.execute("ALTER TABLE members ADD COLUMN speech_formatting INTEGER NOT NULL DEFAULT 0")
            if "strikethrough_speech" not in columns:
                db.execute("ALTER TABLE members ADD COLUMN strikethrough_speech TEXT NOT NULL DEFAULT 'normal'")
            if "alias" not in columns:
                db.execute("ALTER TABLE members ADD COLUMN alias TEXT")
            if "default_form_id" not in columns:
                db.execute("ALTER TABLE members ADD COLUMN default_form_id TEXT")
            if "description" not in columns:
                db.execute("ALTER TABLE members ADD COLUMN description TEXT NOT NULL DEFAULT ''")
            if "pronouns" not in columns:
                db.execute("ALTER TABLE members ADD COLUMN pronouns TEXT")
            if "banner" not in columns:
                db.execute("ALTER TABLE members ADD COLUMN banner TEXT")
            form_columns = {row[1] for row in db.execute("PRAGMA table_info(forms)")}
            if "pronouns" not in form_columns:
                db.execute("ALTER TABLE forms ADD COLUMN pronouns TEXT")
            if "prefix" not in form_columns:
                db.execute("ALTER TABLE forms ADD COLUMN prefix TEXT NOT NULL DEFAULT ''")
            if "suffix" not in form_columns:
                db.execute("ALTER TABLE forms ADD COLUMN suffix TEXT NOT NULL DEFAULT ''")
            if "banner" not in form_columns:
                db.execute("ALTER TABLE forms ADD COLUMN banner TEXT")
            system_columns = {row[1] for row in db.execute("PRAGMA table_info(systems)")}
            if "description" not in system_columns:
                db.execute("ALTER TABLE systems ADD COLUMN description TEXT NOT NULL DEFAULT ''")
            if "logo" not in system_columns:
                db.execute("ALTER TABLE systems ADD COLUMN logo TEXT")
            if "system_tag" not in system_columns:
                db.execute("ALTER TABLE systems ADD COLUMN system_tag TEXT")
            if "show_system_tag" not in system_columns:
                db.execute("ALTER TABLE systems ADD COLUMN show_system_tag INTEGER NOT NULL DEFAULT 1")
            if "banner" not in system_columns:
                db.execute("ALTER TABLE systems ADD COLUMN banner TEXT")
            groups_sql = db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='groups'"
            ).fetchone()[0]
            if "length(id)=5" in groups_sql.replace(" ", ""):
                groups = db.execute("SELECT * FROM groups").fetchall()
                memberships = db.execute("SELECT * FROM group_members").fetchall()
                active = db.execute("SELECT * FROM active_groups").fetchall()
                replacements: dict[str, str] = {}
                for row in groups:
                    replacement = short_hash(8)
                    while replacement in replacements.values():
                        replacement = short_hash(8)
                    replacements[row["id"]] = replacement
                db.execute("DELETE FROM active_groups")
                db.execute("DELETE FROM group_members")
                db.execute("DROP TABLE groups")
                db.execute("""CREATE TABLE groups (
                    id TEXT PRIMARY KEY CHECK(length(id)=8),
                    system_id TEXT NOT NULL REFERENCES systems(id) ON DELETE CASCADE,
                    name TEXT NOT NULL COLLATE NOCASE, alias TEXT NOT NULL COLLATE NOCASE,
                    avatar TEXT, UNIQUE(system_id,name), UNIQUE(system_id,alias)
                )""")
                db.executemany("INSERT INTO groups VALUES (?,?,?,?,?)", (
                    (replacements[row["id"]], row["system_id"], row["name"], row["alias"], row["avatar"])
                    for row in groups
                ))
                db.executemany("INSERT INTO group_members VALUES (?,?)", (
                    (replacements[row["group_id"]], row["member_id"]) for row in memberships
                ))
                db.executemany("INSERT INTO active_groups VALUES (?,?)", (
                    (row["system_id"], replacements[row["group_id"]]) for row in active
                ))
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS member_system_alias ON members(system_id, alias) WHERE alias IS NOT NULL")
            # Seed the normalized table when opening an older database. The
            # legacy columns remain as the primary tag for API compatibility.
            db.execute("""INSERT OR IGNORE INTO proxy_tags(system_id,member_id,prefix,suffix)
                SELECT system_id,id,prefix,suffix FROM members WHERE prefix<>''""")
            db.execute("""INSERT OR IGNORE INTO proxy_tags(system_id,form_id,prefix,suffix)
                SELECT m.system_id,f.id,f.prefix,f.suffix FROM forms f
                JOIN members m ON m.id=f.member_id WHERE f.prefix<>''""")

    def systems_for_account(self, account_id: str) -> list[System]:
        """Return the account's linked system as a list (the schema permits at most one)."""
        with self.connect() as db:
            rows = db.execute("""SELECT s.* FROM systems s JOIN owners o ON o.system_id=s.id
                WHERE o.account_id=? ORDER BY s.display_name""", (account_id,)).fetchall()
        return [System(**dict(row)) for row in rows]

    def account_has_system(self, account_id: str, system_id: str) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM owners WHERE account_id=? AND system_id=?",
                              (account_id, system_id)).fetchone() is not None

    def update_system(self, account_id: str, system_id: str, **changes: Any) -> System:
        if not self.account_has_system(account_id, system_id):
            raise PermissionError("System not owned by this account.")
        allowed = {"display_name", "description", "logo", "system_tag", "show_system_tag", "banner"}
        if not changes or not set(changes) <= allowed:
            raise ValueError("No supported system fields were supplied.")
        with self.connect() as db:
            db.execute(f"UPDATE systems SET {','.join(f'{key}=?' for key in changes)} WHERE id=?",
                       (*changes.values(), system_id))
        return self.system_info(system_id)  # type: ignore[return-value]

    def update_member(self, account_id: str, member_id: str, **changes: Any) -> Member:
        member = self.member_selected(account_id, member_id)
        if member is None:
            raise PermissionError("Member not found or not owned by this account.")
        allowed = {"name", "prefix", "suffix", "avatar", "color", "alias", "description",
                   "pronouns", "default_form_id", "banner"}
        if not changes or not set(changes) <= allowed:
            raise ValueError("No supported member fields were supplied.")
        if "prefix" in changes or "suffix" in changes:
            self._validate_proxy_tag(changes.get("prefix", member.prefix), changes.get("suffix", member.suffix))
        try:
            with self.connect() as db:
                db.execute(f"UPDATE members SET {','.join(f'{key}=?' for key in changes)} WHERE id=?",
                           (*changes.values(), member.id))
        except sqlite3.IntegrityError as error:
            raise ValueError("The member name, alias, or proxy tag is already in use.") from error
        return self.member_selected(account_id, member.id)  # type: ignore[return-value]

    def update_form(self, account_id: str, form_id: str, **changes: Any) -> Form:
        selected = self.form_selected(account_id, form_id)
        if selected is None:
            raise PermissionError("Form not found or not owned by this account.")
        allowed = {"display_name", "avatar", "soma", "pronouns", "prefix", "suffix", "banner"}
        if not changes or not set(changes) <= allowed:
            raise ValueError("No supported form fields were supplied.")
        form, _ = selected
        try:
            with self.connect() as db:
                db.execute(f"UPDATE forms SET {','.join(f'{key}=?' for key in changes)} WHERE id=?",
                           (*changes.values(), form.id))
        except sqlite3.IntegrityError as error:
            raise ValueError("That form name or proxy tag is already in use.") from error
        return self.form_selected(account_id, form.id)[0]  # type: ignore[index]

    def delete_form(self, account_id: str, form_id: str) -> Form:
        selected = self.form_selected(account_id, form_id)
        if selected is None:
            raise PermissionError("Form not found or not owned by this account.")
        form, _ = selected
        with self.connect() as db:
            db.execute("UPDATE members SET default_form_id=NULL WHERE default_form_id=?", (form.id,))
            db.execute("UPDATE current_fronts SET form_id=NULL WHERE form_id=?", (form.id,))
            db.execute("DELETE FROM forms WHERE id=?", (form.id,))
        return form

    def clear_front(self, account_id: str) -> None:
        system_id = self.system_for(account_id)
        if system_id:
            with self.connect() as db:
                db.execute("DELETE FROM current_fronts WHERE system_id=?", (system_id,))

    def create_system(self, account_id: str, name: str, description: str = "") -> str:
        description = description.strip()
        if len(description) > 1000:
            raise ValueError("System description must be no more than 1000 characters.")
        with self.connect() as db:
            existing = db.execute("SELECT system_id FROM owners WHERE account_id=?", (account_id,)).fetchone()
            if existing:
                return str(existing[0])
            while True:
                system_id = short_hash(10)
                try:
                    db.execute(
                        "INSERT INTO systems(id,display_name,description) VALUES (?,?,?)",
                        (system_id, name, description),
                    )
                    db.execute("INSERT INTO owners VALUES (?,?)", (account_id, system_id))
                    return system_id
                except sqlite3.IntegrityError:
                    continue

    def system_for(self, account_id: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT system_id FROM owners WHERE account_id=?", (account_id,)).fetchone()
            return str(row[0]) if row else None

    def system_info(self, selector: str) -> System | None:
        """Resolve a system by stable ID or exact name for its public card."""
        with self.connect() as db:
            rows = db.execute("""SELECT * FROM systems WHERE id=? OR display_name=?
                ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END""", (selector, selector, selector)).fetchall()
        if not rows:
            return None
        if len(rows) > 1 and rows[0]["id"] != selector:
            raise ValueError("More than one system has that name; use the system ID instead.")
        return System(**dict(rows[0]))

    def system_by_id(self, system_id: str) -> System | None:
        """Resolve a public system using only its globally stable ID."""
        with self.connect() as db:
            row = db.execute("SELECT * FROM systems WHERE id=?", (system_id.strip(),)).fetchone()
        return System(**dict(row)) if row else None

    def system_named_for(self, account_id: str, display_name: str) -> System | None:
        """Resolve an exact display name only within the caller's linked system."""
        with self.connect() as db:
            row = db.execute("""SELECT s.* FROM systems s JOIN owners o ON o.system_id=s.id
                WHERE o.account_id=? AND s.display_name=?""",
                (account_id, display_name.strip())).fetchone()
        return System(**dict(row)) if row else None

    def configure_system_tag(self, account_id: str, tag: str | None) -> System:
        """Set the tag owned by a system's stable ID, or clear it."""
        system_id = self.system_for(account_id)
        if system_id is None:
            raise PermissionError("Create a system first.")
        normalized = tag.strip() if tag else None
        if normalized and len(normalized) > 32:
            raise ValueError("System tags must be no more than 32 characters.")
        with self.connect() as db:
            db.execute("UPDATE systems SET system_tag=? WHERE id=?", (normalized, system_id))
        return self.system_info(system_id)  # type: ignore[return-value]

    def configure_system_tag_visibility(self, account_id: str, show: bool,
                                        scope_type: str = "system",
                                        scope_id: str | None = None) -> None:
        """Set system-wide visibility or a server/channel override."""
        system_id = self.system_for(account_id)
        if system_id is None:
            raise PermissionError("Create a system first.")
        if scope_type == "system":
            with self.connect() as db:
                db.execute("UPDATE systems SET show_system_tag=? WHERE id=?", (int(show), system_id))
            return
        if scope_type not in {"server", "channel"} or not scope_id:
            raise ValueError("A server or channel visibility setting requires its ID.")
        with self.connect() as db:
            db.execute("""INSERT INTO system_tag_overrides
                (system_id,scope_type,scope_id,show_system_tag) VALUES (?,?,?,?)
                ON CONFLICT(system_id,scope_type,scope_id) DO UPDATE SET
                    show_system_tag=excluded.show_system_tag""",
                (system_id, scope_type, scope_id, int(show)))

    def clear_system_tag_visibility_override(self, account_id: str, scope_type: str,
                                             scope_id: str) -> None:
        if scope_type not in {"server", "channel"}:
            raise ValueError("Only server and channel settings are overrides.")
        system_id = self.system_for(account_id)
        if system_id is None:
            raise PermissionError("Create a system first.")
        with self.connect() as db:
            db.execute("DELETE FROM system_tag_overrides WHERE system_id=? AND scope_type=? AND scope_id=?",
                       (system_id, scope_type, scope_id))

    def proxy_name(self, member: Member, server_id: str | None = None,
                   channel_id: str | None = None) -> str:
        """Return a presentation name with the most-specific visible system tag."""
        with self.connect() as db:
            system = db.execute(
                "SELECT system_tag,show_system_tag FROM systems WHERE id=?", (member.system_id,)
            ).fetchone()
            if system is None or not system["system_tag"]:
                return member.name
            show = bool(system["show_system_tag"])
            if server_id:
                row = db.execute("""SELECT show_system_tag FROM system_tag_overrides
                    WHERE system_id=? AND scope_type='server' AND scope_id=?""",
                    (member.system_id, server_id)).fetchone()
                if row is not None:
                    show = bool(row[0])
            if channel_id:
                row = db.execute("""SELECT show_system_tag FROM system_tag_overrides
                    WHERE system_id=? AND scope_type='channel' AND scope_id=?""",
                    (member.system_id, channel_id)).fetchone()
                if row is not None:
                    show = bool(row[0])
        return f"{member.name} {system['system_tag']}" if show else member.name

    def members_for_system(self, system_id: str) -> list[Member]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM members WHERE system_id=? ORDER BY name", (system_id,)).fetchall()
        return [Member(**dict(row)) for row in rows]

    def public_member_selected(self, selector: str, system_id: str | None = None) -> Member | None:
        """Resolve a card selector without granting any mutation permissions."""
        query = "SELECT * FROM members WHERE (id=? OR name=? OR alias=?)"
        parameters: list[str] = [selector.strip(), selector.strip(), selector.strip()]
        if system_id:
            query += " AND system_id=?"
            parameters.append(system_id)
        query += " ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END"
        parameters.append(selector.strip())
        with self.connect() as db:
            rows = db.execute(query, parameters).fetchall()
        if not rows:
            return None
        if len(rows) > 1 and rows[0]["id"] != selector.strip():
            raise ValueError("More than one member has that name; use the member ID instead.")
        return Member(**dict(rows[0]))

    def public_member_by_id(self, member_id: str) -> Member | None:
        """Resolve a public member using only its globally stable ID."""
        with self.connect() as db:
            row = db.execute("SELECT * FROM members WHERE id=?", (member_id.strip(),)).fetchone()
        return Member(**dict(row)) if row else None

    def forms_for_member(self, member_id: str) -> list[Form]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM forms WHERE member_id=? ORDER BY display_name", (member_id,)).fetchall()
        return [Form(**dict(row)) for row in rows]

    def delete_member(self, account_id: str, selector: str) -> Member:
        """Delete an owned member and every form and attribution tied to it."""
        member = self.member_selected(account_id, selector)
        if member is None:
            raise PermissionError("Member not found or not owned by this account.")
        with self.connect() as db:
            db.execute("DELETE FROM proxied_messages WHERE member_id=?", (member.id,))
            db.execute("DELETE FROM current_fronts WHERE member_id=?", (member.id,))
            db.execute("UPDATE autoproxy_settings SET member_id=NULL WHERE member_id=?", (member.id,))
            db.execute("DELETE FROM members WHERE id=?", (member.id,))
        return member

    def delete_system(self, account_id: str, confirmation: str) -> str:
        """Irreversibly erase an owned system after its exact ID is supplied."""
        system_id = self.system_for(account_id)
        if system_id is None or confirmation.strip() != system_id:
            raise PermissionError("The confirmation must exactly match your system ID.")
        with self.connect() as db:
            if not db.execute("SELECT 1 FROM owners WHERE account_id=? AND system_id=?",
                              (account_id, system_id)).fetchone():
                raise PermissionError("That system is not owned by this account.")
            db.execute("DELETE FROM proxied_messages WHERE system_id=?", (system_id,))
            db.execute("DELETE FROM current_fronts WHERE system_id=?", (system_id,))
            db.execute("DELETE FROM autoproxy_settings WHERE system_id=?", (system_id,))
            db.execute("DELETE FROM links WHERE system_id=?", (system_id,))
            db.execute("DELETE FROM members WHERE system_id=?", (system_id,))
            db.execute("DELETE FROM owners WHERE system_id=?", (system_id,))
            db.execute("DELETE FROM systems WHERE id=?", (system_id,))
        return system_id

    def add_member(self, account_id: str, name: str, prefix: str, suffix: str = "",
                   description: str = "", *, alias: str | None = None,
                   pronouns: str | None = None, color: str | None = None,
                   avatar: str | None = None) -> Member:
        system_id = self.system_for(account_id)
        if not system_id:
            raise PermissionError("Create a system first.")
        description = description.strip()
        if len(description) > 1000:
            raise ValueError("Member description must be no more than 1000 characters.")
        with self.connect() as db:
            while True:
                member_id = new_profile_id(db)
                try:
                    db.execute(
                        """INSERT INTO members
                        (id,system_id,name,prefix,suffix,description,alias,pronouns,color,avatar)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (member_id, system_id, name, prefix, suffix, description,
                         alias, pronouns, color, avatar),
                    )
                    db.execute("""INSERT INTO proxy_tags(system_id,member_id,prefix,suffix)
                        VALUES (?,?,?,?)""", (system_id, member_id, prefix, suffix))
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
                        member_id = new_profile_id(db)
                        db.execute("""INSERT INTO members
                            (id,system_id,name,prefix,suffix,avatar,color,pronouns) VALUES (?,?,?,?,?,?,?,?)""",
                            (member_id, system_id, member.name, member.prefix, member.suffix,
                             member.avatar, member.color, member.pronouns))
                        db.execute("""INSERT INTO proxy_tags(system_id,member_id,prefix,suffix)
                            VALUES (?,?,?,?)""", (system_id, member_id, member.prefix, member.suffix))
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
        """Resolve a member's stable ID, full name, short alias, or proxy prefix."""
        selector = selector.strip()
        with self.connect() as db:
            row = db.execute("""SELECT DISTINCT m.* FROM members m
                JOIN owners o ON o.system_id=m.system_id
                LEFT JOIN proxy_tags t ON t.member_id=m.id
                WHERE o.account_id=? AND (m.id=? OR m.name=? OR m.alias=? OR t.prefix=?)""",
                (account_id, selector, selector, selector, selector)).fetchone()
            return Member(**dict(row)) if row else None

    def public_form_selected(self, selector: str) -> tuple[Form, Member] | None:
        """Resolve a form publicly by stable ID or an unambiguous display name."""
        selector = selector.strip()
        with self.connect() as db:
            rows = db.execute("""SELECT f.id form_id, f.member_id, f.display_name,
                f.avatar form_avatar, f.soma, f.pronouns form_pronouns,
                f.prefix form_prefix, f.suffix form_suffix, f.banner form_banner, m.*
                FROM forms f JOIN members m ON m.id=f.member_id
                WHERE f.id=? OR f.display_name=?
                ORDER BY CASE WHEN f.id=? THEN 0 ELSE 1 END""",
                (selector, selector, selector)).fetchall()
        if not rows:
            return None
        if len(rows) > 1 and rows[0]["form_id"] != selector:
            raise ValueError("More than one form has that name; use the form ID instead.")
        values = dict(rows[0])
        form = Form(values.pop("form_id"), values["member_id"], values.pop("display_name"),
                    values.pop("form_avatar"), values.pop("soma"), values.pop("form_pronouns"),
                    values.pop("form_prefix"), values.pop("form_suffix"), values.pop("form_banner"))
        values.pop("member_id")
        return form, Member(**values)

    def public_form_by_id(self, form_id: str) -> tuple[Form, Member] | None:
        """Resolve a public form using only its globally stable ID."""
        selector = form_id.strip()
        with self.connect() as db:
            row = db.execute("""SELECT f.id form_id, f.member_id, f.display_name,
                f.avatar form_avatar, f.soma, f.pronouns form_pronouns,
                f.prefix form_prefix, f.suffix form_suffix, f.banner form_banner, m.*
                FROM forms f JOIN members m ON m.id=f.member_id WHERE f.id=?""",
                (selector,)).fetchone()
        if row is None:
            return None
        values = dict(row)
        form = Form(values.pop("form_id"), values["member_id"], values.pop("display_name"),
                    values.pop("form_avatar"), values.pop("soma"), values.pop("form_pronouns"),
                    values.pop("form_prefix"), values.pop("form_suffix"), values.pop("form_banner"))
        values.pop("member_id")
        return form, Member(**values)

    def public_info_selected(
        self, account_id: str, selector: str
    ) -> System | Member | tuple[Form, Member] | None:
        """Resolve stable IDs globally and human-readable selectors only locally."""
        selector = selector.strip()

        # An existing stable ID always wins, regardless of who owns its profile.
        value = self.system_by_id(selector)
        if value is not None:
            return value
        member = self.public_member_by_id(selector)
        if member is not None:
            return member
        form = self.public_form_by_id(selector)
        if form is not None:
            return form

        # Names and aliases are contextual to the caller's linked system.
        value = self.system_named_for(account_id, selector)
        if value is not None:
            return value
        member = self.member_selected(account_id, selector)
        if member is not None and selector.casefold() in {
            member.name.casefold(), (member.alias or "").casefold()
        }:
            return member
        return self.form_selected(account_id, selector)

    def create_group(self, account_id: str, name: str, alias: str,
                     avatar: str | None = None) -> Group:
        system_id = self.system_for(account_id)
        if system_id is None:
            raise PermissionError("Create a system first.")
        name, alias = name.strip(), alias.strip()
        if not name or len(name) > 80:
            raise ValueError("Group name must be 1–80 characters.")
        if not re.fullmatch(r"[^\s:]{1,24}", alias):
            raise ValueError("Group alias must be 1–24 characters without spaces or colons.")
        avatar = avatar.strip() if avatar else None
        if avatar and not re.fullmatch(r"https?://\S+", avatar):
            raise ValueError("Group avatar must be an HTTP or HTTPS URL.")
        try:
            with self.connect() as db:
                while True:
                    group_id = new_group_id(db)
                    try:
                        db.execute("INSERT INTO groups(id,system_id,name,alias,avatar) VALUES (?,?,?,?,?)",
                                   (group_id, system_id, name, alias, avatar))
                        db.execute("""INSERT INTO active_groups(system_id,group_id) VALUES (?,?)
                            ON CONFLICT(system_id) DO UPDATE SET group_id=excluded.group_id""",
                                   (system_id, group_id))
                        break
                    except sqlite3.IntegrityError as error:
                        if "groups.id" not in str(error):
                            raise
        except sqlite3.IntegrityError as error:
            raise ValueError("That group name or alias is already in use.") from error
        return self.active_group(account_id)  # type: ignore[return-value]

    def groups_for_system(self, system_id: str) -> list[Group]:
        """List a system's groups without changing its active group."""
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM groups WHERE system_id=? ORDER BY name COLLATE NOCASE, id",
                (system_id,),
            ).fetchall()
        return [Group(**dict(row)) for row in rows]

    def group_selected(self, account_id: str, selector: str) -> Group | None:
        """Resolve an ID, name, or alias only inside the caller's system."""
        system_id = self.system_for(account_id)
        if system_id is None:
            return None
        selector = selector.strip()
        with self.connect() as db:
            row = db.execute(
                """SELECT * FROM groups WHERE system_id=? AND
                   (id=? OR name=? COLLATE NOCASE OR alias=? COLLATE NOCASE)""",
                (system_id, selector, selector, selector),
            ).fetchone()
        return Group(**dict(row)) if row else None

    def group_members(self, account_id: str, group_id: str) -> list[Member]:
        group = self.group_selected(account_id, group_id)
        if group is None or group.id != group_id:
            raise PermissionError("Group not found or not owned by this account.")
        with self.connect() as db:
            rows = db.execute(
                """SELECT m.* FROM members m JOIN group_members gm ON gm.member_id=m.id
                   WHERE gm.group_id=? ORDER BY m.name COLLATE NOCASE, m.id""",
                (group.id,),
            ).fetchall()
        return [Member(**dict(row)) for row in rows]

    def set_active_group(self, account_id: str, group_id: str) -> Group:
        group = self.group_selected(account_id, group_id)
        if group is None or group.id != group_id:
            raise PermissionError("Group not found or not owned by this account.")
        with self.connect() as db:
            db.execute("""INSERT INTO active_groups(system_id,group_id) VALUES (?,?)
                ON CONFLICT(system_id) DO UPDATE SET group_id=excluded.group_id""",
                       (group.system_id, group.id))
        return group

    def update_group(self, account_id: str, group_id: str, **changes: Any) -> Group:
        group = self.group_selected(account_id, group_id)
        if group is None or group.id != group_id:
            raise PermissionError("Group not found or not owned by this account.")
        if not changes or not set(changes) <= {"name", "alias", "avatar"}:
            raise ValueError("No supported group fields were supplied.")
        if "name" in changes:
            changes["name"] = changes["name"].strip()
            if not changes["name"] or len(changes["name"]) > 80:
                raise ValueError("Group name must be 1–80 characters.")
        if "alias" in changes:
            changes["alias"] = changes["alias"].strip()
            if not re.fullmatch(r"[^\s:]{1,24}", changes["alias"]):
                raise ValueError("Group alias must be 1–24 characters without spaces or colons.")
        if "avatar" in changes:
            changes["avatar"] = changes["avatar"].strip() if changes["avatar"] else None
            if changes["avatar"] and not re.fullmatch(r"https?://\S+", changes["avatar"]):
                raise ValueError("Group avatar must be an HTTP or HTTPS URL.")
        try:
            with self.connect() as db:
                db.execute(f"UPDATE groups SET {','.join(f'{key}=?' for key in changes)} "
                           "WHERE id=? AND system_id=?",
                           (*changes.values(), group.id, group.system_id))
        except sqlite3.IntegrityError as error:
            duplicate = "name" if "name" in changes else "alias"
            raise ValueError(f"That group {duplicate} is already in use.") from error
        return self.group_selected(account_id, group.id)  # type: ignore[return-value]

    def add_members_to_group(self, account_id: str, group_id: str,
                             selectors: Iterable[str]) -> tuple[Group, list[Member]]:
        group = self.group_selected(account_id, group_id)
        if group is None or group.id != group_id:
            raise PermissionError("Group not found or not owned by this account.")
        members: list[Member] = []
        for selector in selectors:
            member = self.member_selected(account_id, selector)
            if member is None or member.system_id != group.system_id:
                raise ValueError(f"Member `{selector}` was not found in this system.")
            members.append(member)
        with self.connect() as db:
            db.executemany("INSERT OR IGNORE INTO group_members(group_id,member_id) VALUES (?,?)",
                           ((group.id, member.id) for member in members))
        return group, members

    def remove_members_from_group(self, account_id: str, group_id: str,
                                  selectors: Iterable[str]) -> tuple[Group, list[Member]]:
        group = self.group_selected(account_id, group_id)
        if group is None or group.id != group_id:
            raise PermissionError("Group not found or not owned by this account.")
        members: list[Member] = []
        for selector in selectors:
            member = self.member_selected(account_id, selector)
            if member is None or member.system_id != group.system_id:
                raise ValueError(f"Member `{selector}` was not found in this system.")
            members.append(member)
        with self.connect() as db:
            db.executemany("DELETE FROM group_members WHERE group_id=? AND member_id=?",
                           ((group.id, member.id) for member in members))
        return group, members

    def replace_group_members(self, account_id: str, group_id: str,
                              selectors: Iterable[str]) -> tuple[Group, list[Member]]:
        group, members = self.add_members_to_group(account_id, group_id, selectors)
        with self.connect() as db:
            db.execute("DELETE FROM group_members WHERE group_id=?", (group.id,))
            db.executemany("INSERT INTO group_members(group_id,member_id) VALUES (?,?)",
                           ((group.id, member.id) for member in members))
        return group, members

    def delete_group(self, account_id: str, group_id: str) -> Group:
        group = self.group_selected(account_id, group_id)
        if group is None or group.id != group_id:
            raise PermissionError("Group not found or not owned by this account.")
        with self.connect() as db:
            db.execute("DELETE FROM groups WHERE id=? AND system_id=?", (group.id, group.system_id))
        return group

    def active_group(self, account_id: str) -> Group | None:
        with self.connect() as db:
            row = db.execute("""SELECT g.* FROM groups g JOIN active_groups a ON a.group_id=g.id
                JOIN owners o ON o.system_id=g.system_id WHERE o.account_id=?""", (account_id,)).fetchone()
        return Group(**dict(row)) if row else None

    def add_group_members(self, account_id: str, selectors: Iterable[str]) -> tuple[Group, list[Member]]:
        group = self.active_group(account_id)
        if group is None:
            raise PermissionError("Create a group first.")
        selectors = list(selectors)
        if not selectors:
            raise ValueError("Supply at least one member alias or ID.")
        return self.add_members_to_group(account_id, group.id, selectors)

    def update_active_group(self, account_id: str, **changes: Any) -> Group:
        group = self.active_group(account_id)
        if group is None:
            raise PermissionError("Create a group first.")
        return self.update_group(account_id, group.id, **changes)

    def configure_alias(self, account_id: str, member_selector: str, alias: str | None) -> Member:
        """Set a short lookup name without changing the member's full display name."""
        member = self.member_selected(account_id, member_selector)
        if member is None:
            raise PermissionError("Member not found or not owned by this account.")
        normalized = alias.strip() if alias else None
        if normalized and (len(normalized) > 24 or not re.fullmatch(r"[^\s:]{1,24}", normalized)):
            raise ValueError("Alias must be 1–24 characters without spaces or colons.")
        try:
            with self.connect() as db:
                db.execute("UPDATE members SET alias=? WHERE id=?", (normalized, member.id))
        except sqlite3.IntegrityError as error:
            raise ValueError("That alias is already used by another member.") from error
        return self.member_selected(account_id, member.id)  # type: ignore[return-value]

    def create_form(self, account_id: str, member_selector: str, display_name: str,
                    avatar: str | None = None, soma: str = "", pronouns: str | None = None,
                    prefix: str = "", suffix: str = "", banner: str | None = None) -> Form:
        """Create a form whose ID permanently resolves back to its member."""
        member = self.member_selected(account_id, member_selector)
        if member is None:
            raise PermissionError("Member not found or not owned by this account.")
        display_name, soma = display_name.strip(), soma.strip()
        avatar = avatar.strip() if avatar else None
        pronouns = self._normalize_pronouns(pronouns)
        if not display_name or len(display_name) > 80:
            raise ValueError("Form display name must be 1–80 characters.")
        if avatar and not re.fullmatch(r"https?://\S+", avatar):
            raise ValueError("Form picture must be an HTTP or HTTPS URL.")
        if len(soma) > 1000:
            raise ValueError("Form soma must be no more than 1000 characters.")
        self._validate_proxy_tag(prefix, suffix)
        try:
            with self.connect() as db:
                if prefix:
                    collision = db.execute("""SELECT 1 FROM forms f JOIN members m ON m.id=f.member_id
                        WHERE m.system_id=? AND f.prefix=? AND f.suffix=?""",
                        (member.system_id, prefix, suffix)).fetchone()
                    member_collision = db.execute("""SELECT 1 FROM members
                        WHERE system_id=? AND prefix=? AND suffix=?""",
                        (member.system_id, prefix, suffix)).fetchone()
                    if collision or member_collision:
                        raise ValueError("That proxy prefix and suffix are already in use.")
                while True:
                    form_id = new_profile_id(db)
                    try:
                        db.execute("""INSERT INTO forms
                            (id,member_id,display_name,avatar,soma,pronouns,prefix,suffix,banner)
                            VALUES (?,?,?,?,?,?,?,?,?)""",
                            (form_id, member.id, display_name, avatar, soma, pronouns, prefix, suffix, banner))
                        if prefix:
                            db.execute("""INSERT INTO proxy_tags(system_id,form_id,prefix,suffix)
                                VALUES (?,?,?,?)""", (member.system_id, form_id, prefix, suffix))
                        break
                    except sqlite3.IntegrityError as error:
                        if "forms.id" not in str(error):
                            raise
        except sqlite3.IntegrityError as error:
            raise ValueError("That member already has a form with this display name.") from error
        return self.form_selected(account_id, form_id)[0]  # type: ignore[index]

    def form_selected(self, account_id: str, selector: str) -> tuple[Form, Member] | None:
        """Resolve an owned form by stable ID or its exact display name."""
        with self.connect() as db:
            rows = db.execute("""SELECT f.id form_id, f.member_id, f.display_name, f.avatar form_avatar,
                f.soma, f.pronouns form_pronouns, f.prefix form_prefix, f.suffix form_suffix,
                f.banner form_banner,
                m.* FROM forms f JOIN members m ON m.id=f.member_id
                JOIN owners o ON o.system_id=m.system_id
                WHERE o.account_id=? AND (f.id=? OR f.display_name=?)
                ORDER BY CASE WHEN f.id=? THEN 0 ELSE 1 END""",
                (account_id, selector.strip(), selector.strip(), selector.strip())).fetchall()
        if not rows:
            return None
        if len(rows) > 1 and rows[0]["form_id"] != selector.strip():
            raise ValueError("More than one form has that name; use the form ID instead.")
        row = rows[0]
        values = dict(row)
        form = Form(values.pop("form_id"), values["member_id"], values.pop("display_name"),
                    values.pop("form_avatar"), values.pop("soma"), values.pop("form_pronouns"),
                    values.pop("form_prefix"), values.pop("form_suffix"), values.pop("form_banner"))
        values.pop("member_id")
        return form, Member(**values)

    def proxy_identity(self, account_id: str, selector: str) -> Member | None:
        """Resolve a selector to the presentation that should appear on a proxy.

        An explicit form always wins.  Selecting a member uses that member's
        configured default form, when present, rather than silently falling
        back to the base avatar and name.
        """
        selected_form = self.form_selected(account_id, selector)
        if selected_form:
            form, member = selected_form
            return replace(member, name=form.display_name,
                           avatar=form.avatar if form.avatar is not None else member.avatar,
                           pronouns=form.pronouns if form.pronouns is not None else member.pronouns)
        member = self.member_selected(account_id, selector)
        if member and member.default_form_id:
            return self.proxy_identity(account_id, member.default_form_id)
        return member

    def switch_front(self, account_id: str, selector: str) -> Front:
        """Persist a front, using a member's default unless a form was explicit."""
        selected_form = self.form_selected(account_id, selector)
        if selected_form:
            form, member = selected_form
        else:
            member = self.member_selected(account_id, selector)
            form = (self.form_selected(account_id, member.default_form_id)[0]
                    if member and member.default_form_id else None)
        if member is None:
            raise PermissionError("Member or form not found or not owned by this account.")
        with self.connect() as db:
            db.execute("""INSERT INTO current_fronts(system_id,member_id,form_id) VALUES (?,?,?)
                ON CONFLICT(system_id) DO UPDATE SET member_id=excluded.member_id, form_id=excluded.form_id""",
                (member.system_id, member.id, form.id if form else None))
            # Fronting and autoproxy remain independent unless the system has
            # explicitly opted into autofront.
            db.execute("""UPDATE autoproxy_settings SET member_id=?
                WHERE system_id=? AND autofront=1""", (member.id, member.system_id))
        return Front(member, form)

    def configure_default_form(self, account_id: str, member_selector: str,
                               form_selector: str | None) -> Member:
        """Choose the form used when a member, rather than a form, is selected."""
        member = self.member_selected(account_id, member_selector)
        if member is None:
            raise PermissionError("Member not found or not owned by this account.")
        form = None
        if form_selector is not None:
            selected = self.form_selected(account_id, form_selector)
            if selected is None:
                raise PermissionError("Form not found or not owned by this account.")
            form = selected[0]
            if form.member_id != member.id:
                raise ValueError("The default form must belong to that member.")
        with self.connect() as db:
            db.execute("UPDATE members SET default_form_id=? WHERE id=?",
                       (form.id if form else None, member.id))
        return self.member_selected(account_id, member.id)  # type: ignore[return-value]

    def current_front(self, account_id: str) -> Front | None:
        system_id = self.system_for(account_id)
        if not system_id:
            return None
        with self.connect() as db:
            row = db.execute("SELECT member_id,form_id FROM current_fronts WHERE system_id=?", (system_id,)).fetchone()
        if not row:
            return None
        member = self.member_selected(account_id, str(row["member_id"]))
        form = self.form_selected(account_id, str(row["form_id"]))[0] if row["form_id"] else None
        return Front(member, form) if member else None

    def configure_autoproxy(self, account_id: str, selector: str | None) -> Autoproxy:
        """Select an autoproxy member, or disable autoproxy without changing the front."""
        system_id = self.system_for(account_id)
        if not system_id:
            raise PermissionError("Create a system first.")
        member = None if selector is None else self.member_selected(account_id, selector)
        if selector is not None and member is None:
            raise PermissionError("Member not found or not owned by this account.")
        with self.connect() as db:
            db.execute("""INSERT INTO autoproxy_settings(system_id,member_id) VALUES (?,?)
                ON CONFLICT(system_id) DO UPDATE SET member_id=excluded.member_id""",
                (system_id, member.id if member else None))
        return self.autoproxy(account_id)

    def configure_autofront(self, account_id: str, enabled: bool) -> Autoproxy:
        """Optionally make autoproxy follow the first (currently selected) fronter."""
        system_id = self.system_for(account_id)
        if not system_id:
            raise PermissionError("Create a system first.")
        front = self.current_front(account_id) if enabled else None
        with self.connect() as db:
            db.execute("""INSERT INTO autoproxy_settings(system_id,member_id,autofront) VALUES (?,?,?)
                ON CONFLICT(system_id) DO UPDATE SET
                    member_id=CASE WHEN excluded.autofront=1 AND excluded.member_id IS NOT NULL
                        THEN excluded.member_id ELSE autoproxy_settings.member_id END,
                    autofront=excluded.autofront""",
                (system_id, front.member.id if front else None, int(enabled)))
        return self.autoproxy(account_id)

    def autoproxy(self, account_id: str) -> Autoproxy:
        """Return settings; absent rows mean both features are off by default."""
        system_id = self.system_for(account_id)
        if not system_id:
            return Autoproxy(None, False)
        with self.connect() as db:
            row = db.execute("SELECT member_id,autofront FROM autoproxy_settings WHERE system_id=?",
                             (system_id,)).fetchone()
        if not row:
            return Autoproxy(None, False)
        member = self.member_selected(account_id, str(row["member_id"])) if row["member_id"] else None
        return Autoproxy(member, bool(row["autofront"]))

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

    @staticmethod
    def _normalize_pronouns(pronouns: str | None) -> str | None:
        normalized = pronouns.strip() if pronouns else None
        if normalized and len(normalized) > 64:
            raise ValueError("Pronouns must be no more than 64 characters.")
        return normalized

    def configure_pronouns(self, account_id: str, member_selector: str,
                           pronouns: str | None) -> Member:
        """Set or clear the pronouns shown for an owned member."""
        member = self.member_selected(account_id, member_selector)
        if member is None:
            raise PermissionError("Member not found or not owned by this account.")
        normalized = self._normalize_pronouns(pronouns)
        with self.connect() as db:
            db.execute("UPDATE members SET pronouns=? WHERE id=?", (normalized, member.id))
        return self.member_selected(account_id, member.id)  # type: ignore[return-value]

    def configure_form_pronouns(self, account_id: str, form_selector: str,
                                pronouns: str | None) -> Form:
        """Set or clear a form override; cleared forms inherit member pronouns."""
        selected = self.form_selected(account_id, form_selector)
        if selected is None:
            raise PermissionError("Form not found or not owned by this account.")
        form, _ = selected
        normalized = self._normalize_pronouns(pronouns)
        with self.connect() as db:
            db.execute("UPDATE forms SET pronouns=? WHERE id=?", (normalized, form.id))
        return self.form_selected(account_id, form.id)[0]  # type: ignore[index]

    @staticmethod
    def _validate_proxy_tag(prefix: str, suffix: str) -> None:
        if not prefix:
            if suffix:
                raise ValueError("A proxy suffix requires a proxy prefix.")
            return
        if len(prefix) > 32 or len(suffix) > 32:
            raise ValueError("Proxy prefixes and suffixes must each be no more than 32 characters.")

    def configure_form_proxy(self, account_id: str, form_selector: str,
                             prefix: str | None, suffix: str = "") -> Form:
        """Add a form proxy tag, or clear all of its tags when omitted."""
        selected = self.form_selected(account_id, form_selector)
        if selected is None:
            raise PermissionError("Form not found or not owned by this account.")
        form, member = selected
        normalized_prefix = prefix or ""
        self._validate_proxy_tag(normalized_prefix, suffix)
        with self.connect() as db:
            if normalized_prefix:
                count = db.execute("SELECT count(*) FROM proxy_tags WHERE form_id=?", (form.id,)).fetchone()[0]
                exists = db.execute("""SELECT 1 FROM proxy_tags
                    WHERE form_id=? AND prefix=? AND suffix=?""",
                    (form.id, normalized_prefix, suffix)).fetchone()
                if count >= MAX_PROXY_TAGS and not exists:
                    raise ValueError(f"A form can have at most {MAX_PROXY_TAGS} proxy tags.")
                collision = db.execute("""SELECT 1 FROM proxy_tags
                    WHERE system_id=? AND prefix=? AND suffix=?
                    AND (form_id IS NULL OR form_id<>?)""",
                    (member.system_id, normalized_prefix, suffix, form.id)).fetchone()
                if collision:
                    raise ValueError("That proxy prefix and suffix are already in use.")
                db.execute("""INSERT OR IGNORE INTO proxy_tags(system_id,form_id,prefix,suffix)
                    VALUES (?,?,?,?)""", (member.system_id, form.id, normalized_prefix, suffix))
                if not form.prefix:
                    db.execute("UPDATE forms SET prefix=?,suffix=? WHERE id=?",
                               (normalized_prefix, suffix, form.id))
            else:
                db.execute("DELETE FROM proxy_tags WHERE form_id=?", (form.id,))
                db.execute("UPDATE forms SET prefix='',suffix='' WHERE id=?", (form.id,))
        return self.form_selected(account_id, form.id)[0]  # type: ignore[index]

    def configure_member_proxy(self, account_id: str, member_selector: str,
                               prefix: str | None, suffix: str = "") -> Member:
        """Add a member proxy tag, or clear all tags when no prefix is supplied."""
        member = self.member_selected(account_id, member_selector)
        if member is None:
            raise PermissionError("Member not found or not owned by this account.")
        normalized_prefix = prefix or ""
        self._validate_proxy_tag(normalized_prefix, suffix)
        with self.connect() as db:
            if normalized_prefix:
                count = db.execute("SELECT count(*) FROM proxy_tags WHERE member_id=?", (member.id,)).fetchone()[0]
                exists = db.execute("""SELECT 1 FROM proxy_tags
                    WHERE member_id=? AND prefix=? AND suffix=?""",
                    (member.id, normalized_prefix, suffix)).fetchone()
                if count >= MAX_PROXY_TAGS and not exists:
                    raise ValueError(f"A member can have at most {MAX_PROXY_TAGS} proxy tags.")
                collision = db.execute("""SELECT 1 FROM proxy_tags
                    WHERE system_id=? AND prefix=? AND suffix=?
                    AND (member_id IS NULL OR member_id<>?)""",
                    (member.system_id, normalized_prefix, suffix, member.id)).fetchone()
                if collision:
                    raise ValueError("That proxy prefix and suffix are already in use.")
                db.execute("""INSERT OR IGNORE INTO proxy_tags(system_id,member_id,prefix,suffix)
                    VALUES (?,?,?,?)""", (member.system_id, member.id, normalized_prefix, suffix))
                if not member.prefix:
                    db.execute("UPDATE members SET prefix=?,suffix=? WHERE id=?",
                               (normalized_prefix, suffix, member.id))
            else:
                db.execute("DELETE FROM proxy_tags WHERE member_id=?", (member.id,))
                db.execute("UPDATE members SET prefix='',suffix='' WHERE id=?", (member.id,))
        return self.member_selected(account_id, member.id)  # type: ignore[return-value]

    def proxy_tags(self, member_id: str | None = None, form_id: str | None = None) -> list[ProxyTag]:
        """Return all tags for exactly one member or form in configured order."""
        if (member_id is None) == (form_id is None):
            raise ValueError("Specify exactly one member or form.")
        column, value = ("member_id", member_id) if member_id else ("form_id", form_id)
        with self.connect() as db:
            rows = db.execute(f"""SELECT prefix,suffix FROM proxy_tags WHERE {column}=?
                ORDER BY id""", (value,)).fetchall()
        return [ProxyTag(str(row["prefix"]), str(row["suffix"])) for row in rows]

    def remove_proxy_tag(self, account_id: str, selector: str, prefix: str,
                         suffix: str = "", *, form: bool = False) -> None:
        """Remove one owned tag while retaining every other configured tag."""
        selected = self.form_selected(account_id, selector) if form else self.member_selected(account_id, selector)
        if selected is None:
            raise PermissionError("Member or form not found or not owned by this account.")
        target = selected[0] if form else selected
        column = "form_id" if form else "member_id"
        with self.connect() as db:
            result = db.execute(f"DELETE FROM proxy_tags WHERE {column}=? AND prefix=? AND suffix=?",
                                (target.id, prefix, suffix))
            if not result.rowcount:
                raise ValueError("That proxy tag is not configured.")
            remaining = db.execute(f"""SELECT prefix,suffix FROM proxy_tags WHERE {column}=?
                ORDER BY id LIMIT 1""", (target.id,)).fetchone()
            table = "forms" if form else "members"
            db.execute(f"UPDATE {table} SET prefix=?,suffix=? WHERE id=?",
                       (remaining["prefix"], remaining["suffix"], target.id) if remaining else ("", "", target.id))

    def replace_proxy_tags(self, account_id: str, selector: str,
                           tags: Iterable[ProxyTag], *, form: bool = False) -> list[ProxyTag]:
        """Replace all tags for one owned identity in one transaction."""
        selected = self.form_selected(account_id, selector) if form else self.member_selected(account_id, selector)
        if selected is None:
            raise PermissionError("Member or form not found or not owned by this account.")
        target = selected[0] if form else selected
        member = selected[1] if form else selected
        values = [ProxyTag(tag.prefix.strip(), tag.suffix.strip()) for tag in tags]
        if len(values) > MAX_PROXY_TAGS:
            raise ValueError(f"An identity can have at most {MAX_PROXY_TAGS} proxy tags.")
        for tag in values:
            self._validate_proxy_tag(tag.prefix, tag.suffix)
            if not tag.prefix:
                raise ValueError("Proxy prefixes cannot be empty.")
        pairs = {(tag.prefix, tag.suffix) for tag in values}
        if len(pairs) != len(values):
            raise ValueError("Duplicate proxy tags are not allowed.")
        column = "form_id" if form else "member_id"
        table = "forms" if form else "members"
        with self.connect() as db:
            if values:
                placeholders = ",".join("(?,?)" for _ in values)
                parameters = [part for tag in values for part in (tag.prefix, tag.suffix)]
                collision = db.execute(f"""SELECT 1 FROM proxy_tags
                    WHERE system_id=? AND {column} IS NOT ? AND (prefix,suffix) IN ({placeholders})""",
                    (member.system_id, target.id, *parameters)).fetchone()
                if collision:
                    raise ValueError("A proxy prefix and suffix are already in use.")
            db.execute(f"DELETE FROM proxy_tags WHERE {column}=?", (target.id,))
            db.executemany(
                f"INSERT INTO proxy_tags(system_id,{column},prefix,suffix) VALUES (?,?,?,?)",
                [(member.system_id, target.id, tag.prefix, tag.suffix) for tag in values],
            )
            primary = values[0] if values else ProxyTag("")
            db.execute(f"UPDATE {table} SET prefix=?,suffix=? WHERE id=?",
                       (primary.prefix, primary.suffix, target.id))
        return self.proxy_tags(form_id=target.id) if form else self.proxy_tags(member_id=target.id)

    def configure_voice(self, account_id: str, member_selector: str, voice_reference: str | None,
                        voice_settings: str | dict[str, Any] = "{}", playback: str = "send") -> Member:
        """Configure an owned member's synthesis and playback destinations."""
        if playback not in {"off", "local", "send", "both"}:
            raise ValueError("Playback must be off, local, send, or both.")
        normalized = normalize_voice_settings(voice_settings)
        member = self.member_selected(account_id, member_selector)
        if member is None:
            raise PermissionError("Member not found or not owned by this account.")
        if playback != "off" and not voice_reference:
            raise ValueError("Voice playback requires a voice reference.")
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
            form_rows = db.execute("""SELECT f.id,t.prefix,t.suffix FROM proxy_tags t
                JOIN forms f ON f.id=t.form_id JOIN members m ON m.id=f.member_id
                JOIN owners o ON o.system_id=m.system_id WHERE o.account_id=?
                ORDER BY length(t.prefix)+length(t.suffix) DESC,t.id""", (account_id,)).fetchall()
            rows = db.execute("""SELECT m.*,t.prefix tag_prefix,t.suffix tag_suffix FROM proxy_tags t
                JOIN members m ON m.id=t.member_id JOIN owners o ON o.system_id=m.system_id
                WHERE o.account_id=? ORDER BY length(t.prefix)+length(t.suffix) DESC,t.id""",
                (account_id,)).fetchall()
        for form in form_rows:
            if content.startswith(form["prefix"]) and (not form["suffix"] or content.endswith(form["suffix"])):
                end = -len(form["suffix"]) if form["suffix"] else None
                body = content[len(form["prefix"]):end].strip()
                if body:
                    identity = self.proxy_identity(account_id, str(form["id"]))
                    if identity:
                        return identity, body
        for row in rows:
            values = dict(row)
            prefix, suffix = values.pop("tag_prefix"), values.pop("tag_suffix")
            member = Member(**values)
            if content.startswith(prefix) and (not suffix or content.endswith(suffix)):
                end = -len(suffix) if suffix else None
                body = content[len(prefix):end].strip()
                if body:
                    # Proxy tags select the stable member, but presentation is
                    # allowed to come from their configured default form.
                    return self.proxy_identity(account_id, member.id) or member, body
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

    def proxy_identity_for(self, proxy_id: str, account_id: str) -> Member | None:
        """Return the current identity for an owned, live proxy message."""
        with self.connect() as db:
            row = db.execute("""SELECT p.member_id FROM proxied_messages p
                JOIN owners o ON o.system_id=p.system_id
                WHERE p.proxy_message_id=? AND o.account_id=? AND p.deleted_at IS NULL""",
                (proxy_id, account_id)).fetchone()
        return self.proxy_identity(account_id, str(row["member_id"])) if row else None

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
