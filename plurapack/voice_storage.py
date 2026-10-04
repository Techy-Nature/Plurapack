"""Private, replaceable storage for member voice reference audio."""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote, urlsplit


class VoiceStorageError(RuntimeError):
    """A sanitized private-storage failure."""


@dataclass(frozen=True)
class StoredVoice:
    storage_id: str
    provider: str


class VoiceStorage(Protocol):
    def put_voice(self, voice_id: str, audio: bytes) -> StoredVoice: ...
    def get_voice(self, voice_id: str) -> bytes: ...
    def delete_voice(self, voice_id: str) -> None: ...
    def exists(self, voice_id: str) -> bool: ...


class ForgejoVoiceStorage:
    """Store UUID-named WAV files through Forgejo's concurrency-safe contents API."""

    provider = "forgejo"

    def __init__(self, base_url: str, owner: str, repo: str, branch: str,
                 username: str, token: str):
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username:
            raise ValueError("VOICE_FORGEJO_BASE_URL must be a plain HTTP(S) URL.")
        if not all((owner, repo, branch, token)):
            raise ValueError("Private voice storage configuration is incomplete.")
        self.base_url = base_url.rstrip("/")
        self.owner, self.repo, self.branch = owner, repo, branch
        self.username, self._token = username, token

    @classmethod
    def configured(cls) -> "ForgejoVoiceStorage":
        if os.getenv("VOICE_STORAGE_PROVIDER", "forgejo").casefold() != "forgejo":
            raise ValueError("Unsupported VOICE_STORAGE_PROVIDER.")
        return cls(os.getenv("VOICE_FORGEJO_BASE_URL", "https://git.gay"),
                   os.getenv("VOICE_FORGEJO_OWNER", ""),
                   os.getenv("VOICE_FORGEJO_REPO", "chatterbox-voices-host"),
                   os.getenv("VOICE_FORGEJO_BRANCH", "main"),
                   os.getenv("VOICE_FORGEJO_USERNAME", ""),
                   os.getenv("VOICE-STORAGE-API-KEY", ""))

    def _url(self, voice_id: str, *, reading: bool = False) -> str:
        path = quote(f"custom/{voice_id}.wav", safe="/")
        url = f"{self.base_url}/api/v1/repos/{quote(self.owner, safe='')}/{quote(self.repo, safe='')}/contents/{path}"
        return url + (f"?ref={quote(self.branch, safe='')}" if reading else "")

    def _request(self, method: str, voice_id: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self._url(voice_id, reading=method == "GET"), data=data, method=method,
            headers={"Authorization": f"token {self._token}", "Accept": "application/json",
                     "Content-Type": "application/json", "User-Agent": "Plurapack/voice-storage"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise VoiceStorageError("Private voice storage request failed.") from error

    def put_voice(self, voice_id: str, audio: bytes) -> StoredVoice:
        self._request("POST", voice_id, {"branch": self.branch,
            "message": f"voice: add {voice_id}", "content": base64.b64encode(audio).decode()})
        return StoredVoice(voice_id, self.provider)

    def get_voice(self, voice_id: str) -> bytes:
        result = self._request("GET", voice_id)
        try:
            return base64.b64decode(result["content"], validate=True)
        except (KeyError, ValueError) as error:
            raise VoiceStorageError("Private voice storage returned invalid content.") from error

    def exists(self, voice_id: str) -> bool:
        try:
            self._request("GET", voice_id)
            return True
        except VoiceStorageError:
            return False

    def delete_voice(self, voice_id: str) -> None:
        current = self._request("GET", voice_id)
        # Forgejo deletion removes the current tree entry, not prior Git history.
        self._request("DELETE", voice_id, {"branch": self.branch, "sha": current["sha"],
            "message": f"voice: delete {voice_id}"})
