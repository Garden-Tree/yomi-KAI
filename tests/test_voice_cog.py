"""VoiceCog.on_message（読み上げの受け付け）のテスト。Discord には接続しない。"""

import asyncio
import json
import logging
import time
from datetime import datetime
from types import SimpleNamespace

import pytest

from yomi_kai.cogs.voice import VoiceCog
from yomi_kai.config import Config
from yomi_kai.dictionary import DictionaryStore
from yomi_kai.logging_setup import PAYLOAD_LOGGERS, set_text_logging
from yomi_kai.settings import SettingsStore
from yomi_kai.text import TRUNCATED_SUFFIX
from yomi_kai.tts import Speaker
from yomi_kai.usage import UsageStore

SECRET = "ひみつのほんぶんです。二文目もあります！"
GUILD_ID = 1
CHANNEL_ID = 42
USER_ID = 7


class FakeEngine:
    def __init__(self):
        self.calls = []

    @property
    def default_speaker(self):
        return "ずんだもん"

    def speakers(self):
        return [Speaker("ずんだもん", "fake")]

    def synthesize(self, text, speaker, speed=1.0):
        self.calls.append(text)
        return b"wav"


class FakeVoiceClient:
    def play(self, source, *, after=None):
        if after is not None:
            after(None)

    def is_playing(self):
        return False

    def stop(self):
        pass

    async def disconnect(self, *, force=False):
        pass


class FakeChannel:
    id = CHANNEL_ID

    async def send(self, content):
        pass


class CapturingHandler(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def make_config(**kwargs):
    return Config(discord_token="dummy", prefix="y.", afk_time=60, sd_msg="突然の死", **kwargs)


def make_message(content, guild_id=GUILD_ID, user_id=USER_ID):
    guild = SimpleNamespace(
        id=guild_id,
        voice_client=object(),
        get_member=lambda i: None,
        get_role=lambda i: None,
        get_channel=lambda i: None,
    )
    return SimpleNamespace(
        content=content,
        author=SimpleNamespace(bot=False, id=user_id),
        guild=guild,
        channel=SimpleNamespace(id=CHANNEL_ID),
        attachments=[],
        stickers=[],
    )


def make_cog(tmp_path, config, engine, **kwargs):
    bot = SimpleNamespace(user=None)
    return VoiceCog(
        bot,
        config,
        engine,
        DictionaryStore(tmp_path / "dict"),
        SettingsStore(tmp_path / "settings"),
        lambda wav: wav,
        **kwargs,
    )


async def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        await asyncio.sleep(0.005)


async def read_message(cog, engine, content):
    """接続済みのセッションに1件読み上げさせ、合成が終わるまで待つ。"""
    session = cog.get_session(GUILD_ID)
    await session.start(FakeVoiceClient(), FakeChannel())
    before = len(engine.calls)
    await cog.on_message(make_message(content))
    await wait_until(lambda: len(engine.calls) > before and not session.is_busy)
    await session.close()


@pytest.fixture
def captured_logs():
    handler = CapturingHandler()
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield handler.messages
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


def test_read_text_is_not_logged_by_default(tmp_path, captured_logs):
    config = make_config()
    assert config.log_read_text is False
    engine = FakeEngine()
    cog = make_cog(tmp_path, config, engine)

    asyncio.run(read_message(cog, engine, SECRET))

    assert engine.calls, "読み上げが行われていない"
    assert any("読み上げキューに追加しました" in m for m in captured_logs)  # ログ自体は出ている
    for fragment in (SECRET, "ひみつ", "二文目"):
        assert all(fragment not in m for m in captured_logs), fragment


def test_read_text_is_logged_when_enabled(tmp_path, captured_logs):
    engine = FakeEngine()
    cog = make_cog(tmp_path, make_config(log_read_text=True), engine)
    asyncio.run(read_message(cog, engine, SECRET))
    assert any(SECRET in m for m in captured_logs)


def test_accepted_message_is_metered_after_truncation(tmp_path):
    engine = FakeEngine()
    usage = UsageStore(tmp_path / "usage", clock=lambda: datetime(2026, 11, 9))
    cog = make_cog(tmp_path, make_config(max_read_length=5), engine, usage=usage)
    asyncio.run(read_message(cog, engine, "あいうえおかきくけこ"))
    usage.flush()
    with open(tmp_path / "usage" / "2026-11.json", encoding="UTF-8") as f:
        guild = json.load(f)["guilds"][str(GUILD_ID)]
    expected = len("あいうえお" + TRUNCATED_SUFFIX)
    assert guild["chars"] == expected and guild["messages"] == 1
    assert guild["users"] == {str(USER_ID): {"chars": expected, "messages": 1}}


def test_set_text_logging_silences_discord_payload_logs():
    try:
        set_text_logging(False)
        for name in PAYLOAD_LOGGERS:
            assert not logging.getLogger(name).isEnabledFor(logging.DEBUG), name
        set_text_logging(True)
        for name in PAYLOAD_LOGGERS:
            assert logging.getLogger(name).level == logging.NOTSET, name
    finally:
        set_text_logging(True)
