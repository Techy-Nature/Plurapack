import pytest

from plurapack.storage import ProxyTag, Store


def test_member_can_match_multiple_proxy_tags(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.create_system("owner", "System")
    member = store.add_member("owner", "Alex", "[a]", "")
    store.configure_member_proxy("owner", member.id, "A:", ":A")

    assert [(tag.prefix, tag.suffix) for tag in store.proxy_tags(member_id=member.id)] == [
        ("[a]", ""), ("A:", ":A")
    ]
    assert store.match_member("owner", "[a] first")[1] == "first"
    assert store.match_member("owner", "A: second :A")[1] == "second"
    assert store.member_selected("owner", "A:").id == member.id


def test_form_can_match_multiple_tags_and_remove_one(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.create_system("owner", "System")
    member = store.add_member("owner", "Alex", "[a]")
    form = store.create_form("owner", member.id, "Formal", prefix="F:")
    store.configure_form_proxy("owner", form.id, "formal:", ":formal")

    assert store.match_member("owner", "F: hello")[0].name == "Formal"
    assert store.match_member("owner", "formal: hello :formal")[0].name == "Formal"
    store.remove_proxy_tag("owner", form.id, "F:", form=True)
    assert store.match_member("owner", "F: hello") is None
    assert store.match_member("owner", "formal: hello :formal")[1] == "hello"


def test_proxy_tag_limit_and_atomic_replacement(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    store.create_system("owner", "System")
    member = store.add_member("owner", "Alex", "tag-0:")
    store.replace_proxy_tags("owner", member.id, [ProxyTag(f"tag-{index}:") for index in range(100)])

    assert len(store.proxy_tags(member_id=member.id)) == 100
    with pytest.raises(ValueError, match="at most 100"):
        store.configure_member_proxy("owner", member.id, "overflow:")
    with pytest.raises(ValueError, match="at most 100"):
        store.replace_proxy_tags("owner", member.id, [ProxyTag(f"new-{index}:") for index in range(101)])
    assert len(store.proxy_tags(member_id=member.id)) == 100
