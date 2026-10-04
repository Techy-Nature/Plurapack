import pytest

from plurapack.storage import Store


def test_explicit_group_storage_crud_and_membership(tmp_path):
    store = Store(tmp_path / "groups.sqlite3")
    system_id = store.create_system("owner", "System")
    first = store.add_member("owner", "Alex", "a:")
    second = store.add_member("owner", "Nest", "n:")
    group = store.create_group("owner", "Main Crew", "main", "https://example.com/g.png")
    other = store.create_group("owner", "Work", "work")

    assert len(group.id) == 8
    assert [item.name for item in store.groups_for_system(system_id)] == ["Main Crew", "Work"]
    assert store.group_selected("owner", group.id) == group
    assert store.active_group("owner") == other  # Existing creation semantics are retained.

    group = store.update_group("owner", group.id, name="Core", alias="core",
                               avatar="https://example.com/core.png")
    assert (group.name, group.alias, group.avatar) == (
        "Core", "core", "https://example.com/core.png")
    with pytest.raises(ValueError, match="name"):
        store.update_group("owner", other.id, name="Core")
    with pytest.raises(ValueError, match="alias"):
        store.update_group("owner", other.id, alias="core")

    store.add_members_to_group("owner", group.id, [first.id, second.id])
    store.remove_members_from_group("owner", group.id, [second.id])
    assert [member.id for member in store.group_members("owner", group.id)] == [first.id]
    store.replace_group_members("owner", group.id, [second.id])
    assert [member.id for member in store.group_members("owner", group.id)] == [second.id]
    assert store.set_active_group("owner", group.id) == group
    assert store.active_group("owner") == group

    store.delete_group("owner", other.id)
    assert store.active_group("owner") == group
    store.delete_group("owner", group.id)
    assert store.active_group("owner") is None
    assert store.member_selected("owner", first.id) == first
    assert store.member_selected("owner", second.id) == second


def test_group_storage_rejects_cross_system_operations(tmp_path):
    store = Store(tmp_path / "groups.sqlite3")
    store.create_system("owner", "Owner")
    store.create_system("other", "Other")
    group = store.create_group("owner", "Owner group", "owner")
    outsider = store.add_member("other", "Outsider", "o:")

    assert store.group_selected("other", group.id) is None
    with pytest.raises(PermissionError):
        store.update_group("other", group.id, name="Stolen")
    with pytest.raises(ValueError, match="not found in this system"):
        store.add_members_to_group("owner", group.id, [outsider.id])


def test_failed_membership_replacement_is_atomic(tmp_path):
    store = Store(tmp_path / "groups.sqlite3")
    store.create_system("owner", "Owner")
    store.create_system("other", "Other")
    original = store.add_member("owner", "Original", "original:")
    replacement = store.add_member("owner", "Replacement", "replacement:")
    outsider = store.add_member("other", "Outsider", "outsider:")
    group = store.create_group("owner", "Group", "group")
    store.replace_group_members("owner", group.id, [original.id])

    with pytest.raises(ValueError, match="not found in this system"):
        store.replace_group_members("owner", group.id, [replacement.id, outsider.id])

    assert [member.id for member in store.group_members("owner", group.id)] == [original.id]


def test_group_update_defensively_rejects_null_identity_fields(tmp_path):
    store = Store(tmp_path / "groups.sqlite3")
    store.create_system("owner", "Owner")
    group = store.create_group("owner", "Group", "group")
    with pytest.raises(ValueError, match="name cannot be null"):
        store.update_group("owner", group.id, name=None)
    with pytest.raises(ValueError, match="alias cannot be null"):
        store.update_group("owner", group.id, alias=None)


def test_active_group_can_be_selected_by_id_name_and_alias(tmp_path):
    store = Store(tmp_path / "selection.sqlite3")
    store.create_system("owner", "Owner")
    first = store.create_group("owner", "Main Crew", "main")
    assert store.active_group("owner") == first

    second = store.create_group("owner", "Work", "work")
    assert store.active_group("owner") == second

    for selector in (first.id, "Main Crew", "main"):
        assert store.set_active_group("owner", selector) == first
        assert store.active_group("owner") == first
        store.set_active_group("owner", second.id)


def test_active_group_selection_rejects_missing_and_foreign_groups(tmp_path):
    store = Store(tmp_path / "selection-permissions.sqlite3")
    store.create_system("owner", "Owner")
    store.create_system("other", "Other")
    foreign = store.create_group("other", "Foreign", "foreign")

    with pytest.raises(PermissionError, match="not found or not owned"):
        store.set_active_group("owner", "missing")
    for selector in (foreign.id, foreign.name, foreign.alias):
        with pytest.raises(PermissionError, match="not found or not owned"):
            store.set_active_group("owner", selector)
