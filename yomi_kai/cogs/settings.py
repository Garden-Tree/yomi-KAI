"""読み上げ設定コマンド（ユーザーごとの voice / speed、サーバー全体の announce）。

いずれも hybrid コマンドで、``y.voice`` と ``/voice`` のどちらでも同じ処理を行う。
スラッシュコマンドで呼ばれた場合、個人設定に関する返信は本人にだけ見える（ephemeral）。
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from ..config import Config
from ..settings import DEFAULT_SPEED, SPEED_MAX, SPEED_MIN, SettingsStore
from ..text import chunk_lines
from ..tts import TTSEngine, find_speakers
from ..ui import MAX_OPTIONS, DropdownView
from .common import SAVE_FAILED_NOTICE, original_error, reply

logger = logging.getLogger(__name__)

SPEED_INVALID_NOTICE = "速度は0.5〜2.0の数値で指定してください"
# 話者名が曖昧なときに表示する候補の数
MAX_CANDIDATES = 10


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
        # 引数の数・値が不正（例: y.speed 1 2、y.announce maybe）
        if isinstance(error, commands.UserInputError):
            if ctx.command is not None and ctx.command.qualified_name == "speed":
                await reply(ctx, SPEED_INVALID_NOTICE, private=True)
            else:
                await self._invalid(ctx)
            return
        # 設定ファイルが壊れている・書き込めない場合（ログは Bot.on_command_error が出す）
        if isinstance(original_error(error), (OSError, ValueError)):
            await reply(ctx, SAVE_FAILED_NOTICE, private=True)

    async def _invalid(self, ctx: commands.Context) -> None:
        await reply(ctx, f"コマンドが間違っています。`{self.config.prefix}help`を参照してください", private=True)

    @commands.hybrid_command(name="voice", description="自分の話者を設定します（名前を省略するとドロップダウンを表示）")
    @commands.guild_only()
    @app_commands.describe(name="話者の名前（一部でも可）。list で一覧、reset で個人設定を解除")
    async def voice(self, ctx: commands.Context, *, name: str | None = None) -> None:
        guild_id = ctx.guild.id
        name = name.strip() if name is not None else None

        if name == "reset":
            await asyncio.to_thread(self.settings.clear_user_speaker, guild_id, ctx.author.id)
            await reply(ctx, "話者の個人設定を解除しました", private=True)
            logger.info("%sさんの話者の個人設定を解除しました (guild=%s)", ctx.author, guild_id)
            return
        if name == "list":
            await self._send_speaker_list(ctx)
            return
        if name:
            await self._set_speaker_by_name(ctx, name)
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
        speakers = self.engine.speakers()
        view = DropdownView(speakers, f"あなたの話者: {current}", select_user_speaker)
        message = "自分の話者を選んでください"
        if len(speakers) > MAX_OPTIONS:
            message += (
                f"（話者が{len(speakers)}件あるため先頭{MAX_OPTIONS}件のみ表示しています。"
                f"`{self.config.prefix}voice list`で一覧を確認し、`{self.config.prefix}voice 名前`で指定できます）"
            )
        # ドロップダウンは誰でも操作できる（操作した人の話者が変わる）ので、スラッシュでも全員に見せる
        await reply(ctx, message, view=view)

    async def _send_speaker_list(self, ctx: commands.Context) -> None:
        names = [s.name for s in self.engine.speakers()]
        lines = [f"使える話者（{len(names)}件）:", *names]
        for chunk in chunk_lines(lines):
            await reply(ctx, chunk, private=True)

    async def _set_speaker_by_name(self, ctx: commands.Context, query: str) -> None:
        names = [s.name for s in self.engine.speakers()]
        found = find_speakers(query, names)
        if not found:
            await reply(
                ctx,
                f"話者「{query}」が見つかりません。`{self.config.prefix}voice list`で話者の一覧を確認できます",
                private=True,
            )
            return
        if len(found) > 1:
            shown = "、".join(found[:MAX_CANDIDATES])
            more = f" ほか{len(found) - MAX_CANDIDATES}件" if len(found) > MAX_CANDIDATES else ""
            await reply(ctx, f"「{query}」に当てはまる話者が複数あります: {shown}{more}", private=True)
            return
        name = found[0]
        await asyncio.to_thread(self.settings.set_user_speaker, ctx.guild.id, ctx.author.id, name)
        await reply(ctx, f"{ctx.author.display_name}さんの話者を{name}にしました", private=True)
        logger.info("%sさんの話者を%sに変更しました (guild=%s)", ctx.author, name, ctx.guild.id)

    @commands.hybrid_command(
        name="announce", description="ボイスチャンネルへの入退室を読み上げるか設定します", ignore_extra=False
    )
    @commands.guild_only()
    @app_commands.describe(state="on で読み上げる、off で読み上げない（省略すると現在の設定を表示）")
    async def announce(self, ctx: commands.Context, state: Literal["on", "off"] | None = None) -> None:
        guild_id = ctx.guild.id
        if state is None:
            enabled = (await self.settings.aget(guild_id)).announce_voice
            await reply(ctx, f"入退室の読み上げは現在{'オン' if enabled else 'オフ'}です")
            return
        enabled = state == "on"
        await asyncio.to_thread(self.settings.set_announce_voice, guild_id, enabled)
        await reply(ctx, f"入退室の読み上げを{'オン' if enabled else 'オフ'}にしました")
        logger.info("入退室の読み上げを%sにしました (guild=%s)", "オン" if enabled else "オフ", guild_id)

    @commands.hybrid_command(
        name="speed", description="自分の読み上げ速度を設定します（0.5〜2.0倍）", ignore_extra=False
    )
    @commands.guild_only()
    @app_commands.describe(value="0.5〜2.0 の倍率、または reset（省略すると現在の速度を表示）")
    async def speed(self, ctx: commands.Context, value: str | None = None) -> None:
        guild_id = ctx.guild.id
        user = ctx.author

        if value is None:
            saved = (await self.settings.aget(guild_id)).user(user.id).speed
            current = DEFAULT_SPEED if saved is None else saved
            await reply(ctx, f"現在の読み上げ速度は{format_speed(current)}倍です", private=True)
            return
        if value == "reset":
            await asyncio.to_thread(self.settings.clear_user_speed, guild_id, user.id)
            await reply(ctx, "読み上げ速度の個人設定を解除しました", private=True)
            logger.info("%sさんの読み上げ速度の個人設定を解除しました (guild=%s)", user, guild_id)
            return

        speed = parse_speed(value)
        if speed is None:
            await reply(ctx, SPEED_INVALID_NOTICE, private=True)
            return
        await asyncio.to_thread(self.settings.set_user_speed, guild_id, user.id, speed)
        await reply(ctx, f"{user.display_name}さんの読み上げ速度を{format_speed(speed)}倍にしました", private=True)
        logger.info("%sさんの読み上げ速度を%s倍に変更しました (guild=%s)", user, speed, guild_id)
