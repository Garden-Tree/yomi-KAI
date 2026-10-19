"""読み上げ設定コマンド（ユーザーごとの voice / speed、サーバー全体の announce）。"""

from __future__ import annotations

import asyncio
import logging
import math

import discord
from discord.ext import commands

from ..config import Config
from ..settings import DEFAULT_SPEED, SPEED_MAX, SPEED_MIN, SettingsStore
from ..tts import TTSEngine
from ..ui import DropdownView
from .voice import SAVE_FAILED_NOTICE

logger = logging.getLogger(__name__)

SPEED_INVALID_NOTICE = "速度は0.5〜2.0の数値で指定してください"


def format_speed(speed: float) -> str:
    """1.0 → "1", 1.25 → "1.25"。"""
    return f"{speed:g}"


def parse_speed(raw: str) -> float | None:
    """0.5〜2.0 の数値なら float、それ以外は None。"""
    try:
        speed = float(raw)
    except ValueError:
        return None
    if not math.isfinite(speed) or not SPEED_MIN <= speed <= SPEED_MAX:
        return None
    return speed


class SettingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot, config: Config, engine: TTSEngine, settings: SettingsStore) -> None:
        self.bot = bot
        self.config = config
        self.engine = engine
        self.settings = settings
        self.available_speakers = frozenset(s.name for s in engine.speakers())

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        # 設定ファイルが壊れている・書き込めない場合（ログは Bot.on_command_error が出す）
        if isinstance(error, commands.CommandInvokeError) and isinstance(error.original, (OSError, ValueError)):
            await ctx.channel.send(SAVE_FAILED_NOTICE)

    async def _invalid(self, ctx: commands.Context) -> None:
        await ctx.channel.send(f"コマンドが間違っています。`{self.config.prefix}help`を参照してください")

    @commands.command(name="voice")
    @commands.guild_only()
    async def voice(self, ctx: commands.Context, *args: str) -> None:
        guild_id = ctx.guild.id

        if args == ("reset",):
            await asyncio.to_thread(self.settings.clear_user_speaker, guild_id, ctx.author.id)
            await ctx.channel.send("話者の個人設定を解除しました")
            logger.info("%sさんの話者の個人設定を解除しました (guild=%s)", ctx.author, guild_id)
            return
        if args:
            await self._invalid(ctx)
            return

        async def select_user_speaker(interaction: discord.Interaction, name: str) -> None:
            # コマンドを打った人ではなく、選択した人の設定を変える
            user = interaction.user
            try:
                await asyncio.to_thread(self.settings.set_user_speaker, guild_id, user.id, name)
            except Exception:
                logger.exception("個人の話者の保存に失敗しました (guild=%s, user=%s)", guild_id, user.id)
                await interaction.response.send_message(SAVE_FAILED_NOTICE, ephemeral=True)
                return
            await interaction.response.send_message(f"{user.display_name}さんの話者を{name}にしました", ephemeral=True)
            logger.info("%sさんの話者を%sに変更しました (guild=%s)", user, name, guild_id)

        settings = await self.settings.aget(guild_id)
        saved = settings.user(ctx.author.id).speaker
        current = saved if saved in self.available_speakers else "未設定（サーバー設定に従う）"
        view = DropdownView(self.engine.speakers(), f"あなたの話者: {current}", select_user_speaker)
        await ctx.send("自分の話者を選んでください", view=view)

    @commands.command(name="announce")
    @commands.guild_only()
    async def announce(self, ctx: commands.Context, *args: str) -> None:
        guild_id = ctx.guild.id
        if not args:
            enabled = (await self.settings.aget(guild_id)).announce_voice
            await ctx.channel.send(f"入退室の読み上げは現在{'オン' if enabled else 'オフ'}です")
            return
        if args not in (("on",), ("off",)):
            await self._invalid(ctx)
            return
        enabled = args == ("on",)
        await asyncio.to_thread(self.settings.set_announce_voice, guild_id, enabled)
        await ctx.channel.send(f"入退室の読み上げを{'オン' if enabled else 'オフ'}にしました")
        logger.info("入退室の読み上げを%sにしました (guild=%s)", "オン" if enabled else "オフ", guild_id)

    @commands.command(name="speed")
    @commands.guild_only()
    async def speed(self, ctx: commands.Context, *args: str) -> None:
        guild_id = ctx.guild.id
        user = ctx.author

        if not args:
            saved = (await self.settings.aget(guild_id)).user(user.id).speed
            current = DEFAULT_SPEED if saved is None else saved
            await ctx.channel.send(f"現在の読み上げ速度は{format_speed(current)}倍です")
            return
        if args == ("reset",):
            await asyncio.to_thread(self.settings.clear_user_speed, guild_id, user.id)
            await ctx.channel.send("読み上げ速度の個人設定を解除しました")
            logger.info("%sさんの読み上げ速度の個人設定を解除しました (guild=%s)", user, guild_id)
            return

        value = parse_speed(args[0]) if len(args) == 1 else None
        if value is None:
            await ctx.channel.send(SPEED_INVALID_NOTICE)
            return
        await asyncio.to_thread(self.settings.set_user_speed, guild_id, user.id, value)
        await ctx.channel.send(f"{user.display_name}さんの読み上げ速度を{format_speed(value)}倍にしました")
        logger.info("%sさんの読み上げ速度を%s倍に変更しました (guild=%s)", user, value, guild_id)
