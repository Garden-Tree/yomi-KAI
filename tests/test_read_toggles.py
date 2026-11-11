"""サーバーごとの読み上げの切り替え（y.read url / emoji / code / spoiler）のテスト。"""

import asyncio
from types import SimpleNamespace

import pytest

from yomi_kai.cogs.common import MANAGE_GUILD_NOTICE
from yomi_kai.cogs.settings import SettingsCog, read_state_lines
from yomi_kai.config import Config
from yomi_kai.settings import GuildSettings, SettingsStore
from yomi_kai.text import CODE_BLOCK_TEXT, ReadOptions, build_read_text, compile_dictionary
from yomi_kai.tts import Speaker

MESSAGE = "見て https://example.com <:smile:1> ||犯人はヤス|| ```py\nprint('hi')\n```"


def test_defaults_keep_previous_behavior():
    assert build_read_text(MESSAGE) == f"見て URL smile ネタバレ {CODE_BLOCK_TEXT}"
    assert build_read_text(MESSAGE, options=ReadOptions()) == build_read_text(MESSAGE)


def test_url_off_skips_urls():
    assert build_read_text("見て https://example.com/a?b=c", options=ReadOptions(url=False)).strip() == "見て"


def test_emoji_off_skips_custom_emoji():
    out = build_read_text("いいね<:smile:123><a:party:456>", options=ReadOptions(emoji=False))
    assert out == "いいね"


def test_code_on_reads_content_without_language():
    options = ReadOptions(code=True)
    assert build_read_text("```py\nprint(1)\n```", options=options) == "print(1)"
    assert build_read_text("```hello```", options=options) == "hello"
    assert build_read_text("前```\na\nb\n```後", options=options) == "前a\nb後"
    assert build_read_text("`inline` だけ", options=options) == "inline だけ"


def test_spoiler_on_reads_content():
    options = ReadOptions(spoiler=True)
    assert build_read_text("犯人は||ヤス||です", options=options) == "犯人はヤスです"
    assert build_read_text("||一行目\n二行目||", options=options) == "一行目\n二行目"


def test_toggles_still_apply_dictionary_and_protect_mentions():
    d = compile_dictionary({"1": "いち", "ヤス": "やす"})
    options = ReadOptions(spoiler=True, code=True)
    out = build_read_text("<@1> ||ヤス|| ```1```", d, user_name={1: "太郎"}.get, options=options)
    assert out == "アット太郎 やす いち"


def test_settings_read_options_roundtrip(tmp_path):
    store = SettingsStore(tmp_path)
    assert store.get(1).read_options() == ReadOptions()
    store.set_flag(1, "read_url", False)
    store.set_flag(1, "read_spoiler", True)
    assert store.get(1).read_options() == ReadOptions(url=False, spoiler=True)
    with pytest.raises(ValueError):
        GuildSettings.from_json({"read_code": "yes"})


def test_read_state_lines_describe_defaults():
    assert read_state_lines(GuildSettings()) == [
        "URL: 「URL」と読む（on）",
        "サーバー絵文字: 名前を読む（on）",
        "コードブロック: 「コード省略」と読む（off）",
        "ネタバレ: 「ネタバレ」と読む（off）",
    ]


class Engine:
    @property
    def default_speaker(self):
        return "ずんだもん"

    def speakers(self):
        return [Speaker("ずんだもん", "fake")]

    def synthesize(self, text, speaker, speed=1.0, pitch=0.0):
        return b""


class FakeCtx:
    def __init__(self, manage_guild):
        self.interaction = None
        self.guild = SimpleNamespace(id=1)
        self.author = SimpleNamespace(id=7, guild_permissions=SimpleNamespace(manage_guild=manage_guild))
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


def test_read_command_requires_manage_guild_to_change(tmp_path):
    store = SettingsStore(tmp_path)
    config = Config(discord_token="d", prefix="y.", afk_time=60, sd_msg="x")
    cog = SettingsCog(SimpleNamespace(), config, Engine(), store)

    async def run(callback, manage_guild, state):
        ctx = FakeCtx(manage_guild)
        await callback(cog, ctx, state)
        return ctx.sent[0]

    assert asyncio.run(run(SettingsCog.read_url.callback, False, None)) == "URL: 「URL」と読む（on）"
    assert asyncio.run(run(SettingsCog.read_url.callback, False, "off")) == MANAGE_GUILD_NOTICE
    assert store.get(1).read_url is True
    assert asyncio.run(run(SettingsCog.read_url.callback, True, "off")) == "URL: 読まない（off）にしました"
    assert store.get(1).read_url is False
    assert asyncio.run(run(SettingsCog.read_spoiler.callback, True, "on")) == "ネタバレ: 中身を読む（on）にしました"
    assert store.get(1).read_options() == ReadOptions(url=False, spoiler=True)
