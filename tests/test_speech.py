import asyncio
import json

import pytest

from plurapack.chatterbox import ChatterboxBackend, ChatterboxError
from plurapack.proxy import Incoming, ProxyService
from plurapack.speech import SpeechJob, SpeechQueue, speech_parts, speech_worker
from plurapack.storage import Store
from test_proxy import FakePlatform


@pytest.fixture
def voice_store(tmp_path):
    store = Store(tmp_path / "voice.db")
    store.create_system("owner", "Crew")
    store.add_member("owner", "Alex", "[a]")
    return store


class Backend:
    def __init__(self, fail=False):
        self.fail = fail

    async def synthesize(self, text, member):
        if self.fail:
            raise TimeoutError
        return b"mp3"


async def test_off_does_not_enqueue_and_send_delivers_complete_identity(voice_store):
    delivered = []
    async def deliver(job, audio):
        delivered.append((job.channel_id, job.proxy_message_id, audio))
    member = voice_store.member_named("owner", "Alex")
    queue = SpeechQueue(Backend(), deliver)
    assert not queue.submit(SpeechJob("channel", "proxy", "hello", member))
    member = voice_store.configure_voice("owner", "Alex", "alex.wav", "{}", "send")
    assert queue.submit(SpeechJob("channel", "proxy", "hello", member))
    await queue.run_one()
    assert delivered == [("channel", "proxy", b"mp3")]


def test_semantic_speech_formatting_is_opt_in(voice_store):
    member = voice_store.member_named("owner", "Alex")
    source = 'I said "hello" *waves* **very clearly** and ~~never mind~~.'
    assert [part.text for part in speech_parts(source, member)] == [source]

    member = voice_store.configure_speech_formatting("owner", "Alex", True, "whisper")
    assert [(part.text, part.style) for part in speech_parts(source, member)] == [
        ('I said "hello"', "normal"),
        ("very clearly", "emphasis"),
        ("and", "normal"),
        ("never mind", "whisper"),
        (".", "normal"),
    ]


def test_store_rejects_settings_chatterbox_does_not_support(voice_store):
    with pytest.raises(ValueError, match="unsupported fields: arbitrary"):
        voice_store.configure_voice("owner", "Alex", "alex.wav", '{"arbitrary":true}', "send")


@pytest.mark.parametrize("mode,expected", [
    ("normal", [("keep this", "normal")]),
    ("mumble", [("keep this", "mumble")]),
    ("whisper", [("keep this", "whisper")]),
    ("omit", []),
])
def test_strikethrough_modes(voice_store, mode, expected):
    member = voice_store.configure_speech_formatting("owner", "Alex", True, mode)
    assert [(part.text, part.style) for part in speech_parts("~~keep this~~", member)] == expected


async def test_queue_uses_styled_backend_when_formatting_enabled(voice_store):
    member = voice_store.configure_voice("owner", "Alex", "alex.wav", "{}", "send")
    member = voice_store.configure_speech_formatting("owner", "Alex", True, "omit")

    class StyledBackend(Backend):
        def __init__(self): self.parts = None
        async def synthesize_styled(self, parts, member):
            self.parts = parts
            return b"styled-mp3"

    backend = StyledBackend()
    delivered = []
    async def deliver(job, audio): delivered.append(audio)
    queue = SpeechQueue(backend, deliver)
    queue.submit(SpeechJob("c", "p", "hello *waves* **there** ~~no~~", member))
    await queue.run_one()
    assert [(part.text, part.style) for part in backend.parts] == [
        ("hello", "normal"), ("there", "emphasis")
    ]
    assert delivered == [b"styled-mp3"]


async def test_enqueue_occurs_after_record_and_saturation_preserves_proxy(voice_store, caplog):
    member = voice_store.configure_voice("owner", "Alex", "alex.wav", "{}", "send")
    events = []

    class RecordingStore:
        def __getattr__(self, name):
            return getattr(voice_store, name)

        def record_proxy(self, *args):
            events.append("record")
            return voice_store.record_proxy(*args)

    queue = SpeechQueue(Backend(), lambda job, audio: None, limit=1)
    original_submit = queue.submit
    queue.submit = lambda job: (events.append("submit"), original_submit(job))[1]
    assert queue.submit(SpeechJob("c", "already", "x", member))
    service = ProxyService(RecordingStore(), FakePlatform(), speech_queue=queue)
    result = await service.handle(
        Incoming("source", "channel", "owner", "[a] hello"))
    assert result == "proxy-1"
    assert events[-2:] == ["record", "submit"]
    assert voice_store.proxy_owned_by("proxy-1", "owner")
    assert "proxy-1" not in service._proxy_text
    assert "Speech queue is full; audio was not queued" in caplog.text


async def test_edit_replaces_audio_with_new_text_and_current_voice(voice_store):
    voice_store.configure_voice("owner", "Alex", "alex.wav", "{}", "send")
    queue = SpeechQueue(Backend(), lambda job, audio: None)
    service = ProxyService(voice_store, FakePlatform(), speech_queue=queue)
    await service.handle(Incoming("source", "channel", "owner", "[a] original"))
    assert queue.queue.qsize() == 1
    await service.handle_reaction("channel", "proxy-1", "owner", "✏️")
    await service.handle(Incoming("edit", "channel", "owner", "edited"))
    assert queue.queue.qsize() == 2
    jobs = [queue.queue.get_nowait(), queue.queue.get_nowait()]
    assert [job.text for job in jobs] == ["original", "edited"]
    assert not queue.is_current(jobs[0])
    assert queue.is_current(jobs[1])


async def test_edit_of_non_voiced_proxy_does_not_enqueue(voice_store, caplog):
    queue = SpeechQueue(Backend(), lambda job, audio: None)
    service = ProxyService(voice_store, FakePlatform(), speech_queue=queue)
    await service.handle(Incoming("source", "channel", "owner", "[a] original"))
    await service.handle_reaction("channel", "proxy-1", "owner", "✏️")
    assert await service.handle(Incoming("edit", "channel", "owner", "edited")) == "proxy-1"
    assert queue.queue.empty()
    assert "proxy-1" not in service._proxy_text
    assert not caplog.records


@pytest.mark.parametrize("old_voice,new_voice,expected", [
    (True, True, "sam.wav"),
    (True, False, None),
    (False, True, "sam.wav"),
])
async def test_reproxy_uses_only_new_identity_voice(voice_store, old_voice, new_voice, expected):
    voice_store.add_member("owner", "Sam", "[s]")
    if old_voice:
        voice_store.configure_voice("owner", "Alex", "alex.wav", "{}", "send")
    if new_voice:
        voice_store.configure_voice("owner", "Sam", "sam.wav", "{}", "send")
    queue = SpeechQueue(Backend(), lambda job, audio: None)
    service = ProxyService(voice_store, FakePlatform(), speech_queue=queue)
    await service.handle(Incoming("source", "channel", "owner", "[a] original"))
    original = queue.queue.get_nowait() if old_voice else None

    assert await service.handle(Incoming(
        "reproxy", "channel", "owner", "Sam", reply_to_id="proxy-1"
    )) == "proxy-2"
    if original:
        assert not queue.is_current(original)
    if expected:
        replacement = queue.queue.get_nowait()
        assert (replacement.proxy_message_id, replacement.text,
                replacement.member.voice_reference) == ("proxy-2", "original", expected)
    else:
        assert queue.queue.empty()


async def test_rapid_edits_and_running_synthesis_discard_stale_audio(voice_store):
    voice_store.configure_voice("owner", "Alex", "alex.wav", "{}", "send")
    started, release = asyncio.Event(), asyncio.Event()

    class Slow(Backend):
        async def synthesize(self, text, member):
            if text == "original":
                started.set()
                await release.wait()
            return text.encode()

    delivered = []
    async def deliver(job, audio): delivered.append((job.text, audio))
    queue = SpeechQueue(Slow(), deliver)
    service = ProxyService(voice_store, FakePlatform(), speech_queue=queue)
    await service.handle(Incoming("source", "channel", "owner", "[a] original"))
    worker = asyncio.create_task(speech_worker(queue))
    await started.wait()
    for source, text in (("edit-1", "first"), ("edit-2", "latest")):
        await service.handle_reaction("channel", "proxy-1", "owner", "✏️")
        await service.handle(Incoming(source, "channel", "owner", text))
    release.set()
    await asyncio.wait_for(queue.queue.join(), 1)
    assert delivered == [("latest", b"latest")]
    worker.cancel()
    with pytest.raises(asyncio.CancelledError):
        await worker


async def test_voice_queue_failure_does_not_break_proxy_lifecycle(voice_store):
    voice_store.configure_voice("owner", "Alex", "alex.wav", "{}", "send")
    queue = SpeechQueue(Backend(), lambda job, audio: None)
    queue.submit = lambda job: (_ for _ in ()).throw(RuntimeError("offline"))
    service = ProxyService(voice_store, FakePlatform(), speech_queue=queue)
    assert await service.handle(Incoming("source", "channel", "owner", "[a] original")) == "proxy-1"
    await service.handle_reaction("channel", "proxy-1", "owner", "✏️")
    assert await service.handle(Incoming("edit", "channel", "owner", "edited")) == "proxy-1"


async def test_failures_do_not_kill_worker_and_delete_cancels_delivery(voice_store):
    member = voice_store.configure_voice("owner", "Alex", "alex.wav", "{}", "send")
    delivered = []

    class OnceFailing(Backend):
        async def synthesize(self, text, member):
            if text == "bad":
                raise TimeoutError
            return b"ok"

    async def deliver(job, audio):
        delivered.append(job.proxy_message_id)

    queue = SpeechQueue(OnceFailing(), deliver)
    task = asyncio.create_task(speech_worker(queue))
    queue.submit(SpeechJob("c", "bad-id", "bad", member))
    queue.submit(SpeechJob("c", "good-id", "good", member))
    await asyncio.wait_for(queue.queue.join(), 1)
    assert delivered == ["good-id"] and not task.done()

    started = asyncio.Event()
    release = asyncio.Event()

    class Slow(Backend):
        async def synthesize(self, text, member):
            started.set()
            await release.wait()
            return b"late"

    queue.backend = Slow()
    platform = FakePlatform()
    service = ProxyService(voice_store, platform, speech_queue=queue)
    await service.handle(Incoming("delete-source", "c", "owner", "[a] later"))
    await started.wait()
    await service.handle_reaction("c", "proxy-1", "owner", "❌")
    release.set()
    await asyncio.wait_for(queue.queue.join(), 1)
    assert "proxy-1" not in delivered
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_invalid_or_unauthorized_voice_configuration(voice_store):
    with pytest.raises(ValueError):
        voice_store.configure_voice("owner", "Alex", "a.wav", "[]", "send")
    with pytest.raises(ValueError, match="not implemented"):
        voice_store.configure_voice("owner", "Alex", "a.wav", "{}", "both")
    with pytest.raises(PermissionError):
        voice_store.configure_voice("stranger", "Alex", "a.wav", "{}", "send")
    with pytest.raises(ValueError, match="mumble"):
        voice_store.configure_speech_formatting("owner", "Alex", True, "shout")


async def test_backend_rejects_settings_non_audio_and_oversized(voice_store, monkeypatch):
    member = voice_store.configure_voice("owner", "Alex", "a.wav", "{}", "send")
    backend = ChatterboxBackend("http://localhost/tts", max_response_bytes=3)
    bad_member = member.__class__(**{**member.__dict__, "voice_settings": "[]"})
    with pytest.raises(ChatterboxError):
        await backend.synthesize("private", bad_member)

    class Response:
        status = 200
        def __init__(self, content_type, body): self.content_type, self.body = content_type, body
        def getheader(self, name, default=None): return self.content_type if name == "Content-Type" else default
        def read(self, amount): return self.body

    class Connection:
        sock = None
        response = None
        def __init__(self, *args, **kwargs): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return self.response
        def close(self): pass

    monkeypatch.setattr("plurapack.chatterbox.http.client.HTTPConnection", Connection)
    Connection.response = Response("application/json", b"err")
    with pytest.raises(ChatterboxError, match="non-MP3"):
        await backend.synthesize("private", member)
    Connection.response = Response("audio/mpeg", b"four")
    with pytest.raises(ChatterboxError, match="too large"):
        await backend.synthesize("private", member)
