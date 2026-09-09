import pytest

from plurapack.bot import COMMAND_SHORTCUTS
from plurapack.storage import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "forms.sqlite3")
    value.create_system("owner", "Test system")
    value.add_member("owner", "Alexandra North", "[alex]", "")
    return value


def test_every_command_has_a_unique_one_or_two_letter_shortcut():
    assert all(1 <= len(shortcut) <= 2 for shortcut in COMMAND_SHORTCUTS.values())
    assert len(COMMAND_SHORTCUTS) == len(set(COMMAND_SHORTCUTS.values()))


def test_alias_selects_member_without_replacing_full_display_name(store):
    configured = store.configure_alias("owner", "Alexandra North", "Alex")

    assert configured.name == "Alexandra North"
    assert store.member_selected("owner", "Alex").id == configured.id
    assert store.proxy_identity("owner", "Alex").name == "Alexandra North"


def test_form_id_resolves_member_and_switches_presentation_together(store):
    member = store.member_selected("owner", "Alexandra North")
    form = store.create_form(
        "owner", member.id, "Alex at Sea", "https://example.test/sea.png", "Blue fins and a long tail."
    )

    assert len(form.id) == 5
    assert form.member_id == member.id
    identity = store.proxy_identity("owner", form.id)
    assert identity.id == member.id
    assert identity.name == "Alex at Sea"
    assert identity.avatar == "https://example.test/sea.png"

    switched = store.switch_front("owner", form.id)
    assert switched.member.id == member.id
    assert switched.form == form
    assert store.current_front("owner") == switched


def test_form_and_alias_are_scoped_to_an_owned_system(store):
    with pytest.raises(PermissionError):
        store.configure_alias("stranger", "Alexandra North", "Alex")
    with pytest.raises(PermissionError):
        store.create_form("stranger", "Alexandra North", "Other")
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        store = store.create_form("owner", "Alexandra North", "Unsafe", "file:///secret")

