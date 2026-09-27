"""Shared validation for stored Chatterbox voice configuration."""
from __future__ import annotations

import json
from typing import Any

PERMITTED_SETTINGS = {
    "temperature", "exaggeration", "cfg_weight", "seed", "speed_factor",
    "language", "split_text", "chunk_size",
}


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
