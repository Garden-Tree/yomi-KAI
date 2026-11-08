"""起動処理。import しただけでは何も起きない（ログ設定・モデル読み込み等は main() 内で行う）。"""

from __future__ import annotations

import asyncio
import logging
import math
import sys
from collections.abc import Iterable
from pathlib import Path

import discord
from discord.ext import commands

from .blocklist import CHECK_INTERVAL, GuildBlocklist
from .config import Config, ConfigError, load_config
from .dictionary import DictionaryStore
from .health import DisconnectAlerter, heartbeat_loop, mask_url, webhook_sender
from .settings import SettingsStore
from .tts import TTSEngine
from .usage import UsageStore

logger = logging.getLogger(__name__)


def build_engine(config: Config) -> TTSEngine:
    """設定に応じて音声合成エンジンを構築する。重いライブラリはここで初めて import する。"""
    from .tts import CompositeEngine
    from .tts.voicevox import VoicevoxEngine

    voicevox = VoicevoxEngine(load_all_models=config.voicevox_load_all_models, workers=config.voicevox_workers)
    if not config.use_google_tts:
        return voicevox

    from .tts.google import GoogleEngine

    google = GoogleEngine(config.google_api_key or "")
    # Google TTS 有効時は従来どおり Google の話者を先頭・初期設定にする
    return CompositeEngine([google, voicevox], default_speaker=google.default_speaker)


def apply_speaker_allowlist(engine: TTSEngine, allowlist: tuple[str, ...]) -> TTSEngine:
    """SPEAKER_ALLOWLIST が指定されていれば、話者をその名前に絞ったエンジンを返す。

    存在しない名前は警告し、使える話者が1つも残らなければ :class:`ConfigError` を送出する。
    """
    if not allowlist:
        return engine
    from .tts import FilteredEngine

    try:
        filtered = FilteredEngine(engine, allowlist)
    except ValueError:
        raise ConfigError(
            "config.ini の `SPEAKER_ALLOWLIST` に使える話者がありません。"
            f"`{'` `'.join(allowlist)}` はいずれも見つかりませんでした"
            "（話者の名前は `voice list` の表示どおりにカンマ区切りで指定してください）。"
        ) from None
    if filtered.unknown:
        logger.warning("SPEAKER_ALLOWLIST の次の話者は見つからないため無視します: %s", "、".join(filtered.unknown))
    logger.info(
        "話者を%d件に絞りました（既定の話者: %s）", len(filtered.speakers()), filtered.default_speaker
    )
    return filtered


class YomiBot(commands.Bot):
    def __init__(
        self,
        config: Config,
        engine: TTSEngine,
        store: DictionaryStore,
        settings: SettingsStore,
        usage: UsageStore | None = None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix=config.prefix, intents=intents, help_command=None)
        self.config = config
        self.engine = engine
        self.store = store
        self.settings = settings
        # 使用量の計測（常に有効。本文は保存しない）
        self.usage = usage if usage is not None else UsageStore(config.usage_dir)
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._usage_task: asyncio.Task[None] | None = None
        # 利用を断るサーバー。ブロックされたサーバーのコマンド・スラッシュコマンドはグローバルチェックで無視する
        self.blocklist = GuildBlocklist(config.blocked_guilds_file)
        self._blocklist_task: asyncio.Task[None] | None = None
        self.add_check(self._not_blocked)
        # on_disconnect で True、on_ready / on_resumed で False（is_ready() は切断中も True のままのことがある）
        self._gateway_down = True
        self.alerter: DisconnectAlerter | None = None
        if config.alert_webhook_url:
            self.alerter = DisconnectAlerter(webhook_sender(config.alert_webhook_url), alert_after=config.alert_after)
            logger.info("切断通知を有効にしました (%s, %d秒)", mask_url(config.alert_webhook_url), config.alert_after)

    # --- 接続監視（yomi_kai.health） ---
    def is_gateway_alive(self) -> bool:
        if self._gateway_down:
            return False
        return self.is_ready() and not self.is_closed() and math.isfinite(self.latency)

    def _start_monitoring(self) -> None:
        self._heartbeat_task = asyncio.create_task(heartbeat_loop(self.is_gateway_alive), name="yomi-kai-heartbeat")
        self._usage_task = asyncio.create_task(self.usage.run_flusher(), name="yomi-kai-usage")
        if self.alerter is not None:
            # 起動直後に接続できない状態が続いた場合も通知できるよう、接続完了までは切断中として扱う
            self.alerter.disconnected()

    async def on_disconnect(self) -> None:
        self._gateway_down = True
        if self.alerter is not None:
            self.alerter.disconnected()

    async def on_resumed(self) -> None:
        self._gateway_down = False
        if self.alerter is not None:
            self.alerter.connected()

    async def close(self) -> None:
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
        if self._usage_task is not None:
            self._usage_task.cancel()
        if self._blocklist_task is not None:
            self._blocklist_task.cancel()
        if self.alerter is not None:
            await self.alerter.close()
        # Cog の取り外し（読み上げの停止）まで済んでから、残りの使用量を書き込む
        await super().close()
        try:
            await self.usage.aflush()
        except Exception:
            logger.exception("使用量の書き込みに失敗しました")

    # --- ブロックリスト（yomi_kai.blocklist） ---
    async def _not_blocked(self, ctx: commands.Context) -> bool:
        return not (ctx.guild is not None and self.blocklist.is_blocked(ctx.guild.id))

    async def on_message(self, message: discord.Message) -> None:
        # ブロックされたサーバーのプレフィックスのコマンドは処理しない（読み上げは VoiceCog 側で弾く）
        if message.guild is not None and self.blocklist.is_blocked(message.guild.id):
            return
        await self.process_commands(message)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if self.blocklist.is_blocked(guild.id):
            await self.leave_blocked_guild(guild)

    async def leave_blocked_guild(self, guild: discord.Guild) -> None:
        """ブロックされたサーバーの読み上げを止めて退出する。"""
        logger.warning("ブロックリストにあるサーバーから退出します (guild=%s, name=%s)", guild.id, guild.name)
        voice = self.get_cog("VoiceCog")
        session = getattr(voice, "sessions", {}).get(guild.id)
        if session is not None and session.is_connected:
            await session.close()
        try:
            await guild.leave()
        except Exception:
            logger.exception("サーバーからの退出に失敗しました (guild=%s)", guild.id)

    async def enforce_blocklist(self, guilds: Iterable[discord.Guild] | None = None) -> None:
        """参加中のサーバーのうち、ブロックされているものから退出する。"""
        for guild in list(self.guilds if guilds is None else guilds):
            if self.blocklist.is_blocked(guild.id):
                await self.leave_blocked_guild(guild)

    async def _watch_blocklist(self, interval: float = CHECK_INTERVAL) -> None:
        """ブロックリストの更新を interval 秒ごとに確認し、新たにブロックされたサーバーから退出する。"""
        while True:
            await asyncio.sleep(interval)
            try:
                added = await asyncio.to_thread(self.blocklist.reload_if_changed)
                if added and self.is_ready():
                    await self.enforce_blocklist()
            except Exception:
                logger.exception("ブロックリストの確認に失敗しました")

    async def setup_hook(self) -> None:
        self._start_monitoring()
        await asyncio.to_thread(self.blocklist.reload_if_changed)
        self._blocklist_task = asyncio.create_task(self._watch_blocklist(), name="yomi-kai-blocklist")

        from .cogs.dictionary import DictionaryCog
        from .cogs.misc import MiscCog
        from .cogs.settings import SettingsCog
        from .cogs.voice import VoiceCog
        from .session import ffmpeg_source_factory, find_ffmpeg

        ffmpeg_path = find_ffmpeg()
        logger.debug("ffmpeg: %s", ffmpeg_path)
        await self.add_cog(
            VoiceCog(
                self,
                self.config,
                self.engine,
                self.store,
                self.settings,
                ffmpeg_source_factory(ffmpeg_path),
                usage=self.usage,
                blocklist=self.blocklist,
            )
        )
        await self.add_cog(SettingsCog(self, self.config, self.engine, self.settings))
        await self.add_cog(DictionaryCog(self, self.config, self.store))
        await self.add_cog(MiscCog(self, self.config, self.engine))
        await self.sync_app_commands()

    async def sync_app_commands(self) -> None:
        """スラッシュコマンドを Discord に登録する（全サーバー共通）。失敗しても起動は続ける。

        グローバルコマンドは、登録・変更してから Discord のクライアントに表示されるまで時間がかかることがある。
        """
        try:
            synced = await self.tree.sync()
        except Exception:
            logger.exception("スラッシュコマンドの同期に失敗しました（プレフィックスのコマンドは使えます）")
            return
        logger.info("スラッシュコマンドを%d件同期しました", len(synced))

    async def on_ready(self) -> None:
        self._gateway_down = False
        if self.alerter is not None:
            self.alerter.connected()
        logger.info("Launch complete! Logged in as [%s]. PREFIX is [%s].", self.user.name, self.config.prefix)
        await self.enforce_blocklist()

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if ctx.guild is not None and self.blocklist.is_blocked(ctx.guild.id):
            return  # ブロックされたサーバー（グローバルチェックで弾いたもの）には何も返さない
        if isinstance(error, commands.UserInputError):
            # 引数の誤りは各 Cog の cog_command_error が案内を返す
            logger.debug("コマンドの引数が不正です: %s", error)
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("このコマンドはサーバー内でのみ使用できます")
            return
        logger.error("コマンド処理中にエラーが発生しました", exc_info=error)


def main(config_path: str = "config.ini") -> None:
    from .logging_setup import set_text_logging, setup_logging

    setup_logging("log")

    try:
        config = load_config(config_path)
    except ConfigError as e:
        logger.error("%s", e)
        sys.exit(1)
    set_text_logging(config.log_read_text)

    try:
        engine = build_engine(config)
    except Exception:
        logger.exception(
            "音声合成エンジンの初期化に失敗しました。"
            "`voicevox_core`フォルダ内にモデルや辞書データが正しく配置されているか確認してください。"
        )
        sys.exit(1)
    try:
        engine = apply_speaker_allowlist(engine, config.speaker_allowlist)
    except ConfigError as e:
        logger.error("%s", e)
        sys.exit(1)

    Path("dict").mkdir(exist_ok=True)
    Path("settings").mkdir(exist_ok=True)
    bot = YomiBot(config, engine, DictionaryStore("dict"), SettingsStore("settings"))
    try:
        # ログ設定は setup_logging で済んでいるので、discord.py 独自のハンドラは付けない（二重出力防止）
        bot.run(config.discord_token, log_handler=None)
    except discord.LoginFailure:
        logger.exception("DISCORD_TOKENが不適切です。")
        sys.exit(1)
    except discord.PrivilegedIntentsRequired:
        logger.exception("Developer PortalでPrivileged Gateway Intentsをすべて有効化してください。")
        sys.exit(1)
    except Exception:
        logger.exception("起動に失敗しました。")
        sys.exit(1)


if __name__ == "__main__":
    main()
