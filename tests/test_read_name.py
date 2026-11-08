"""名前の読み上げ（read_name）のテスト。"""

import asyncio
import json

from test_voice_cog import FakeChannel, FakeEngine, FakeVoiceClient, make_cog, make_config, make_message, wait_until

from yomi_kai.settings import GuildSettings, SettingsStore
from yomi_kai.text import should_read_name, with_name

GUILD_ID = 1


def test_should_read_name_skips_consecutive_posts_within_window():
    assert should_read_name(None, 1, 100.0)
    assert not should_read_name((1, 100.0), 1, 129.0)
    assert should_read_name((1, 100.0), 1, 131.0)
    assert should_read_name((2, 100.0), 1, 101.0)


def test_with_name():
    assert with_name("こんにちは", "アリス") == "アリス、こんにちは"


def test_read_name_setting_default_off_and_saved(tmp_path):
    assert GuildSettings.from_json({}).read_name is False
    store = SettingsStore(tmp_path)
    store.set_read_name(GUILD_ID, True)
    assert store.get(GUILD_ID).read_name is True
    with open(store.path_for(GUILD_ID), encoding="UTF-8") as f:
        assert json.load(f)["read_name"] is True


def test_on_message_prefixes_name_after_truncation(tmp_path):
    engine = FakeEngine()
    cog = make_cog(tmp_path, make_config(max_read_length=3), engine)
    cog.settings.set_read_name(GUILD_ID, True)
    clock = [1000.0]
    cog._clock = lambda: clock[0]

    def message(content, user_id, name):
        m = make_message(content, user_id=user_id)
        m.author.display_name = name
        return m

    async def run():
        session = cog.get_session(GUILD_ID)
        await session.start(FakeVoiceClient(), FakeChannel())
        for content, user_id, name, at in [
            ("あいうえお", 7, "アリス", 1000.0),
            ("かき", 7, "アリス", 1010.0),  # 30秒以内の連投は名前なし
            ("さし", 8, "ボブ", 1011.0),
            ("たち", 8, "ボブ", 1050.0),  # 30秒を過ぎたら再び名前を読む
        ]:
            clock[0] = at
            await cog.on_message(message(content, user_id, name))
        await wait_until(lambda: len(engine.calls) >= 4 and not session.is_busy)
        await session.close()

    asyncio.run(run())
    assert engine.calls == ["アリス、あいう 以下略", "かき", "ボブ、さし", "ボブ、たち"]
