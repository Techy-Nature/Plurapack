from types import SimpleNamespace
import io
import wave

import pytest

from plurapack import bot
from plurapack.proxy import Incoming
from plurapack.storage import Store


def test_help_lists_every_command_with_usage_and_shortcut():
    pages = bot._help_pages("p;", limit=500)
    rendered = "\n".join(pages)

    assert len(pages) > 1
    assert all(len(page) <= 500 for page in pages)
    for name, (shortcut, usage, summary) in bot.COMMAND_HELP.items():
        assert f"p;{name}" in rendered
        assert f"(`{shortcut}`)" in rendered
        assert summary in rendered
        if usage:
            assert usage in rendered


def test_help_details_accept_command_shortcuts_and_compatibility_aliases():
    assert bot._help_pages("!", "m") == [
        "**member** — Add a member and proxy tag.\n"
        "Usage: `!member NAME PREFIX [SUFFIX] [--avatar URL|--a URL] [DESCRIPTION]`\nAliases: `!m`"
    ]
    assert bot._help_pages("p;", "view")[0].startswith("**viewinfo**")
    assert bot._help_pages("p;", "missing") == [
        "Unknown command `missing`. Use `p;help` to list every command."
    ]


def test_group_help_advertises_selection():
    detail = bot._help_pages("p;", "group")[0]
    assert "create|select|add|alias|avatar" in detail
    assert "Create, select, and edit the active group." in detail


async def test_stoat_group_select_changes_the_active_group(tmp_path):
    database = tmp_path / "stoat-group-select.sqlite3"
    client = bot.create_bot("p;", str(database))
    store = Store(database)
    store.create_system("owner", "Crew")
    first = store.create_group("owner", "Main Crew", "main")
    store.create_group("owner", "Work", "work")
    replies = []

    async def send(message):
        replies.append(message)

    ctx = SimpleNamespace(author=SimpleNamespace(id="owner"), send=send)
    await client.get_command("group").callback(ctx, "select", arguments="main")

    assert store.active_group("owner") == first
    assert replies == [f"Selected group **Main Crew** (`{first.id}`)."]


@pytest.mark.parametrize(
    ("suffix", "description", "expected"),
    [
        ("--avatar", "https://example.test/alex.png About Alex",
         ("", "About Alex", "https://example.test/alex.png")),
        ("--a", "https://example.test/alex.png", ("", "", "https://example.test/alex.png")),
        (":a", "--avatar https://example.test/alex.png About Alex",
         (":a", "About Alex", "https://example.test/alex.png")),
        ("", "About Alex", ("", "About Alex", None)),
    ],
)
def test_member_creation_avatar_options(suffix, description, expected):
    assert bot._member_creation_options(suffix, description) == expected


@pytest.mark.parametrize("value", ["--avatar", "--a relative/image.png"])
def test_member_creation_avatar_option_requires_complete_url(value):
    suffix, _, description = value.partition(" ")
    with pytest.raises(ValueError, match="avatar"):
        bot._member_creation_options(suffix, description)


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ("--member abc12 https://example.test/member.png",
         ("member", "abc12", "https://example.test/member.png")),
        ("-f 'Happy form' https://example.test/form.png",
         ("form", "Happy form", "https://example.test/form.png")),
        ("--s https://example.test/system.png",
         ("system", None, "https://example.test/system.png")),
    ],
)
def test_profile_image_options_accept_long_and_short_targets(arguments, expected):
    assert bot._profile_image_options(arguments) == expected


@pytest.mark.parametrize(
    "arguments", ["", "--member abc12", "--system abc12 extra", "--group x https://example.test/x.png"]
)
def test_profile_image_options_require_exactly_one_complete_target(arguments):
    with pytest.raises(ValueError):
        bot._profile_image_options(arguments)


def test_profile_images_update_members_forms_and_system(tmp_path):
    store = Store(tmp_path / "images.sqlite3")
    system_id = store.create_system("owner", "Crew")
    member = store.add_member("owner", "Alex", "A:")
    form = store.create_form("owner", member.id, "Happy")

    bot._update_profile_image(store, "owner", "avatar", f"-m {member.id} https://example.test/m.png")
    bot._update_profile_image(store, "owner", "banner", f"--form {form.id} https://example.test/f.png")
    bot._update_profile_image(store, "owner", "avatar", "--system https://example.test/s.png")

    assert store.member_selected("owner", member.id).avatar == "https://example.test/m.png"
    assert store.form_selected("owner", form.id)[0].banner == "https://example.test/f.png"
    assert store.system_info(system_id).logo == "https://example.test/s.png"


def test_bot_module_can_be_imported_without_stoat(monkeypatch):
    monkeypatch.setattr(bot.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(bot.StoatDependencyError, match="pip install -e"):
        bot.create_bot("p;", ":memory:")


def test_main_explains_how_to_install_stoat(monkeypatch):
    monkeypatch.setenv("STOAT_BOT_TOKEN", "test-token")
    monkeypatch.setattr(bot.importlib.util, "find_spec", lambda name: None)

    with pytest.raises(SystemExit, match="python -m pip install -e"):
        bot.main()


async def test_cli_reports_connected_and_ready_states(capsys):
    listeners = {}

    class FakeBot:
        def listen(self, event_type):
            def register(callback):
                listeners[event_type] = callback
                return callback

            return register

    sdk = SimpleNamespace(AfterConnectEvent=object(), ReadyEvent=object())
    bot._register_cli_status_listeners(FakeBot(), sdk, "p;")

    await listeners[sdk.AfterConnectEvent](SimpleNamespace())
    assert capsys.readouterr().out == "[Plurapack] Connected to Stoat.\n"

    await listeners[sdk.ReadyEvent](SimpleNamespace())
    assert capsys.readouterr().out == "[Plurapack] Ready to use. Command prefix: p;\n"


def test_main_reports_that_it_is_connecting(monkeypatch, capsys):
    running = SimpleNamespace(run=lambda token: None)
    monkeypatch.setenv("STOAT_BOT_TOKEN", "test-token")
    monkeypatch.setattr(bot, "create_bot", lambda prefix, database: running)

    bot.main()

    assert capsys.readouterr().out == "[Plurapack] Connecting to Stoat...\n"


def test_main_can_run_fluxer_without_stoat(monkeypatch, capsys):
    from plurapack import fluxer_bot

    tokens = []
    monkeypatch.delenv("STOAT_BOT_TOKEN", raising=False)
    monkeypatch.setenv("FLUXER_BOT_TOKEN", "fluxer-token")
    monkeypatch.setattr(
        fluxer_bot, "create_fluxer_bot", lambda prefix, database: SimpleNamespace(run=tokens.append)
    )

    bot.main()

    assert tokens == ["fluxer-token"]
    assert capsys.readouterr().out == "[Plurapack] Connecting to Fluxer...\n"


def test_main_runs_stoat_and_fluxer_together(monkeypatch, capsys):
    from plurapack import fluxer_bot

    started = []
    monkeypatch.setenv("STOAT_BOT_TOKEN", "stoat-token")
    monkeypatch.setenv("FLUXER_BOT_TOKEN", "fluxer-token")
    monkeypatch.setattr(
        bot, "create_bot", lambda prefix, database: SimpleNamespace(run=lambda token: started.append(token))
    )
    monkeypatch.setattr(
        fluxer_bot,
        "create_fluxer_bot",
        lambda prefix, database: SimpleNamespace(run=lambda token: started.append(token)),
    )

    bot.main()

    assert sorted(started) == ["fluxer-token", "stoat-token"]
    output = capsys.readouterr().out
    assert "[Plurapack] Connecting to Stoat...\n" in output
    assert "[Plurapack] Connecting to Fluxer...\n" in output


async def test_stoat_platform_applies_member_color_to_username():
    class Masquerade:
        def __init__(self, **kwargs):
            self.name = kwargs["name"]
            self.avatar = kwargs["avatar"]
            self.color = kwargs["color"]

    class Channel:
        async def send(self, content, *, masquerade):
            assert content == "hello"
            assert masquerade.color == "#7b68ee"
            return SimpleNamespace(id="proxy-id")

    source = SimpleNamespace(get_channel=lambda: Channel())
    platform = bot.StoatPlatform(
        {"source-id": source}, SimpleNamespace(), SimpleNamespace(MessageMasquerade=Masquerade)
    )
    incoming = Incoming("source-id", "channel-id", "owner-id", "[alex] hello")
    member = SimpleNamespace(name="Alex", avatar=None, color="#7b68ee")

    assert await platform.send_proxy(incoming, member, "hello") == "proxy-id"


async def test_stoat_platform_applies_form_name_and_avatar_to_masquerade():
    captured = None

    class Masquerade:
        def __init__(self, **kwargs):
            nonlocal captured
            captured = kwargs

    class Channel:
        async def send(self, content, *, masquerade):
            return SimpleNamespace(id="proxy-id")

    platform = bot.StoatPlatform(
        {"source-id": SimpleNamespace(get_channel=lambda: Channel())},
        SimpleNamespace(),
        SimpleNamespace(MessageMasquerade=Masquerade),
    )
    form_identity = SimpleNamespace(
        name="Alex at Sea", avatar="https://example.test/sea.png", color="#123456"
    )

    await platform.send_proxy(
        Incoming("source-id", "channel-id", "owner-id", "hello"), form_identity, "hello"
    )

    assert captured == {
        "name": "Alex at Sea",
        "avatar": "https://example.test/sea.png",
        "color": "#123456",
    }


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("M" * 27 + " " + "T" * 4, "M" * 27 + " " + "T" * 4),  # exactly 32
        ("M" * 27 + " " + "T" * 5, "M" * 27 + " " + "T" * 4),  # 33, tag shortened
        ("Member" * 6, ("Member" * 6)[:32]),
        ("Form name " * 5, ("Form name " * 5)[:32].rstrip()),
        ("M" * 29 + " 🪶CQD📗", "M" * 29 + " 🪶C"),
        ("M" * 28 + " 👩‍👩‍👧‍👧", "M" * 28),
    ],
)
async def test_stoat_masquerade_names_are_limited_without_splitting_unicode(name, expected):
    captured = []

    class Channel:
        async def send(self, content, *, masquerade):
            captured.append(masquerade.name)
            return SimpleNamespace(id=f"proxy-{len(captured)}")

    class HTTP:
        async def get_message(self, channel_id, proxy_id):
            return SimpleNamespace(content="original")

    channel = Channel()
    source = SimpleNamespace(get_channel=lambda: channel)
    state = SimpleNamespace(http=HTTP())
    platform = bot.StoatPlatform(
        {"source": source}, state, SimpleNamespace(MessageMasquerade=FakeMasquerade)
    )
    identity = SimpleNamespace(name=name, avatar=None, color=None)

    await platform.send_proxy(Incoming("source", "channel", "owner", "hello"), identity, "hello")
    await platform.send_reproxy(
        Incoming("source", "channel", "owner", "selector"), "old-proxy", identity
    )

    assert captured == [expected, expected]
    assert all(len(value) <= 32 for value in captured)


def test_stoat_name_limit_does_not_mutate_stored_member_form_or_system_tag(tmp_path):
    store = Store(tmp_path / "names.sqlite3")
    store.create_system("owner", "Crew")
    member_name = "Member with a deliberately very long name"
    tag = "🪶CQD📗"
    member = store.add_member("owner", member_name, "m:")
    form_name = "Form with a deliberately very long display name"
    form = store.create_form("owner", member.id, form_name, prefix="f:")
    store.configure_system_tag("owner", tag)

    assert store.member_selected("owner", member.id).name == member_name
    assert store.form_selected("owner", form.id)[0].display_name == form_name
    assert store.system_info(store.system_for("owner")).system_tag == tag
    assert bot._stoat_presentation_name(store.proxy_name(member)) == member_name[:32].rstrip()


def test_stoat_validation_failure_has_specific_user_message():
    error = RuntimeError("FailedValidation masquerade.name: Validation error: length max: 32")
    assert bot._stoat_proxy_failure_message(error) == (
        "Could not proxy: the Stoat display name exceeded the 32-character limit."
    )


class FakeStoatForbidden(Exception):
    def __init__(self, error_type):
        self.type = error_type
        super().__init__(error_type)


class FakeTextChannel:
    def __init__(self):
        self.sent = []

    async def send(self, content, *, masquerade):
        self.sent.append((content, masquerade))
        return SimpleNamespace(id="group-proxy")


class FakeGroupChannel(FakeTextChannel):
    pass


class FakeServerChannel(FakeTextChannel):
    pass


class FakeDMChannel(FakeTextChannel):
    pass


class FakeMasquerade:
    def __init__(self, **values):
        self.__dict__.update(values)


FAKE_STOAT = SimpleNamespace(
    GroupChannel=FakeGroupChannel,
    Forbidden=FakeStoatForbidden,
    MessageMasquerade=FakeMasquerade,
)


async def test_stoat_autoproxy_and_autofront_use_limited_presentation_names(tmp_path):
    store = Store(tmp_path / "autoproxy-names.sqlite3")
    store.create_system("owner", "Crew")
    member = store.add_member("owner", "Member " + "M" * 30, "m:")
    form = store.create_form("owner", member.id, "Form " + "F" * 35, prefix="f:")
    store.configure_system_tag("owner", "🪶CQD📗")
    store.configure_autoproxy("owner", member.id)
    channel = FakeServerChannel()

    async def delete():
        pass

    source = SimpleNamespace(get_channel=lambda: channel, delete=delete)
    platform = bot.StoatPlatform({"auto": source, "front": source}, SimpleNamespace(), FAKE_STOAT)
    service = bot.ProxyService(store, platform)

    await service.handle(Incoming("auto", "channel", "owner", "autoproxy"))
    store.switch_front("owner", form.id)
    store.configure_autofront("owner", True)
    await service.handle(Incoming("front", "channel", "owner", "autofront"))

    assert channel.sent[0][1].name == ("Member " + "M" * 30)[:32]
    assert channel.sent[1][1].name == ("Form " + "F" * 35)[:32]
    assert store.member_selected("owner", member.id).name == "Member " + "M" * 30
    assert store.form_selected("owner", form.id)[0].display_name == "Form " + "F" * 35


def test_stoat_group_detection_uses_sdk_model_not_missing_server_id():
    assert bot._is_group_channel(FakeGroupChannel(), FAKE_STOAT)
    assert not bot._is_group_channel(FakeDMChannel(), FAKE_STOAT)
    assert not bot._is_group_channel(FakeServerChannel(), FAKE_STOAT)


async def test_group_message_translation_reaches_shared_proxy_flow_and_preserves_reply(tmp_path):
    store = Store(tmp_path / "group.sqlite3")
    store.create_system("owner", "Crew")
    store.add_member(
        "owner", "Varinn", "[v]", avatar="https://example.test/v.png", color="#7b68ee"
    )
    channel = FakeGroupChannel()
    deleted = []

    async def delete():
        deleted.append("source")

    message = SimpleNamespace(
        id="source",
        channel_id="group",
        server_id=None,
        content="[v]Hello",
        replies=[SimpleNamespace(id="replied-to")],
        get_channel=lambda: channel,
        delete=delete,
    )
    incoming = bot._incoming_from_stoat_message(message, SimpleNamespace(id="owner", bot=False))
    platform = bot.StoatPlatform({"source": message}, SimpleNamespace(), FAKE_STOAT)

    assert incoming.reply_to_id == "replied-to"
    assert await bot.ProxyService(store, platform).handle(incoming) == "group-proxy"
    assert deleted == ["source"]
    assert channel.sent[0][0] == "Hello"
    assert channel.sent[0][1].name == "Varinn"
    assert channel.sent[0][1].avatar == "https://example.test/v.png"
    assert channel.sent[0][1].color is None
    assert store.proxy_owned_by("group-proxy", "owner", "group")


async def test_server_proxy_preserves_member_color(tmp_path):
    store = Store(tmp_path / "server-color.sqlite3")
    store.create_system("owner", "Crew")
    store.add_member("owner", "Varinn", "[v]", color="#7b68ee")
    channel = FakeServerChannel()

    async def delete():
        pass

    message = SimpleNamespace(get_channel=lambda: channel, delete=delete)
    platform = bot.StoatPlatform({"source": message}, SimpleNamespace(), FAKE_STOAT)

    assert await bot.ProxyService(store, platform).handle(
        Incoming("source", "server", "owner", "[v]Hello")
    ) == "group-proxy"
    assert channel.sent[0][1].color == "#7b68ee"


async def test_group_permission_denial_keeps_durable_masqueraded_proxy(tmp_path):
    store = Store(tmp_path / "denied.sqlite3")
    store.create_system("owner", "Crew")
    store.add_member("owner", "Varinn", "[v]")
    channel = FakeGroupChannel()

    async def delete():
        raise FakeStoatForbidden("MissingPermission")

    source = SimpleNamespace(get_channel=lambda: channel, delete=delete)
    platform = bot.StoatPlatform({"source": source}, SimpleNamespace(), FAKE_STOAT)
    incoming = Incoming("source", "group", "owner", "[v]Hello")

    assert await bot.ProxyService(store, platform).handle(incoming) == "group-proxy"
    assert "source" in platform.messages
    assert channel.sent[0][0] == "Hello"
    assert store.proxy_owned_by("group-proxy", "owner", "group")


@pytest.mark.parametrize("channel", [FakeGroupChannel(), FakeServerChannel()])
async def test_unexpected_stoat_source_deletion_failure_is_not_swallowed(channel, tmp_path):
    store = Store(tmp_path / f"unexpected-{type(channel).__name__}.sqlite3")
    store.create_system("owner", "Crew")
    store.add_member("owner", "Varinn", "[v]")

    async def delete():
        raise RuntimeError("network failure")

    source = SimpleNamespace(get_channel=lambda: channel, delete=delete)
    platform = bot.StoatPlatform({"source": source}, SimpleNamespace(), FAKE_STOAT)

    with pytest.raises(RuntimeError, match="network failure"):
        await bot.ProxyService(store, platform).handle(Incoming("source", "channel", "owner", "[v]Hi"))


async def test_server_permission_denial_remains_an_error():
    channel = FakeServerChannel()

    async def delete():
        raise FakeStoatForbidden("MissingPermission")

    platform = bot.StoatPlatform(
        {"source": SimpleNamespace(get_channel=lambda: channel, delete=delete)},
        SimpleNamespace(),
        FAKE_STOAT,
    )
    with pytest.raises(FakeStoatForbidden):
        await platform.delete_source(Incoming("source", "server-channel", "owner", "[v]Hi"))


async def test_unrelated_group_forbidden_remains_an_error():
    channel = FakeGroupChannel()

    async def delete():
        raise FakeStoatForbidden("Unknown")

    platform = bot.StoatPlatform(
        {"source": SimpleNamespace(get_channel=lambda: channel, delete=delete)},
        SimpleNamespace(),
        FAKE_STOAT,
    )
    with pytest.raises(FakeStoatForbidden, match="Unknown"):
        await platform.delete_source(Incoming("source", "group", "owner", "[v]Hi"))


async def test_server_source_deletion_still_succeeds():
    channel = FakeServerChannel()
    deleted = []

    async def delete():
        deleted.append("source")

    platform = bot.StoatPlatform(
        {"source": SimpleNamespace(get_channel=lambda: channel, delete=delete)},
        SimpleNamespace(),
        FAKE_STOAT,
    )

    assert await platform.delete_source(
        Incoming("source", "server-channel", "owner", "[v]Hi", server_id="server")
    )
    assert deleted == ["source"]
    assert "source" not in platform.messages


def test_group_message_translation_preserves_prefix_command_text_and_author():
    message = SimpleNamespace(
        id="command", channel_id="group", content="p;help", replies=[], server_id=None
    )
    incoming = bot._incoming_from_stoat_message(message, SimpleNamespace(id="owner", bot=False))

    assert incoming.content == "p;help"
    assert incoming.author_id == "owner"


def test_voice_upload_installs_wav_and_replaces_same_name(monkeypatch, tmp_path):
    def wav_bytes(frames):
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(24000)
            audio.writeframes(frames)
        return output.getvalue()

    payloads = iter([wav_bytes(b"\0\0"), wav_bytes(b"\1\0\2\0")])

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.close()

    monkeypatch.setattr(bot.urllib.request, "urlopen", lambda request, timeout: Response(next(payloads)))
    attachment = SimpleNamespace(filename="My Voice.wav", url="https://example.test/voice.wav")

    assert bot._install_voice_attachment(attachment, tmp_path) == "My-Voice.wav"
    first = (tmp_path / "My-Voice.wav").read_bytes()
    assert bot._install_voice_attachment(attachment, tmp_path) == "My-Voice.wav"
    assert (tmp_path / "My-Voice.wav").read_bytes() != first


def test_groups_create_add_and_update_active_group(tmp_path):
    store = Store(tmp_path / "groups.sqlite3")
    store.create_system("owner", "System")
    first = store.add_member("owner", "Alex", "[a]")
    second = store.add_member("owner", "Bea", "[b]")
    store.configure_alias("owner", first.id, "al")

    group = store.create_group("owner", "Friends at Work", "work")
    configured, members = store.add_group_members("owner", ["al", second.id])
    renamed = store.update_active_group("owner", alias="coworkers")
    pictured = store.update_active_group("owner", avatar="https://example.test/group.png")

    assert configured.id == group.id
    assert [member.id for member in members] == [first.id, second.id]
    assert renamed.alias == "coworkers"
    assert pictured.avatar == "https://example.test/group.png"


def test_voice_help_and_attachment_formats_are_consistent():
    assert "VOICE_NAME" in bot.COMMAND_HELP["voice"][1]
    assert "WAV/MP3" in bot.COMMAND_HELP["voice"][1]


async def test_stoat_speech_attachment_is_wav_and_preserves_bytes():
    from tests.test_custom_voices import wav_bytes
    sent = []

    async def send(**kwargs):
        sent.append(kwargs)

    channel = SimpleNamespace(send=send)
    platform = bot.StoatPlatform({}, SimpleNamespace(get_channel=lambda _: channel),
                                SimpleNamespace(Reply=lambda message_id: message_id))
    audio = wav_bytes()
    await platform.deliver_speech("channel", "proxy-id", audio)
    assert sent == [{"attachments": [("speech-proxy-id.wav", audio)], "replies": ["proxy-id"]}]
