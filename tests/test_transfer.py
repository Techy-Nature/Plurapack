import json

import pytest

from plurapack.storage import Store
from plurapack.transfer import TransferError, export_document, parse_import


def test_plural_k_it_import_normalizes_common_export_fields(tmp_path):
    transfer = parse_import("pluralkit", json.dumps({
        "name": "Crew",
        "members": [{
            "name": "alex-internal", "display_name": "Alex", "avatar_url": "https://example.test/a.png",
            "color": "7B68EE", "proxy_tags": [{"prefix": "[a]", "suffix": ""}],
        }],
    }))
    assert transfer.name == "Crew"
    assert transfer.members[0].name == "Alex"
    assert transfer.members[0].prefix == "[a]"
    assert transfer.members[0].color == "#7b68ee"


def test_tupperbox_import_supports_brackets_and_tagless_members():
    transfer = parse_import("tupperbox", json.dumps({"tuppers": [
        {"name": "Sam", "brackets": ["S:", ":S"]},
        {"name": "No Tag", "brackets": []},
    ]}))
    assert (transfer.members[0].prefix, transfer.members[0].suffix) == ("S:", ":S")
    assert transfer.members[1].prefix == "No Tag:"


def test_import_is_atomic_when_a_member_conflicts(tmp_path):
    store = Store(tmp_path / "transfer.db")
    store.create_system("owner", "Existing")
    store.add_member("owner", "Alex", "[a]")
    transfer = parse_import("tupperbox", json.dumps({"tuppers": [
        {"name": "New", "brackets": ["N:", ""]},
        {"name": "Alex", "brackets": ["A:", ""]},
    ]}))
    with pytest.raises(ValueError, match="Nothing was imported"):
        store.import_members("owner", transfer.members)
    assert store.member_named("owner", "New") is None


@pytest.mark.parametrize("format_name,root_key", [
    ("pluralkit", "members"), ("tupperbox", "tuppers"), ("plurapack", "members")
])
def test_export_formats_are_json_and_exclude_private_records(tmp_path, format_name, root_key):
    store = Store(tmp_path / "export.db")
    store.create_system("owner-account", "Crew")
    store.add_member("owner-account", "Alex", "[a]")
    name, members = store.export_system("owner-account")
    document = export_document(format_name, name, members)
    exported = json.loads(document)
    assert exported["name"] == "Crew"
    assert exported[root_key][0]["name"] == "Alex"
    assert b"owner-account" not in document


def test_transfer_rejects_unknown_sources_and_malformed_shapes():
    with pytest.raises(TransferError, match="Source must be"):
        parse_import("other", "{}")
    with pytest.raises(TransferError, match="JSON array"):
        parse_import("pluralkit", '{"members": {}}')

from plurapack.transfer import detect_format, export_system as export_store, import_system


def test_native_v1_round_trip_preserves_portable_records(tmp_path):
    source = Store(tmp_path / "source.db")
    source.create_system("source", "Café 系统", "Unicode 🌈")
    member = source.add_member("source", "Ålex", "[a]", "]", description="Hello 世界",
                               alias="alex", pronouns="they/them", color="#7b68ee",
                               avatar="https://example.test/a.png")
    source.configure_member_proxy("source", member.id, "A:", "")
    source.create_group("source", "Friends", "friends")
    source.add_group_members("source", [member.id])
    source.create_form("source", member.id, "Happy", None, "bright", None, "H:", "")

    document, report = export_store(source, "source")
    body = json.loads(document)
    assert (body["format"], body["version"], body["generator"]["name"]) == ("plurapack", 1, "Plurapack")
    assert body["exported_at"].endswith("Z")
    assert b"source" not in document

    target = Store(tmp_path / "target.db")
    imported = import_system(target, "different-owner", document)
    restored = target.export_system("different-owner")[1][0]
    assert imported.members_imported == 1
    assert (target.export_system("different-owner")[0], restored.name, restored.description) == ("Café 系统", "Ålex", "Hello 世界")
    assert len(target.proxy_tags(member_id=restored.id)) == 2
    assert target.forms_for_member(restored.id)[0].display_name == "Happy"


def test_format_detection_and_ambiguity():
    assert detect_format({"format": "plurapack", "members": [], "system": {}}) == "plurapack"
    assert detect_format({"tuppers": [], "groups": []}) == "tupperbox"
    assert detect_format({"system": {}, "members": [], "groups": [], "switches": []}) == "pluralkit"
    with pytest.raises(TransferError, match="ambiguous"):
        detect_format({"tuppers": [], "members": [], "switches": []})


def test_tupperbox_flat_multiple_bracket_pairs_and_groups():
    transfer = parse_import("tupperbox", json.dumps({"groups": [{"id": 4, "name": "Crew"}], "tuppers": [{
        "id": 8, "name": "Sam", "brackets": ["[", "]", "S:", ""], "group_id": 4,
        "user_id": "must-not-be-owner", "posts": 99,
    }]}))
    assert transfer.members[0].proxy_tags == [{"prefix": "[", "suffix": "]"}, {"prefix": "S:", "suffix": ""}]
    assert transfer.members[0].groups == ["4"]


def test_validation_failure_does_not_modify_store(tmp_path):
    store = Store(tmp_path / "atomic.db")
    store.create_system("owner", "Existing")
    store.add_member("owner", "Safe", "S:")
    bad = {"format": "plurapack", "version": 1, "system": {"name": "Bad"}, "groups": [],
           "settings": {}, "members": [{"export_id": "x", "name": "Broken", "groups": ["missing"],
                                           "proxy_tags": [{"prefix": "B:", "suffix": ""}]}]}
    with pytest.raises(TransferError, match="unknown group"):
        import_system(store, "owner", json.dumps(bad))
    assert [member.name for member in store.export_system("owner")[1]] == ["Safe"]


def test_conflict_strategies(tmp_path):
    store = Store(tmp_path / "conflict.db")
    store.create_system("owner", "Existing")
    store.add_member("owner", "Alex", "old:", description="keep")
    body = {"format": "plurapack", "version": 1, "system": {"name": "Imported"}, "groups": [],
            "settings": {}, "members": [{"export_id": "m", "name": "Alex", "description": "new",
                                            "proxy_tags": [{"prefix": "new:", "suffix": ""}]}]}
    merged = import_system(store, "owner", json.dumps(body), strategy="merge")
    assert merged.existing_members_skipped == 1 and store.member_named("owner", "Alex").description == "keep"
    import_system(store, "owner", json.dumps(body), strategy="overwrite")
    assert store.member_named("owner", "Alex").description == "new"


@pytest.mark.parametrize(("format_name", "collection"), [
    ("pluralkit", "members"),
    ("tupperbox", "tuppers"),
])
def test_external_export_can_promote_forms_to_members(tmp_path, format_name, collection):
    store = Store(tmp_path / f"{format_name}.db")
    store.create_system("owner", "Crew")
    member = store.add_member("owner", "Alex", "A:", color="#7b68ee")
    store.create_group("owner", "Friends", "friends")
    group = store.active_group("owner")
    store.add_group_members("owner", [member.id])
    form = store.create_form(
        "owner", member.id, "Happy", "https://example.test/happy.png",
        "A happy presentation", "they/she", "H:", ":H",
        "https://example.test/banner.png",
    )
    store.configure_form_proxy("owner", form.id, "happy:", "")

    document, report = export_store(store, "owner", format_name, forms_mode="members")
    exported = json.loads(document)
    promoted = next(item for item in exported[collection] if item["name"] == "Alex — Happy")
    assert promoted["id"] == f"form-{form.id}"
    assert promoted["description"] == "A happy presentation"
    assert promoted["avatar_url"] == "https://example.test/happy.png"
    if format_name == "pluralkit":
        assert promoted["proxy_tags"] == [
            {"prefix": "H:", "suffix": ":H"},
            {"prefix": "happy:", "suffix": ""},
        ]
        exported_group = next(item for item in exported["groups"] if item["id"] == f"group-{group.id}")
        assert promoted["id"] in exported_group["members"]
    else:
        assert promoted["brackets"] == ["H:", ":H", "happy:", ""]
        assert promoted["group_id"] == f"group-{group.id}"
    assert "qualified standalone" in report.warnings[0]


@pytest.mark.parametrize("format_name", ["pluralkit", "tupperbox"])
def test_external_export_forms_loss_is_explicit_default(tmp_path, format_name):
    store = Store(tmp_path / f"loss-{format_name}.db")
    store.create_system("owner", "Crew")
    member = store.add_member("owner", "Alex", "A:")
    store.create_form("owner", member.id, "Happy", None, "", None, "H:", "")
    document, report = export_store(store, "owner", format_name)
    exported = json.loads(document)
    assert len(exported["members" if format_name == "pluralkit" else "tuppers"]) == 1
    assert "1 forms were omitted" in report.warnings[0]


def test_external_export_rejects_unknown_forms_mode(tmp_path):
    store = Store(tmp_path / "invalid-mode.db")
    store.create_system("owner", "Crew")
    with pytest.raises(TransferError, match="Forms mode"):
        export_store(store, "owner", "pluralkit", forms_mode="surprise")
