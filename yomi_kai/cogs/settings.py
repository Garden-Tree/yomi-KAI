"""読み上げ設定コマンド（ユーザーごとの voice / speed、サーバー全体の announce）。

いずれも hybrid コマンドで、``y.voice`` と ``/voice`` のどちらでも同じ処理を行う。
スラッシュコマンドで呼ばれた場合、個人設定に関する返信は本人にだけ見える（ephemeral）。
"""

from __future__ import annotations

import asyncio
import logging
import math
import unicodedata
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from ..config import Config
from ..plans import PlanManager
from ..settings import (
    DEFAULT_SPEED,
    PITCH_MAX,
    PITCH_MIN,
    READ_KEYS,
    SPEED_MAX,
    SPEED_MIN,
    GuildSettings,
    SettingsStore,
)
from ..text import chunk_lines
from ..tts import TTSEngine, find_speakers
from ..ui import MAX_OPTIONS, DropdownView
from .common import (
    MANAGE_GUILD_NOTICE,
    SAVE_FAILED_NOTICE,
    guild_only_check,
    handle_command_error,
    invalid_command_notice,
    reply,
    reply_invalid,
)

logger = logging.getLogger(__name__)

SPEED_INVALID_NOTICE = "速度は0.5〜2.0の数値で指定してください"
PITCH_INVALID_NOTICE = f"声の高さは{PITCH_MIN}〜+{PITCH_MAX}の整数で指定してください（0 が標準）"
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


# y.read の対象: 名前 -> (表示名, オンのときの読み方, オフのときの読み方)
READ_TARGETS = {
    "url": ("URL", "「URL」と読む", "読まない"),
    "emoji": ("サーバー絵文字", "名前を読む", "読まない"),
    "code": ("コードブロック", "中身を読む", "「コード省略」と読む"),
    "spoiler": ("ネタバレ", "中身を読む", "「ネタバレ」と読む"),
}


def read_state_line(target: str, enabled: bool) -> str:
    label, on, off = READ_TARGETS[target]
    return f"{label}: {on if enabled else off}（{'on' if enabled else 'off'}）"


def read_state_lines(settings: GuildSettings) -> list[str]:
    return [read_state_line(target, getattr(settings, key)) for target, key in READ_KEYS.items()]


def format_pitch(level: int) -> str:
    """3 → "+3", 0 → "0（標準）", -2 → "-2"。"""
    if level == 0:
        return "0（標準）"
    return f"{level:+d}"


def parse_pitch(raw: str) -> int | None:
    """-10〜+10 の整数（全角数字・全角の符号も可）なら int、それ以外は None。"""
    text = unicodedata.normalize("NFKC", raw).strip().replace("−", "-")
    try:
        level = int(text)
    except ValueError:
        return None
    if not PITCH_MIN <= level <= PITCH_MAX:
        return None
    return level


class SettingsCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        config: Config,
        engine: TTSEngine,
        settings: SettingsStore,
        *,
        plans: PlanManager | None = None,
    ) -> None:
        self.bot = bot
        self.config = config
        self.engine = engine
        self.settings = settings
        # サーバーで使える話者はプランで決まる（省略時はすべてのサーバーで engine の話者）
        self.plans = plans if plans is not None else PlanManager(config, engine)

    async def cog_check(self, ctx: commands.Context) -> bool:
        return guild_only_check(ctx)

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        # 引数の数・値が不正（例: y.speed 1 2、y.announce maybe）、設定ファイルが壊れている・書き込めない、など
        if ctx.command is not None and ctx.command.qualified_name == "speed":
            invalid_notice = SPEED_INVALID_NOTICE
        elif ctx.command is not None and ctx.command.qualified_name == "pitch":
            invalid_notice = PITCH_INVALID_NOTICE
        else:
            invalid_notice = invalid_command_notice(self.config.prefix)
        await handle_command_error(ctx, error, invalid_notice=invalid_notice)

    @commands.hybrid_command(name="voice", description="自分の話者を設定します（名前を省略するとドロップダウンを表示）")
    @app_commands.guild_only()
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
        engine, available = self.plans.speakers_for(guild_id)
        current = saved if saved in available else "未設定（サーバー設定に従う）"
        speakers = engine.speakers()
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
        names = [s.name for s in self.plans.engine_for(ctx.guild.id).speakers()]
        lines = [f"使える話者（{len(names)}件）:", *names]
        for chunk in chunk_lines(lines):
            await reply(ctx, chunk, private=True)

    async def _set_speaker_by_name(self, ctx: commands.Context, query: str) -> None:
        names = [s.name for s in self.plans.engine_for(ctx.guild.id).speakers()]
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
    @app_commands.guild_only()
    @app_commands.describe(state="on で読み上げる、off で読み上げない（省略すると現在の設定を表示）")
    async def announce(self, ctx: commands.Context, state: Literal["on", "off"] | None = None) -> None:
        await self._toggle(ctx, state, "announce_voice", "入退室の読み上げ")

    async def _toggle(self, ctx: commands.Context, state: str | None, key: str, label: str) -> None:
        """サーバー全体のオン/オフの設定。state を省略すると現在の設定を表示する。"""
        guild_id = ctx.guild.id
        if state is None:
            enabled = getattr(await self.settings.aget(guild_id), key)
            await reply(ctx, f"{label}は現在{'オン' if enabled else 'オフ'}です")
            return
        enabled = state == "on"
        await asyncio.to_thread(self.settings.set_flag, guild_id, key, enabled)
        await reply(ctx, f"{label}を{'オン' if enabled else 'オフ'}にしました")
        logger.info("%sを%sにしました (guild=%s)", label, "オン" if enabled else "オフ", guild_id)

    @commands.hybrid_command(
        name="readname", description="発言者の名前を読み上げるか設定します（サーバー全体）", ignore_extra=False
    )
    @app_commands.guild_only()
    @app_commands.describe(state="on で名前を読み上げる、off で読み上げない（省略すると現在の設定を表示）")
    async def readname(self, ctx: commands.Context, state: Literal["on", "off"] | None = None) -> None:
        await self._toggle(ctx, state, "read_name", "名前の読み上げ")

    @commands.hybrid_command(
        name="speedup",
        description="読み上げ待ちが溜まったときに速く読むか設定します（サーバー全体）",
        ignore_extra=False,
    )
    @app_commands.guild_only()
    @app_commands.describe(state="on で混雑時に速く読む、off で常に設定どおりの速度（省略すると現在の設定を表示）")
    async def speedup(self, ctx: commands.Context, state: Literal["on", "off"] | None = None) -> None:
        await self._toggle(ctx, state, "queue_speedup", "混雑時の早読み")

    @commands.hybrid_command(
        name="readbots",
        description="ほかの Bot・Webhook の発言も読み上げるか設定します（サーバー全体）",
        ignore_extra=False,
    )
    @app_commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    @app_commands.describe(state="on で Bot の発言も読み上げる、off で読み上げない（省略すると現在の設定を表示）")
    async def readbots(self, ctx: commands.Context, state: Literal["on", "off"] | None = None) -> None:
        await self._toggle(ctx, state, "read_bots", "Bot の発言の読み上げ")

    @commands.hybrid_command(
        name="speed", description="自分の読み上げ速度を設定します（0.5〜2.0倍）", ignore_extra=False
    )
    @app_commands.guild_only()
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

    @commands.hybrid_command(
        name="pitch", description="自分の声の高さを設定します（-10〜+10、0 が標準）", ignore_extra=False
    )
    @app_commands.guild_only()
    @app_commands.describe(value="-10〜+10 の整数、または reset（省略すると現在の高さを表示）")
    async def pitch(self, ctx: commands.Context, value: str | None = None) -> None:
        guild_id = ctx.guild.id
        user = ctx.author

        if value is None:
            saved = (await self.settings.aget(guild_id)).user(user.id).pitch
            await reply(ctx, f"現在の声の高さは{format_pitch(saved or 0)}です", private=True)
            return
        if value == "reset":
            await asyncio.to_thread(self.settings.clear_user_pitch, guild_id, user.id)
            await reply(ctx, "声の高さの個人設定を解除しました", private=True)
            logger.info("%sさんの声の高さの個人設定を解除しました (guild=%s)", user, guild_id)
            return

        level = parse_pitch(value)
        if level is None:
            await reply(ctx, PITCH_INVALID_NOTICE, private=True)
            return
        await asyncio.to_thread(self.settings.set_user_pitch, guild_id, user.id, level)
        await reply(ctx, f"{user.display_name}さんの声の高さを{format_pitch(level)}にしました", private=True)
        logger.info("%sさんの声の高さを%sに変更しました (guild=%s)", user, level, guild_id)

    # --- 読み上げの切り替え（y.read） ---
    @commands.hybrid_group(
        name="read", invoke_without_command=True, description="URL・絵文字・コード・ネタバレの読み方を設定します"
    )
    @app_commands.guild_only()
    async def read_group(self, ctx: commands.Context, *args: str) -> None:
        # サブコマンドなし（プレフィックスのみ）は現在の設定を表示。一致しないサブコマンドは案内
        if args:
            await reply_invalid(ctx, self.config.prefix)
            return
        await self._send_read_settings(ctx)

    async def _send_read_settings(self, ctx: commands.Context) -> None:
        settings = await self.settings.aget(ctx.guild.id)
        await reply(ctx, "\n".join(["読み上げの設定（サーバー全体）:", *read_state_lines(settings)]))

    @read_group.command(
        name="list", description="URL・絵文字・コード・ネタバレの読み方を表示します", ignore_extra=False
    )
    async def read_list(self, ctx: commands.Context) -> None:
        await self._send_read_settings(ctx)

    async def _read_toggle(self, ctx: commands.Context, target: str, state: str | None) -> None:
        guild_id = ctx.guild.id
        key = READ_KEYS[target]
        if state is None:
            enabled = getattr(await self.settings.aget(guild_id), key)
            await reply(ctx, read_state_line(target, enabled))
            return
        # 表示は誰でもできる。変更は「サーバー管理」の権限が必要
        permissions = getattr(ctx.author, "guild_permissions", None)
        if permissions is None or not permissions.manage_guild:
            await reply(ctx, MANAGE_GUILD_NOTICE, private=True)
            return
        enabled = state == "on"
        await asyncio.to_thread(self.settings.set_flag, guild_id, key, enabled)
        await reply(ctx, f"{read_state_line(target, enabled)}にしました")
        logger.info("読み上げの設定 %s を%sにしました (guild=%s)", key, state, guild_id)

    @read_group.command(name="url", description="URL を「URL」と読むか設定します", ignore_extra=False)
    @app_commands.describe(state="on で「URL」と読む、off で読まない（省略すると現在の設定を表示）")
    async def read_url(self, ctx: commands.Context, state: Literal["on", "off"] | None = None) -> None:
        await self._read_toggle(ctx, "url", state)

    @read_group.command(name="emoji", description="サーバー絵文字の名前を読むか設定します", ignore_extra=False)
    @app_commands.describe(state="on で名前を読む、off で読まない（省略すると現在の設定を表示）")
    async def read_emoji(self, ctx: commands.Context, state: Literal["on", "off"] | None = None) -> None:
        await self._read_toggle(ctx, "emoji", state)

    @read_group.command(name="code", description="コードブロックの中身を読むか設定します", ignore_extra=False)
    @app_commands.describe(state="on で中身を読む、off で「コード省略」と読む（省略すると現在の設定を表示）")
    async def read_code(self, ctx: commands.Context, state: Literal["on", "off"] | None = None) -> None:
        await self._read_toggle(ctx, "code", state)

    @read_group.command(name="spoiler", description="ネタバレ（||...||）の中身を読むか設定します", ignore_extra=False)
    @app_commands.describe(state="on で中身を読む、off で「ネタバレ」と読む（省略すると現在の設定を表示）")
    async def read_spoiler(self, ctx: commands.Context, state: Literal["on", "off"] | None = None) -> None:
        await self._read_toggle(ctx, "spoiler", state)
