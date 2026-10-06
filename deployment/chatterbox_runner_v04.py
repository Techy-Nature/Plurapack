"""Modal-hosted Chatterbox endpoint for Plurapack.

v04 keeps the v03 ``text`` + ``voice_id`` request compatible while adding a
``parts`` request for semantic speech formatting. Plain speech uses Chatterbox
Turbo. Formatted speech uses the controllable original Chatterbox model so
emphasis/mumble/whisper are not silently flattened by Turbo.
"""
from __future__ import annotations

import io
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import modal

VOICE_REPOSITORY_BASE = (
    "https://git.gay/api/v1/repos/TechyNestBots/plurapack-voice-index/raw"
)
VOICE_REPOSITORY_BRANCH = "main"
VOICE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
VOICE_CACHE_ROOT = Path("/tmp/plurapack-voices")
MAX_VOICE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARACTERS = 500
MAX_PARTS = 64
SUPPORTED_STYLES = frozenset({"normal", "emphasis", "mumble", "whisper"})
STYLE_PRESETS = {
    "normal": {"exaggeration": 0.5, "cfg_weight": 0.5},
    "emphasis": {"exaggeration": 0.85, "cfg_weight": 0.35},
    "mumble": {"exaggeration": 0.2, "cfg_weight": 0.2},
    "whisper": {"exaggeration": 0.05, "cfg_weight": 0.15},
}


class VoiceRepositoryError(RuntimeError):
    """Sanitized private-voice repository failure."""


class SpeechGenerationError(RuntimeError):
    """Sanitized model-generation failure."""


image = (
    modal.Image.debian_slim(python_version="3.10")
    .uv_pip_install(
        "chatterbox-tts==0.1.6",
        "fastapi[standard]==0.124.4",
        "peft==0.18.0",
    )
    .env({"HF_HOME": "/cache/huggingface", "HF_XET_HIGH_PERFORMANCE": "1"})
)

app = modal.App("plurapack-chatterbox", image=image)
hf_cache = modal.Volume.from_name("plurapack-chatterbox-hf-cache", create_if_missing=True)


def parse_voice_id(voice_id: str) -> tuple[str, str]:
    if not isinstance(voice_id, str) or voice_id.count(":") != 1:
        raise ValueError("A valid voice ID is required.")
    namespace, name = voice_id.split(":", 1)
    if namespace not in {"generic", "custom"} or not VOICE_NAME_PATTERN.fullmatch(name):
        raise ValueError("A valid voice ID is required.")
    return namespace, name


def validate_text(text: object) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Speech text must be non-empty.")
    if len(text) > MAX_TEXT_CHARACTERS:
        raise ValueError("Speech text exceeds the 500-character limit.")
    return text


def validate_parts(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value or len(value) > MAX_PARTS:
        raise ValueError("Speech parts are invalid.")
    parts: list[dict[str, str]] = []
    total = 0
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Speech parts are invalid.")
        text = item.get("text")
        style = item.get("style")
        if not isinstance(text, str) or not text.strip() or style not in SUPPORTED_STYLES:
            raise ValueError("Speech parts are invalid.")
        total += len(text)
        parts.append({"text": text, "style": style})
    if total > MAX_TEXT_CHARACTERS:
        raise ValueError("Speech text exceeds the 500-character limit.")
    return parts


@app.cls(
    gpu="a10g",
    max_containers=1,
    scaledown_window=30,
    secrets=[
        modal.Secret.from_name("hf-token"),
        modal.Secret.from_name("gitgay-voice-reader"),
    ],
    volumes={"/cache/huggingface": hf_cache},
)
class Chatterbox:
    @modal.enter()
    def load(self) -> None:
        from chatterbox.tts_turbo import ChatterboxTurboTTS

        self.model = ChatterboxTurboTTS.from_pretrained(device="cuda")
        self.style_model = None

    def _load_style_model(self):
        if self.style_model is None:
            from chatterbox.tts import ChatterboxTTS

            self.style_model = ChatterboxTTS.from_pretrained(device="cuda")
        return self.style_model

    def get_voice(self, voice_id: str) -> Path:
        namespace, name = parse_voice_id(voice_id)
        destination = VOICE_CACHE_ROOT / namespace / f"{name}.wav"
        if destination.exists():
            return destination

        token = os.environ.get("GITGAY_TOKEN")
        if not token:
            raise PermissionError("Voice repository authentication is unavailable.")
        quoted_name = urllib.parse.quote(name, safe="")
        url = (
            f"{VOICE_REPOSITORY_BASE}/{namespace}/{quoted_name}.wav"
            f"?ref={urllib.parse.quote(VOICE_REPOSITORY_BRANCH, safe='')}"
        )
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"token {token}",
                "User-Agent": "Plurapack-Chatterbox/0.4",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                audio_data = response.read(MAX_VOICE_BYTES + 1)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise FileNotFoundError("Voice reference was not found.") from None
            if error.code in {401, 403}:
                raise PermissionError("Voice repository authentication failed.") from None
            raise VoiceRepositoryError("Voice repository request failed.") from None
        except (OSError, urllib.error.URLError):
            raise VoiceRepositoryError("Voice repository request failed.") from None

        if len(audio_data) > MAX_VOICE_BYTES:
            raise ValueError("Voice reference exceeds the size limit.")
        if not audio_data.startswith((b"RIFF", b"RF64")):
            raise ValueError("Voice reference is not a WAV file.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(audio_data)
        return destination

    @staticmethod
    def _wav_bytes(wav, sample_rate: int) -> bytes:
        import torchaudio as ta

        buffer = io.BytesIO()
        ta.save(buffer, wav.detach().cpu(), sample_rate, format="wav")
        buffer.seek(0)
        return buffer.read()

    @modal.method()
    def generate(self, prompt: str, voice_id: str) -> bytes:
        prompt = validate_text(prompt)
        voice_path = self.get_voice(voice_id)
        try:
            wav = self.model.generate(prompt, audio_prompt_path=str(voice_path))
            return self._wav_bytes(wav, self.model.sr)
        except Exception:
            raise SpeechGenerationError("Speech generation failed.") from None

    @modal.method()
    def generate_parts(self, parts: list[dict[str, str]], voice_id: str) -> bytes:
        import torch

        parts = validate_parts(parts)
        voice_path = self.get_voice(voice_id)
        model = self._load_style_model()
        try:
            rendered = []
            for part in parts:
                preset = STYLE_PRESETS[part["style"]]
                wav = model.generate(
                    part["text"],
                    audio_prompt_path=str(voice_path),
                    exaggeration=preset["exaggeration"],
                    cfg_weight=preset["cfg_weight"],
                )
                rendered.append(wav.detach().cpu())
            combined = torch.cat(rendered, dim=-1)
            return self._wav_bytes(combined, model.sr)
        except Exception:
            raise SpeechGenerationError("Speech generation failed.") from None

    @modal.fastapi_endpoint(method="POST", docs=True, requires_proxy_auth=True)
    def api(self, payload: dict):
        from fastapi import HTTPException
        from fastapi.responses import StreamingResponse

        try:
            if not isinstance(payload, dict):
                raise ValueError("A JSON object is required.")
            voice_id = payload.get("voice_id")
            # Validate before entering the remote method so malformed IDs are
            # rejected without touching private voice storage.
            parse_voice_id(voice_id)
            has_text = "text" in payload
            has_parts = "parts" in payload
            if has_text == has_parts:
                raise ValueError("Provide either text or parts.")
            if has_parts:
                parts = validate_parts(payload.get("parts"))
                audio_bytes = self.generate_parts.local(parts, voice_id)
            else:
                text = validate_text(payload.get("text"))
                audio_bytes = self.generate.local(text, voice_id)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Voice reference was not found.") from None
        except PermissionError:
            raise HTTPException(status_code=502, detail="Voice repository authentication failed.") from None
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        except VoiceRepositoryError:
            raise HTTPException(status_code=502, detail="Voice repository request failed.") from None
        except SpeechGenerationError:
            raise HTTPException(status_code=502, detail="Speech generation failed.") from None

        return StreamingResponse(io.BytesIO(audio_bytes), media_type="audio/wav")


@app.local_entrypoint()
def main(text: str = "Hello from Plurapack.", voice_id: str = "generic:Jordan") -> None:
    output = Chatterbox().generate.remote(text, voice_id)
    Path("chatterbox-test.wav").write_bytes(output)
    print("Wrote chatterbox-test.wav")
