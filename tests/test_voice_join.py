"""接続（y.c・自動接続）・切断・サーバーからの退出のテスト。Discord には接続しない。"""

import asyncio
from types import SimpleNamespace

import discord
from test_voice_cog import GUILD_ID, FakeEngine, FakeVoiceClient, make_cog, make_config

TEXT = 42
VC = 500


class FakeVoiceChannel:
    def __init__(self, *, connect=True, speak=True, error=None):
        self.id = VC
        self.name = "vc"
        self.members = []
        self._permissions = SimpleNamespace(connect=connect, speak=speak)
        self.error = error
        self.connects = 0

    def permissions_for(self, member):
        return self._permissions

    async def connect(self):
        self.connects += 1
        if self.error is not None:
            raise self.error
        return FakeVoiceClient()


class FakeTextChannel:
    id = TEXT
    name = "text"

    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


class FakeCtx:
    interaction = None

    def __init__(self, voice_channel, guild=None):
        self.author = SimpleNamespace(voice=SimpleNamespace(channel=voice_channel), mention="@alice", id=7)
        self.guild = guild or SimpleNamespace(id=GUILD_ID, me=object(), voice_client=None)
        self.channel = FakeTextChannel()
        self.sent = []
        self.deferred = False

    async def defer(self):
        self.deferred = True

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


def test_connect_starts_session_and_replies(tmp_path):
    cog = make_cog(tmp_path, make_config(), FakeEngine())
    ctx = FakeCtx(FakeVoiceChannel())

    async def run():
        await cog.connect.callback(cog, ctx)
        session = cog.sessions[GUILD_ID]
        assert session.is_connected and session.text_channel is ctx.channel
        await session.close()

    asyncio.run(run())
    assert ctx.deferred
    assert ctx.sent[0][0] == "話者を選んでください"
    assert ctx.sent[1][1]["embed"].title == "読み上げ開始"


def test_connect_without_permissions_explains_and_does_not_connect(tmp_path):
    cog = make_cog(tmp_path, make_config(), FakeEngine())
    voice_channel = FakeVoiceChannel(speak=False)
    ctx = FakeCtx(voice_channel)
    asyncio.run(cog.connect.callback(cog, ctx))
    assert voice_channel.connects == 0
    assert not ctx.deferred  # 本人にだけ見える返信にするため、応答を保留しない
    assert ctx.sent == [("vcに接続できません。Bot に「発言」の権限を付けてください", {"ephemeral": False})]
    assert GUILD_ID not in cog.sessions


def test_connect_failure_replies_instead_of_hanging(tmp_path):
    cog = make_cog(tmp_path, make_config(), FakeEngine())
    for error in (TimeoutError(), discord.ClientException("Already connected to a voice channel.")):
        ctx = FakeCtx(FakeVoiceChannel(error=error))
        asyncio.run(cog.connect.callback(cog, ctx))
        assert ctx.sent == [("vcに接続できませんでした。少し待ってからもう一度お試しください", {"ephemeral": False})]
        session = cog.sessions.get(GUILD_ID)
        assert session is None or not session.is_connected


def test_autojoin_skips_without_permissions_and_survives_connect_failure(tmp_path):
    cog = make_cog(tmp_path, make_config(), FakeEngine())
    cog.settings.set_autojoin(GUILD_ID, VC, TEXT)
    text_channel = FakeTextChannel()

    async def join(voice_channel):
        guild = SimpleNamespace(
            id=GUILD_ID, me=object(), voice_client=None, get_channel=lambda i: text_channel if i == TEXT else None
        )
        member = SimpleNamespace(id=7, bot=False, guild=guild, display_name="alice")
        return await cog._maybe_autojoin(member, SimpleNamespace(channel=None), SimpleNamespace(channel=voice_channel))

    no_permission = FakeVoiceChannel(connect=False)
    assert not asyncio.run(join(no_permission))
    assert no_permission.connects == 0
    failing = FakeVoiceChannel(error=TimeoutError())
    assert not asyncio.run(join(failing))
    assert failing.connects == 1
    assert text_channel.sent == []
    assert GUILD_ID not in cog._autojoining
