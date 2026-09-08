"""HTTP adapter for devnen/Chatterbox-TTS-Server's JSON ``/tts`` API."""
from __future__ import annotations

import asyncio
import http.client
import json
from urllib.parse import urlsplit

from .storage import Member

PERMITTED_SETTINGS = {
    "temperature", "exaggeration", "cfg_weight", "seed", "speed_factor",
    "language", "split_text", "chunk_size",
}


class ChatterboxError(RuntimeError):
    """A deliberately input-free error raised for an invalid or failed request."""


class ChatterboxBackend:
    """Call the operator-hosted Chatterbox-TTS-Server ``/tts`` endpoint.

    ``voice_reference`` is the filename already installed in that server's
    ``reference_audio`` directory; reference bytes are never accepted in chat.
    """

    def __init__(self, url: str, *, connect_timeout: float = 5, response_timeout: float = 120,
                 max_response_bytes: int = 16 * 1024 * 1024):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("PLURAPACK_TTS_URL must be an HTTP(S) URL without embedded credentials.")
        self.url = parsed
        self.connect_timeout = connect_timeout
        self.response_timeout = response_timeout
        self.max_response_bytes = max_response_bytes

    async def synthesize(self, text: str, member: Member) -> bytes:
        if not member.voice_reference:
            raise ChatterboxError("Speech voice reference is not configured.")
        try:
            settings = json.loads(member.voice_settings)
        except (TypeError, json.JSONDecodeError) as error:
            raise ChatterboxError("Speech voice settings are invalid.") from error
        if not isinstance(settings, dict):
            raise ChatterboxError("Speech voice settings must be an object.")
        if set(settings) - PERMITTED_SETTINGS:
            raise ChatterboxError("Speech voice settings contain unsupported fields.")
        payload = {
            "text": text,
            "voice_mode": "clone",
            "reference_audio_filename": member.voice_reference,
            "output_format": "mp3",
            "stream": False,
            **settings,
        }
        return await asyncio.to_thread(self._request, json.dumps(payload).encode())

    def _request(self, body: bytes) -> bytes:
        connection_type = http.client.HTTPSConnection if self.url.scheme == "https" else http.client.HTTPConnection
        connection = connection_type(self.url.hostname, self.url.port, timeout=self.connect_timeout)
        path = self.url.path or "/tts"
        if self.url.query:
            path += "?" + self.url.query
        try:
            connection.request("POST", path, body, {"Content-Type": "application/json", "Accept": "audio/mpeg"})
            if connection.sock is not None:
                connection.sock.settimeout(self.response_timeout)
            response = connection.getresponse()
            if response.status < 200 or response.status >= 300:
                raise ChatterboxError("Speech service returned an unsuccessful status.")
            content_type = response.getheader("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type not in {"audio/mpeg", "audio/mp3"}:
                raise ChatterboxError("Speech service returned a non-MP3 response.")
            declared = response.getheader("Content-Length")
            if declared:
                try:
                    if int(declared) > self.max_response_bytes:
                        raise ChatterboxError("Speech service response is too large.")
                except ValueError as error:
                    raise ChatterboxError("Speech service returned invalid metadata.") from error
            data = response.read(self.max_response_bytes + 1)
            if len(data) > self.max_response_bytes:
                raise ChatterboxError("Speech service response is too large.")
            if not data:
                raise ChatterboxError("Speech service returned an empty response.")
            return data
        except (OSError, TimeoutError, http.client.HTTPException) as error:
            raise ChatterboxError("Speech service request failed.") from error
        finally:
            connection.close()
