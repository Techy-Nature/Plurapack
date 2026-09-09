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


def test_member_and_form_pronouns_can_be_set_independently(store):
    member = store.configure_pronouns("owner", "Alexandra North", "they / them")
    inherited = store.create_form("owner", member.id, "Everyday")
    specific = store.create_form("owner", member.id, "Formal", pronouns="she / her")

    assert store.proxy_identity("owner", inherited.id).pronouns == "they / them"
    assert store.proxy_identity("owner", specific.id).pronouns == "she / her"

    cleared = store.configure_form_pronouns("owner", specific.id, None)
    assert cleared.pronouns is None
    assert store.proxy_identity("owner", specific.id).pronouns == "they / them"


def test_pronouns_are_validated_and_scoped_to_owners(store):
    with pytest.raises(ValueError, match="64 characters"):
        store.configure_pronouns("owner", "Alexandra North", "x" * 65)
    form = store.create_form("owner", "Alexandra North", "Formal")
    with pytest.raises(PermissionError):
        store.configure_form_pronouns("stranger", form.id, "she / her")


def test_member_switch_uses_configurable_default_form(store):
    member = store.member_selected("owner", "Alexandra North")
    form = store.create_form("owner", member.id, "Alex at Sea")

    configured = store.configure_default_form("owner", member.id, "Alex at Sea")
    assert configured.default_form_id == form.id
    assert store.switch_front("owner", member.id).form == form

    cleared = store.configure_default_form("owner", member.id, None)
    assert cleared.default_form_id is None
    assert store.switch_front("owner", member.id).form is None


def test_explicit_form_overrides_member_default(store):
    member = store.member_selected("owner", "Alexandra North")
    default = store.create_form("owner", member.id, "Everyday")
    requested = store.create_form("owner", member.id, "Formal")
    store.configure_default_form("owner", member.id, default.id)

    switched = store.switch_front("owner", "Formal")
    assert switched.form == requested


def test_default_form_must_belong_to_selected_member(store):
    other = store.add_member("owner", "Jamie", "[jamie]")
    other_form = store.create_form("owner", other.id, "Jamie's form")

    with pytest.raises(ValueError, match="must belong"):
        store.configure_default_form("owner", "Alexandra North", other_form.id)


def test_form_and_alias_are_scoped_to_an_owned_system(store):
    with pytest.raises(PermissionError):
        store.configure_alias("stranger", "Alexandra North", "Alex")
    with pytest.raises(PermissionError):
        store.create_form("stranger", "Alexandra North", "Other")
    with pytest.raises(ValueError, match="HTTP or HTTPS"):
        store = store.create_form("owner", "Alexandra North", "Unsafe", "file:///secret")
