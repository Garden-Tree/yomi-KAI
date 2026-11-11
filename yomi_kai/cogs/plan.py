"""プランと上限の表示（plan）。``y.plan`` と ``/plan`` のどちらでも使える。

プレミアムについては PREMIUM_ENABLED = True のときだけ表示する（既定ではこのサーバーの上限だけを表示する）。
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..config import Config
from ..dictionary import DictionaryStore
from ..plans import Limits, PlanManager, format_limit, format_usage
from .common import EMBED_COLOR, guild_only_check, handle_command_error, invalid_command_notice, reply

logger = logging.getLogger(__name__)


def plan_name(limits: Limits) -> str:
    if not limits.is_premium:
        return "フリー"
    if limits.expires is None:
        return "プレミアム"
    return f"プレミアム（{limits.expires.isoformat()} まで）"


def premium_summary(limits: Limits, speakers: int) -> str:
    """プレミアムの上限の1行の説明（フリーのサーバーに案内する）。"""
    return (
        f"読み上げ {format_limit(limits.max_read_length, '文字')}・"
        f"サーバー辞書 {format_limit(limits.max_dict_entries, '件')}・"
        f"個人辞書 {format_limit(limits.max_user_dict_entries, '件')}・"
        f"話者 {speakers}種類"
    )


class PlanCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        config: Config,
        plans: PlanManager,
        store: DictionaryStore,
        user_store: object | None = None,
    ) -> None:
        self.bot = bot
        self.config = config
        self.plans = plans
        self.store = store
        # 個人辞書（UserDictionaryStore）。None なら個人の登録数は表示しない
        self.user_store = user_store

    async def cog_check(self, ctx: commands.Context) -> bool:
        return guild_only_check(ctx)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        await handle_command_error(ctx, error, invalid_notice=invalid_command_notice(self.config.prefix))

    @commands.hybrid_command(name="plan", description="このサーバーのプランと上限を表示します", ignore_extra=False)
    @app_commands.guild_only()
    async def plan(self, ctx: commands.Context) -> None:
        guild_id = ctx.guild.id
        limits = self.plans.limits_for(guild_id)
        _, speakers = self.plans.speakers_for(guild_id)
        dict_count = len((await self.store.aget(guild_id)).words)

        embed = discord.Embed(title="このサーバーの上限", color=EMBED_COLOR)
        if self.plans.enabled:
            embed.add_field(name="プラン", value=plan_name(limits), inline=False)
        embed.add_field(name="読み上げる文字数", value=format_limit(limits.max_read_length, "文字"), inline=True)
        embed.add_field(name="サーバー辞書", value=format_usage(dict_count, limits.max_dict_entries), inline=True)
        user_dict = format_limit(limits.max_user_dict_entries, "件")
        if self.user_store is not None:
            user_count = len(await self.user_store.awords(guild_id, ctx.author.id))
            user_dict = format_usage(user_count, limits.max_user_dict_entries)
        embed.add_field(name="個人辞書（あなた）", value=user_dict, inline=True)
        embed.add_field(name="話者", value=f"{len(speakers)}種類", inline=True)
        if self.plans.enabled and not limits.is_premium:
            premium_speakers = len(self.plans.premium_engine.speakers())
            embed.add_field(
                name="プレミアムでは", value=premium_summary(self.plans.premium, premium_speakers), inline=False
            )
        await reply(ctx, embed=embed)
        logger.info("planを表示 (guild=%s, %s)", guild_id, limits.plan)
