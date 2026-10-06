"""Server-to-server adapter for the Modal Chatterbox endpoint."""
from __future__ import annotations

import asyncio
import http.client
import json
import os
import struct
from urllib.parse import urlsplit

from .storage import Member
from .voice import validate_voice_settings
from .voice_service import VoiceService, resolve_modal_voice_id

MODAL_TEXT_LIMIT = 500
SUPPORTED_STYLES = frozenset({"normal", "emphasis", "mumble", "whisper"})


class ChatterboxError(RuntimeError):
    """A deliberately input-free error raised for an invalid or failed request."""


class ChatterboxBackend:
    """Send spoken text or semantic speech parts plus a namespaced voice ID.

    Modal reads reference audio from private Forgejo independently. Plain speech
    keeps the v03 ``text`` + ``voice_id`` request contract. When semantic speech
    formatting is enabled, ``synthesize_styled`` uses the v04 ``parts`` contract
    so the runner can render styles before returning one valid WAV container.

    During a rolling v03 -> v04 deployment, a protocol-level 400/422 response to
    the new ``parts`` payload falls back once to the old plain-text contract. That
    keeps speech available without weakening authentication/network failures into
    silent retries; styles simply remain flattened until v04 is live.
    """

    def __init__(self, url: str, *, voice_service: VoiceService | None = None,
                 api_key: str | None = None, connect_timeout: float = 5,
                 response_timeout: float = 120, max_response_bytes: int = 16 * 1024 * 1024):
        try:
            parsed = urlsplit(url)
            if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.fragment):
                raise ValueError
            parsed.port  # Validate without exposing a malformed URL in errors.
        except ValueError:
            raise ValueError("PLURAPACK_TTS_URL must be an HTTP(S) URL without embedded credentials or fragments.") from None
        if max_response_bytes < 1:
            raise ValueError("Speech response size limit must be positive.")
        self.url = parsed
        self.connect_timeout = connect_timeout
        self.response_timeout = response_timeout
        self.max_response_bytes = max_response_bytes
        self.voice_service = voice_service
        self.api_key = api_key if api_key is not None else os.getenv("PLURAPACK_TTS_API_KEY")

    async def _voice_id(self, member: Member) -> str:
        try:
            validate_voice_settings(member.voice_settings)
        except (TypeError, ValueError):
            raise ChatterboxError("Speech voice settings are invalid.") from None
        try:
            return await asyncio.to_thread(
                resolve_modal_voice_id,
                self.voice_service.store if self.voice_service else None, member)
        except ValueError as error:
            # The resolver's errors are fixed, input-free configuration messages.
            raise ChatterboxError(str(error)) from None
        except Exception:
            raise ChatterboxError("Speech voice configuration could not be read.") from None

    @staticmethod
    def _validate_text(text: str) -> str:
        if not isinstance(text, str) or not text.strip():
            raise ChatterboxError("Speech text must be non-empty.")
        if len(text) > MODAL_TEXT_LIMIT:
            raise ChatterboxError("Speech text exceeds the Modal limit of 500 characters.")
        return text

    async def synthesize(self, text: str, member: Member) -> bytes:
        text = self._validate_text(text)
        voice_id = await self._voice_id(member)
        payload = {"text": text, "voice_id": voice_id}
        return await asyncio.to_thread(self._request, json.dumps(payload).encode())

    async def synthesize_styled(self, parts, member: Member) -> bytes:
        """Render semantic speech parts through Modal v04 as one WAV response."""
        payload_parts: list[dict[str, str]] = []
        fallback_words: list[str] = []
        total_characters = 0
        for part in parts:
            text = getattr(part, "text", None)
            style = getattr(part, "style", None)
            if not isinstance(text, str) or not text.strip():
                continue
            if style not in SUPPORTED_STYLES:
                raise ChatterboxError("Speech formatting contains an unsupported style.")
            total_characters += len(text)
            payload_parts.append({"text": text, "style": style})
            fallback_words.append(text)
        if not payload_parts:
            raise ChatterboxError("Speech text must be non-empty.")
        if total_characters > MODAL_TEXT_LIMIT:
            raise ChatterboxError("Speech text exceeds the Modal limit of 500 characters.")
        fallback_text = self._validate_text(" ".join(fallback_words))
        voice_id = await self._voice_id(member)
        payload = {"parts": payload_parts, "voice_id": voice_id}
        fallback = {"text": fallback_text, "voice_id": voice_id}
        return await asyncio.to_thread(
            self._request,
            json.dumps(payload).encode(),
            json.dumps(fallback).encode(),
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "audio/wav"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

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
            # Error bodies can contain private input. Never read or surface them.
            if not 200 <= response.status < 300:
                return response.status, b"", content_type
            return response.status, response.read(self.max_response_bytes + 1), content_type
        except (OSError, ValueError, http.client.HTTPException):
            # Suppress upstream exception chaining too: headers, URLs, and text
            # must not escape via a formatted traceback.
            raise ChatterboxError("Speech service request failed.") from None
        finally:
            connection.close()

    def _request(self, body: bytes, fallback_body: bytes | None = None) -> bytes:
        path = self.url.path or "/"
        if self.url.query:
            path += "?" + self.url.query
        headers = self._headers()
        status, data, content_type = self._raw_request(path, body, headers)
        if fallback_body is not None and status in {400, 422}:
            status, data, content_type = self._raw_request(path, fallback_body, headers)
        return self._validate_response(status, data, content_type)

    def _validate_response(self, status: int, data: bytes, content_type: str) -> bytes:
        if not 200 <= status < 300:
            raise ChatterboxError("Speech service returned an unsuccessful status.")
        if content_type != "audio/wav":
            raise ChatterboxError("Speech service returned a non-WAV response.")
        if len(data) > self.max_response_bytes:
            raise ChatterboxError("Speech service response is too large.")
        if not data:
            raise ChatterboxError("Speech service returned an empty response.")
        if not _is_wav(data):
            raise ChatterboxError("Speech service returned invalid WAV audio.")
        return data


def _is_wav(data: bytes) -> bool:
    """Check a complete RIFF/WAVE container, including PCM or float WAVs.

    Reject truncated chunks and appended containers. Do not require a particular
    sample encoding: Modal controls that, and browsers also support float WAVs.
    """
    if (len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE"
            or struct.unpack_from("<I", data, 4)[0] + 8 != len(data)):
        return False
    offset, has_format, has_audio = 12, False, False
    while offset + 8 <= len(data):
        kind = data[offset:offset + 4]
        size = struct.unpack_from("<I", data, offset + 4)[0]
        start = offset + 8
        end = start + size
        if end > len(data):
            return False
        if kind == b"fmt ":
            if size < 16:
                return False
            _, channels, rate, _, alignment, _ = struct.unpack_from("<HHIIHH", data, start)
            has_format = channels > 0 and rate > 0 and alignment > 0
        if kind == b"data":
            has_audio = size > 0
        offset = end + size % 2
    return offset == len(data) and has_format and has_audio
