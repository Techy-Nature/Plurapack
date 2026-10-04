"""HTTP adapter for devnen/Chatterbox-TTS-Server's JSON ``/tts`` API."""
from __future__ import annotations

import asyncio
import http.client
import json
import os
import secrets
import threading
from urllib.parse import urlsplit

from .storage import Member
from .speech import SpeechPart
from .voice import validate_voice_settings
from .voice_service import VoiceService


class ChatterboxError(RuntimeError):
    """A deliberately input-free error raised for an invalid or failed request."""


class _MissingReference(ChatterboxError):
    pass


class ChatterboxBackend:
    """Call the operator-hosted Chatterbox-TTS-Server ``/tts`` endpoint.

    ``voice_reference`` is the filename already installed in that server's
    ``reference_audio`` directory; reference bytes are never accepted in chat.
    """

    def __init__(self, url: str, *, voice_service: VoiceService | None = None,
                 api_key: str | None = None, connect_timeout: float = 5,
                 response_timeout: float = 120, max_response_bytes: int = 16 * 1024 * 1024):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("PLURAPACK_TTS_URL must be an HTTP(S) URL without embedded credentials.")
        self.url = parsed
        self.connect_timeout = connect_timeout
        self.response_timeout = response_timeout
        self.max_response_bytes = max_response_bytes
        self.voice_service = voice_service
        self.api_key = api_key if api_key is not None else os.getenv("PLURAPACK_TTS_API_KEY")
        self._known_references: set[str] = set()
        self._reference_lock = threading.Lock()
        if self.voice_service is not None:
            self.voice_service.add_delete_listener(self.invalidate_reference)

    def invalidate_reference(self, storage_id: str) -> None:
        """Forget a deleted UUID locally; upstream currently cannot delete its copy."""
        self._known_references.discard(f"{storage_id}.wav")

    async def _prepare_reference(
        self, member: Member
    ) -> tuple[Member, str | None, bytes | None, bool]:
        resolved = (await asyncio.to_thread(self.voice_service.resolve_member_voice, member)
                    if self.voice_service else None)
        if resolved is None:
            # Databases migrated from the pre-UUID voice implementation retain
            # their installed Chatterbox reference as a clone without upload.
            return member, None, None, member.voice_source == "legacy_clone"
        filename, audio = resolved
        await asyncio.to_thread(self.ensure_reference, filename, audio)
        prepared = member.__class__(**{**member.__dict__, "voice_reference": filename})
        return prepared, filename, audio, True

    async def synthesize(self, text: str, member: Member) -> bytes:
        if not member.voice_reference:
            raise ChatterboxError("Speech voice reference is not configured.")
        try:
            settings = validate_voice_settings(member.voice_settings)
        except (TypeError, ValueError) as error:
            raise ChatterboxError("Speech voice settings are invalid.") from error
        member, filename, audio, custom = await self._prepare_reference(member)
        try:
            return await self._synthesize_with_settings(text, member, settings, custom=custom)
        except _MissingReference:
            if filename is None or audio is None:
                raise ChatterboxError("Speech voice reference is unavailable.")
            self._known_references.discard(filename)
            await asyncio.to_thread(self.ensure_reference, filename, audio)
            return await self._synthesize_with_settings(text, member, settings, custom=True)

    async def synthesize_styled(self, parts: tuple[SpeechPart, ...], member: Member) -> bytes:
        """Render spans separately so Chatterbox's controls can convey formatting."""
        try:
            base = validate_voice_settings(member.voice_settings)
        except (TypeError, ValueError) as error:
            raise ChatterboxError("Speech voice settings are invalid.") from error
        presets = {
            "normal": {},
            "emphasis": {"exaggeration": 0.85, "cfg_weight": 0.35},
            "mumble": {"exaggeration": 0.2, "cfg_weight": 0.2, "speed_factor": 1.12},
            "whisper": {"exaggeration": 0.05, "cfg_weight": 0.15, "speed_factor": 0.9},
        }
        member, filename, reference_audio, custom = await self._prepare_reference(member)
        audio = []
        for part in parts:
            settings = {**base, **presets[part.style]}
            try:
                audio.append(await self._synthesize_with_settings(
                    part.text, member, settings, custom=custom))
            except _MissingReference:
                if filename is None or reference_audio is None:
                    raise ChatterboxError("Speech voice reference is unavailable.")
                self._known_references.discard(filename)
                await asyncio.to_thread(self.ensure_reference, filename, reference_audio)
                audio.append(await self._synthesize_with_settings(
                    part.text, member, settings, custom=True))
        # MPEG audio frames are independently decodable, so sequential streams
        # remain one playable .mp3 attachment without requiring ffmpeg.
        return b"".join(audio)

    def _headers(self, content_type: str, accept: str) -> dict[str, str]:
        headers = {"Content-Type": content_type, "Accept": accept}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def ensure_reference(self, filename: str, audio: bytes) -> None:
        """Upload a private reference once per process to Chatterbox."""
        with self._reference_lock:
            if filename in self._known_references:
                return
            boundary = "----plurapack-" + secrets.token_hex(16)
            body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; "
                    f"filename=\"{filename}\"\r\nContent-Type: audio/wav\r\n\r\n").encode()
            body += audio + f"\r\n--{boundary}--\r\n".encode()
            status, response, _ = self._raw_request(
                "/upload_reference", body,
                self._headers(f"multipart/form-data; boundary={boundary}", "application/json"))
            if not 200 <= status < 300:
                raise ChatterboxError("The selected voice could not be synchronized with the speech server.")
            try:
                result = json.loads(response)
                if filename not in result.get("uploaded_files", []):
                    raise ValueError
            except (ValueError, TypeError):
                raise ChatterboxError("The selected voice could not be synchronized with the speech server.")
            self._known_references.add(filename)

    async def _synthesize_with_settings(self, text: str, member: Member, settings: dict,
                                        *, custom: bool = False) -> bytes:
        if not member.voice_reference:
            raise ChatterboxError("Speech voice reference is not configured.")
        try:
            settings = validate_voice_settings(settings)
        except (TypeError, ValueError) as error:
            raise ChatterboxError("Speech voice settings are invalid.") from error
        payload = {
            "text": text,
            "voice_mode": "clone" if custom else "predefined",
            "output_format": "mp3",
            "stream": False,
            **settings,
        }
        payload["reference_audio_filename" if custom else "predefined_voice_id"] = member.voice_reference
        return await asyncio.to_thread(self._request, json.dumps(payload).encode())

    def _raw_request(self, path: str, body: bytes,
                     headers: dict[str, str]) -> tuple[int, bytes, str]:
        connection_type = http.client.HTTPSConnection if self.url.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(self.url.hostname, self.url.port, timeout=self.connect_timeout)
        try:
            connection.request("POST", path, body, headers)
            if connection.sock is not None:
                connection.sock.settimeout(self.response_timeout)
            response = connection.getresponse()
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            return response.status, response.read(self.max_response_bytes + 1), content_type
        except (OSError, TimeoutError, http.client.HTTPException) as error:
            raise ChatterboxError("Speech service request failed.") from error
        finally:
            connection.close()

    def _request(self, body: bytes) -> bytes:
        path = self.url.path or "/tts"
        if self.url.query:
            path += "?" + self.url.query
        status, data, content_type = self._raw_request(
            path, body, self._headers("application/json", "audio/mpeg"))
        if status == 404:
            raise _MissingReference("Speech voice reference is unavailable.")
        if status < 200 or status >= 300:
            raise ChatterboxError("Speech service returned an unsuccessful status.")
        # The low-level helper intentionally does not expose response bodies on errors.
        # Successful /tts responses are required to contain MP3 bytes.
        try:
            if content_type not in {"audio/mpeg", "audio/mp3"}:
                raise ChatterboxError("Speech service returned a non-MP3 response.")
            if len(data) > self.max_response_bytes:
                raise ChatterboxError("Speech service response is too large.")
            if not data:
                raise ChatterboxError("Speech service returned an empty response.")
            return data
        except (OSError, TimeoutError, http.client.HTTPException) as error:
            raise ChatterboxError("Speech service request failed.") from error
