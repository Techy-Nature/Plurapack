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


def member():
    return SimpleNamespace(voice_settings="{}")


async def test_styled_synthesis_sends_semantic_parts_in_one_modal_request(modal_boundary):
    backend = ChatterboxBackend("https://modal.example/invoke?version=4", api_key="wk-test.ws-test")
    parts = (
        SpeechPart("ordinary words", "normal"),
        SpeechPart("important words", "emphasis"),
        SpeechPart("quiet words", "whisper"),
        SpeechPart("unclear words", "mumble"),
    )

    assert await backend.synthesize_styled(parts, member()) == wav_bytes()
    assert modal_boundary.calls == [(
        "POST",
        "/invoke?version=4",
        {
            "parts": [
                {"text": "ordinary words", "style": "normal"},
                {"text": "important words", "style": "emphasis"},
                {"text": "quiet words", "style": "whisper"},
                {"text": "unclear words", "style": "mumble"},
            ],
            "voice_id": "generic:Jordan",
        },
        {
            "Content-Type": "application/json",
            "Accept": "audio/wav",
            "Authorization": "Bearer wk-test.ws-test",
        },
    )]


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
