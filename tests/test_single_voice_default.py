"""A member with exactly one uploaded voice never has to pick it."""
import pytest

from plurapack.storage import ProxyTag, Store
from plurapack.voice_service import VoiceService, resolve_modal_voice_id, selected_voice_type
from plurapack.voice_shortcuts import voice_shortcut_reply
from test_custom_voices import MemoryStorage, wav_bytes


@pytest.fixture
def solo(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    store.create_system("owner", "Crew")
    member = store.add_member("owner", "Jaden", "j:")
    service = VoiceService(store, MemoryStorage())
    voice = service.upload_member_voice("owner", member.id, "Calm", wav_bytes())
    return store, service, member, voice


def test_first_upload_is_default_and_selected(solo):
    store, _, member, voice = solo
    member = store.member_selected("owner", member.id)
    assert voice.is_default
    assert selected_voice_type(store, member) == "custom"
    assert resolve_modal_voice_id(store, member) == f"custom:{voice.storage_id}"


def test_turning_on_playback_with_nothing_selected_uses_the_only_voice(solo):
    store, _, member, voice = solo
    store.configure_voice("owner", member.id, None, "{}", "off")
    configured = store.configure_voice("owner", member.id, None, "{}", "send")
    assert configured.voice_reference == f"{voice.storage_id}.wav"
    assert configured.voice_source == "custom" and configured.playback == "send"


def test_playback_still_needs_a_choice_with_several_voices(solo):
    store, service, member, _ = solo
    service.upload_member_voice("owner", member.id, "Loud", wav_bytes())
    store.configure_voice("owner", member.id, None, "{}", "off")
    with pytest.raises(ValueError):
        store.configure_voice("owner", member.id, None, "{}", "send")


def test_select_without_a_name_returns_the_only_voice(solo):
    _, service, member, voice = solo
    assert service.select_member_voice("owner", member.id).id == voice.id
    service.upload_member_voice("owner", member.id, "Loud", wav_bytes())
    with pytest.raises(ValueError):
        service.select_member_voice("owner", member.id)


def test_shortcut_without_voice_uses_the_only_voice(solo):
    store, service, member, voice = solo
    reply = voice_shortcut_reply(store, service, "owner", "shortcut", ["Jaden", "jc:"], "p;")
    assert "**Calm**" in reply
    assert store.match_proxy("owner", "jc: hi")[2] == voice.id
    voice_shortcut_reply(store, service, "owner", "shortcut", ["Jaden", "[", "]"], "p;")
    assert ProxyTag("[", "]") in store.proxy_tags(member_id=member.id)
    assert store.match_proxy("owner", "[hi]")[2] == voice.id
    # Naming the voice still works with three words.
    voice_shortcut_reply(store, service, "owner", "shortcut", ["Jaden", "Calm", "c:"], "p;")
    assert store.match_proxy("owner", "c: hi")[2] == voice.id


def test_shortcut_without_voice_is_refused_with_several_voices(solo):
    store, service, _, _ = solo
    service.upload_member_voice("owner", solo[2].id, "Loud", wav_bytes())
    with pytest.raises(ValueError):
        voice_shortcut_reply(store, service, "owner", "shortcut", ["Jaden", "jc:"], "p;")


def test_dashboard_offers_to_use_an_unselected_default_voice():
    source = open("app.js", encoding="utf-8").read()
    assert "Use this voice" in source
