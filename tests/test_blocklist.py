"""ブラックリスト（利用を断るサーバー）のテスト。Discord には接続しない。"""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

from yomi_kai.blocklist import GuildBlocklist, guilds_to_leave, parse_blocklist
from yomi_kai.config import Config
from yomi_kai.dictionary import DictionaryStore
from yomi_kai.main import YomiBot
from yomi_kai.settings import SettingsStore
from yomi_kai.tts import Speaker

BLOCKED = 111111111111111111
OTHER = 222222222222222222


def test_parse_ignores_comments_blank_lines_and_garbage(caplog):
    text = f"# ブロックするサーバー\n\n{BLOCKED}  # 荒らし\n  {OTHER}\nabc\n１２３\n{BLOCKED}\n"
    assert parse_blocklist(text) == {BLOCKED, OTHER}
    assert "abc" in caplog.text


def test_guilds_to_leave():
    assert guilds_to_leave([OTHER, BLOCKED, 3], frozenset({BLOCKED, 3})) == [BLOCKED, 3]
    assert guilds_to_leave([OTHER], frozenset()) == []


def test_missing_file_blocks_nothing(tmp_path):
    blocklist = GuildBlocklist(tmp_path / "blocked_guilds.txt")
    assert blocklist.reload_if_changed() == frozenset()
    assert not blocklist.is_blocked(BLOCKED)
    assert not blocklist.is_blocked(None)


def test_reloads_only_when_mtime_changes(tmp_path):
    path = tmp_path / "blocked_guilds.txt"
    path.write_text(f"{BLOCKED}\n", encoding="utf-8")
    blocklist = GuildBlocklist(path)
    assert blocklist.reload_if_changed() == {BLOCKED}
    assert blocklist.is_blocked(BLOCKED)

    # 更新時刻が同じなら読み直さない
    stat = path.stat()
    path.write_text(f"{BLOCKED}\n{OTHER}\n", encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert blocklist.reload_if_changed() == frozenset()
    assert not blocklist.is_blocked(OTHER)

    # 更新時刻が変われば読み直し、新たにブロックされたものを返す
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert blocklist.reload_if_changed() == {OTHER}
    assert blocklist.is_blocked(OTHER)

    # 外せばブロックされなくなる。ファイルを消せば何もブロックしない
    path.write_text(f"{OTHER}\n", encoding="utf-8")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2 * 10**9))
    assert blocklist.reload_if_changed() == frozenset()
    assert not blocklist.is_blocked(BLOCKED)
    path.unlink()
    blocklist.reload_if_changed()
    assert blocklist.blocked == frozenset()


# --- Bot の振る舞い ---


class StubEngine:
    @property
    def default_speaker(self):
        return "ずんだもん"

    def speakers(self):
        return [Speaker("ずんだもん", "stub")]

    def synthesize(self, text, speaker, speed=1.0):
        return b""


def make_bot(tmp_path):
    path = tmp_path / "blocked_guilds.txt"
    path.write_text(f"{BLOCKED}\n", encoding="utf-8")
    config = Config(
        discord_token="dummy",
        prefix="y.",
        afk_time=60,
        sd_msg="x",
        blocked_guilds_file=str(path),
        usage_dir=str(tmp_path / "usage"),
    )
    bot = YomiBot(config, StubEngine(), DictionaryStore(tmp_path / "dict"), SettingsStore(tmp_path / "settings"))
    bot.blocklist.reload_if_changed()
    return bot


def fake_guild(guild_id):
    return SimpleNamespace(id=guild_id, name=f"guild-{guild_id}", leave=AsyncMock())


def test_bot_leaves_blocked_guild_on_join(tmp_path):
    async def run():
        bot = make_bot(tmp_path)
        blocked, other = fake_guild(BLOCKED), fake_guild(OTHER)
        await bot.on_guild_join(blocked)
        await bot.on_guild_join(other)
        return blocked, other

    blocked, other = asyncio.run(run())
    blocked.leave.assert_awaited_once()
    other.leave.assert_not_awaited()


def test_enforce_closes_session_then_leaves(tmp_path):
    order = []

    class Session:
        is_connected = True

        async def close(self):
            order.append("close")

    async def run():
        bot = make_bot(tmp_path)
        blocked, other = fake_guild(BLOCKED), fake_guild(OTHER)
        blocked.leave.side_effect = lambda: order.append("leave")
        bot.get_cog = lambda name: SimpleNamespace(sessions={BLOCKED: Session()}) if name == "VoiceCog" else None
        await bot.enforce_blocklist([other, blocked])
        return other

    other = asyncio.run(run())
    assert order == ["close", "leave"]
    other.leave.assert_not_awaited()


def test_global_check_and_on_message_ignore_blocked_guilds(tmp_path):
    async def run():
        bot = make_bot(tmp_path)
        bot.process_commands = AsyncMock()
        blocked_ctx = SimpleNamespace(guild=fake_guild(BLOCKED))
        results = [
            await bot._not_blocked(blocked_ctx),
            await bot._not_blocked(SimpleNamespace(guild=fake_guild(OTHER))),
            await bot._not_blocked(SimpleNamespace(guild=None)),  # DM
            await bot.can_run(blocked_ctx),  # add_check で登録されている
        ]
        await bot.on_message(SimpleNamespace(guild=fake_guild(BLOCKED)))
        assert bot.process_commands.await_count == 0
        await bot.on_message(SimpleNamespace(guild=fake_guild(OTHER)))
        assert bot.process_commands.await_count == 1
        return results

    assert asyncio.run(run()) == [False, True, True, False]


def test_voice_cog_does_not_read_blocked_guild(tmp_path):
    from yomi_kai.cogs.voice import VoiceCog

    blocklist = GuildBlocklist(tmp_path / "blocked_guilds.txt")
    (tmp_path / "blocked_guilds.txt").write_text(f"{BLOCKED}\n", encoding="utf-8")
    blocklist.reload_if_changed()
    config = Config(discord_token="dummy", prefix="y.", afk_time=60, sd_msg="x")
    cog = VoiceCog(
        SimpleNamespace(),
        config,
        StubEngine(),
        DictionaryStore(tmp_path / "dict"),
        SettingsStore(tmp_path / "settings"),
        lambda wav: wav,
        blocklist=blocklist,
    )
    calls = []
    session = SimpleNamespace(
        is_connected=True, text_channel=SimpleNamespace(id=1), enqueue=lambda *a: calls.append(a) or True
    )
    cog.sessions[BLOCKED] = session
    message = SimpleNamespace(
        content="こんにちは",
        author=SimpleNamespace(bot=False, id=7),
        guild=SimpleNamespace(id=BLOCKED, voice_client=object()),
        channel=SimpleNamespace(id=1),
        attachments=[],
        stickers=[],
    )
    asyncio.run(cog.on_message(message))
    assert calls == []
