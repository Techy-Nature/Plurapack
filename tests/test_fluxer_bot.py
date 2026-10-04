from types import SimpleNamespace

from plurapack.fluxer_bot import FluxerPlatform, create_fluxer_bot
from plurapack.bot import COMMAND_ALIASES, COMMAND_HELP, COMMAND_SHORTCUTS
from plurapack.login import LoginService
from plurapack.storage import Store
from plurapack.proxy import Incoming


async def test_fluxer_platform_proxies_with_webhook_identity():
    sent = {}

    class Webhook:
        name = "Plurapack Proxy"

        async def send(self, content, **kwargs):
            sent.update(content=content, **kwargs)
            return SimpleNamespace(id=42)

    webhook = Webhook()
    client = SimpleNamespace(fetch_channel_webhooks=lambda channel_id: None)
    platform = FluxerPlatform(client, webhooks={"20": webhook})
    member = SimpleNamespace(name="Alex", avatar="https://example.test/a.png")

    proxy_id = await platform.send_proxy(Incoming("10", "20", "30", "[a] hi"), member, "hi")

    assert proxy_id == "42"
    assert sent == {
        "content": "hi",
        "username": "Alex",
        "avatar_url": "https://example.test/a.png",
        "wait": True,
    }
    assert platform.proxy_webhooks["42"] is webhook


async def test_fluxer_platform_deletes_source_message():
    deleted = False

    async def delete():
        nonlocal deleted
        deleted = True

    platform = FluxerPlatform(SimpleNamespace(), messages={"10": SimpleNamespace(delete=delete)})
    await platform.delete_source(Incoming("10", "20", "30", "hello"))

    assert deleted
    assert platform.messages == {}


async def test_fluxer_login_command_approves_dashboard_attempt(tmp_path, monkeypatch):
    commands = {}

    class Bot:
        def __init__(self, **kwargs):
            pass

        def command(self, name):
            def register(callback):
                commands[name] = callback
                return callback
            return register

        def event(self, callback):
            return callback

    fluxer = SimpleNamespace(
        Bot=Bot,
        Intents=SimpleNamespace(default=lambda: 0, MESSAGE_CONTENT=1),
    )
    monkeypatch.setattr("plurapack.fluxer_bot._load_fluxer", lambda: fluxer)
    database = tmp_path / "fluxer-login.sqlite3"
    create_fluxer_bot("p;", str(database))
    attempt = LoginService(Store(database)).start()
    replies = []

    async def send(message):
        replies.append(message)

    ctx = SimpleNamespace(
        author=SimpleNamespace(id="fluxer-user", display_name="Fluxer User"),
        send=send,
    )

    await commands["login"](ctx, attempt.code)

    user = LoginService(Store(database)).complete(attempt.id, attempt.browser_secret)
    assert (user.id, user.username) == ("fluxer-user", "Fluxer User")
    assert replies == ["Dashboard login approved. You can return to your browser."]
    assert commands["lg"] is commands["login"]


def make_fluxer_commands(monkeypatch, database):
    commands = {}

    class Bot:
        def __init__(self, **kwargs): pass
        def command(self, name):
            def register(callback):
                commands[name] = callback
                return callback
            return register
        def event(self, callback): return callback

    monkeypatch.setattr(
        "plurapack.fluxer_bot._load_fluxer",
        lambda: SimpleNamespace(Bot=Bot, Intents=SimpleNamespace(default=lambda: 0, MESSAGE_CONTENT=1),
                                File=lambda data, filename: SimpleNamespace(data=data, filename=filename)),
    )
    create_fluxer_bot("p;", str(database))
    return commands


def test_every_public_fluxer_command_and_alias_is_registered(tmp_path, monkeypatch):
    commands = make_fluxer_commands(monkeypatch, tmp_path / "registration.sqlite3")

    assert set(COMMAND_HELP) <= commands.keys()
    for name, shortcut in COMMAND_SHORTCUTS.items():
        assert commands[shortcut] is commands[name]
    for name, aliases in COMMAND_ALIASES.items():
        for alias in aliases:
            assert commands[alias] is commands[name]


async def test_fluxer_profile_form_front_and_link_commands_share_storage(tmp_path, monkeypatch):
    database = tmp_path / "commands.sqlite3"
    commands = make_fluxer_commands(monkeypatch, database)
    store = Store(database)
    store.create_system("owner", "Our system")
    store.add_member("owner", "Alex", "[a]", "")
    replies = []

    async def send(message, **kwargs): replies.append(message)
    owner = SimpleNamespace(author=SimpleNamespace(id=123), send=send)
    # Numeric platform IDs are always normalized before entering Store.
    store.create_system("123", "Fluxer system")
    created = store.add_member("123", "River", "[r]", "")

    await commands["alias"](owner, created.id, "Riv")
    await commands["pronouns"](owner, "Riv", value="they/them")
    await commands["memberproxy"](owner, "Riv", "<r>", "")
    await commands["form"](owner, "Riv", "Formal", "", soma="At work")
    form = store.forms_for_member(created.id)[0]
    await commands["formproxy"](owner, form.id, "{", "}")
    await commands["formpronouns"](owner, form.id, value="")
    await commands["front"](owner, form.id)
    await commands["autoproxy"](owner, "Riv")
    await commands["autofront"](owner, "on")
    await commands["voiceformat"](owner, "Riv", "on", "skip")
    await commands["voiceoff"](owner, "Riv")

    updated = store.member_selected("123", "Riv")
    assert updated.alias == "Riv"
    assert updated.pronouns == "they/them"
    assert store.current_front("123").form.id == form.id
    assert store.autoproxy("123").member.id == created.id
    assert updated.id == created.id

    await commands["link"](owner)
    token = replies[-1].split("`")[1]
    other_replies = []
    other = SimpleNamespace(author=SimpleNamespace(id=456),
                            send=lambda message, **kwargs: _append(other_replies, message))
    await commands["verify"](other, token)
    assert store.system_for("456") == store.system_for("123")


async def test_fluxer_info_can_show_a_group_and_its_members(tmp_path, monkeypatch):
    database = tmp_path / "group-info.sqlite3"
    commands = make_fluxer_commands(monkeypatch, database)
    store = Store(database)
    store.create_system("owner", "Crew")
    member = store.add_member("owner", "Alex", "[a]", "", pronouns="they/them")
    group = store.create_group("owner", "Friends", "pals")
    store.add_group_members("owner", [member.id])
    replies = []

    async def send(message, **kwargs):
        replies.append(message)

    ctx = SimpleNamespace(author=SimpleNamespace(id="owner"), send=send)
    await commands["info"](ctx, selector="pals")

    assert len(replies) == 1
    assert replies[0].startswith(f"**Friends**\nGroup ID: `{group.id}`")
    assert f"**Alex** (`{member.id}`)" in replies[0]
    assert "Pronouns: they/them" in replies[0]
    assert "Proxies: `[a]text`" in replies[0]


async def test_fluxer_info_includes_groups_with_a_system(tmp_path, monkeypatch):
    database = tmp_path / "system-info.sqlite3"
    commands = make_fluxer_commands(monkeypatch, database)
    store = Store(database)
    system_id = store.create_system("owner", "Crew")
    group = store.create_group("owner", "Friends", "pals")
    replies = []

    async def send(message, **kwargs):
        replies.append(message)

    ctx = SimpleNamespace(author=SimpleNamespace(id="owner"), send=send)
    await commands["info"](ctx, selector=system_id)

    assert len(replies) == 1
    assert replies[0].startswith("**Crew**")
    assert f"**Friends**\nGroup ID: `{group.id}`" in replies[0]
    assert "Members:\nNone" in replies[0]


async def test_fluxer_group_select_and_add_multiple_quoted_members(tmp_path, monkeypatch):
    database = tmp_path / "group-select.sqlite3"
    commands = make_fluxer_commands(monkeypatch, database)
    store = Store(database)
    store.create_system("owner", "Crew")
    kellin = store.add_member("owner", "Kellin Jaden", "[k]")
    varinn = store.add_member("owner", "Varinn Toft", "[v]")
    mereid = store.add_member("owner", "Mereid", "[m]")
    selected = store.create_group("owner", "Drakongaru", "dragon")
    store.create_group("owner", "Other", "other")
    replies = []

    async def send(message, **kwargs):
        replies.append(message)

    ctx = SimpleNamespace(author=SimpleNamespace(id="owner"), send=send)
    await commands["group"](ctx, "select", arguments="dragon")
    await commands["g"](
        ctx, "add", arguments='"Kellin Jaden" "Varinn Toft" Mereid'
    )

    assert store.active_group("owner") == selected
    assert {member.id for member in store.group_members("owner", selected.id)} == {
        kellin.id, varinn.id, mereid.id
    }
    assert replies == [
        f"Selected group **Drakongaru** (`{selected.id}`).",
        "Added 3 member(s) to **Drakongaru**.",
    ]


async def test_fluxer_group_select_reports_usage_and_unknown_group(tmp_path, monkeypatch):
    database = tmp_path / "group-select-errors.sqlite3"
    commands = make_fluxer_commands(monkeypatch, database)
    Store(database).create_system("owner", "Crew")
    replies = []

    async def send(message, **kwargs):
        replies.append(message)

    ctx = SimpleNamespace(author=SimpleNamespace(id="owner"), send=send)
    await commands["group"](ctx, "select")
    await commands["group"](ctx, "select", arguments="missing")

    assert replies == [
        "Usage: `p;group select GROUP`",
        "Group not found or not owned by this account.",
    ]


async def _append(values, value):
    values.append(value)

async def test_fluxer_sanitizes_voice_storage_errors(tmp_path, monkeypatch):
    from plurapack.voice_storage import StoredVoice, VoiceStorageError

    class FailedStorage:
        def put_voice(self, voice_id, audio): raise VoiceStorageError("Custom voice storage is unavailable.")
        def get_voice(self, voice_id): raise VoiceStorageError("Custom voice storage is unavailable.")
        def delete_voice(self, voice_id): raise VoiceStorageError("Custom voice storage is unavailable.")
        def exists(self, voice_id): raise VoiceStorageError("Custom voice storage is unavailable.")

    monkeypatch.setattr("plurapack.fluxer_bot.ForgejoVoiceStorage.configured", lambda: FailedStorage())
    monkeypatch.setattr("plurapack.fluxer_bot._download_voice_attachment", lambda attachment, maximum: __import__("tests.test_custom_voices", fromlist=["wav_bytes"]).wav_bytes())
    database = tmp_path / "voice-error.sqlite3"
    commands = make_fluxer_commands(monkeypatch, database)
    store = Store(database); store.create_system("owner", "System")
    member = store.add_member("owner", "Member", "m:")
    replies = []
    async def send(message, **kwargs): replies.append(message)
    ctx = SimpleNamespace(author=SimpleNamespace(id="owner"), send=send,
                          message=SimpleNamespace(attachments=[SimpleNamespace(url="https://invalid", filename="x.wav")]))
    await commands["voice"](ctx, arguments=f'upload {member.id} Normal')
    # VoiceStorageError is converted into a sanitized chat response.
    assert replies == ["Custom voice storage is unavailable."]
