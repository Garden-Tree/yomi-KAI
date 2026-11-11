"""再起動後の自動再接続（yomi_kai.rejoin と VoiceCog）のテスト。"""

import asyncio
import json
from types import SimpleNamespace

from test_voice_cog import FakeEngine, FakeVoiceClient, make_cog, make_config

from yomi_kai.blocklist import GuildBlocklist
from yomi_kai.rejoin import VoiceSessionRegistry, parse_sessions, rejoin_skip_reason

GUILD_ID = 1
VC = 500
TEXT = 42


def test_registry_record_forget_and_load(tmp_path):
    registry = VoiceSessionRegistry(tmp_path / "voice_sessions.json")
    assert registry.load() == {}
    assert registry.record(1, VC, TEXT)
    assert not registry.record(1, VC, TEXT)  # 変わらなければ書かない
    assert registry.record(2, 600, 43)
    assert registry.record(1, 501, TEXT)  # 移動
    assert registry.load() == {1: (501, TEXT), 2: (600, 43)}
    assert json.loads((tmp_path / "voice_sessions.json").read_text(encoding="utf-8"))["2"] == {
        "voice": "600",
        "text": "43",
    }
    assert registry.forget(1)
    assert not registry.forget(1)
    assert VoiceSessionRegistry(tmp_path / "voice_sessions.json").load() == {2: (600, 43)}


def test_registry_handles_broken_file(tmp_path):
    path = tmp_path / "voice_sessions.json"
    path.write_text("{broken", encoding="utf-8")
    registry = VoiceSessionRegistry(path)
    assert registry.load() == {}
    assert registry.record(1, VC, TEXT)  # 作り直す
    assert registry.load() == {1: (VC, TEXT)}


def test_parse_sessions_skips_invalid_entries():
    data = {"1": {"voice": "500", "text": "42"}, "x": {"voice": "1", "text": "2"}, "2": {"voice": "a"}, "3": 5}
    assert parse_sessions(data) == {1: (500, 42)}


def test_rejoin_skip_reason():
    ok = {
        "guild_found": True,
        "voice_found": True,
        "text_found": True,
        "blocked": False,
        "connected": False,
        "members": 1,
        "missing_permissions": [],
    }
    assert rejoin_skip_reason(**ok) is None
    assert rejoin_skip_reason(**{**ok, "guild_found": False}) == "サーバーが見つかりません"
    assert rejoin_skip_reason(**{**ok, "blocked": True}) == "ブロックリストにあるサーバーです"
    assert rejoin_skip_reason(**{**ok, "connected": True}) == "すでに接続しています"
    assert rejoin_skip_reason(**{**ok, "text_found": False}) == "チャンネルが見つかりません"
    assert rejoin_skip_reason(**{**ok, "members": 0}) == "ボイスチャンネルに誰もいません"
    assert "接続" in rejoin_skip_reason(**{**ok, "missing_permissions": ["接続"]})


class FakeVoiceChannel:
    def __init__(self, channel_id=VC, members=(), connect=True, fail=False):
        self.id = channel_id
        self.name = f"vc-{channel_id}"
        self.members = list(members)
        self._connect = connect
        self.fail = fail
        self.connects = 0

    def permissions_for(self, member):
        return SimpleNamespace(connect=self._connect, speak=True)

    async def connect(self):
        self.connects += 1
        if self.fail:
            raise TimeoutError("voice connect timed out")
        return FakeVoiceClient()


class FakeTextChannel:
    id = TEXT
    name = "text"

    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


def make_guild(voice_channel, text_channel, guild_id=GUILD_ID):
    channels = {voice_channel.id: voice_channel, text_channel.id: text_channel}
    return SimpleNamespace(id=guild_id, me=object(), voice_client=None, get_channel=channels.get)


def human():
    return SimpleNamespace(bot=False)


def rejoin_cog(tmp_path, guilds, **kwargs):
    registry = VoiceSessionRegistry(tmp_path / "voice_sessions.json")
    cog = make_cog(tmp_path, make_config(), FakeEngine(), voice_sessions=registry, **kwargs)
    cog.bot = SimpleNamespace(user=None, get_guild={g.id: g for g in guilds}.get)
    return cog, registry


def test_join_records_and_leave_forgets(tmp_path):
    voice_channel, text_channel = FakeVoiceChannel(members=[human()]), FakeTextChannel()
    guild = make_guild(voice_channel, text_channel)
    cog, registry = rejoin_cog(tmp_path, [guild])

    async def run():
        async def announce(embed):
            return None

        assert await cog._join(guild, voice_channel, text_channel, "読み上げ開始", announce)
        assert registry.load() == {GUILD_ID: (VC, TEXT)}
        guild.voice_client = cog.sessions[GUILD_ID].voice_client
        await cog._leave(guild)  # y.dc・AFK・無人など通常の切断
        assert registry.load() == {}

    asyncio.run(run())


def test_shutdown_keeps_record(tmp_path):
    voice_channel, text_channel = FakeVoiceChannel(members=[human()]), FakeTextChannel()
    guild = make_guild(voice_channel, text_channel)
    cog, registry = rejoin_cog(tmp_path, [guild])

    async def run():
        async def announce(embed):
            return None

        await cog._join(guild, voice_channel, text_channel, "読み上げ開始", announce)
        await cog.cog_unload()  # Bot の終了（docker stop・デプロイ）
        await cog._forget_session(GUILD_ID)  # 終了中に届いたイベントでも消さない

    asyncio.run(run())
    assert registry.load() == {GUILD_ID: (VC, TEXT)}


def test_rejoin_connects_where_members_remain(tmp_path):
    occupied = FakeVoiceChannel(VC, members=[human(), SimpleNamespace(bot=True)])
    empty = FakeVoiceChannel(600, members=[SimpleNamespace(bot=True)])
    text1, text2 = FakeTextChannel(), FakeTextChannel()
    guild1, guild2 = make_guild(occupied, text1, 1), make_guild(empty, text2, 2)
    cog, registry = rejoin_cog(tmp_path, [guild1, guild2])
    registry.record(1, VC, TEXT)
    registry.record(2, 600, TEXT)
    registry.record(3, 700, TEXT)  # 退出したサーバー

    async def run():
        rejoined = await cog.rejoin_sessions()
        assert cog.sessions[1].is_connected and cog.sessions[1].text_channel is text1
        await cog.cog_unload()
        return rejoined

    assert asyncio.run(run()) == 1
    assert occupied.connects == 1 and empty.connects == 0
    assert text1.sent == []  # デプロイのたびに投稿しない
    assert registry.load() == {1: (VC, TEXT)}  # 再接続しなかったものは消す


def test_rejoin_skips_blocked_and_failed_and_missing_permissions(tmp_path):
    blocked_vc = FakeVoiceChannel(VC, members=[human()])
    failing_vc = FakeVoiceChannel(600, members=[human()], fail=True)
    no_perm_vc = FakeVoiceChannel(700, members=[human()], connect=False)
    guilds = [
        make_guild(blocked_vc, FakeTextChannel(), 1),
        make_guild(failing_vc, FakeTextChannel(), 2),
        make_guild(no_perm_vc, FakeTextChannel(), 3),
    ]
    (tmp_path / "blocked.txt").write_text("1\n", encoding="utf-8")
    blocklist = GuildBlocklist(tmp_path / "blocked.txt")
    blocklist.reload_if_changed()
    cog, registry = rejoin_cog(tmp_path, guilds, blocklist=blocklist)
    for guild_id, vc in ((1, VC), (2, 600), (3, 700)):
        registry.record(guild_id, vc, TEXT)

    assert asyncio.run(cog.rejoin_sessions()) == 0
    assert blocked_vc.connects == 0 and failing_vc.connects == 1 and no_perm_vc.connects == 0
    assert registry.load() == {}


def test_rejoin_does_not_forget_when_already_connected(tmp_path):
    voice_channel, text_channel = FakeVoiceChannel(members=[human()]), FakeTextChannel()
    guild = make_guild(voice_channel, text_channel)
    guild.voice_client = object()  # 自動接続などで先に接続していた
    cog, registry = rejoin_cog(tmp_path, [guild])
    registry.record(GUILD_ID, VC, TEXT)
    assert asyncio.run(cog.rejoin_sessions()) == 0
    assert voice_channel.connects == 0
    assert registry.load() == {GUILD_ID: (VC, TEXT)}


def test_on_ready_rejoins_only_once(tmp_path):
    cog, registry = rejoin_cog(tmp_path, [])
    calls = []

    async def fake_rejoin():
        calls.append(1)
        return 0

    cog.rejoin_sessions = fake_rejoin

    async def run():
        await cog.on_ready()
        await cog.on_ready()  # Gateway に接続し直したときにも呼ばれる
        await asyncio.sleep(0)

    asyncio.run(run())
    assert calls == [1]
