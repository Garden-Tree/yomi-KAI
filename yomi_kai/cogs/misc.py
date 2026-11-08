"""「突然の死」コマンド（sd）。``y.sd`` と ``/sd`` のどちらでも使える。"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..config import Config
from ..text import gen_sd_text
from .common import EMBED_COLOR, reply

logger = logging.getLogger(__name__)


class MiscCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config) -> None:
        self.bot = bot
        self.config = config

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        # 引数が多すぎる（例: y.sd a b）
        if isinstance(error, commands.UserInputError):
            await self._invalid(ctx)

    async def _invalid(self, ctx: commands.Context) -> None:
        await reply(ctx, f"コマンドが間違っています。`{self.config.prefix}sd help`を参照してください", private=True)

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
