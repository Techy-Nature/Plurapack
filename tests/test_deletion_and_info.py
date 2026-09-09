import pytest

from plurapack.bot import _member_embed, _system_embed
from plurapack.storage import Store


def test_delete_member_removes_forms_front_and_proxy_records(tmp_path):
    store = Store(tmp_path / "delete-member.sqlite3")
    store.create_system("owner", "Crew")
    member = store.add_member("owner", "Alex", "A:")
    form = store.create_form("owner", member.id, "Alex at Sea", "https://example.test/sea.png")
    store.configure_default_form("owner", member.id, form.id)
    store.switch_front("owner", form.id)
    store.record_proxy("source", "proxy", "channel", member, "owner")

    removed = store.delete_member("owner", member.id)

    assert removed.id == member.id
    assert store.public_member_selected(member.id) is None
    assert store.forms_for_member(member.id) == []
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM proxied_messages").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM current_fronts").fetchone()[0] == 0


def test_delete_member_cannot_cross_system_boundary(tmp_path):
    store = Store(tmp_path / "delete-permission.sqlite3")
    store.create_system("owner", "Crew")
    member = store.add_member("owner", "Alex", "A:")
    store.create_system("stranger", "Other")

    with pytest.raises(PermissionError):
        store.delete_member("stranger", member.id)


def test_delete_system_requires_exact_id_and_nukes_all_associated_rows(tmp_path):
    store = Store(tmp_path / "delete-system.sqlite3")
    system_id = store.create_system("owner", "Crew")
    member = store.add_member("owner", "Alex", "A:")
    store.create_form("owner", member.id, "Alex at Sea")
    store.create_link("owner")
    store.record_proxy("source", "proxy", "channel", member, "owner")

    with pytest.raises(PermissionError, match="exactly match"):
        store.delete_system("owner", system_id.upper())
    assert store.system_for("owner") == system_id

    assert store.delete_system("owner", system_id) == system_id
    assert store.system_for("owner") is None
    with store.connect() as db:
        for table in ("systems", "owners", "members", "forms", "links", "proxied_messages"):
            assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_public_info_resolves_ids_and_rejects_ambiguous_names(tmp_path):
    store = Store(tmp_path / "info.sqlite3")
    first = store.create_system("one", "Crew")
    second = store.create_system("two", "Crew")
    member = store.add_member("one", "Alex", "A:")
    store.add_member("two", "Alex", "B:")

    assert store.system_info(first).id == first
    assert store.public_member_selected(member.id).id == member.id
    assert store.public_member_selected("Alex", first).system_id == first
    with pytest.raises(ValueError, match="system ID"):
        store.system_info("Crew")
    with pytest.raises(ValueError, match="member ID"):
        store.public_member_selected("Alex")


def test_info_embeds_include_profiles_default_form_and_preview(tmp_path):
    class Embed:
        def __init__(self, **values):
            self.__dict__.update(values)

    class SDK:
        SendableEmbed = Embed

    store = Store(tmp_path / "embeds.sqlite3")
    system_id = store.create_system("owner", "Crew")
    member = store.add_member("owner", "Alex", "A:")
    form = store.create_form("owner", member.id, "Sea", "https://example.test/sea.png")
    member = store.configure_default_form("owner", member.id, form.id)
    member = store.configure_pronouns("owner", member.id, "they / them")
    store.configure_form_pronouns("owner", form.id, "sea / seas")

    member_card = _member_embed(SDK, store, member)
    system_card = _system_embed(SDK, store.system_info(system_id), 1)

    assert member_card.title == "Alex"
    assert member_card.icon_url == "https://example.test/sea.png"
    assert "**ID:**" in member_card.description
    assert "[▣](https://example.test/sea.png" in member_card.description
    assert "★ default" in member_card.description
    assert "**Pronouns:** they / them" in member_card.description
    assert "sea / seas" in member_card.description
    assert system_card.title == "Crew"
    assert system_id in system_card.description
