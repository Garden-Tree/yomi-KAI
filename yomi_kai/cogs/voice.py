"""接続・切断・ヘルプと、メッセージの読み上げ。

コマンドは hybrid コマンドで、``y.c`` と ``/c`` のどちらでも同じ処理を行う。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from ..blocklist import GuildBlocklist
from ..config import Config
from ..dictionary import DictionaryStore
from ..session import AFK_NOTICE, GuildSession, SourceFactory, SynthesisStats
from ..settings import SettingsStore, is_ignored, resolve_voice, server_speaker, should_read_author
from ..status import bot_version, status_fields
from ..text import (
    announce_text,
    autojoin_allowed,
    autojoin_text_channel,
    build_read_text,
    describe_attachments,
    should_read_name,
    voice_transition,
    with_name,
)
from ..tts import TTSEngine
from ..ui import DropdownView
from ..usage import UsageStore
from .common import EMBED_COLOR, SAVE_FAILED_NOTICE, reply

logger = logging.getLogger(__name__)


class VoiceCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        config: Config,
        engine: TTSEngine,
        store: DictionaryStore,
        settings: SettingsStore,
        source_factory: SourceFactory,
        *,
        usage: UsageStore | None = None,
        blocklist: GuildBlocklist | None = None,
    ) -> None:
        self.bot = bot
        self.config = config
        self.engine = engine
        self.store = store
        self.settings = settings
        # 使用量の計測（None なら記録しない）
        self.usage = usage
        # 利用を断るサーバー（None ならブロックしない）
        self.blocklist = blocklist
        self.available_speakers = frozenset(s.name for s in engine.speakers())
        self.source_factory = source_factory
        # guild.id -> GuildSession（サーバーごとの状態はここにだけ持つ）
        self.sessions: dict[int, GuildSession] = {}
        # guild.id -> 直前に読み上げた発言の (ユーザーID, 時刻)。名前の読み上げを連投で省くために使う
        self._last_reader: dict[int, tuple[int, float]] = {}
        self._clock = time.monotonic
        # guild.id -> 最後に自動切断した時刻（自動接続の再接続を少し待つ）
        self._auto_disconnected_at: dict[int, float] = {}
        # 自動接続の途中のサーバー（同時に複数人が入っても1回だけ接続する）
        self._autojoining: set[int] = set()
        # 音声合成の成否（全サーバー共通、y.status で表示）
        self.stats = SynthesisStats()

    def get_session(self, guild_id: int) -> GuildSession:
        session = self.sessions.get(guild_id)
        if session is None:
            session = GuildSession(
                guild_id,
                self.engine,
                afk_time=self.config.afk_time,
                source_factory=self.source_factory,
                stats=self.stats,
            )
            session.on_auto_close = lambda: self._note_auto_disconnect(guild_id)
            self.sessions[guild_id] = session
        return session

    def _note_auto_disconnect(self, guild_id: int) -> None:
        self._auto_disconnected_at[guild_id] = self._clock()

    async def cog_unload(self) -> None:
        for session in self.sessions.values():
            await session.close()

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        # 引数が不正（例: y.s foo、y.s all foo）
        if isinstance(error, commands.UserInputError):
            await self._invalid(ctx)

    async def _invalid(self, ctx: commands.Context) -> None:
        await reply(ctx, f"コマンドが間違っています。`{self.config.prefix}help`を参照してください", private=True)

    # --- コマンド ---
    @commands.hybrid_command(name="help", description="コマンドの一覧を表示します")
    async def help_command(self, ctx: commands.Context) -> None:
        prefix = self.config.prefix
        help_embed = discord.Embed(title="yomi-KAI", color=EMBED_COLOR)
        help_embed.add_field(name=f"{prefix}c", value="発言者と同じボイスチャンネルに接続します。", inline=False)
        help_embed.add_field(name=f"{prefix}dc", value="ボイスチャンネルから切断します。", inline=False)
        help_embed.add_field(
            name=f"{prefix}s",
            value=f"読み上げ中のメッセージを飛ばします。`{prefix}s all`で溜まっている分もすべて飛ばします。",
            inline=False,
        )
        help_embed.add_field(
            name=f"{prefix}voice",
            value=(
                f"自分の話者を選びます。`{prefix}voice 名前`で名前を指定して設定、"
                f"`{prefix}voice list`で話者の一覧を表示、"
                f"`{prefix}voice reset`で個人設定を解除し、サーバーの話者に戻します。"
            ),
            inline=False,
        )
        help_embed.add_field(
            name=f"{prefix}speed `倍率`",
            value=(
                "自分の読み上げ速度を0.5〜2.0倍で設定します。"
                f"`{prefix}speed`で現在の速度を表示、`{prefix}speed reset`で解除します。"
            ),
            inline=False,
        )
        help_embed.add_field(
            name=f"{prefix}announce `on|off`",
            value=(
                "ボイスチャンネルへの入退室を読み上げるか設定します（サーバー全体）。"
                f"`{prefix}announce`で現在の設定を表示します。"
            ),
            inline=False,
        )
        help_embed.add_field(
            name=f"{prefix}readname `on|off` / {prefix}speedup `on|off`",
            value=(
                "発言者の名前を読み上げるか（連投では省略）／読み上げ待ちが溜まったら速く読むかを設定します"
                "（サーバー全体）。"
            ),
            inline=False,
        )
        help_embed.add_field(
            name=f"{prefix}dict",
            value=f"辞書に関する操作です。詳しくは`{prefix}dict help`を参照してください。",
            inline=False,
        )
        help_embed.add_field(
            name="サーバー管理者向け（「サーバー管理」の権限が必要）",
            value=(
                f"`{prefix}ignore add|del @ユーザー|@ロール`・`{prefix}ignore list`: 読み上げない人・ロール\n"
                f"`{prefix}autojoin add|del`・`{prefix}autojoin list`: VC に人が入ったら自動で接続\n"
                f"`{prefix}readbots on|off`: ほかの Bot・Webhook の発言も読み上げる\n"
                f"`{prefix}dict import`: 添付した JSON の辞書を読み込む（`{prefix}dict export` で書き出し）"
            ),
            inline=False,
        )
        help_embed.add_field(name=f"{prefix}status", value="Bot の動作状況を表示します。", inline=False)
        help_embed.add_field(
            name=f"{prefix}sd",
            value=f"「突然の死」に関する操作です。詳しくは`{prefix}sd help`を参照してください。",
            inline=False,
        )
        help_embed.add_field(
            name=f"{prefix}credit",
            value="読み上げ音声のクレジット（VOICEVOX:ずんだもん など）を表示します。",
            inline=False,
        )
        help_embed.add_field(name=f"{prefix}help", value="このヘルプを表示します。", inline=False)
        help_embed.set_footer(text="どのコマンドもスラッシュコマンド（/c、/voice など）として使うこともできます。")
        await reply(ctx, embed=help_embed)
        logger.info("helpを表示")

    @commands.hybrid_command(name="c", description="あなたのいるボイスチャンネルに接続し、このチャンネルを読み上げます")
    @commands.guild_only()
    async def connect(self, ctx: commands.Context) -> None:
        if ctx.author.voice is None or ctx.author.voice.channel is None:
            await reply(ctx, f"{ctx.author.mention}さんはボイスチャンネルに接続していません")
            logger.info("%sさんはボイスチャンネルに接続していません", ctx.author)
            return

        # 接続に3秒以上かかるとスラッシュコマンドが「応答しませんでした」になるので先に応答を保留する
        # （プレフィックスでは何もしない）
        await ctx.defer()

        voice_channel = ctx.author.voice.channel
        voice_client = ctx.guild.voice_client
        if voice_client is not None:  # 他のボイスチャンネルに接続していた場合
            await voice_client.move_to(voice_channel)
        else:
            voice_client = await voice_channel.connect()

        guild_id = ctx.guild.id
        session = self.get_session(guild_id)
        await session.start(voice_client, ctx.channel)

        async def select_server_speaker(interaction: discord.Interaction, name: str) -> None:
            try:
                await asyncio.to_thread(self.settings.set_guild_speaker, guild_id, name)
            except Exception:
                logger.exception("サーバーの話者の保存に失敗しました (guild=%s)", guild_id)
                await interaction.response.send_message(SAVE_FAILED_NOTICE, ephemeral=True)
                return
            await interaction.response.send_message(f"{name}が選択されました")
            logger.info("サーバーの話者を%sに変更しました (guild=%s)", name, guild_id)

        settings = await self.settings.aget(guild_id)
        current = server_speaker(settings, self.available_speakers, self.engine.default_speaker)
        view = DropdownView(self.engine.speakers(), f"サーバーの話者: {current}", select_server_speaker)
        await reply(ctx, "話者を選んでください", view=view)
        connected_embed = discord.Embed(title="読み上げ開始", color=EMBED_COLOR)
        connected_embed.add_field(name="テキストチャンネル", value=f"{ctx.channel.name}", inline=False)
        connected_embed.add_field(name="ボイスチャンネル", value=f"{voice_channel.name}", inline=False)
        await reply(ctx, embed=connected_embed)
        logger.info("%sに接続しました", voice_channel.name)

        # 辞書を先読みしておき、読み上げ時にファイルI/Oが発生しないようにする
        await self.store.aget(ctx.guild.id)

    @commands.hybrid_command(name="status", description="Bot の動作状況を表示します", ignore_extra=False)
    async def status(self, ctx: commands.Context) -> None:
        session = self.sessions.get(ctx.guild.id) if ctx.guild is not None else None
        started_at = getattr(self.bot, "started_at", None)
        fields = status_fields(
            latency=self.bot.latency,
            sessions=sum(1 for s in self.sessions.values() if s.is_connected),
            queue=None if ctx.guild is None else (session.pending if session is not None else 0),
            workers=self.config.voicevox_workers,
            healthy=self.stats.healthy,
            last_error=self.stats.last_error,
            errors=self.stats.errors,
            uptime=None if started_at is None else time.time() - started_at,
            version=bot_version(),
        )
        embed = discord.Embed(title="yomi-KAI の状態", color=EMBED_COLOR)
        for name, value in fields:
            embed.add_field(name=name, value=value, inline=False)
        await reply(ctx, embed=embed)

    @commands.hybrid_command(name="dc", description="ボイスチャンネルから切断します")
    @commands.guild_only()
    async def disconnect(self, ctx: commands.Context) -> None:
        voice_client = ctx.guild.voice_client
        if voice_client is None:
            await reply(ctx, "ボイスチャンネルに接続していません")
            logger.info("ボイスチャンネルに接続していません")
            return

        await ctx.defer()
        session = self.sessions.get(ctx.guild.id)
        if session is not None and session.is_connected:
            await session.close()
        else:
            await voice_client.disconnect(force=True)
        await reply(ctx, "切断しました")
        logger.info("切断しました")

    @commands.hybrid_command(name="s", description="読み上げ中のメッセージを飛ばします", ignore_extra=False)
    @commands.guild_only()
    @app_commands.describe(mode="all を指定すると、溜まっている未読み上げのメッセージもすべて飛ばします")
    async def skip(self, ctx: commands.Context, mode: Literal["all"] | None = None) -> None:
        session = self.sessions.get(ctx.guild.id)
        if session is None or not session.is_connected:
            await reply(ctx, "ボイスチャンネルに接続していません")
            return

        if mode is None:
            if session.skip():
                await reply(ctx, "スキップしました")
                logger.info("読み上げをスキップしました (guild=%s)", ctx.guild.id)
            else:
                await reply(ctx, "読み上げ中のメッセージはありません")
            return

        dropped = session.skip_all()
        if dropped:
            await reply(ctx, f"読み上げを{dropped}件スキップしました")
            logger.info("読み上げを%d件スキップしました (guild=%s)", dropped, ctx.guild.id)
        else:
            await reply(ctx, "読み上げ中のメッセージはありません")

    # --- イベント ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # プレフィックス付きはコマンドとして Bot 側で処理されるので読み上げない
        if message.content.startswith(self.config.prefix):
            return
        # 自分の発言は読まない（ほかの Bot・Webhook はサーバーの設定 read_bots で決める）
        me = getattr(self.bot, "user", None)
        is_self = me is not None and message.author.id == me.id
        if is_self:
            return
        is_webhook = getattr(message, "webhook_id", None) is not None
        # DMなど、サーバー外のメッセージは無視
        guild = message.guild
        if guild is None:
            return
        if self.blocklist is not None and self.blocklist.is_blocked(guild.id):
            return

        session = self.sessions.get(guild.id)
        if (
            session is None
            or not session.is_connected
            or session.text_channel is None
            or session.text_channel.id != message.channel.id
            or guild.voice_client is None
        ):
            return

        dictionary = await self.store.aget(guild.id)
        read_msg = build_read_text(
            message.content,
            dictionary,
            user_name=lambda i: m.display_name if (m := guild.get_member(i)) else None,
            role_name=lambda i: r.name if (r := guild.get_role(i)) else None,
            channel_name=lambda i: c.name if (c := guild.get_channel(i)) else None,
            max_length=self.config.max_read_length,
            attachments=describe_attachments(
                [(a.content_type, a.filename) for a in message.attachments],
                [s.name for s in message.stickers],
            ),
        )

        # 空メッセージ（埋め込みのみ等）は読み上げをスキップ
        if not read_msg.strip():
            return

        settings = await self.settings.aget(guild.id)
        if not should_read_author(
            is_self=is_self, is_bot=message.author.bot, is_webhook=is_webhook, read_bots=settings.read_bots
        ):
            return
        # 読み上げないユーザー・ロール（y.ignore）
        roles = getattr(message.author, "roles", None) or ()
        if is_ignored(settings, message.author.id, (r.id for r in roles)):
            return
        speaker, speed = resolve_voice(
            settings, message.author.id, self.available_speakers, self.engine.default_speaker
        )
        now = self._clock()
        # 名前は本文の切り詰め後に付ける（MAX_READ_LENGTH は本文だけに適用）
        if settings.read_name and should_read_name(self._last_reader.get(guild.id), message.author.id, now):
            read_msg = with_name(read_msg, message.author.display_name)
        if session.enqueue(read_msg, speaker, speed, speedup=settings.queue_speedup):
            self._last_reader[guild.id] = (message.author.id, now)
            if self.usage is not None:
                self.usage.record(guild.id, message.author.id, len(read_msg))
            logger.info(
                "読み上げキューに追加しました (guild=%s, %d文字, %s, %s倍)", guild.id, len(read_msg), speaker, speed
            )
            # 本文は LOG_READ_TEXT = True のときだけ記録する（既定では文字数だけ）
            if self.config.log_read_text:
                logger.debug("ReadSentence:%s", read_msg)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        guild = member.guild
        session = self.sessions.get(guild.id)

        if self.bot.user is not None and member.id == self.bot.user.id:
            if session is None or not session.is_connected:
                return
            if after.channel is None:
                # キック・切断された
                logger.info("ボイスチャンネルから切断されました (guild=%s)", guild.id)
                self._note_auto_disconnect(guild.id)
                await session.close()
            elif before.channel != after.channel and len(after.channel.members) == 1:
                # 誰もいないチャンネルへ移動させられた
                self._note_auto_disconnect(guild.id)
                await session.close(notice=AFK_NOTICE)
            return

        voice_client = guild.voice_client
        if (
            voice_client is not None
            and voice_client.channel == before.channel
            and len(voice_client.channel.members) == 1
        ):  # ボイスチャンネルに自分だけ残っていたら
            self._note_auto_disconnect(guild.id)
            if session is not None and session.is_connected:
                await session.close(notice=AFK_NOTICE)
            else:
                await voice_client.disconnect(force=True)
                logger.info(AFK_NOTICE)
            return

        if await self._maybe_autojoin(member, before, after):
            return
        await self._announce_voice(member, before, after, session)

    async def _maybe_autojoin(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> bool:
        """登録されたボイスチャンネルにメンバーが入ったら自動で接続する。接続したら True。"""
        guild = member.guild
        session = self.sessions.get(guild.id)
        connected = (session is not None and session.is_connected) or guild.voice_client is not None
        if connected or guild.id in self._autojoining or after.channel is None:
            return False
        settings = await self.settings.aget(guild.id)
        text_id = autojoin_text_channel(
            settings.autojoin,
            member_is_bot=member.bot,
            before_id=before.channel.id if before.channel is not None else None,
            after_id=after.channel.id,
            connected=connected,
        )
        if text_id is None:
            return False
        if not autojoin_allowed(self._auto_disconnected_at.get(guild.id), self._clock()):
            logger.info("自動切断の直後のため自動接続しません (guild=%s)", guild.id)
            return False
        text_channel = guild.get_channel(text_id)
        if text_channel is None:
            logger.warning("自動接続先のテキストチャンネル %s が見つかりません (guild=%s)", text_id, guild.id)
            return False

        voice_channel = after.channel
        self._autojoining.add(guild.id)
        try:
            voice_client = await voice_channel.connect()
            await self.get_session(guild.id).start(voice_client, text_channel)
            embed = discord.Embed(title="自動接続しました", color=EMBED_COLOR)
            embed.add_field(name="テキストチャンネル", value=f"{text_channel.name}", inline=False)
            embed.add_field(name="ボイスチャンネル", value=f"{voice_channel.name}", inline=False)
            await text_channel.send(embed=embed)
            logger.info("%sに自動接続しました (guild=%s)", voice_channel.name, guild.id)
        except Exception:
            logger.exception("自動接続に失敗しました (guild=%s)", guild.id)
            return False
        finally:
            self._autojoining.discard(guild.id)
        await self.store.aget(guild.id)
        return True

    async def _announce_voice(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
        session: GuildSession | None,
    ) -> None:
        """Bot のいるボイスチャンネルへの入退室を、サーバーの話者・標準の速度で読み上げる。"""
        voice_client = member.guild.voice_client
        if session is None or not session.is_connected or voice_client is None or voice_client.channel is None:
            return
        kind = voice_transition(
            before.channel.id if before.channel is not None else None,
            after.channel.id if after.channel is not None else None,
            voice_client.channel.id,
            is_bot=member.bot,
        )
        if kind is None:
            return
        settings = await self.settings.aget(member.guild.id)
        if not settings.announce_voice:
            return
        text = announce_text(kind, member.display_name)
        speaker = server_speaker(settings, self.available_speakers, self.engine.default_speaker)
        if session.enqueue(text, speaker, 1.0, speedup=settings.queue_speedup):
            if self.usage is not None:
                # 入退室はユーザーの使用量ではなく、サーバーの system_chars に数える
                self.usage.record_system(member.guild.id, len(text))
            logger.info("入退室を読み上げキューに追加しました (guild=%s, %s)", member.guild.id, kind)
