"""Shared validation for stored Chatterbox voice configuration."""
from __future__ import annotations

import json
import re
from typing import Any

# Preserve the historical database/API settings. Modal v05 actively uses
# temperature, exaggeration, cfg_weight, seed, and speed_factor. The legacy
# language/split_text/chunk_size fields remain accepted so existing records and
# older clients do not need a migration; the current English Nano/Original
# runner does not send those fields to Chatterbox.
PERMITTED_SETTINGS = {
    "temperature", "exaggeration", "cfg_weight", "seed", "speed_factor",
    "language", "split_text", "chunk_size",
}


def generic_voice_name(filename: str) -> str:
    """Validate an existing generic filename without accepting paths/namespaces."""
    if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.wav", filename):
        raise ValueError("Generic voice must be a safe WAV filename.")
    return filename[:-4]


def custom_voice_uuid(filename: str) -> str:
    """Accept only canonical UUID filenames used by private custom storage."""
    if not isinstance(filename, str) or not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.wav", filename
    ):
        raise ValueError("Custom voice configuration requires a valid storage UUID.")
    return filename[:-4]


def validate_voice_settings(value: str | dict[str, Any]) -> dict[str, Any]:
    """Return supported Chatterbox settings or raise a user-facing value error."""
    if isinstance(value, str):
        try:
            settings = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("Voice settings must be a JSON object.") from error
    else:
        settings = value
    if not isinstance(settings, dict):
        raise ValueError("Voice settings must be a JSON object.")
    unsupported = set(settings) - PERMITTED_SETTINGS
    if unsupported:
        raise ValueError(
            "Voice settings contain unsupported fields: " + ", ".join(sorted(unsupported)) + "."
        )
    return settings


def normalize_voice_settings(value: str | dict[str, Any]) -> str:
    """Validate and serialize settings in the canonical database format."""
    return json.dumps(validate_voice_settings(value), separators=(",", ":"), sort_keys=True)
