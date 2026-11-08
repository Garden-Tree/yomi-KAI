"""サーバー管理者向けのコマンド（ignore: 読み上げないユーザー・ロール）。

登録・解除には「サーバー管理」（manage_guild）の権限が必要。一覧は誰でも見られる。
hybrid グループなので ``y.ignore add @ユーザー`` と ``/ignore add`` のどちらでも同じ処理を行う。
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..config import Config
from ..settings import MAX_IGNORED, SettingsStore
from .common import SAVE_FAILED_NOTICE, original_error, reply

logger = logging.getLogger(__name__)

MANAGE_GUILD_NOTICE = "このコマンドを使うには「サーバー管理」の権限が必要です"


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
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        return True

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        error = original_error(error)
        if isinstance(error, commands.MissingPermissions):
            await reply(ctx, MANAGE_GUILD_NOTICE, private=True)
        elif isinstance(error, commands.UserInputError):
            await self._invalid(ctx)
        elif isinstance(error, (OSError, ValueError)):
            await reply(ctx, SAVE_FAILED_NOTICE, private=True)

    async def _invalid(self, ctx: commands.Context) -> None:
        await reply(ctx, f"コマンドが間違っています。`{self.config.prefix}help`を参照してください", private=True)

    # --- 読み上げないユーザー・ロール ---
    @commands.hybrid_group(
        name="ignore", invoke_without_command=True, description="読み上げないユーザー・ロールを設定します"
    )
    @commands.guild_only()
    async def ignore_group(self, ctx: commands.Context, *args: str) -> None:
        await self._invalid(ctx)

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
