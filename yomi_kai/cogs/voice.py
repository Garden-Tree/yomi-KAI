"""接続・切断・ヘルプと、メッセージの読み上げ。"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from ..config import Config
from ..dictionary import DictionaryStore
from ..session import AFK_NOTICE, GuildSession, SourceFactory
from ..settings import SettingsStore, resolve_voice, server_speaker
from ..text import announce_text, build_read_text, describe_attachments, voice_transition
from ..tts import TTSEngine
from ..ui import DropdownView

logger = logging.getLogger(__name__)

EMBED_COLOR = 0x3399CC
SAVE_FAILED_NOTICE = "設定の保存に失敗しました"


class VoiceCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        config: Config,
        engine: TTSEngine,
        store: DictionaryStore,
        settings: SettingsStore,
        source_factory: SourceFactory,
    ) -> None:
        self.bot = bot
        self.config = config
        self.engine = engine
        self.store = store
        self.settings = settings
        self.available_speakers = frozenset(s.name for s in engine.speakers())
        self.source_factory = source_factory
        # guild.id -> GuildSession（サーバーごとの状態はここにだけ持つ）
        self.sessions: dict[int, GuildSession] = {}

    def get_session(self, guild_id: int) -> GuildSession:
        session = self.sessions.get(guild_id)
        if session is None:
            session = GuildSession(
                guild_id,
                self.engine,
                afk_time=self.config.afk_time,
                source_factory=self.source_factory,
            )
            self.sessions[guild_id] = session
        return session

    async def cog_unload(self) -> None:
        for session in self.sessions.values():
            await session.close()

    # --- コマンド ---
    @commands.command(name="help")
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
            value=f"自分の話者を選びます。`{prefix}voice reset`で個人設定を解除し、サーバーの話者に戻します。",
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
            value=f"ボイスチャンネルへの入退室を読み上げるか設定します（サーバー全体）。`{prefix}announce`で現在の設定を表示します。",
            inline=False,
        )
        help_embed.add_field(
            name=f"{prefix}dict",
            value=f"辞書に関する操作です。詳しくは`{prefix}dict help`を参照してください。",
            inline=False,
        )
        help_embed.add_field(
            name=f"{prefix}sd",
            value=f"「突然の死」に関する操作です。詳しくは`{prefix}sd help`を参照してください。",
            inline=False,
        )
        help_embed.add_field(name=f"{prefix}help", value="このヘルプを表示します。", inline=False)
        await ctx.send(embed=help_embed)
        logger.info("helpを表示")

    @commands.command(name="c")
    @commands.guild_only()
    async def connect(self, ctx: commands.Context) -> None:
        if ctx.author.voice is None:
            await ctx.channel.send(f"{ctx.author.mention}さんはボイスチャンネルに接続していません")
            logger.info("%sさんはボイスチャンネルに接続していません", ctx.author)
            return

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
        await ctx.send("話者を選んでください", view=view)
        connected_embed = discord.Embed(title="読み上げ開始", color=EMBED_COLOR)
        connected_embed.add_field(name="テキストチャンネル", value=f"{ctx.channel.name}", inline=False)
        connected_embed.add_field(name="ボイスチャンネル", value=f"{voice_channel.name}", inline=False)
        await ctx.send(embed=connected_embed)
        logger.info("%sに接続しました", voice_channel.name)

        # 辞書を先読みしておき、読み上げ時にファイルI/Oが発生しないようにする
        await self.store.aget(ctx.guild.id)

    @commands.command(name="dc")
    @commands.guild_only()
    async def disconnect(self, ctx: commands.Context) -> None:
        voice_client = ctx.guild.voice_client
        if voice_client is None:
            await ctx.channel.send("ボイスチャンネルに接続していません")
            logger.info("ボイスチャンネルに接続していません")
            return

        session = self.sessions.get(ctx.guild.id)
        if session is not None and session.is_connected:
            await session.close()
        else:
            await voice_client.disconnect(force=True)
        await ctx.channel.send("切断しました")
        logger.info("切断しました")

    @commands.command(name="s")
    @commands.guild_only()
    async def skip(self, ctx: commands.Context, *args: str) -> None:
        session = self.sessions.get(ctx.guild.id)
        if session is None or not session.is_connected:
            await ctx.channel.send("ボイスチャンネルに接続していません")
            return

        if not args:
            if session.skip():
                await ctx.channel.send("スキップしました")
                logger.info("読み上げをスキップしました (guild=%s)", ctx.guild.id)
            else:
                await ctx.channel.send("読み上げ中のメッセージはありません")
            return

        if args == ("all",):
            dropped = session.skip_all()
            if dropped:
                await ctx.channel.send(f"読み上げを{dropped}件スキップしました")
                logger.info("読み上げを%d件スキップしました (guild=%s)", dropped, ctx.guild.id)
            else:
                await ctx.channel.send("読み上げ中のメッセージはありません")
            return

        await ctx.channel.send(f"コマンドが間違っています。`{self.config.prefix}help`を参照してください")

    # --- イベント ---
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # プレフィックス付きはコマンドとして Bot 側で処理されるので読み上げない
        if message.content.startswith(self.config.prefix):
            return
        # botの発言は無視
        if message.author.bot:
            return
        # DMなど、サーバー外のメッセージは無視
        guild = message.guild
        if guild is None:
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
        speaker, speed = resolve_voice(
            settings, message.author.id, self.available_speakers, self.engine.default_speaker
        )
        if session.enqueue(read_msg, speaker, speed):
            logger.info(
                "読み上げキューに追加しました (guild=%s, %d文字, %s, %s倍)", guild.id, len(read_msg), speaker, speed
            )
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
                await session.close()
            elif before.channel != after.channel and len(after.channel.members) == 1:
                # 誰もいないチャンネルへ移動させられた
                await session.close(notice=AFK_NOTICE)
            return

        voice_client = guild.voice_client
        if (
            voice_client is not None
            and voice_client.channel == before.channel
            and len(voice_client.channel.members) == 1
        ):  # ボイスチャンネルに自分だけ残っていたら
            if session is not None and session.is_connected:
                await session.close(notice=AFK_NOTICE)
            else:
                await voice_client.disconnect(force=True)
                logger.info(AFK_NOTICE)
            return

        await self._announce_voice(member, before, after, session)

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
        if session.enqueue(text, speaker, 1.0):
            logger.info("入退室を読み上げキューに追加しました (guild=%s, %s)", member.guild.id, kind)
