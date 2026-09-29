"""Short-lived, account-scoped audio exchange between bot and dashboard processes."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BrowserAudioEvent:
    id: str
    proxy_message_id: str
    generation: int


class BrowserAudioStore:
    """A bounded secure spool shared by independently-run bot and web processes.

    Metadata contains only routing identifiers. Message text and audio never enter
    SQLite, and audio is consumed on retrieval or removed after its TTL.
    """

    def __init__(self, root: str | Path, ttl: int = 120, limit: int = 100,
                 clock=time.time):
        self.root = Path(root)
        self.ttl, self.limit, self.clock = ttl, limit, clock
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Apply the restrictive mode even when the configured directory already
        # existed. Failing closed is safer than publishing private clips into a
        # directory whose permissions could not be secured.
        self.root.chmod(0o700)

    @classmethod
    def configured(cls, database: str | Path) -> "BrowserAudioStore":
        configured = os.getenv("PLURAPACK_BROWSER_AUDIO_DIR")
        if configured:
            root = Path(configured)
        else:
            digest = hashlib.sha256(str(Path(database).resolve()).encode()).hexdigest()[:16]
            root = Path(tempfile.gettempdir()) / f"plurapack-audio-{digest}"
        return cls(root, int(os.getenv("PLURAPACK_BROWSER_AUDIO_TTL", "120")),
                   int(os.getenv("PLURAPACK_BROWSER_AUDIO_LIMIT", "100")))

    def _metadata_path(self, event_id: str) -> Path:
        return self.root / f"{event_id}.json"

    def _audio_path(self, event_id: str) -> Path:
        return self.root / f"{event_id}.audio"

    def _remove(self, event_id: str) -> None:
        for path in (self._metadata_path(event_id), self._audio_path(event_id)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _write_private(path: Path, content: bytes) -> None:
        """Create a new spool file with mode 0600 regardless of process umask."""
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)

    def cleanup(self) -> None:
        now = self.clock()
        records: list[tuple[float, str]] = []
        for path in self.root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                event_id = path.stem
                created = float(data["created"])
                if created + self.ttl <= now or not self._audio_path(event_id).is_file():
                    self._remove(event_id)
                else:
                    records.append((created, event_id))
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                self._remove(path.stem)
        # A process interruption between atomic renames can leave an audio or
        # temporary file without metadata. Keep recent files to avoid racing an
        # active publisher, but remove them after the same bounded lifetime.
        for pattern in ("*.audio", ".*.tmp"):
            for path in self.root.glob(pattern):
                try:
                    missing_metadata = (path.suffix == ".audio"
                                        and not self._metadata_path(path.stem).exists())
                    if (missing_metadata or path.name.endswith(".tmp")) and \
                            path.stat().st_mtime + self.ttl <= now:
                        path.unlink()
                except FileNotFoundError:
                    pass
        for _, event_id in sorted(records)[:-self.limit]:
            self._remove(event_id)

    def publish(self, account_id: str, proxy_message_id: str, generation: int,
                audio: bytes) -> BrowserAudioEvent:
        self.cleanup()
        event_id = secrets.token_urlsafe(24)
        metadata = {"account_id": account_id, "proxy_message_id": proxy_message_id,
                    "generation": generation, "created": self.clock()}
        audio_tmp = self.root / f".{event_id}.audio.tmp"
        metadata_tmp = self.root / f".{event_id}.json.tmp"
        self._write_private(audio_tmp, audio)
        self._write_private(
            metadata_tmp, json.dumps(metadata, separators=(",", ":")).encode("utf-8")
        )
        os.replace(audio_tmp, self._audio_path(event_id))
        os.replace(metadata_tmp, self._metadata_path(event_id))
        self.cleanup()
        return BrowserAudioEvent(event_id, proxy_message_id, generation)

    def events(self, account_id: str) -> list[BrowserAudioEvent]:
        self.cleanup()
        found: list[tuple[float, BrowserAudioEvent]] = []
        for path in self.root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data["account_id"] == account_id:
                    found.append((float(data["created"]), BrowserAudioEvent(
                        path.stem, str(data["proxy_message_id"]), int(data["generation"]))))
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
        return [event for _, event in sorted(found, key=lambda item: item[0])]

    def take(self, account_id: str, event_id: str) -> bytes | None:
        # IDs are opaque and validated before touching paths; ownership is also
        # checked from server-side metadata rather than trusted request input.
        if not event_id or any(character not in
                               "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
                               for character in event_id):
            return None
        self.cleanup()
        try:
            data = json.loads(self._metadata_path(event_id).read_text(encoding="utf-8"))
            if data["account_id"] != account_id:
                return None
            audio = self._audio_path(event_id).read_bytes()
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            return None
        self._remove(event_id)
        return audio

    def invalidate(self, proxy_message_id: str) -> None:
        for path in self.root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("proxy_message_id") == proxy_message_id:
                    self._remove(path.stem)
            except (OSError, json.JSONDecodeError):
                continue
