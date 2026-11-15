import json

import pytest

from plurapack.storage import Store
from plurapack.transfer import TransferError, detect_format, import_system, parse_import

EXPORT = {
    "proxies": [
        {
            "name": "Alex",
            "description": "Likes tea.",
            "avatar_url": "https://example.test/alex.png",
            "triggers": ["a: {}", "[{}]", '{text.contains("hello") && text}', "{}"],
            "times_used": 12,
            "creation_date": "2026-09-27T00:04:08.785241",
            "nickname": "Alexandra",
            "forms": {},
            "current_form": "",
            "pronouns": "she/her",
            "tags": [],
        },
        {
            "name": "Sam",
            "description": "",
            "avatar_url": "",
            "triggers": ["s\\{ {} \\}"],
            "times_used": 0,
            "creation_date": "2026-09-27T00:19:22.834996",
            "nickname": "",
            "forms": {"happy": "https://example.test/sam-happy.png", "sad": ""},
            "current_form": "happy",
            "pronouns": "",
            "tags": ["$0"],
        },
        {
            "name": "Quiet One",
            "description": "",
            "avatar_url": "",
            "triggers": ["{text.startswith('q') && text}"],
            "times_used": 0,
            "creation_date": "2026-09-27T00:19:22.834996",
            "nickname": "",
            "forms": {},
            "current_form": "",
            "pronouns": "",
            "tags": ["$0"],
        },
    ],
    "tags": {
        "$0": {"name": "Night Crew", "description": "", "creation_date": "2026-09-27T00:18:50.289000",
               "tag": "{} 🌙"},
    },
}


def test_detects_fishing_bucket_exports():
    assert detect_format(EXPORT) == "fishingbucket"
    assert detect_format({"proxies": [], "tags": {}}) == "fishingbucket"


@pytest.mark.parametrize("source", [None, "fishingbucket", "fb", "fishing-bucket", "Fishing_Bucket"])
def test_parses_proxies_triggers_forms_and_tags(source):
    result = parse_import(source, json.dumps(EXPORT))
    alex, sam, quiet = result.members
    assert alex.name == "Alexandra" and alex.alias == "Alex"
    assert alex.proxy_tags == [{"prefix": "a:", "suffix": ""}, {"prefix": "[", "suffix": "]"}]
    assert (alex.prefix, alex.suffix) == ("a:", "")
    assert alex.avatar == "https://example.test/alex.png"
    assert alex.pronouns == "she/her" and alex.description == "Likes tea."
    assert sam.proxy_tags == [{"prefix": "s{", "suffix": "}"}]
    assert sam.avatar is None and sam.groups == ["$0"]
    assert [(f.display_name, f.nickname, f.avatar) for f in sam.forms] == [
        ("Sam (happy)", "happy", "https://example.test/sam-happy.png"), ("Sam (sad)", "sad", None)]
    assert sam.default_form == sam.forms[0].export_id
    # A member whose only trigger is scripted falls back to "Name:".
    assert quiet.proxy_tags == [{"prefix": "Quiet One:", "suffix": ""}]
    assert [(g.export_id, g.name, g.alias) for g in result.groups] == [("$0", "Night Crew", "Night-Crew")]
    assert any("3 scripted or catch-all" in w for w in result.warnings)
    assert any("name templates" in w for w in result.warnings)


def test_clashing_nicknames_fall_back_to_proxy_names():
    body = {"proxies": [
        {"name": "One", "nickname": "Same", "triggers": ["1:{}"]},
        {"name": "Two", "nickname": "Same", "triggers": ["2:{}"]},
    ], "tags": {}}
    assert [m.name for m in parse_import("fb", json.dumps(body)).members] == ["One", "Two"]


def test_long_descriptions_are_shortened_with_a_warning():
    body = {"proxies": [{"name": "Long", "description": "x" * 1500, "triggers": []}], "tags": {}}
    result = parse_import("fb", json.dumps(body))
    assert len(result.members[0].description) == 1000
    assert any("shortened" in w for w in result.warnings)


def test_unknown_tag_reference_is_rejected():
    body = {"proxies": [{"name": "Lost", "triggers": [], "tags": ["$9"]}], "tags": {}}
    with pytest.raises(TransferError, match="unknown group"):
        parse_import("fb", json.dumps(body))


def test_import_creates_members_forms_groups_and_default_form(tmp_path):
    store = Store(tmp_path / "fb.db")
    report = import_system(store, "owner", json.dumps(EXPORT))
    assert report.format == "fishingbucket"
    assert report.members_imported == 3 and report.groups_imported == 1
    alex = store.member_named("owner", "Alexandra")
    assert alex.alias == "Alex" and alex.pronouns == "she/her"
    sam = store.member_named("owner", "Sam")
    forms = {f.display_name: f for f in store.forms_for_member(sam.id)}
    assert set(forms) == {"Sam (happy)", "Sam (sad)"}
    assert forms["Sam (happy)"].nickname == "happy"
    assert sam.default_form_id == forms["Sam (happy)"].id
    # Importing again merges instead of duplicating.
    again = import_system(store, "owner", json.dumps(EXPORT), "fb")
    assert again.members_imported == 0 and again.groups_imported == 0
    assert len(store.forms_for_member(sam.id)) == 2
