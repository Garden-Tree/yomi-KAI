"""コマンド登録（hybrid コマンド・スラッシュコマンド）のテスト。Discord には接続しない。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from discord import app_commands
from discord.ext import commands

from yomi_kai.cogs.common import original_error, reply
from yomi_kai.config import Config
from yomi_kai.dictionary import DictionaryStore
from yomi_kai.main import YomiBot
from yomi_kai.settings import SettingsStore
from yomi_kai.tts import Speaker

TOP_LEVEL = {"help", "c", "dc", "s", "voice", "speed", "announce", "dict", "sd"}
GUILD_ONLY = {"c", "dc", "s", "voice", "speed", "announce", "dict"}


class StubEngine:
    @property
    def default_speaker(self):
        return "ずんだもん"

    def speakers(self):
        return [Speaker("ずんだもん", "stub"), Speaker("春日部つむぎ", "stub")]

    def synthesize(self, text, speaker, speed=1.0):
        return b""


@pytest.fixture(scope="module")
def setup_result(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("bot")
    config = Config(discord_token="dummy", prefix="y.", afk_time=60, sd_msg="突然の死")

    async def run():
        bot = YomiBot(config, StubEngine(), DictionaryStore(tmp_path / "dict"), SettingsStore(tmp_path / "settings"))
        bot.tree.sync = AsyncMock(return_value=[object()] * 9)
        await bot.setup_hook()
        result = {
            "prefix": {c.qualified_name: c for c in bot.walk_commands()},
            "app": {c.name: c for c in bot.tree.get_commands()},
            "sync_calls": bot.tree.sync.await_count,
        }
        await bot.close()
        return result

    return asyncio.run(run())


def test_prefix_commands_are_hybrid(setup_result):
    prefix = setup_result["prefix"]
    assert set(prefix) == TOP_LEVEL | {"dict add", "dict del", "dict list", "dict help"}
    for name, command in prefix.items():
        assert isinstance(command, (commands.HybridCommand, commands.HybridGroup)), name


def test_app_commands_registered_and_synced(setup_result):
    app = setup_result["app"]
    assert set(app) == TOP_LEVEL
    assert setup_result["sync_calls"] == 1
    assert {c.name for c in app["dict"].commands} == {"add", "del", "list", "help"}
    for name, command in app.items():
        assert command.description and command.description != "…", name
        assert name.isascii() and name == name.lower()


def test_guild_only_commands(setup_result):
    app = setup_result["app"]
    assert {name for name, c in app.items() if c.guild_only} == GUILD_ONLY


def test_parameters(setup_result):
    app = setup_result["app"]

    def params(name):
        return {p.name: p for p in app[name].parameters}

    skip_mode = params("s")["mode"]
    assert not skip_mode.required
    assert [c.value for c in skip_mode.choices] == ["all"]
    assert [c.value for c in params("announce")["state"].choices] == ["on", "off"]
    assert params("speed")["value"].type is discord.AppCommandOptionType.string
    assert not params("voice")["name"].required
    add = {p.name: p for p in app["dict"].get_command("add").parameters}
    assert add["word"].required and add["reading"].required
    for name in ("s", "announce", "speed", "voice", "sd"):
        for param in app[name].parameters:
            assert param.description and param.description != "…", (name, param.name)


# --- 共通ヘルパー ---


class FakeCtx:
    def __init__(self, interaction):
        self.interaction = interaction
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


def test_reply_is_ephemeral_only_for_slash():
    async def run():
        prefix_ctx, slash_ctx = FakeCtx(None), FakeCtx(object())
        await reply(prefix_ctx, "a", private=True)
        await reply(slash_ctx, "b", private=True)
        await reply(slash_ctx, "c")
        return prefix_ctx.sent, slash_ctx.sent

    prefix_sent, slash_sent = asyncio.run(run())
    assert prefix_sent == [("a", {"ephemeral": False})]
    assert slash_sent == [("b", {"ephemeral": True}), ("c", {"ephemeral": False})]


def test_original_error_unwraps_hybrid_and_prefix_errors():
    cause = OSError("disk full")
    assert original_error(commands.CommandInvokeError(cause)) is cause
    wrapped = commands.HybridCommandError(app_commands.CommandInvokeError(SimpleNamespace(name="voice"), cause))
    assert original_error(wrapped) is cause
    error = commands.BadArgument("x")
    assert original_error(error) is error
