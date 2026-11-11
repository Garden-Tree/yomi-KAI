"""プランと上限の表示（plan）。``y.plan`` と ``/plan`` のどちらでも使える。

プレミアムについては PREMIUM_ENABLED = True のときだけ表示する（既定ではこのサーバーの上限だけを表示する）。

PREMIUM_ENABLED = True で PREMIUM_SITE_URL / PREMIUM_LINK_SECRET を設定していると、「サーバー管理」の権限を
持つ人にだけ、申し込み・契約の管理のページへの署名付きリンク（yomi_kai.premium_link）を本人にだけ見える形で渡す
（スラッシュコマンドでは ephemeral、プレフィックスのコマンドでは DM。チャンネルには出さない）。
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..config import Config
from ..dictionary import DictionaryStore
from ..plans import Limits, PlanManager, format_limit, format_usage
from ..premium_link import LINK_TTL, premium_url
from .common import EMBED_COLOR, guild_only_check, handle_command_error, invalid_command_notice, is_slash, reply

logger = logging.getLogger(__name__)

PREMIUM_LINK_LABEL = "プレミアムに申し込む / 契約を管理"
DM_SENT_NOTICE = "プレミアムの申し込み・契約の管理のリンクを DM で送りました"
DM_FAILED_NOTICE = "DM を送れませんでした。`/plan` を使うと、リンクをあなたにだけ見える形で表示します"


def premium_link_message(url: str, ttl: int = LINK_TTL) -> str:
    """管理者にだけ渡すリンクの文面。"""
    return (
        f"**{PREMIUM_LINK_LABEL}**\n{url}\n"
        f"このリンクはこのサーバー専用で、{ttl // 60}分間有効です。ほかの人には共有しないでください"
        "（期限が切れたら、もう一度 plan を使ってください）。"
    )


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
        await self._send_premium_link(ctx)

    def premium_link_enabled(self) -> bool:
        return bool(self.plans.enabled and self.config.premium_site_url and self.config.premium_link_secret)

    async def _send_premium_link(self, ctx: commands.Context) -> None:
        """「サーバー管理」の権限を持つ人にだけ、申し込み・契約の管理のリンクを本人にだけ見える形で渡す。"""
        if not self.premium_link_enabled():
            return
        permissions = getattr(ctx.author, "guild_permissions", None)
        if permissions is None or not permissions.manage_guild:
            return
        url = premium_url(self.config.premium_site_url or "", self.config.premium_link_secret or "", ctx.guild.id)
        message = premium_link_message(url)
        if is_slash(ctx):
            await ctx.send(message, ephemeral=True, suppress_embeds=True)
        else:
            # プレフィックスのコマンドの返信はチャンネルの全員に見えるので、リンクは DM で送る
            try:
                await ctx.author.send(message, suppress_embeds=True)
            except discord.HTTPException:
                await reply(ctx, DM_FAILED_NOTICE)
                return
            await reply(ctx, DM_SENT_NOTICE)
        logger.info("プレミアムのリンクを渡しました (guild=%s)", ctx.guild.id)
