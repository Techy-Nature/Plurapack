import dataclasses
import http.client
import json
import struct
import traceback

import pytest

from plurapack.chatterbox import ChatterboxBackend, ChatterboxError
from plurapack.storage import Store
from plurapack.voice_service import VoiceService, resolve_modal_voice_id
from tests.test_custom_voices import MemoryStorage, wav_bytes


@pytest.fixture
def voices(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.create_system("owner", "Sensitive System")
    member = store.add_member("owner", "Sensitive Member", "m:")
    storage = MemoryStorage()
    service = VoiceService(store, storage)
    return store, member, storage, service


@pytest.fixture
def http_boundary(monkeypatch):
    """Mock actual HTTP connection, preserving request/response adapter coverage."""
    class Response:
        status = 200
        content_type = "audio/wav"
        body = wav_bytes()
        reads = []

        def getheader(self, name, default=""):
            return self.content_type if name == "Content-Type" else default

        def read(self, amount):
            self.reads.append(amount)
            return self.body[:amount]

    class Connection:
        calls = []
        instances = []
        response = Response()
        failure = None

        def __init__(self, host, port, timeout):
            self.host, self.port, self.timeout = host, port, timeout
            self.response.reads = []
            self.response_timeout = None
            self.sock = self
            self.closed = False
            self.instances.append(self)

        def settimeout(self, timeout):
            self.response_timeout = timeout

        def request(self, method, path, body, headers):
            self.calls.append((method, path, json.loads(body), headers))
            if self.failure:
                raise self.failure

        def getresponse(self):
            return self.response

        def close(self):
            self.closed = True

    monkeypatch.setattr("plurapack.chatterbox.http.client.HTTPSConnection", Connection)
    return Connection


async def test_generic_mapping_json_auth_timeouts_and_unchanged_wav(voices, http_boundary, monkeypatch):
    _, member, _, service = voices
    member = service.select_generic_voice("owner", member.id, "Jordan.wav")
    monkeypatch.setenv("PLURAPACK_TTS_API_KEY", "wk-test.ws-test")
    backend = ChatterboxBackend("https://modal.example/invoke?version=3", voice_service=service)
    audio = http_boundary.response.body
    assert await backend.synthesize("Whatever the user actually said.", member) == audio
    assert http_boundary.calls == [("POST", "/invoke?version=3", {
        "text": "Whatever the user actually said.", "voice_id": "generic:Jordan",
    }, {"Content-Type": "application/json", "Accept": "audio/wav",
        "Authorization": "Bearer wk-test.ws-test"})]
    instance = http_boundary.instances[0]
    assert instance.timeout == 5 and instance.response_timeout == 120 and instance.closed
    assert http_boundary.response.reads == [backend.max_response_bytes + 1]


async def test_custom_named_jordan_uses_uuid_without_reading_or_sending_reference(voices, http_boundary, monkeypatch):
    store, member, storage, service = voices
    voice = service.upload_member_voice("owner", member.id, "Jordan", wav_bytes())
    member = store.member_selected("owner", member.id)
    monkeypatch.setattr(storage, "get_voice", lambda *_: pytest.fail("TTS must not load private WAVs"))
    monkeypatch.setattr(storage, "exists", lambda *_: pytest.fail("TTS must not contact Forgejo"))
    backend = ChatterboxBackend("https://modal.example/invoke", voice_service=service, api_key="wk-test.ws-test")
    for text in ("one", "two"):
        assert await backend.synthesize(text, member) == wav_bytes()
    assert [call[1] for call in http_boundary.calls] == ["/invoke", "/invoke"]
    assert [call[2] for call in http_boundary.calls] == [
        {"text": "one", "voice_id": f"custom:{voice.storage_id}"},
        {"text": "two", "voice_id": f"custom:{voice.storage_id}"},
    ]
    assert "Jordan" not in json.dumps(http_boundary.calls)
    assert "Sensitive" not in json.dumps(http_boundary.calls)
    assert not hasattr(backend, "ensure_reference")
    assert not hasattr(backend, "_known_references")


async def test_selection_uses_selected_uuid_rather_than_display_name_or_other_default(voices, http_boundary):
    store, member, _, service = voices
    first = service.upload_member_voice("owner", member.id, "Jordan", wav_bytes())
    second = service.upload_member_voice("owner", member.id, "Other", wav_bytes(), make_default=True)
    member = store.configure_voice("owner", member.id, f"{first.storage_id}.wav", {}, "send", "custom")
    service.rename_member_voice("owner", member.id, first.id, "Renamed")
    backend = ChatterboxBackend("https://modal.example", voice_service=service)
    await backend.synthesize("hello", member)
    assert http_boundary.calls[0][2]["voice_id"] == f"custom:{first.storage_id}"
    assert second.storage_id not in json.dumps(http_boundary.calls)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502, 503])
async def test_status_errors_are_sanitized_and_never_retry_or_read_body(voices, http_boundary, caplog, status):
    store, member, _, service = voices
    voice = service.upload_member_voice("owner", member.id, "Jordan", wav_bytes())
    member = store.member_selected("owner", member.id)
    token, text = "wk-private.ws-private", "private message"
    http_boundary.response.status = status
    http_boundary.response.body = f"{text} {voice.storage_id} {token}".encode()
    backend = ChatterboxBackend("https://modal.example", voice_service=service, api_key=token)
    with pytest.raises(ChatterboxError, match="unsuccessful") as caught:
        await backend.synthesize(text, member)
    assert len(http_boundary.calls) == 1 and http_boundary.response.reads == []
    output = str(caught.value) + "".join(traceback.format_exception(caught.value)) + caplog.text
    assert all(secret not in output for secret in (token, text, voice.storage_id))


@pytest.mark.parametrize("error_type", [OSError, TimeoutError, http.client.HTTPException, ValueError])
async def test_network_errors_suppress_sensitive_exception_chains(voices, http_boundary, caplog, error_type):
    _, member, _, service = voices
    member = service.select_generic_voice("owner", member.id, "Jordan.wav")
    secret = "wk-private.ws-private message custom:private-id"
    http_boundary.failure = error_type(secret)
    backend = ChatterboxBackend("https://modal.example", voice_service=service, api_key="wk-private.ws-private")
    with pytest.raises(ChatterboxError, match="request failed") as caught:
        await backend.synthesize("private message", member)
    assert secret not in "".join(traceback.format_exception(caught.value)) + caplog.text
    assert caught.value.__cause__ is None and caught.value.__suppress_context__
    assert http_boundary.instances[0].closed


@pytest.mark.parametrize("content_type", ["audio/mpeg", "audio/mp3", "application/json", "text/html", "application/octet-stream", ""])
async def test_non_wav_content_type_is_rejected(voices, http_boundary, content_type):
    _, member, _, service = voices
    member = service.select_generic_voice("owner", member.id, "Jordan.wav")
    http_boundary.response.content_type = content_type
    with pytest.raises(ChatterboxError, match="non-WAV"):
        await ChatterboxBackend("https://modal.example").synthesize("hello", member)


async def test_content_type_parameters_are_accepted(voices, http_boundary):
    _, member, _, service = voices
    member = service.select_generic_voice("owner", member.id, "Jordan.wav")
    http_boundary.response.content_type = "Audio/WAV; charset=binary"
    assert await ChatterboxBackend("https://modal.example").synthesize("hello", member) == wav_bytes()


async def test_response_limit_is_bounded_and_inclusive(voices, http_boundary):
    _, member, _, service = voices
    member = service.select_generic_voice("owner", member.id, "Jordan.wav")
    size = len(wav_bytes())
    assert await ChatterboxBackend("https://modal.example", max_response_bytes=size).synthesize("hello", member) == wav_bytes()
    with pytest.raises(ChatterboxError, match="too large"):
        await ChatterboxBackend("https://modal.example", max_response_bytes=size - 1).synthesize("hello", member)
    assert http_boundary.response.reads == [size]


@pytest.mark.parametrize("body", [b"", b"not WAV", wav_bytes()[:-1], wav_bytes() + wav_bytes(), b"RIFF\x04\x00\x00\x00WAVE"])
async def test_invalid_empty_truncated_or_concatenated_wavs_are_rejected(voices, http_boundary, body):
    _, member, _, service = voices
    member = service.select_generic_voice("owner", member.id, "Jordan.wav")
    http_boundary.response.body = body
    with pytest.raises(ChatterboxError):
        await ChatterboxBackend("https://modal.example").synthesize("hello", member)


async def test_valid_float_wav_is_supported(voices, http_boundary):
    _, member, _, service = voices
    member = service.select_generic_voice("owner", member.id, "Jordan.wav")
    body = bytearray(wav_bytes())
    struct.pack_into("<H", body, 20, 3)  # WAVE_FORMAT_IEEE_FLOAT
    struct.pack_into("<H", body, 32, 4)
    struct.pack_into("<H", body, 34, 32)
    struct.pack_into("<I", body, 28, 24000 * 4)
    http_boundary.response.body = bytes(body)
    assert await ChatterboxBackend("https://modal.example").synthesize("hello", member) == bytes(body)


@pytest.mark.parametrize("text", ["x" * 501, "😃" * 501, "", "   "])
async def test_invalid_or_overlong_text_fails_before_http_without_echoing_input(voices, http_boundary, text):
    _, member, _, service = voices
    member = service.select_generic_voice("owner", member.id, "Jordan.wav")
    with pytest.raises(ChatterboxError) as caught:
        await ChatterboxBackend("https://modal.example").synthesize(text, member)
    assert not http_boundary.calls
    if len(text) > 500:
        assert "500 characters" in str(caught.value) and text not in str(caught.value)


async def test_exactly_500_characters_is_not_truncated_and_settings_are_metadata(voices, http_boundary):
    store, member, _, service = voices
    member = service.select_generic_voice("owner", member.id, "Jordan.wav")
    member = store.configure_voice("owner", member.id, member.voice_reference,
                                   {"exaggeration": 0.7, "speed_factor": 0.9}, "send")
    text = "😃" * 500
    await ChatterboxBackend("https://modal.example").synthesize(text, member)
    assert http_boundary.calls[0][2] == {"text": text, "voice_id": "generic:Jordan"}
    assert json.loads(store.member_selected("owner", member.id).voice_settings)["exaggeration"] == 0.7


@pytest.mark.parametrize("source,reference", [
    ("generic", "../Jordan.wav"), ("generic", "generic:Jordan"),
    ("generic", "custom/Jordan.wav"), ("generic", "Jordan.wav/../../secret"),
    ("generic", "Jordan\\secret.wav"), ("generic", "Jordan.wav\n"),
    ("custom", "Jordan.wav"), ("custom", "generic:Jordan"),
    ("custom", "../00000000-0000-4000-8000-000000000000.wav"),
    ("custom", "custom/00000000-0000-4000-8000-000000000000.wav"),
    ("unknown", "Jordan.wav"), ("custom", None),
])
async def test_namespaces_and_paths_cannot_be_injected(voices, http_boundary, source, reference):
    _, member, _, service = voices
    member = dataclasses.replace(member, voice_source=source, voice_reference=reference)
    with pytest.raises(ChatterboxError):
        await ChatterboxBackend("https://modal.example", voice_service=service).synthesize("hello", member)
    assert not http_boundary.calls


async def test_custom_uuid_must_belong_to_selected_member(voices, http_boundary):
    store, member, _, service = voices
    other = store.add_member("owner", "Other", "o:")
    voice = service.upload_member_voice("owner", other.id, "Jordan", wav_bytes())
    member = dataclasses.replace(member, voice_reference=f"{voice.storage_id}.wav", voice_source="custom")
    with pytest.raises(ChatterboxError, match="metadata is unavailable"):
        await ChatterboxBackend("https://modal.example", voice_service=service).synthesize("hello", member)
    assert not http_boundary.calls


async def test_custom_requires_metadata_but_generic_does_not_require_write_credential(voices, http_boundary):
    store, member, _, service = voices
    voice = service.upload_member_voice("owner", member.id, "Jordan", wav_bytes())
    member = store.member_selected("owner", member.id)
    with pytest.raises(ChatterboxError, match="configure private voice storage"):
        await ChatterboxBackend("https://modal.example").synthesize("hello", member)
    assert voice.storage_id not in http_boundary.calls
    generic = service.select_generic_voice("owner", member.id, "Jordan.wav")
    await ChatterboxBackend("https://modal.example").synthesize("hello", generic)
    assert http_boundary.calls[0][2]["voice_id"] == "generic:Jordan"


async def test_legacy_named_clone_requires_explicit_configuration(voices, http_boundary):
    store, member, _, service = voices
    member = store.configure_voice("owner", member.id, "legacy-sensitive-name.wav", {}, "send")
    with pytest.raises(ChatterboxError, match="Legacy speech voice must be re-uploaded") as caught:
        await ChatterboxBackend("https://modal.example", voice_service=service).synthesize("hello", member)
    assert "legacy-sensitive-name" not in str(caught.value)
    assert not http_boundary.calls


def test_legacy_uuid_maps_only_with_existing_owned_metadata(voices):
    store, member, _, service = voices
    voice = service.upload_member_voice("owner", member.id, "Jordan", wav_bytes())
    member = dataclasses.replace(store.member_selected("owner", member.id), voice_source="legacy_clone")
    assert resolve_modal_voice_id(store, member) == f"custom:{voice.storage_id}"


async def test_deleted_custom_voice_is_not_sent(voices, http_boundary):
    store, member, storage, service = voices
    voice = service.upload_member_voice("owner", member.id, "Jordan", wav_bytes())
    selected = store.member_selected("owner", member.id)
    service.delete_member_voice("owner", member.id, voice.id)
    assert not storage.values
    with pytest.raises(ChatterboxError, match="metadata is unavailable"):
        await ChatterboxBackend("https://modal.example", voice_service=service).synthesize("hello", selected)
    assert not http_boundary.calls
