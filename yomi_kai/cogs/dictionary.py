"""辞書コマンド（dict add / del / list / help）。"""

from __future__ import annotations

import asyncio
import logging
import pprint

import discord
from discord.ext import commands

from ..config import Config
from ..dictionary import DictionaryStore

logger = logging.getLogger(__name__)

EMBED_COLOR = 0x3399CC
# Discordの2000文字制限を超えないように分割して送信
CHUNK_SIZE = 1900


class DictionaryCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config, store: DictionaryStore) -> None:
        self.bot = bot
        self.config = config
        self.store = store

    async def cog_check(self, ctx: commands.Context) -> bool:
        # サブコマンドにも確実に適用されるよう、Cog 単位でサーバー限定にする
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        return True

    async def _invalid(self, ctx: commands.Context) -> None:
        msg = f"コマンドが間違っています。`{self.config.prefix}dict help`を参照してください"
        await ctx.channel.send(msg)
        logger.info(msg)

    @commands.group(name="dict", invoke_without_command=True)
    async def dict_group(self, ctx: commands.Context, *args: str) -> None:
        # サブコマンドに一致しなかった場合（引数なしを含む）
        await self._invalid(ctx)

    @dict_group.command(name="add")
    async def dict_add(self, ctx: commands.Context, *args: str) -> None:
        if len(args) != 2:
            await self._invalid(ctx)
            return
        word, reading = args
        await asyncio.to_thread(self.store.add, ctx.guild.id, word, reading)
        embed = discord.Embed(title="辞書追加", color=EMBED_COLOR)
        embed.add_field(name="単語", value=f"{word}", inline=False)
        embed.add_field(name="読み", value=f"{reading}", inline=False)
        await ctx.send(embed=embed)
        logger.info("辞書に%sを%sとして追加しました", word, reading)

    @dict_group.command(name="del")
    async def dict_del(self, ctx: commands.Context, *args: str) -> None:
        if len(args) != 1:
            await self._invalid(ctx)
            return
        word = args[0]
        removed = await asyncio.to_thread(self.store.remove, ctx.guild.id, word)
        if not removed:
            await ctx.channel.send(f"`{word}`は辞書に登録されていません")
            logger.info("辞書に未登録の単語%sの削除が試みられました", word)
            return
        await ctx.channel.send(f"辞書から`{word}`を削除しました")
        logger.info("辞書から%sを削除しました", word)

    @dict_group.command(name="list")
    async def dict_list(self, ctx: commands.Context, *args: str) -> None:
        if args:
            await self._invalid(ctx)
            return
        words = (await self.store.aget(ctx.guild.id)).words
        await ctx.channel.send("辞書を表示します")
        logger.info("辞書を表示します")
        body = pprint.pformat(dict(words), depth=1)
        for i in range(0, len(body), CHUNK_SIZE):
            await ctx.channel.send("```" + body[i : i + CHUNK_SIZE] + "```")

    @dict_group.command(name="help")
    async def dict_help(self, ctx: commands.Context, *args: str) -> None:
        if args:
            await self._invalid(ctx)
            return
        prefix = self.config.prefix
        embed = discord.Embed(title="辞書機能ヘルプ", color=EMBED_COLOR)
        embed.add_field(
            name=f"{prefix}dict add `word` `yomi`", value="`word`を`yomi`と読むように辞書に追加します。", inline=False
        )
        embed.add_field(name=f"{prefix}dict del `word`", value="`word`を辞書から削除します。", inline=False)
        embed.add_field(name=f"{prefix}dict list", value="現在登録されている辞書を表示します。", inline=False)
        embed.add_field(name=f"{prefix}dict help", value="このヘルプを表示します。", inline=False)
        await ctx.send(embed=embed)
        logger.info("dict helpを表示")
