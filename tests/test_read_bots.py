"""ほかの Bot・Webhook の発言の読み上げ（read_bots）のテスト。"""

import asyncio
from types import SimpleNamespace

from test_voice_cog import FakeChannel, FakeEngine, FakeVoiceClient, make_cog, make_config, make_message, wait_until

from yomi_kai.settings import GuildSettings, should_read_author

GUILD_ID = 1
SELF_ID = 999


def test_should_read_author():
    def check(**kwargs):
        base = {"is_self": False, "is_bot": False, "is_webhook": False, "read_bots": False}
        return should_read_author(**{**base, **kwargs})

    assert check()
    assert not check(is_bot=True)
    assert not check(is_webhook=True)
    assert check(is_bot=True, read_bots=True)
    assert check(is_webhook=True, read_bots=True)
    assert not check(is_self=True, is_bot=True, read_bots=True)  # 自分の発言は読まない
    assert GuildSettings.from_json({}).read_bots is False


def run_messages(tmp_path, read_bots):
    engine = FakeEngine()
    cog = make_cog(tmp_path, make_config(), engine)
    cog.bot = SimpleNamespace(user=SimpleNamespace(id=SELF_ID))
    if read_bots:
        cog.settings.set_flag(GUILD_ID, "read_bots", True)

    def message(content, user_id, bot=False, webhook_id=None):
        m = make_message(content, user_id=user_id)
        m.author.bot = bot
        m.webhook_id = webhook_id
        return m

    async def run():
        session = cog.get_session(GUILD_ID)
        await session.start(FakeVoiceClient(), FakeChannel())
        await cog.on_message(message("自分", SELF_ID, bot=True))
        await cog.on_message(message("ほかのBot", 5, bot=True))
        await cog.on_message(message("ウェブフック", 6, bot=True, webhook_id=123))
        await cog.on_message(message("人間", 7))
        await wait_until(lambda: "人間" in engine.calls and not session.is_busy)
        await session.close()

    asyncio.run(run())
    return engine.calls


def test_bots_are_not_read_by_default(tmp_path):
    assert run_messages(tmp_path, read_bots=False) == ["人間"]


def test_bots_and_webhooks_are_read_when_enabled(tmp_path):
    assert run_messages(tmp_path, read_bots=True) == ["ほかのBot", "ウェブフック", "人間"]
