"""サーバー管理者向けのコマンド（ignore: 読み上げないユーザー・ロール、autojoin: 自動接続）。

登録・解除には「サーバー管理」（manage_guild）の権限が必要。一覧は誰でも見られる。
hybrid グループなので ``y.ignore add @ユーザー`` と ``/ignore add`` のどちらでも同じ処理を行う。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

import discord
from discord import app_commands
from discord.ext import commands

from ..config import Config
from ..settings import MAX_AUTOJOIN, MAX_IGNORED, SettingsStore
from .common import guild_only_check, handle_command_error, invalid_command_notice, reply, reply_invalid

logger = logging.getLogger(__name__)


def autojoin_to_remove(mapping: Mapping[int, int], current_voice_id: int | None) -> int | None:
    """y.autojoin del で外すボイスチャンネル: いるボイスチャンネルが登録済みならそれ、登録が1件だけならそれ。"""
    if current_voice_id is not None and current_voice_id in mapping:
        return current_voice_id
    if len(mapping) == 1:
        return next(iter(mapping))
    return None


def ignore_key(target: discord.abc.Snowflake) -> str:
    return "ignored_roles" if isinstance(target, discord.Role) else "ignored_users"


def describe_target(target: discord.abc.Snowflake) -> str:
    if isinstance(target, discord.Role):
        return f"ロール「{target.name}」"
    return f"{getattr(target, 'display_name', target)}さん"


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config, settings: SettingsStore) -> None:
        self.bot = bot
        self.config = config
        self.settings = settings

    async def cog_check(self, ctx: commands.Context) -> bool:
        return guild_only_check(ctx)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        await handle_command_error(ctx, error, invalid_notice=invalid_command_notice(self.config.prefix))

    # --- 読み上げないユーザー・ロール ---
    @commands.hybrid_group(
        name="ignore", invoke_without_command=True, description="読み上げないユーザー・ロールを設定します"
    )
    @app_commands.guild_only()
    async def ignore_group(self, ctx: commands.Context, *args: str) -> None:
        await reply_invalid(ctx, self.config.prefix)

    @ignore_group.command(name="add", description="ユーザーまたはロールの発言を読み上げないようにします")
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.describe(target="読み上げないユーザーまたはロール")
    async def ignore_add(self, ctx: commands.Context, target: discord.Member | discord.Role) -> None:
        result = await asyncio.to_thread(self.settings.add_ignored, ctx.guild.id, ignore_key(target), target.id)
        name = describe_target(target)
        if result == "exists":
            await reply(ctx, f"{name}は既に読み上げない設定です", private=True)
        elif result == "full":
            await reply(ctx, f"読み上げないユーザー・ロールはそれぞれ{MAX_IGNORED}件まで登録できます", private=True)
        else:
            await reply(ctx, f"{name}の発言を読み上げないようにしました")
            logger.info("%s=%s を読み上げない設定にしました (guild=%s)", ignore_key(target), target.id, ctx.guild.id)

    @ignore_group.command(name="del", description="読み上げない設定を解除します")
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.describe(target="解除するユーザーまたはロール")
    async def ignore_del(self, ctx: commands.Context, target: discord.Member | discord.Role) -> None:
        removed = await asyncio.to_thread(self.settings.remove_ignored, ctx.guild.id, ignore_key(target), target.id)
        name = describe_target(target)
        if not removed:
            await reply(ctx, f"{name}は読み上げない設定になっていません", private=True)
            return
        await reply(ctx, f"{name}の発言を再び読み上げるようにしました")
        logger.info("%s=%s の読み上げない設定を解除しました (guild=%s)", ignore_key(target), target.id, ctx.guild.id)

    @ignore_group.command(name="list", description="読み上げないユーザー・ロールの一覧を表示します")
    async def ignore_list(self, ctx: commands.Context) -> None:
        settings = await self.settings.aget(ctx.guild.id)
        if not settings.ignored_users and not settings.ignored_roles:
            await reply(ctx, "読み上げないユーザー・ロールは登録されていません")
            return
        lines = ["読み上げないユーザー・ロール:"]
        # メンションは通知を飛ばさないよう、表示名に置き換える（取得できなければ ID）
        for user_id in sorted(settings.ignored_users):
            member = ctx.guild.get_member(user_id)
            lines.append(f"ユーザー: {member.display_name if member else user_id}")
        for role_id in sorted(settings.ignored_roles):
            role = ctx.guild.get_role(role_id)
            lines.append(f"ロール: {role.name if role else role_id}")
        await reply(ctx, "\n".join(lines))

    # --- 自動接続 ---
    @commands.hybrid_group(
        name="autojoin",
        invoke_without_command=True,
        description="ボイスチャンネルに人が入ったら自動で接続する設定をします",
    )
    @app_commands.guild_only()
    async def autojoin_group(self, ctx: commands.Context, *args: str) -> None:
        await reply_invalid(ctx, self.config.prefix)

    @autojoin_group.command(
        name="add", description="いまいるボイスチャンネルに人が入ったら、このチャンネルを読み上げるよう自動接続します"
    )
    @commands.has_guild_permissions(manage_guild=True)
    async def autojoin_add(self, ctx: commands.Context) -> None:
        voice = getattr(ctx.author, "voice", None)
        if voice is None or voice.channel is None:
            await reply(ctx, "自動接続するボイスチャンネルに入ってから実行してください", private=True)
            return
        voice_channel = voice.channel
        result = await asyncio.to_thread(self.settings.set_autojoin, ctx.guild.id, voice_channel.id, ctx.channel.id)
        if result == "full":
            await reply(ctx, f"自動接続は{MAX_AUTOJOIN}件まで登録できます", private=True)
            return
        await reply(ctx, f"{voice_channel.name}に人が入ったら自動で接続し、{ctx.channel.name}を読み上げます")
        logger.info("自動接続を登録しました (guild=%s, voice=%s)", ctx.guild.id, voice_channel.id)

    @autojoin_group.command(
        name="del", description="いまいるボイスチャンネル（登録が1件ならそれ）の自動接続を解除します"
    )
    @commands.has_guild_permissions(manage_guild=True)
    async def autojoin_del(self, ctx: commands.Context) -> None:
        mapping = (await self.settings.aget(ctx.guild.id)).autojoin
        voice = getattr(ctx.author, "voice", None)
        current = voice.channel.id if voice is not None and voice.channel is not None else None
        target = autojoin_to_remove(mapping, current)
        if target is None:
            message = (
                "自動接続は登録されていません"
                if not mapping
                else "解除するボイスチャンネルに入ってから実行してください"
            )
            await reply(ctx, message, private=True)
            return
        await asyncio.to_thread(self.settings.remove_autojoin, ctx.guild.id, target)
        channel = ctx.guild.get_channel(target)
        await reply(ctx, f"{channel.name if channel else target}の自動接続を解除しました")
        logger.info("自動接続を解除しました (guild=%s, voice=%s)", ctx.guild.id, target)

    @autojoin_group.command(name="list", description="自動接続の一覧を表示します")
    async def autojoin_list(self, ctx: commands.Context) -> None:
        mapping = (await self.settings.aget(ctx.guild.id)).autojoin
        if not mapping:
            await reply(ctx, "自動接続は登録されていません")
            return
        lines = ["自動接続（ボイスチャンネル → 読み上げるテキストチャンネル）:"]
        for voice_id, text_id in mapping.items():
            voice_channel, text_channel = ctx.guild.get_channel(voice_id), ctx.guild.get_channel(text_id)
            voice_name = voice_channel.name if voice_channel else voice_id
            text_name = text_channel.name if text_channel else text_id
            lines.append(f"{voice_name} → {text_name}")
        await reply(ctx, "\n".join(lines))
