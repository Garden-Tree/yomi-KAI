"""辞書コマンド（dict add / del / list / export / import / help）。

hybrid グループなので ``y.dict add 単語 読み`` と ``/dict add`` のどちらでも同じ処理を行う。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import pprint

import discord
from discord import app_commands
from discord.ext import commands

from ..config import Config
from ..dictionary import DictionaryStore, InvalidEntryError, validate_entry
from .common import EMBED_COLOR, MANAGE_GUILD_NOTICE, original_error, reply

logger = logging.getLogger(__name__)

# Discordの2000文字制限を超えないように分割して送信
CHUNK_SIZE = 1900


# 読み込める辞書ファイルの大きさの上限
MAX_IMPORT_BYTES = 100 * 1024


def dict_full_notice(limit: int) -> str:
    return f"辞書の登録数が上限（{limit}件）に達しています。不要な単語を削除してください"


def export_dictionary(words: dict[str, str]) -> bytes:
    """辞書ファイルと同じ形式（{"単語": "読み"}、indent=2, ensure_ascii=False）の JSON。"""
    return json.dumps(words, indent=2, ensure_ascii=False).encode("utf-8")


def parse_dictionary_import(data: bytes) -> dict[str, str]:
    """読み込む辞書ファイルを検証する。不正なら理由を添えて ValueError。"""
    if len(data) > MAX_IMPORT_BYTES:
        raise ValueError(f"ファイルが大きすぎます（{MAX_IMPORT_BYTES // 1024}KB まで）")
    try:
        parsed = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ValueError("JSON として読み込めません") from None
    if not isinstance(parsed, dict):
        raise ValueError('{"単語": "読み", ...} の形式で書いてください')
    entries: dict[str, str] = {}
    for word, reading in parsed.items():
        if not isinstance(reading, str):
            raise ValueError(f"「{word}」の読みが正しくありません（読みは文字列にしてください）")
        validate_entry(word, reading)  # 空・長すぎる単語や読み（InvalidEntryError は ValueError）
        entries[word] = reading
    return entries


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

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(original_error(error), commands.MissingPermissions):
            await reply(ctx, MANAGE_GUILD_NOTICE, private=True)
            return
        # 引数の数が合わない（例: y.dict add 単語、y.dict import でファイルを添付していない）
        if isinstance(error, commands.UserInputError):
            await self._invalid(ctx)

    async def _invalid(self, ctx: commands.Context) -> None:
        msg = f"コマンドが間違っています。`{self.config.prefix}dict help`を参照してください"
        await reply(ctx, msg, private=True)
        logger.info(msg)

    @commands.hybrid_group(name="dict", invoke_without_command=True, description="サーバーの読み上げ辞書を操作します")
    @commands.guild_only()
    async def dict_group(self, ctx: commands.Context, *args: str) -> None:
        # サブコマンドに一致しなかった場合（引数なしを含む）。スラッシュでは直接呼ばれない
        await self._invalid(ctx)

    @dict_group.command(name="add", description="単語の読み方を辞書に登録します", ignore_extra=False)
    @app_commands.describe(word="単語（50文字まで）", reading="読み方（100文字まで）")
    async def dict_add(self, ctx: commands.Context, word: str, reading: str) -> None:
        limit = self.config.max_dict_entries
        try:
            added = await asyncio.to_thread(self.store.add, ctx.guild.id, word, reading, max_entries=limit)
        except InvalidEntryError as e:
            await reply(ctx, f"辞書に登録できません: {e}", private=True)
            logger.info("辞書に登録できない単語・読みのため追加しませんでした (guild=%s): %s", ctx.guild.id, e)
            return
        if not added:
            await reply(ctx, dict_full_notice(limit), private=True)
            logger.info("辞書の登録数が上限（%d件）のため追加しませんでした (guild=%s)", limit, ctx.guild.id)
            return
        embed = discord.Embed(title="辞書追加", color=EMBED_COLOR)
        embed.add_field(name="単語", value=f"{word}", inline=False)
        embed.add_field(name="読み", value=f"{reading}", inline=False)
        await reply(ctx, embed=embed)
        logger.info("辞書に%sを%sとして追加しました", word, reading)

    @dict_group.command(name="del", description="単語を辞書から削除します", ignore_extra=False)
    @app_commands.describe(word="削除する単語")
    async def dict_del(self, ctx: commands.Context, word: str) -> None:
        removed = await asyncio.to_thread(self.store.remove, ctx.guild.id, word)
        if not removed:
            await reply(ctx, f"`{word}`は辞書に登録されていません")
            logger.info("辞書に未登録の単語%sの削除が試みられました", word)
            return
        await reply(ctx, f"辞書から`{word}`を削除しました")
        logger.info("辞書から%sを削除しました", word)

    @dict_group.command(name="list", description="登録されている辞書を表示します", ignore_extra=False)
    async def dict_list(self, ctx: commands.Context) -> None:
        words = (await self.store.aget(ctx.guild.id)).words
        await reply(ctx, "辞書を表示します")
        logger.info("辞書を表示します")
        body = pprint.pformat(dict(words), depth=1)
        for i in range(0, len(body), CHUNK_SIZE):
            await reply(ctx, "```" + body[i : i + CHUNK_SIZE] + "```")

    @dict_group.command(name="export", description="辞書を JSON ファイルで書き出します", ignore_extra=False)
    async def dict_export(self, ctx: commands.Context) -> None:
        words = dict((await self.store.aget(ctx.guild.id)).words)
        file = discord.File(io.BytesIO(export_dictionary(words)), filename=f"dict-{ctx.guild.id}.json")
        await reply(ctx, f"辞書を書き出しました（{len(words)}件）", file=file)
        logger.info("辞書を書き出しました (guild=%s, %d件)", ctx.guild.id, len(words))

    @dict_group.command(
        name="import", description="JSON ファイルの辞書を読み込みます（同じ単語はファイルの読みで上書き）"
    )
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.describe(attachment="dict export で書き出した形式の JSON ファイル（100KB まで）")
    async def dict_import(self, ctx: commands.Context, attachment: discord.Attachment) -> None:
        if attachment.size > MAX_IMPORT_BYTES:
            await reply(ctx, f"ファイルが大きすぎます（{MAX_IMPORT_BYTES // 1024}KB まで）", private=True)
            return
        await ctx.defer()
        try:
            entries = parse_dictionary_import(await attachment.read())
        except ValueError as e:
            await reply(ctx, f"辞書を読み込めませんでした: {e}", private=True)
            return
        limit = self.config.max_dict_entries
        added, updated, skipped = await asyncio.to_thread(self.store.merge, ctx.guild.id, entries, max_entries=limit)
        message = f"辞書を読み込みました（追加 {added}件、更新 {updated}件）"
        if skipped:
            message += f"。登録数の上限（{limit}件）のため {skipped}件は追加しませんでした"
        await reply(ctx, message)
        logger.info(
            "辞書を読み込みました (guild=%s, 追加%d件, 更新%d件, 上限で除外%d件)", ctx.guild.id, added, updated, skipped
        )

    @dict_group.command(name="help", description="辞書機能のヘルプを表示します", ignore_extra=False)
    async def dict_help(self, ctx: commands.Context) -> None:
        prefix = self.config.prefix
        embed = discord.Embed(title="辞書機能ヘルプ", color=EMBED_COLOR)
        embed.add_field(
            name=f"{prefix}dict add `word` `yomi`", value="`word`を`yomi`と読むように辞書に追加します。", inline=False
        )
        embed.add_field(name=f"{prefix}dict del `word`", value="`word`を辞書から削除します。", inline=False)
        embed.add_field(name=f"{prefix}dict list", value="現在登録されている辞書を表示します。", inline=False)
        embed.add_field(name=f"{prefix}dict export", value="辞書を JSON ファイルで書き出します。", inline=False)
        embed.add_field(
            name=f"{prefix}dict import",
            value="添付した JSON ファイルの辞書を読み込みます（サーバー管理の権限が必要）。",
            inline=False,
        )
        embed.add_field(name=f"{prefix}dict help", value="このヘルプを表示します。", inline=False)
        await reply(ctx, embed=embed)
        logger.info("dict helpを表示")
