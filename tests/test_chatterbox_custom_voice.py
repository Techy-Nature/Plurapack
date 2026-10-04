import json

import pytest

from plurapack.chatterbox import ChatterboxBackend
from plurapack.storage import Store
from plurapack.voice_service import VoiceService
from tests.test_custom_voices import MemoryStorage, wav_bytes


@pytest.mark.asyncio
async def test_custom_voice_sync_is_uuid_only_and_cached(tmp_path, monkeypatch):
    store = Store(tmp_path / "db.sqlite")
    store.create_system("owner", "Sensitive System")
    member = store.add_member("owner", "Sensitive Member", "m:")
    storage = MemoryStorage(); service = VoiceService(store, storage)
    voice = service.upload_member_voice("owner", member.id, "Sensitive Voice", wav_bytes())
    member = store.member_selected("owner", member.id)
    calls = []
    backend = ChatterboxBackend("http://tts.internal/tts", voice_service=service)

    def request(path, body, headers):
        calls.append((path, body, headers))
        if path == "/upload_reference":
            return 200, json.dumps({"uploaded_files": [f"{voice.id}.wav"]}).encode(), "application/json"
        payload = json.loads(body)
        assert payload["voice_mode"] == "clone"
        assert payload["reference_audio_filename"] == f"{voice.id}.wav"
        assert "predefined_voice_id" not in payload
        return 200, b"mp3", "audio/mpeg"

    monkeypatch.setattr(backend, "_raw_request", request)
    assert await backend.synthesize("one", member) == b"mp3"
    assert await backend.synthesize("two", member) == b"mp3"
    assert [path for path, _, _ in calls].count("/upload_reference") == 1
    private_text = b"".join(body for _, body, _ in calls)
    assert b"Sensitive Member" not in private_text
    assert b"Sensitive Voice" not in private_text

@pytest.mark.asyncio
async def test_stale_reference_cache_reuploads_once(tmp_path, monkeypatch):
    store = Store(tmp_path / "db.sqlite")
    store.create_system("owner", "System")
    member = store.add_member("owner", "Member", "m:")
    storage = MemoryStorage(); service = VoiceService(store, storage)
    voice = service.upload_member_voice("owner", member.id, "Normal", wav_bytes())
    member = store.member_selected("owner", member.id)
    backend = ChatterboxBackend("http://tts.internal/tts", voice_service=service)
    filename = f"{voice.id}.wav"
    backend._known_references.add(filename)
    calls = []

    def request(path, body, headers):
        calls.append(path)
        if path == "/tts" and calls.count("/tts") == 1:
            return 404, b"not found", "application/json"
        if path == "/upload_reference":
            return 200, json.dumps({"uploaded_files": [filename]}).encode(), "application/json"
        return 200, b"mp3", "audio/mpeg"

    monkeypatch.setattr(backend, "_raw_request", request)
    assert await backend.synthesize("hello", member) == b"mp3"
    assert calls == ["/tts", "/upload_reference", "/tts"]

@pytest.mark.asyncio
async def test_bad_request_does_not_reupload_reference(tmp_path, monkeypatch):
    store = Store(tmp_path / "db.sqlite")
    store.create_system("owner", "System")
    member = store.add_member("owner", "Member", "m:")
    storage = MemoryStorage(); service = VoiceService(store, storage)
    voice = service.upload_member_voice("owner", member.id, "Normal", wav_bytes())
    member = store.member_selected("owner", member.id)
    backend = ChatterboxBackend("http://tts.internal/tts", voice_service=service)
    backend._known_references.add(f"{voice.id}.wav")
    calls = []
    monkeypatch.setattr(backend, "_raw_request", lambda path, body, headers: (calls.append(path) or (400, b"bad request", "application/json")))
    from plurapack.chatterbox import ChatterboxError
    with pytest.raises(ChatterboxError, match="unsuccessful"):
        await backend.synthesize("hello", member)
    assert calls == ["/tts"]

@pytest.mark.asyncio
async def test_generic_voice_uses_predefined_payload_without_reference_upload(tmp_path, monkeypatch):
    store = Store(tmp_path / "db.sqlite")
    store.create_system("owner", "System")
    member = store.add_member("owner", "Member", "m:")
    storage = MemoryStorage(); service = VoiceService(store, storage)
    member = service.select_generic_voice("owner", member.id, "Abigail.wav")
    calls = []
    backend = ChatterboxBackend("http://tts.internal/tts", voice_service=service)

    def request(path, body, headers):
        calls.append((path, json.loads(body)))
        return 200, b"mp3", "audio/mpeg"

    monkeypatch.setattr(backend, "_raw_request", request)
    assert await backend.synthesize("hello", member) == b"mp3"
    assert [path for path, _ in calls] == ["/tts"]
    payload = calls[0][1]
    assert payload["voice_mode"] == "predefined"
    assert payload["predefined_voice_id"] == "Abigail.wav"
    assert "reference_audio_filename" not in payload


def test_voice_deletion_invalidates_process_local_chatterbox_cache(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.create_system("owner", "System")
    member = store.add_member("owner", "Member", "m:")
    storage = MemoryStorage(); service = VoiceService(store, storage)
    voice = service.upload_member_voice("owner", member.id, "Normal", wav_bytes())
    backend = ChatterboxBackend("http://tts.internal/tts", voice_service=service)
    filename = f"{voice.storage_id}.wav"
    backend._known_references.add(filename)

    service.delete_member_voice("owner", member.id, voice.id)

    assert filename not in backend._known_references

@pytest.mark.asyncio
async def test_legacy_clone_reference_keeps_clone_payload(tmp_path, monkeypatch):
    store = Store(tmp_path / "db.sqlite")
    store.create_system("owner", "System")
    member = store.add_member("owner", "Member", "m:")
    member = store.configure_voice("owner", member.id, "legacy-reference.wav", {}, "send")
    storage = MemoryStorage(); service = VoiceService(store, storage)
    calls = []
    backend = ChatterboxBackend("http://tts.internal/tts", voice_service=service)

    def request(path, body, headers):
        calls.append((path, json.loads(body)))
        return 200, b"mp3", "audio/mpeg"

    monkeypatch.setattr(backend, "_raw_request", request)
    assert await backend.synthesize("hello", member) == b"mp3"
    assert [path for path, _ in calls] == ["/tts"]
    payload = calls[0][1]
    assert payload["voice_mode"] == "clone"
    assert payload["reference_audio_filename"] == "legacy-reference.wav"
    assert "predefined_voice_id" not in payload
