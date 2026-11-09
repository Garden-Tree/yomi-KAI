"""YomiBot の起動・終了まわりのテスト。Discord には接続しない。"""

import asyncio
import signal
from unittest.mock import AsyncMock

from test_commands import StubEngine

from yomi_kai.config import Config
from yomi_kai.dictionary import DictionaryStore
from yomi_kai.main import YomiBot
from yomi_kai.settings import SettingsStore


def make_bot(tmp_path):
    config = Config(discord_token="dummy", prefix="y.", afk_time=60, sd_msg="x")
    return YomiBot(config, StubEngine(), DictionaryStore(tmp_path / "dict"), SettingsStore(tmp_path / "settings"))


def test_bot_does_not_ping_by_default(tmp_path):
    mentions = make_bot(tmp_path).allowed_mentions
    assert mentions is not None
    assert not mentions.everyone and not mentions.users and not mentions.roles and not mentions.replied_user


class RecordingLoop:
    def __init__(self):
        self.handlers = {}

    def add_signal_handler(self, sig, callback):
        self.handlers[sig] = callback


class UnsupportedLoop:
    def add_signal_handler(self, sig, callback):
        raise NotImplementedError  # Windows のイベントループ


def test_sigterm_closes_the_bot_once(tmp_path):
    bot = make_bot(tmp_path)
    bot.close = AsyncMock()
    loop = RecordingLoop()
    assert bot.install_signal_handlers(loop)

    async def run():
        handler = loop.handlers[signal.SIGTERM]
        handler()
        handler()  # 2回受け取っても終了処理は1回
        await bot._close_task

    asyncio.run(run())
    assert bot.close.await_count == 1


def test_signal_handler_is_optional(tmp_path):
    assert not make_bot(tmp_path).install_signal_handlers(UnsupportedLoop())
