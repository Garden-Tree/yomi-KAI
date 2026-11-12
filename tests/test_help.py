"""ヘルプ（y.help / /help）のテスト。Discord には接続しない。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from discord.ext import commands

from yomi_kai.cogs.help import (
    ADMIN_MARK,
    HELP_ENTRIES,
    HELP_GROUPS,
    WEBSITE_URL,
    HelpCog,
    autocomplete_names,
    command_embed,
    normalize_query,
    overview_embed,
)
from yomi_kai.config import Config
from yomi_kai.dictionary import DictionaryStore
from yomi_kai.main import YomiBot
from yomi_kai.settings import SettingsStore
from yomi_kai.tts import Speaker

# 一覧の長さの目安（スマホで読める長さ。Discord の上限は 6000 文字）
MAX_OVERVIEW_LENGTH = 1500


class StubEngine:
    @property
    def default_speaker(self):
        return "ずんだもん"

    def speakers(self):
        return [Speaker("ずんだもん", "stub")]

    def synthesize(self, text, speaker, speed=1.0, pitch=0.0):
        return b""


def make_config(**kwargs):
    return Config(discord_token="dummy", prefix="y.", afk_time=60, sd_msg="x", **kwargs)


def registered_commands(tmp_path, config):
    async def run():
        bot = YomiBot(config, StubEngine(), DictionaryStore(tmp_path / "dict"), SettingsStore(tmp_path / "settings"))
        bot.tree.sync = AsyncMock(return_value=[])
        await bot.setup_hook()
        result = {c.name: c for c in bot.commands}
        await bot.close()
        return result

    return asyncio.run(run())


@pytest.fixture(scope="module")
def bot_commands(tmp_path_factory):
    return registered_commands(tmp_path_factory.mktemp("bot"), make_config())


class FakeCtx:
    def __init__(self, interaction=None):
        self.interaction = interaction
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


def make_cog(names):
    bot = SimpleNamespace(commands=[SimpleNamespace(name=name) for name in names])
    return HelpCog(bot, make_config())


def run_help(cog, command=None, interaction=None):
    ctx = FakeCtx(interaction)
    asyncio.run(HelpCog.help_command.callback(cog, ctx, command=command))
    return ctx.sent


def embed_text(embed):
    parts = [embed.title or "", embed.description or "", embed.footer.text or ""]
    for field in embed.fields:
        parts += [field.name, field.value]
    return "\n".join(parts)


# --- 登録されているコマンドとヘルプがずれていないこと ---
def test_every_registered_command_is_in_help(bot_commands):
    # コマンドを追加したら HELP_GROUPS にも追加する
    missing = set(bot_commands) - set(HELP_ENTRIES)
    assert not missing, f"ヘルプに載っていないコマンド: {missing}"


def test_help_has_no_unknown_commands(bot_commands):
    assert set(HELP_ENTRIES) - set(bot_commands) == set()


def test_each_command_is_in_one_group():
    names = [entry.name for _, entries in HELP_GROUPS for entry in entries]
    assert len(names) == len(set(names))


def test_every_subcommand_is_in_usage(bot_commands):
    for name, command in bot_commands.items():
        if not isinstance(command, commands.Group):
            continue
        usage = "\n".join(HELP_ENTRIES[name].usage)
        for sub in command.commands:
            assert f"{{p}}{sub.qualified_name}" in usage, sub.qualified_name


def needs_manage_guild(command):
    targets = [command, *getattr(command, "commands", [])]
    return any("has_guild_permissions" in getattr(check, "__qualname__", "") for c in targets for check in c.checks)


def test_admin_commands_are_marked(bot_commands):
    for name, command in bot_commands.items():
        if needs_manage_guild(command):
            assert HELP_ENTRIES[name].admin, name
    # y.read はコマンドの中で権限を確かめている
    assert HELP_ENTRIES["read"].admin


# --- y.help（一覧） ---
def test_overview_groups_and_links():
    names = set(HELP_ENTRIES)
    embed = overview_embed("y.", names)
    titles = [field.name for field in embed.fields]
    assert titles[:4] == ["はじめに", "自分の声", "サーバーの設定", "情報"]
    text = embed_text(embed)
    for name in names:
        assert f"`y.{name}`" in text
    assert WEBSITE_URL in text
    assert "`y.plan`" in text
    assert f"`y.readbots` ほかの Bot・Webhook の発言も読み上げるか{ADMIN_MARK}" in text
    assert "`y.voice` 自分の話者（声）を選ぶ\n" in text


def test_overview_is_short():
    embed = overview_embed("y.", set(HELP_ENTRIES))
    assert len(embed) <= MAX_OVERVIEW_LENGTH
    for field in embed.fields:
        assert len(field.value) <= 1024


@pytest.mark.parametrize("enabled", [True, False])
def test_sd_only_when_enabled(tmp_path, enabled):
    names = set(registered_commands(tmp_path, make_config(enable_sudden_death=enabled)))
    cog = make_cog(names)
    text = embed_text(run_help(cog)[0][1]["embed"])
    assert ("y.sd" in text) is enabled
    assert ("おまけ" in text) is enabled
    assert "y.credit" in text
    sent = run_help(cog, "sd")
    if enabled:
        assert sent[0][1]["embed"].title == "y.sd"
    else:
        assert sent[0][0].startswith("「sd」というコマンドはありません")
    assert ("sd" in autocomplete_names("", names)) is enabled


def test_overview_uses_prefix():
    text = embed_text(overview_embed("!", set(HELP_ENTRIES)))
    assert "`!c`" in text and "`!help コマンド名`" in text
    assert "y." not in text


# --- y.help コマンド名 ---
def test_command_help_shows_usage_and_examples():
    embed = command_embed(HELP_ENTRIES["voice"], "y.")
    fields = {field.name: field.value for field in embed.fields}
    assert embed.title == "y.voice"
    assert "`y.voice list`" in fields["使い方"]
    assert fields["例"] == "`y.voice ずんだもん`"
    assert "権限" not in fields
    assert "{p}" not in embed_text(embed)


def test_command_help_shows_permission():
    fields = {field.name: field.value for field in command_embed(HELP_ENTRIES["ignore"], "y.").fields}
    assert fields["権限"] == "登録・解除には「サーバー管理」の権限が必要です。"


@pytest.mark.parametrize("query", ["voice", "VOICE", "y.voice", "/voice", " voice list "])
def test_help_command_accepts_variants(query):
    sent = run_help(make_cog(HELP_ENTRIES), query)
    assert sent[0][1]["embed"].title == "y.voice"


def test_help_unknown_command_is_private_for_slash():
    sent = run_help(make_cog(HELP_ENTRIES), "foo", interaction=object())
    content, kwargs = sent[0]
    assert content == "「foo」というコマンドはありません。`y.help`で一覧を確認できます"
    assert kwargs["ephemeral"] is True


def test_help_without_argument_shows_overview():
    sent = run_help(make_cog(HELP_ENTRIES))
    assert sent[0][1]["embed"].title == "yomi-KAI の使い方"


def test_normalize_query():
    assert normalize_query("y.dict add", "y.") == "dict"
    assert normalize_query("  ", "y.") == ""
    assert normalize_query("Pitch", "y.") == "pitch"


def test_autocomplete_names():
    names = set(HELP_ENTRIES)
    assert autocomplete_names("rea", names)[:3] == ["read", "readname", "readbots"]
    # 前方一致を先に、部分一致を後に
    result = autocomplete_names("dict", names)
    assert result[0] == "dict" and "mydict" in result
    assert len(autocomplete_names("", names)) <= 25


def test_slash_help_has_autocomplete(bot_commands):
    param = {p.name: p for p in bot_commands["help"].app_command.parameters}["command"]
    assert not param.required
    assert param.autocomplete
