from types import SimpleNamespace

import pytest

from plurapack.bot import _group_embed, _member_embed, _system_embed, create_bot
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


def test_info_resolution_keeps_ids_global_and_names_local(tmp_path):
    store = Store(tmp_path / "local-info.sqlite3")
    first = store.create_system("one", "Nest")
    second = store.create_system("two", "Nest")
    first_member = store.add_member("one", "Alex", "A:", alias="alexander")
    second_member = store.add_member("two", "Alex", "B:", alias="lex")
    first_form = store.create_form("one", first_member.id, "Happy")
    second_form = store.create_form("two", second_member.id, "Happy")
    first_group = store.create_group("one", "Friends", "pals")
    second_group = store.create_group("two", "Work", "coworkers")

    assert store.public_info_selected("one", "  Alex  ").id == first_member.id
    assert store.public_info_selected("one", "alexander").id == first_member.id
    assert store.public_info_selected("one", "Happy")[0].id == first_form.id
    assert store.public_info_selected("one", "Friends").id == first_group.id
    assert store.public_info_selected("one", "pals").id == first_group.id
    assert store.public_info_selected("one", "Nest").id == first

    assert store.public_info_selected("one", second_member.id).id == second_member.id
    assert store.public_info_selected("one", second_form.id)[0].id == second_form.id
    assert store.public_info_selected("one", second_group.id).id == second_group.id
    assert store.public_info_selected("one", second).id == second


def test_info_resolution_does_not_fall_back_to_other_system_names(tmp_path):
    store = Store(tmp_path / "no-global-names.sqlite3")
    store.create_system("one", "First")
    store.create_system("two", "Second")
    member = store.add_member("two", "Alex", "B:", alias="alexander")
    store.create_form("two", member.id, "Happy")
    store.create_group("two", "Friends", "pals")

    assert store.public_info_selected("one", "Alex") is None
    assert store.public_info_selected("one", "alexander") is None
    assert store.public_info_selected("one", "Happy") is None
    assert store.public_info_selected("one", "Friends") is None
    assert store.public_info_selected("one", "pals") is None
    assert store.public_info_selected("one", "Second") is None


def test_info_exact_id_takes_precedence_over_local_human_readable_selector(tmp_path):
    store = Store(tmp_path / "id-precedence.sqlite3")
    store.create_system("one", "First")
    store.create_system("two", "Second")
    local = store.add_member("one", "Local", "A:")
    remote = store.add_member("two", "Remote", "B:")
    store.configure_alias("one", local.id, remote.id)

    assert store.public_info_selected("one", remote.id).id == remote.id


async def test_info_reports_ambiguous_local_form_names(tmp_path):
    database = tmp_path / "ambiguous-info.sqlite3"
    store = Store(database)
    store.create_system("owner", "Crew")
    alex = store.add_member("owner", "Alex", "A:")
    sam = store.add_member("owner", "Sam", "S:")
    store.create_form("owner", alex.id, "Happy")
    store.create_form("owner", sam.id, "Happy")

    with pytest.raises(ValueError, match="More than one form has that name"):
        store.public_info_selected("owner", "Happy")

    replies = []

    async def send(message):
        replies.append(message)

    ctx = SimpleNamespace(author=SimpleNamespace(id="owner"), send=send)
    bot = create_bot("p;", str(database))
    await bot.get_command("info").callback(ctx, selector="Happy")

    assert replies == ["More than one form has that name; use the form ID instead."]


def test_member_id_generation_retries_ids_already_used_by_forms(tmp_path, monkeypatch):
    store = Store(tmp_path / "member-id-collision.sqlite3")
    system_id = store.create_system("owner", "Crew")
    existing = store.add_member("owner", "Existing", "E:")
    with store.connect() as db:
        db.execute(
            "INSERT INTO forms(id,member_id,display_name) VALUES (?,?,?)",
            ("abc12", existing.id, "Existing form"),
        )
    generated = iter(("abc12", "def34"))
    monkeypatch.setattr("plurapack.storage.short_hash", lambda length: next(generated))

    member = store.add_member("owner", "New", "N:")

    assert member.id == "def34"
    assert member.system_id == system_id


def test_form_id_generation_retries_ids_already_used_by_members(tmp_path, monkeypatch):
    store = Store(tmp_path / "form-id-collision.sqlite3")
    store.create_system("owner", "Crew")
    member = store.add_member("owner", "Alex", "A:")
    generated = iter((member.id, "def34"))
    monkeypatch.setattr("plurapack.storage.short_hash", lambda length: next(generated))

    form = store.create_form("owner", member.id, "Happy")

    assert form.id == "def34"


def test_group_ids_use_their_own_eight_character_namespace(tmp_path, monkeypatch):
    store = Store(tmp_path / "group-ids.sqlite3")
    store.create_system("owner", "Crew")
    generated = iter(("12345678", "12345678", "abcdef01"))
    monkeypatch.setattr("plurapack.storage.short_hash", lambda length: next(generated))

    first = store.create_group("owner", "First", "first")
    second = store.create_group("owner", "Second", "second")

    assert first.id == "12345678"
    assert second.id == "abcdef01"


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


def test_member_embed_falls_back_to_default_form_description(tmp_path):
    class Embed:
        def __init__(self, **values):
            self.__dict__.update(values)

    class SDK:
        SendableEmbed = Embed

    store = Store(tmp_path / "default-form-description.sqlite3")
    store.create_system("owner", "Crew")
    member = store.add_member("owner", "Alex", "A:")
    form = store.create_form("owner", member.id, "Sea", soma="First line\nSecond line")
    member = store.configure_default_form("owner", member.id, form.id)

    card = _member_embed(SDK, store, member)

    assert card.description.startswith("First line\nSecond line\n\n**ID:**")


def test_group_embed_lists_public_group_profile_and_member_details(tmp_path):
    class Embed:
        def __init__(self, **values):
            self.__dict__.update(values)

    class SDK:
        SendableEmbed = Embed

    store = Store(tmp_path / "group-embed.sqlite3")
    store.create_system("owner", "Crew")
    alex = store.add_member("owner", "Alex", "A:")
    store.configure_pronouns("owner", alex.id, "they/them")
    store.configure_member_proxy("owner", alex.id, "[a]", "")
    group = store.create_group("owner", "Friends", "friends", "https://example.test/friends.png")
    store.add_group_members("owner", [alex.id])

    card = _group_embed(SDK, store, group)

    assert card.title == "Friends"
    assert card.icon_url == "https://example.test/friends.png"
    assert f"**Group ID:** `{group.id}`" in card.description
    assert f"**Alex** (`{alex.id}`)" in card.description
    assert "Pronouns: they/them" in card.description
    assert "Proxies: `A:text`, `[a]text`" in card.description


async def test_info_system_includes_group_embeds(tmp_path):
    database = tmp_path / "info-groups.sqlite3"
    store = Store(database)
    system_id = store.create_system("owner", "Crew")
    store.create_group("owner", "No Avatar", "none")
    replies = []

    async def send(*args, **kwargs):
        replies.append((args, kwargs))
        return SimpleNamespace(id="posted", channel_id="channel")

    ctx = SimpleNamespace(author=SimpleNamespace(id="owner"), send=send)
    bot = create_bot("p;", str(database))
    await bot.get_command("info").callback(ctx, selector=system_id)

    embeds = replies[0][1]["embeds"]
    assert [embed.title for embed in embeds] == ["Crew", "No Avatar"]
    assert embeds[1].icon_url is None


@pytest.mark.parametrize("selector_kind", ["name", "alias", "id"])
async def test_info_can_select_group_by_name_alias_or_id(tmp_path, selector_kind):
    database = tmp_path / f"info-group-{selector_kind}.sqlite3"
    store = Store(database)
    store.create_system("owner", "Crew")
    group = store.create_group("owner", "Friends", "pals")
    selector = {"name": group.name, "alias": group.alias, "id": group.id}[selector_kind]
    replies = []

    async def send(*args, **kwargs):
        replies.append((args, kwargs))

    ctx = SimpleNamespace(author=SimpleNamespace(id="owner"), send=send)
    bot = create_bot("p;", str(database))
    await bot.get_command("info").callback(ctx, selector=selector)

    embeds = replies[0][1]["embeds"]
    assert len(embeds) == 1
    assert embeds[0].title == group.name
    assert f"`{group.id}`" in embeds[0].description
