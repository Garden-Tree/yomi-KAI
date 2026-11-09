"""自動接続（y.autojoin）のテスト。"""

import asyncio
from types import SimpleNamespace

from test_voice_cog import FakeEngine, FakeVoiceClient, make_cog, make_config

from yomi_kai.cogs.admin import autojoin_to_remove
from yomi_kai.settings import MAX_AUTOJOIN, GuildSettings, SettingsStore
from yomi_kai.text import autojoin_allowed, autojoin_text_channel

GUILD_ID = 1
VC = 500
OTHER_VC = 501
TEXT = 42


def test_autojoin_text_channel_decision():
    mapping = {VC: TEXT}
    kwargs = {"member_is_bot": False, "before_id": None, "after_id": VC, "connected": False}
    assert autojoin_text_channel(mapping, **kwargs) == TEXT
    assert autojoin_text_channel(mapping, **{**kwargs, "before_id": OTHER_VC}) == TEXT  # 移動して入った
    assert autojoin_text_channel(mapping, **{**kwargs, "member_is_bot": True}) is None
    assert autojoin_text_channel(mapping, **{**kwargs, "connected": True}) is None
    assert autojoin_text_channel(mapping, **{**kwargs, "before_id": VC}) is None  # ミュートなど
    assert autojoin_text_channel(mapping, **{**kwargs, "after_id": OTHER_VC}) is None
    assert autojoin_text_channel(mapping, **{**kwargs, "after_id": None}) is None


def test_autojoin_cooldown():
    assert autojoin_allowed(None, 0.0)
    assert not autojoin_allowed(100.0, 129.9)
    assert autojoin_allowed(100.0, 130.0)


def test_autojoin_to_remove():
    assert autojoin_to_remove({VC: TEXT, OTHER_VC: TEXT}, VC) == VC
    assert autojoin_to_remove({VC: TEXT, OTHER_VC: TEXT}, None) is None
    assert autojoin_to_remove({VC: TEXT}, None) == VC
    assert autojoin_to_remove({VC: TEXT}, 999) == VC
    assert autojoin_to_remove({}, VC) is None


def test_store_autojoin(tmp_path):
    store = SettingsStore(tmp_path)
    assert store.set_autojoin(GUILD_ID, VC, TEXT) == "added"
    assert store.set_autojoin(GUILD_ID, VC, 43) == "updated"
    for i in range(MAX_AUTOJOIN - 1):
        assert store.set_autojoin(GUILD_ID, 600 + i, TEXT) == "added"
    assert store.set_autojoin(GUILD_ID, 999, TEXT) == "full"
    assert store.get(GUILD_ID).autojoin[VC] == 43
    assert store.remove_autojoin(GUILD_ID, VC)
    assert not store.remove_autojoin(GUILD_ID, VC)
    assert GuildSettings.from_json({"autojoin": {"500": "42"}}).autojoin == {VC: TEXT}


class FakeVoiceChannel:
    def __init__(self, channel_id):
        self.id = channel_id
        self.name = f"vc-{channel_id}"
        self.connects = 0

    def permissions_for(self, member):
        return SimpleNamespace(connect=True, speak=True)

    async def connect(self):
        self.connects += 1
        return FakeVoiceClient()


class FakeTextChannel:
    id = TEXT
    name = "text"

    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


async def join(cog, voice_channel, text_channel, *, member_bot=False):
    guild = SimpleNamespace(
        id=GUILD_ID, me=object(), voice_client=None, get_channel=lambda i: text_channel if i == TEXT else None
    )
    member = SimpleNamespace(id=7, bot=member_bot, guild=guild, display_name="alice")
    before, after = SimpleNamespace(channel=None), SimpleNamespace(channel=voice_channel)
    return await cog._maybe_autojoin(member, before, after)


def test_member_join_starts_session_and_posts_embed(tmp_path):
    cog = make_cog(tmp_path, make_config(), FakeEngine())
    cog.settings.set_autojoin(GUILD_ID, VC, TEXT)
    voice_channel, text_channel = FakeVoiceChannel(VC), FakeTextChannel()

    async def run():
        assert await join(cog, voice_channel, text_channel)
        session = cog.sessions[GUILD_ID]
        assert session.is_connected and session.text_channel is text_channel
        # 接続中は、ほかの人が入っても再接続しない
        assert not await join(cog, voice_channel, text_channel)
        await session.close()

    asyncio.run(run())
    assert voice_channel.connects == 1
    assert text_channel.sent[0][1]["embed"].title == "自動接続しました"


def test_no_autojoin_for_bots_or_unmapped_or_during_cooldown(tmp_path):
    cog = make_cog(tmp_path, make_config(), FakeEngine())
    cog.settings.set_autojoin(GUILD_ID, VC, TEXT)
    clock = [1000.0]
    cog._clock = lambda: clock[0]

    async def run():
        assert not await join(cog, FakeVoiceChannel(VC), FakeTextChannel(), member_bot=True)
        assert not await join(cog, FakeVoiceChannel(OTHER_VC), FakeTextChannel())
        cog._note_auto_disconnect(GUILD_ID)  # 自動切断した直後
        clock[0] = 1010.0
        assert not await join(cog, FakeVoiceChannel(VC), FakeTextChannel())
        clock[0] = 1031.0
        assert await join(cog, FakeVoiceChannel(VC), FakeTextChannel())
        await cog.sessions[GUILD_ID].close()

    asyncio.run(run())


def test_afk_close_starts_cooldown(tmp_path):
    cog = make_cog(tmp_path, make_config(), FakeEngine())
    cog._clock = lambda: 500.0
    session = cog.get_session(GUILD_ID)
    session.on_auto_close()
    assert cog._auto_disconnected_at[GUILD_ID] == 500.0
