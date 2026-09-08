import pytest
from pathlib import Path

from plurapack.proxy import Incoming, ProxyService
from plurapack.storage import Store, short_hash


class FakePlatform:
    def __init__(self, fail=False):
        self.sent, self.deleted, self.fail = [], [], fail

    async def send_proxy(self, incoming, member, content):
        self.sent.append((member.id, content))
        if self.fail:
            raise RuntimeError("network")
        return "proxy-1"

    async def delete_source(self, incoming):
        self.deleted.append(incoming.id)


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
