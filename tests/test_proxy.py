import pytest
from pathlib import Path

from plurapack.proxy import Incoming, ProxyService
from plurapack.storage import Store, short_hash


class FakePlatform:
    def __init__(self, fail=False):
        self.sent, self.deleted, self.fail = [], [], fail
        self.edited, self.deleted_proxies, self.reproxied = [], [], []

    async def send_proxy(self, incoming, member, content):
        self.sent.append((member.id, content))
        if self.fail:
            raise RuntimeError("network")
        return "proxy-1"

    async def delete_source(self, incoming):
        self.deleted.append(incoming.id)

    async def edit_proxy(self, channel_id, proxy_id, content):
        self.edited.append((channel_id, proxy_id, content))

    async def delete_proxy(self, channel_id, proxy_id):
        self.deleted_proxies.append((channel_id, proxy_id))

    async def send_reproxy(self, incoming, proxy_id, member):
        self.reproxied.append((proxy_id, member.id))
        return "proxy-2"


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "test.db")
    value.create_system("owner", "Crew")
    value.add_member("owner", "Alex", "[alex]", "")
    return value


def test_ids_are_hex_and_correct_lengths(store):
    assert len(store.system_for("owner")) == 10
    assert len(store.member_named("owner", "alex").id) == 5
    assert all(c in "0123456789abcdef" for c in short_hash(15))


async def test_proxy_tracks_attribution_before_deletion(store):
    platform = FakePlatform()
    service = ProxyService(store, platform)
    result = await service.handle(Incoming("source-1", "channel", "owner", "[alex] hello"))
    assert result == "proxy-1"
    assert platform.deleted == ["source-1"]
    assert store.proxy_owned_by("proxy-1", "owner")


async def test_duplicate_and_commands_are_not_proxied(store):
    platform = FakePlatform()
    service = ProxyService(store, platform)
    message = Incoming("source-1", "channel", "owner", "[alex] hello")
    await service.handle(message)
    assert await service.handle(message) is None
    assert await service.handle(Incoming("cmd", "channel", "owner", "p;setup")) is None
    assert len(platform.sent) == 1
    assert platform.deleted == ["source-1"]


async def test_failed_replacement_preserves_original(store):
    platform = FakePlatform(fail=True)
    with pytest.raises(RuntimeError):
        await ProxyService(store, platform).handle(Incoming("source", "channel", "owner", "[alex] hi"))
    assert platform.deleted == []


async def test_edit_reaction_uses_next_message_in_same_channel(store):
    platform = FakePlatform()
    service = ProxyService(store, platform)
    await service.handle(Incoming("source-1", "channel", "owner", "[alex] before"))

    assert await service.handle_reaction("wrong-channel", "proxy-1", "owner", "✏️") is False
    assert await service.handle_reaction("channel", "proxy-1", "stranger", "✏️") is False
    assert await service.handle_reaction("channel", "proxy-1", "owner", "✏️") is True
    assert await service.handle(Incoming("edit-source", "channel", "owner", "after")) == "proxy-1"
    assert platform.edited == [("channel", "proxy-1", "after")]
    assert platform.deleted == ["source-1", "edit-source"]


async def test_delete_reaction_requires_ownership_and_marks_proxy_deleted(store):
    platform = FakePlatform()
    service = ProxyService(store, platform)
    await service.handle(Incoming("source-1", "channel", "owner", "[alex] hello"))

    assert await service.handle_reaction("channel", "proxy-1", "stranger", "❌") is False
    assert await service.handle_reaction("channel", "proxy-1", "owner", "❌") is True
    assert platform.deleted_proxies == [("channel", "proxy-1")]
    assert not store.proxy_owned_by("proxy-1", "owner")


async def test_reply_with_member_name_id_or_prefix_reproxies_owned_message(store):
    platform = FakePlatform()
    service = ProxyService(store, platform)
    await service.handle(Incoming("source-1", "channel", "owner", "[alex] hello"))
    member = store.member_named("owner", "Alex")

    reply = Incoming("selector", "channel", "owner", member.id, reply_to_id="proxy-1")
    assert await service.handle(reply) == "proxy-2"
    assert platform.reproxied == [("proxy-1", member.id)]
    assert platform.deleted_proxies == [("channel", "proxy-1")]
    assert platform.deleted == ["source-1", "selector"]
    assert store.proxy_owned_by("proxy-2", "owner", "channel")


def test_member_selector_accepts_name_id_and_proxy_prefix(store):
    member = store.member_named("owner", "Alex")
    assert store.member_selected("owner", " alex ") == member
    assert store.member_selected("owner", member.id) == member
    assert store.member_selected("owner", "[alex]") == member
    assert store.member_selected("stranger", member.id) is None


def test_color_is_connected_to_member_id_and_normalized(store):
    member = store.member_named("owner", "Alex")

    configured = store.configure_color("owner", member.id, "7B68EE")

    assert configured.id == member.id
    assert configured.color == "#7b68ee"
    assert store.member_named("owner", "Alex").color == "#7b68ee"


def test_color_rejects_invalid_values_and_unowned_members(store):
    member = store.member_named("owner", "Alex")
    with pytest.raises(ValueError, match="six-digit hex"):
        store.configure_color("owner", member.id, "purple")
    with pytest.raises(PermissionError, match="not found or not owned"):
        store.configure_color("stranger", member.id, "123456")


def test_other_account_cannot_select_or_manage_member(store):
    assert store.match_member("stranger", "[alex] hello") is None
    member = store.member_named("owner", "Alex")
    with pytest.raises(PermissionError):
        store.record_proxy("s", "p", "c", member, "stranger")


def test_one_time_account_link_shares_system_without_disclosing_token(store):
    token = store.create_link("owner")
    assert len(token) == 15
    assert token not in Path(store.path).read_text(errors="ignore")
    assert store.redeem_link("second-owner", token) == store.system_for("owner")
    assert store.member_named("second-owner", "Alex").id == store.member_named("owner", "Alex").id
    with pytest.raises(PermissionError):
        store.redeem_link("third-owner", token)
