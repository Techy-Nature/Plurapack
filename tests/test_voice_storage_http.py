import io
import json
import urllib.error

import pytest

from plurapack.voice_storage import ForgejoVoiceStorage, VoiceStorageError


def storage():
    return ForgejoVoiceStorage("https://forge.example", "owner", "repo", "main", "secret")


class Response:
    def __init__(self, body=b'{}'): self.body = body
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return self.body


@pytest.mark.parametrize("code", [401, 403, 500])
def test_exists_raises_for_non_missing_http_errors(monkeypatch, code):
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("private", code, "error", {}, io.BytesIO(b"sensitive"))
    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(VoiceStorageError): storage().exists("00000000-0000-4000-8000-000000000000")


def test_exists_only_returns_false_for_404(monkeypatch):
    def fail(*args, **kwargs):
        raise urllib.error.HTTPError("private", 404, "missing", {}, io.BytesIO())
    monkeypatch.setattr("urllib.request.urlopen", fail)
    assert storage().exists("00000000-0000-4000-8000-000000000000") is False


def test_exists_true_and_network_failure(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: Response())
    assert storage().exists("00000000-0000-4000-8000-000000000000") is True
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("token detail")))
    with pytest.raises(VoiceStorageError, match="unavailable"):
        storage().exists("00000000-0000-4000-8000-000000000000")


@pytest.mark.parametrize("voice_id", ["../Jordan", "generic:Jordan", "custom/uuid", "Jordan", "uuid\\path"])
def test_private_storage_rejects_unsafe_ids_before_http(monkeypatch, voice_id):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: pytest.fail("Unsafe path reached HTTP"))
    with pytest.raises(VoiceStorageError, match="valid UUID"):
        storage().get_voice(voice_id)
