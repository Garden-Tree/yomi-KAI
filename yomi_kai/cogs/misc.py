"""「突然の死」コマンド（sd）。"""

from __future__ import annotations

import logging

import discord
from discord.ext import commands

from ..config import Config
from ..text import gen_sd_text

logger = logging.getLogger(__name__)

EMBED_COLOR = 0x3399CC


class MiscCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config) -> None:
        self.bot = bot
        self.config = config

    @commands.command(name="sd")
    async def sudden_death(self, ctx: commands.Context, *args: str) -> None:
        prefix = self.config.prefix
        if len(args) == 0:
            await ctx.channel.send(gen_sd_text(self.config.sd_msg))
            return
        if args[0] == "help" and len(args) == 1:
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
            await ctx.send(embed=embed)
            logger.info("sd helpを表示")
            return
        if len(args) == 1:
            await ctx.channel.send(gen_sd_text(args[0]))
            return
        await ctx.channel.send(f"コマンドが間違っています。`{prefix}sd help`を参照してください")
