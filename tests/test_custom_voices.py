import io
import uuid
import wave

import pytest

from plurapack.storage import Store
from plurapack.voice_service import VoiceService, VoiceValidationError
from plurapack.voice_storage import StoredVoice


def wav_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
        audio.writeframes(b"\0\0" * 100)
    return output.getvalue()


class MemoryStorage:
    provider = "memory"
    def __init__(self): self.values = {}
    def put_voice(self, voice_id, audio):
        self.values[f"custom/{voice_id}.wav"] = audio
        return StoredVoice(voice_id, self.provider)
    def get_voice(self, voice_id): return self.values[f"custom/{voice_id}.wav"]
    def delete_voice(self, voice_id): del self.values[f"custom/{voice_id}.wav"]
    def exists(self, voice_id): return f"custom/{voice_id}.wav" in self.values


@pytest.fixture
def voices(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.create_system("owner", "Private System")
    member = store.add_member("owner", "Private Member", "p:")
    storage = MemoryStorage()
    return store, member, storage, VoiceService(store, storage)


def test_upload_uses_uuid_only_and_maps_metadata(voices):
    store, member, storage, service = voices
    voice = service.upload_member_voice("owner", member.id, "Human name", wav_bytes())
    uuid.UUID(voice.id)
    assert list(storage.values) == [f"custom/{voice.id}.wav"]
    assert "Private" not in next(iter(storage.values))
    assert store.member_voices(member.id)[0].name == "Human name"
    assert voice.is_default


def test_rename_member_and_voice_do_not_rename_storage(voices):
    store, member, storage, service = voices
    voice = service.upload_member_voice("owner", member.id, "First", wav_bytes())
    before = set(storage.values)
    store.update_member("owner", member.id, name="Renamed")
    renamed = service.rename_member_voice("owner", member.id, voice.id, "Second")
    assert renamed.name == "Second" and set(storage.values) == before


def test_permissions_validation_and_delete(voices):
    store, member, storage, service = voices
    with pytest.raises(PermissionError):
        service.upload_member_voice("attacker", member.id, "No", wav_bytes())
    with pytest.raises(VoiceValidationError):
        service.upload_member_voice("owner", member.id, "Bad", b"not audio")
    voice = service.upload_member_voice("owner", member.id, "Good", wav_bytes())
    with pytest.raises(PermissionError):
        service.delete_member_voice("attacker", member.id, voice.id)
    service.delete_member_voice("owner", member.id, voice.id)
    assert not storage.values and store.member_voices(member.id) == []


def test_new_upload_gets_new_uuid_and_default_can_change(voices):
    _, member, _, service = voices
    first = service.upload_member_voice("owner", member.id, "First", wav_bytes())
    second = service.upload_member_voice("owner", member.id, "Second", wav_bytes())
    assert first.id != second.id and first.is_default and not second.is_default
    service.set_default_member_voice("owner", member.id, second.id)
    assert [v.name for v in service.list_member_voices("owner", member.id) if v.is_default] == ["Second"]

class FailingDeleteStorage(MemoryStorage):
    def delete_voice(self, voice_id):
        raise RuntimeError("remote secret response")


def test_member_and_system_deletion_clean_storage(voices):
    store, member, storage, service = voices
    first = service.upload_member_voice("owner", member.id, "Normal", wav_bytes())
    second = service.upload_member_voice("owner", member.id, "Sleepy", wav_bytes())
    assert first.storage_id != second.storage_id
    service.delete_member("owner", member.id)
    assert storage.values == {} and store.public_member_by_id(member.id) is None

    other = store.add_member("owner", "Other", "o:")
    service.upload_member_voice("owner", other.id, "Normal", wav_bytes())
    system_id = store.system_for("owner")
    service.delete_system("owner", system_id)
    assert storage.values == {} and store.system_for("owner") is None


def test_failed_remote_cleanup_preserves_member_and_metadata(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.create_system("owner", "System")
    member = store.add_member("owner", "Member", "m:")
    good = MemoryStorage(); service = VoiceService(store, good)
    voice = service.upload_member_voice("owner", member.id, "Normal", wav_bytes())
    failing = FailingDeleteStorage(); failing.values = good.values
    with pytest.raises(RuntimeError):
        VoiceService(store, failing).delete_member("owner", member.id)
    assert store.public_member_by_id(member.id) is not None
    assert store.member_voices(member.id)[0].id == voice.id
