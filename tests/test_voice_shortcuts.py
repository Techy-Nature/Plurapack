import sqlite3

import pytest

from plurapack.proxy import EditRequest, Incoming, ProxyService
from plurapack.speech import SpeechQueue
from plurapack.storage import ProxyTag, Store
from plurapack.voice_service import VoiceService, resolve_modal_voice_id
from plurapack.voice_shortcuts import voice_shortcut_reply
from test_custom_voices import MemoryStorage, wav_bytes
from test_proxy import FakePlatform


@pytest.fixture
def shortcut_setup(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.create_system("owner", "Crew")
    member = store.add_member("owner", "Jaden", "j:")
    service = VoiceService(store, MemoryStorage())
    calm = service.upload_member_voice("owner", member.id, "Calm", wav_bytes(), make_default=True)
    loud = service.upload_member_voice("owner", member.id, "Loud", wav_bytes())
    store.configure_voice("owner", member.id, f"{calm.storage_id}.wav", "{}", "send", "custom")
    return store, service, member, calm, loud


def test_shortcut_adds_tag_and_speaks_with_its_voice(shortcut_setup):
    store, service, member, calm, loud = shortcut_setup
    reply = voice_shortcut_reply(store, service, "owner", "shortcut", ["Jaden", "Loud", "jl:"], "p;")
    assert "`jl:text`" in reply and "**Loud**" in reply
    assert store.proxy_tags(member_id=member.id) == [ProxyTag("j:"), ProxyTag("jl:")]

    shortcut, body, voice_id = store.match_proxy("owner", "jl: hello")
    assert (shortcut.id, body, voice_id) == (member.id, "hello", loud.id)
    assert resolve_modal_voice_id(store, shortcut) == f"custom:{loud.storage_id}"

    plain, _, plain_voice = store.match_proxy("owner", "j: hello")
    assert plain_voice is None
    assert resolve_modal_voice_id(store, plain) == f"custom:{calm.storage_id}"


def test_shortcut_can_be_cleared_and_listed(shortcut_setup):
    store, service, member, _, loud = shortcut_setup
    voice_shortcut_reply(store, service, "owner", "shortcut", ["Jaden", "Loud", "[", "]"], "p;")
    listing = voice_shortcut_reply(store, service, "owner", "shortcuts", ["Jaden"], "p;")
    assert "`j:text` → selected voice" in listing and "`[text]` → Loud" in listing

    voice_shortcut_reply(store, service, "owner", "shortcut", ["Jaden", "off", "[", "]"], "p;")
    assert store.match_proxy("owner", "[hi]")[2] is None
    assert ProxyTag("[", "]") in store.proxy_tags(member_id=member.id)
    with pytest.raises(ValueError):
        store.configure_tag_voice("owner", member.id, "nope:", "", None)


def test_shortcut_rejects_another_members_voice(shortcut_setup):
    store, service, _, _, loud = shortcut_setup
    other = store.add_member("owner", "Nova", "n:")
    with pytest.raises(ValueError):
        store.configure_tag_voice("owner", other.id, "n:", "", loud.id)
    with pytest.raises(ValueError):
        voice_shortcut_reply(store, service, "owner", "shortcut", ["Nova", "Loud", "n:"], "p;")


def test_form_tags_can_be_shortcuts(shortcut_setup):
    store, service, member, _, loud = shortcut_setup
    form = store.create_form("owner", member.id, "Little", prefix="lil:")
    voice_shortcut_reply(store, service, "owner", "shortcut", [form.id, "Loud", "lil:"], "p;")
    identity, body, voice_id = store.match_proxy("owner", "lil: hi")
    assert (identity.name, body, voice_id) == ("Little", "hi", loud.id)
    assert identity.voice_reference == f"{loud.storage_id}.wav"


def test_replacing_tags_keeps_surviving_shortcuts(shortcut_setup):
    store, _, member, _, loud = shortcut_setup
    store.configure_tag_voice("owner", member.id, "jl:", "", loud.id)
    store.replace_proxy_tags("owner", member.id, [ProxyTag("jl:"), ProxyTag("new:")])
    assert store.match_proxy("owner", "jl: hi")[2] == loud.id
    assert store.match_proxy("owner", "new: hi")[2] is None


def test_deleted_voice_falls_back_to_selected_voice(shortcut_setup):
    store, service, member, calm, loud = shortcut_setup
    store.configure_tag_voice("owner", member.id, "jl:", "", loud.id)
    service.delete_member_voice("owner", member.id, loud.id)
    identity, _, voice_id = store.match_proxy("owner", "jl: hi")
    assert voice_id is None
    assert identity.voice_reference == f"{calm.storage_id}.wav"


def test_older_databases_gain_the_voice_columns(tmp_path):
    path = tmp_path / "old.sqlite"
    Store(path)
    with sqlite3.connect(path) as db:
        for table in ("proxy_tags", "proxied_messages"):
            db.execute(f"ALTER TABLE {table} DROP COLUMN voice_id")
    Store(path)
    with sqlite3.connect(path) as db:
        for table in ("proxy_tags", "proxied_messages"):
            assert "voice_id" in {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


class RecordingBackend:
    def __init__(self):
        self.references = []

    async def synthesize(self, text, member):
        self.references.append(member.voice_reference)
        return b"wav"


async def test_proxy_and_its_edits_keep_the_shortcut_voice(shortcut_setup):
    store, _, member, _, loud = shortcut_setup
    store.configure_tag_voice("owner", member.id, "jl:", "", loud.id)
    backend = RecordingBackend()
    queue = SpeechQueue(backend, lambda job, audio: None)
    service = ProxyService(store, FakePlatform(), speech_queue=queue)

    assert await service.handle(Incoming("source", "channel", "owner", "jl: hello")) == "proxy-1"
    await queue.run_one()
    await service.edit_with_command(Incoming("edit", "channel", "owner", "p;edit hi"),
                                    EditRequest("hi"))
    await queue.run_one()
    assert backend.references == [f"{loud.storage_id}.wav"] * 2
