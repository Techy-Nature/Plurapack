from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, replace
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
    alias: str | None
    default_form_id: str | None
    description: str
    pronouns: str | None


@dataclass(frozen=True)
class System:
    id: str
    display_name: str
    description: str
    logo: str | None


@dataclass(frozen=True)
class Form:
    """An alternate presentation connected to one permanent member ID."""
    id: str
    member_id: str
    display_name: str
    avatar: str | None
    soma: str
    pronouns: str | None


@dataclass(frozen=True)
class Front:
    member: Member
    form: Form | None


@dataclass(frozen=True)
class Autoproxy:
    """A system's independent autoproxy selection and front-following preference."""
    member: Member | None
    autofront: bool


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
                    id TEXT PRIMARY KEY CHECK(length(id)=10), display_name TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '', logo TEXT
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
                CREATE TABLE IF NOT EXISTS forms (
                    id TEXT PRIMARY KEY CHECK(length(id)=5),
                    member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
                    display_name TEXT NOT NULL, avatar TEXT, soma TEXT NOT NULL DEFAULT '', pronouns TEXT,
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
            form_columns = {row[1] for row in db.execute("PRAGMA table_info(forms)")}
            if "pronouns" not in form_columns:
                db.execute("ALTER TABLE forms ADD COLUMN pronouns TEXT")
            system_columns = {row[1] for row in db.execute("PRAGMA table_info(systems)")}
            if "description" not in system_columns:
                db.execute("ALTER TABLE systems ADD COLUMN description TEXT NOT NULL DEFAULT ''")
            if "logo" not in system_columns:
                db.execute("ALTER TABLE systems ADD COLUMN logo TEXT")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS member_system_alias ON members(system_id, alias) WHERE alias IS NOT NULL")

    def create_system(self, account_id: str, name: str) -> str:
        with self.connect() as db:
            existing = db.execute("SELECT system_id FROM owners WHERE account_id=?", (account_id,)).fetchone()
            if existing:
                return str(existing[0])
            while True:
                system_id = short_hash(10)
                try:
                    db.execute("INSERT INTO systems(id,display_name) VALUES (?,?)", (system_id, name))
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

    def members_for_system(self, system_id: str) -> list[Member]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM members WHERE system_id=? ORDER BY name", (system_id,)).fetchall()
        return [Member(**dict(row)) for row in rows]

    def public_member_selected(self, selector: str, system_id: str | None = None) -> Member | None:
        """Resolve a card selector without granting any mutation permissions."""
        query = "SELECT * FROM members WHERE (id=? OR name=?)"
        parameters: list[str] = [selector.strip(), selector.strip()]
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
                            (id,system_id,name,prefix,suffix,avatar,color,pronouns) VALUES (?,?,?,?,?,?,?,?)""",
                            (short_hash(5), system_id, member.name, member.prefix, member.suffix,
                             member.avatar, member.color, member.pronouns))
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
            row = db.execute("""SELECT m.* FROM members m JOIN owners o ON o.system_id=m.system_id
                WHERE o.account_id=? AND (m.id=? OR m.name=? OR m.alias=? OR m.prefix=?)""",
                (account_id, selector, selector, selector, selector)).fetchone()
            return Member(**dict(row)) if row else None

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
                    avatar: str | None = None, soma: str = "", pronouns: str | None = None) -> Form:
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
        try:
            with self.connect() as db:
                while True:
                    form_id = short_hash(5)
                    if db.execute("SELECT 1 FROM members WHERE id=?", (form_id,)).fetchone():
                        continue
                    try:
                        db.execute("INSERT INTO forms(id,member_id,display_name,avatar,soma,pronouns) VALUES (?,?,?,?,?,?)",
                                   (form_id, member.id, display_name, avatar, soma, pronouns))
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
                f.soma, f.pronouns form_pronouns, m.* FROM forms f JOIN members m ON m.id=f.member_id
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
                    values.pop("form_avatar"), values.pop("soma"), values.pop("form_pronouns"))
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
