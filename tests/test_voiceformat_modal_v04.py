import json
from types import SimpleNamespace

import pytest

from plurapack.chatterbox import ChatterboxBackend, ChatterboxError
from plurapack.speech import SpeechPart
from tests.test_custom_voices import wav_bytes


@pytest.fixture
def modal_boundary(monkeypatch):
    class Response:
        status = 200
        body = wav_bytes()

        def getheader(self, name, default=""):
            return "audio/wav" if name == "Content-Type" else default

        def read(self, amount):
            return self.body[:amount]

    class Connection:
        calls = []

        def __init__(self, host, port, timeout):
            self.sock = self

        def settimeout(self, timeout):
            pass

        def request(self, method, path, body, headers):
            self.calls.append((method, path, json.loads(body), headers))

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr("plurapack.chatterbox.http.client.HTTPSConnection", Connection)
    monkeypatch.setattr(
        "plurapack.chatterbox.resolve_modal_voice_id",
        lambda store, member: "generic:Jordan",
    )
    return Connection


def member(settings="{}"):
    return SimpleNamespace(voice_settings=settings)


async def test_styled_synthesis_sends_semantic_parts_and_active_settings_in_one_modal_request(modal_boundary):
    backend = ChatterboxBackend("https://modal.example/invoke?version=5", api_key="wk-test.ws-test")
    parts = (
        SpeechPart("ordinary words", "normal"),
        SpeechPart("important words", "emphasis"),
        SpeechPart("quiet words", "whisper"),
        SpeechPart("unclear words", "mumble"),
    )

    selected = member(json.dumps({
        "temperature": 0.7,
        "speed_factor": 1.05,
        "language": "en",
        "split_text": True,
        "chunk_size": 120,
    }))
    assert await backend.synthesize_styled(parts, selected) == wav_bytes()
    assert modal_boundary.calls == [(
        "POST",
        "/invoke?version=5",
        {
            "parts": [
                {"text": "ordinary words", "style": "normal"},
                {"text": "important words", "style": "emphasis"},
                {"text": "quiet words", "style": "whisper"},
                {"text": "unclear words", "style": "mumble"},
            ],
            "voice_id": "generic:Jordan",
            "settings": {"temperature": 0.7, "speed_factor": 1.05},
        },
        {
            "Content-Type": "application/json",
            "Accept": "audio/wav",
            "Authorization": "Bearer wk-test.ws-test",
        },
    )]


async def test_styled_synthesis_falls_back_once_to_pre_parts_plain_text(monkeypatch):
    backend = ChatterboxBackend("https://modal.example")
    monkeypatch.setattr(
        "plurapack.chatterbox.resolve_modal_voice_id",
        lambda store, selected: "generic:Jordan",
    )
    calls = []

    def raw_request(path, body, headers):
        payload = json.loads(body)
        calls.append(payload)
        if "parts" in payload:
            return 400, b"", "application/json"
        return 200, wav_bytes(), "audio/wav"

    monkeypatch.setattr(backend, "_raw_request", raw_request)
    parts = (
        SpeechPart("ordinary words", "normal"),
        SpeechPart("quiet words", "whisper"),
    )
    selected = member('{"temperature":0.6}')
    assert await backend.synthesize_styled(parts, selected) == wav_bytes()
    assert calls == [
        {
            "parts": [
                {"text": "ordinary words", "style": "normal"},
                {"text": "quiet words", "style": "whisper"},
            ],
            "voice_id": "generic:Jordan",
            "settings": {"temperature": 0.6},
        },
        {"text": "ordinary words quiet words", "voice_id": "generic:Jordan"},
    ]


async def test_styled_synthesis_does_not_fallback_on_auth_or_server_failure(monkeypatch):
    backend = ChatterboxBackend("https://modal.example")
    monkeypatch.setattr(
        "plurapack.chatterbox.resolve_modal_voice_id",
        lambda store, selected: "generic:Jordan",
    )
    calls = []

    def raw_request(path, body, headers):
        calls.append(json.loads(body))
        return 503, b"", "application/json"

    monkeypatch.setattr(backend, "_raw_request", raw_request)
    with pytest.raises(ChatterboxError, match="unsuccessful"):
        await backend.synthesize_styled((SpeechPart("private", "whisper"),), member())
    assert len(calls) == 1 and "parts" in calls[0]


async def test_styled_synthesis_rejects_unknown_style_before_http(modal_boundary):
    backend = ChatterboxBackend("https://modal.example")
    with pytest.raises(ChatterboxError, match="unsupported style"):
        await backend.synthesize_styled((SpeechPart("private", "shout"),), member())
    assert modal_boundary.calls == []


async def test_styled_synthesis_enforces_combined_modal_text_limit(modal_boundary):
    backend = ChatterboxBackend("https://modal.example")
    parts = (SpeechPart("a" * 250), SpeechPart("b" * 251, "whisper"))
    with pytest.raises(ChatterboxError, match="500 characters"):
        await backend.synthesize_styled(parts, member())
    assert modal_boundary.calls == []
