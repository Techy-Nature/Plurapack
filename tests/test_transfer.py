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
