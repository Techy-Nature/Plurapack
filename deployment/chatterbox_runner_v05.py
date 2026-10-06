"""Modal-hosted Chatterbox endpoint for Plurapack.

v05 uses Chatterbox Nano for the normal low-cost path and Original Chatterbox
for semantic formatting or member settings that require CFG/exaggeration.
Both paths return one WAV and use the same private Forgejo voice references.
"""
from __future__ import annotations

import io
import os
import random
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
SUPPORTED_SETTINGS = frozenset({
    "temperature", "exaggeration", "cfg_weight", "seed", "speed_factor"
})
STYLE_PRESETS = {
    "normal": {},
    "emphasis": {"exaggeration": 0.85, "cfg_weight": 0.35},
    "mumble": {"exaggeration": 0.2, "cfg_weight": 0.2, "speed_factor": 1.12},
    "whisper": {"exaggeration": 0.05, "cfg_weight": 0.15, "speed_factor": 0.9},
}


class VoiceRepositoryError(RuntimeError):
    """Sanitized private-voice repository failure."""


class SpeechGenerationError(RuntimeError):
    """Sanitized model-generation failure."""


image = (
    modal.Image.debian_slim(python_version="3.10")
    .uv_pip_install(
        "chatterbox-tts==0.1.7",
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


def _numeric_setting(settings: dict, name: str, minimum: float, maximum: float) -> None:
    if name not in settings:
        return
    value = settings[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Speech settings are invalid.")
    if not minimum <= float(value) <= maximum:
        raise ValueError("Speech settings are invalid.")


def validate_settings(value: object) -> dict[str, float | int]:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - SUPPORTED_SETTINGS:
        raise ValueError("Speech settings are invalid.")
    settings = dict(value)
    _numeric_setting(settings, "temperature", 0.05, 5.0)
    _numeric_setting(settings, "exaggeration", 0.0, 2.0)
    _numeric_setting(settings, "cfg_weight", 0.0, 1.0)
    _numeric_setting(settings, "speed_factor", 0.25, 4.0)
    if "seed" in settings:
        seed = settings["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or not 0 <= seed <= 2_147_483_647:
            raise ValueError("Speech settings are invalid.")
    return settings


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


def apply_seed(seed: int) -> None:
    if seed == 0:
        return
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def apply_speed_factor(wav, factor: float):
    """Pitch-preserving time stretch, matching the historical server semantics."""
    if factor == 1.0:
        return wav
    import librosa
    import torch

    samples = wav.detach().cpu().squeeze(0).numpy()
    stretched = librosa.effects.time_stretch(samples, rate=float(factor))
    return torch.from_numpy(stretched.copy()).float().unsqueeze(0)


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

        # Nano is the default path: smallest model, inexpensive for ordinary speech.
        self.nano_model = ChatterboxTurboTTS.from_pretrained(device="cuda", nano=True)
        self.original_model = None

    def _load_original_model(self):
        if self.original_model is None:
            from chatterbox.tts import ChatterboxTTS

            self.original_model = ChatterboxTTS.from_pretrained(device="cuda")
        return self.original_model

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
                "User-Agent": "Plurapack-Chatterbox/0.5",
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

    def _generate_nano(self, prompt: str, voice_path: Path, settings: dict):
        apply_seed(int(settings.get("seed", 0)))
        wav = self.nano_model.generate(
            prompt,
            audio_prompt_path=str(voice_path),
            temperature=float(settings.get("temperature", 0.8)),
        )
        return apply_speed_factor(wav, float(settings.get("speed_factor", 1.0)))

    def _generate_original(self, prompt: str, voice_path: Path, settings: dict):
        model = self._load_original_model()
        apply_seed(int(settings.get("seed", 0)))
        wav = model.generate(
            prompt,
            audio_prompt_path=str(voice_path),
            exaggeration=float(settings.get("exaggeration", 0.5)),
            cfg_weight=float(settings.get("cfg_weight", 0.5)),
            temperature=float(settings.get("temperature", 0.8)),
        )
        return apply_speed_factor(wav, float(settings.get("speed_factor", 1.0)))

    @modal.method()
    def generate(self, prompt: str, voice_id: str, settings: dict | None = None) -> bytes:
        prompt = validate_text(prompt)
        settings = validate_settings(settings)
        voice_path = self.get_voice(voice_id)
        try:
            # Explicit CFG/exaggeration settings require Original; otherwise Nano.
            if "exaggeration" in settings or "cfg_weight" in settings:
                wav = self._generate_original(prompt, voice_path, settings)
                sample_rate = self._load_original_model().sr
            else:
                wav = self._generate_nano(prompt, voice_path, settings)
                sample_rate = self.nano_model.sr
            return self._wav_bytes(wav, sample_rate)
        except Exception:
            raise SpeechGenerationError("Speech generation failed.") from None

    @modal.method()
    def generate_parts(self, parts: list[dict[str, str]], voice_id: str,
                       settings: dict | None = None) -> bytes:
        import torch

        parts = validate_parts(parts)
        base_settings = validate_settings(settings)
        voice_path = self.get_voice(voice_id)
        try:
            model = self._load_original_model()
            rendered = []
            for part in parts:
                part_settings = {**base_settings, **STYLE_PRESETS[part["style"]]}
                rendered.append(self._generate_original(part["text"], voice_path, part_settings).detach().cpu())
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
            parse_voice_id(voice_id)
            settings = validate_settings(payload.get("settings", {}))
            has_text = "text" in payload
            has_parts = "parts" in payload
            if has_text == has_parts:
                raise ValueError("Provide either text or parts.")
            if has_parts:
                parts = validate_parts(payload.get("parts"))
                audio_bytes = self.generate_parts.local(parts, voice_id, settings)
            else:
                text = validate_text(payload.get("text"))
                audio_bytes = self.generate.local(text, voice_id, settings)
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
    output = Chatterbox().generate.remote(text, voice_id, {})
    Path("chatterbox-test.wav").write_bytes(output)
    print("Wrote chatterbox-test.wav")
