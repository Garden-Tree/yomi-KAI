"""「突然の死」コマンド（sd）と、音声のクレジット表記（credit）。``y.sd`` と ``/sd`` のどちらでも使える。

sd は読み上げと関係ない機能なので、config.ini の ``ENABLE_SUDDEN_DEATH = False`` で登録しないようにできる
（:class:`SuddenDeathCog` を追加しない）。credit は常に使える。
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

import discord
from discord import app_commands
from discord.ext import commands

from ..config import Config
from ..text import chunk_lines, gen_sd_text
from ..tts import Speaker, TTSEngine
from .common import EMBED_COLOR, handle_command_error, invalid_command_notice, reply

logger = logging.getLogger(__name__)

VOICEVOX_TERMS_URL = "https://voicevox.hiroshiba.jp/term/"


def credit_lines(speakers: Iterable[Speaker], use_google_tts: bool) -> list[str]:
    """使える話者のクレジット表記（キャラクターごとに1行、話者の並び順）と、利用規約の案内。"""
    credits = list(dict.fromkeys(s.credit for s in speakers if s.credit))
    lines = ["この Bot の読み上げ音声のクレジット:", *credits]
    if credits:
        lines.append(f"VOICEVOX の利用規約・各キャラクターの利用規約: {VOICEVOX_TERMS_URL}")
    if use_google_tts:
        lines.append("Google Cloud Text-to-Speech も使用しています。")
    return lines


class SuddenDeathCog(commands.Cog):
    """「突然の死」コマンド（sd）。ENABLE_SUDDEN_DEATH = False なら Bot に追加しない。"""

    def __init__(self, bot: commands.Bot, config: Config) -> None:
        self.bot = bot
        self.config = config

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        # 引数が多すぎる（例: y.sd a b）
        await handle_command_error(ctx, error, invalid_notice=invalid_command_notice(self.config.prefix, "sd help"))

    @commands.hybrid_command(name="sd", description="「突然の死」風のテキストを出力します", ignore_extra=False)
    @app_commands.describe(word="囲む言葉（省略すると既定のメッセージ、help でヘルプを表示）")
    async def sudden_death(self, ctx: commands.Context, word: str | None = None) -> None:
        prefix = self.config.prefix
        if word is None:
            await reply(ctx, gen_sd_text(self.config.sd_msg))
            return
        if word == "help":
            embed = discord.Embed(title="「突然の死」機能ヘルプ", color=EMBED_COLOR)
            embed.add_field(
                name=f"{prefix}sd", value=f"{gen_sd_text(self.config.sd_msg)}\nと出力します。", inline=False
            )
            embed.add_field(
                name=f"{prefix}sd `word`",
                value="＿人人人人＿\n＞　word　＜\n￣Y^Y^Y^Y￣\nのように出力します。複数行には対応していません。",
                inline=False,
            )
            embed.add_field(name=f"{prefix}sd help", value="このヘルプを表示します。", inline=False)
            await reply(ctx, embed=embed)
            logger.info("sd helpを表示")
            return
        await reply(ctx, gen_sd_text(word))


class MiscCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config, engine: TTSEngine) -> None:
        self.bot = bot
        self.config = config
        self.engine = engine

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        # 引数が多すぎる（例: y.credit a）
        await handle_command_error(ctx, error, invalid_notice=invalid_command_notice(self.config.prefix))

    @commands.hybrid_command(
        name="credit",
        description="読み上げ音声のクレジット（VOICEVOX:ずんだもん など）を表示します",
        ignore_extra=False,
    )
    async def credit(self, ctx: commands.Context) -> None:
        for chunk in chunk_lines(credit_lines(self.engine.speakers(), self.config.use_google_tts)):
            await reply(ctx, chunk)
        logger.info("creditを表示")
