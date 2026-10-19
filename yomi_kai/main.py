"""起動処理。import しただけでは何も起きない（ログ設定・モデル読み込み等は main() 内で行う）。"""

from __future__ import annotations

import asyncio
import logging
import math
import sys
from pathlib import Path

import discord
from discord.ext import commands

from .config import Config, ConfigError, load_config
from .dictionary import DictionaryStore
from .health import DisconnectAlerter, heartbeat_loop, mask_url, webhook_sender
from .settings import SettingsStore
from .tts import TTSEngine

logger = logging.getLogger(__name__)


def build_engine(config: Config) -> TTSEngine:
    """設定に応じて音声合成エンジンを構築する。重いライブラリはここで初めて import する。"""
    from .tts import CompositeEngine
    from .tts.voicevox import VoicevoxEngine

    voicevox = VoicevoxEngine(load_all_models=config.voicevox_load_all_models)
    if not config.use_google_tts:
        return voicevox

    from .tts.google import GoogleEngine

    google = GoogleEngine(config.google_api_key or "")
    # Google TTS 有効時は従来どおり Google の話者を先頭・初期設定にする
    return CompositeEngine([google, voicevox], default_speaker=google.default_speaker)


class YomiBot(commands.Bot):
    def __init__(self, config: Config, engine: TTSEngine, store: DictionaryStore, settings: SettingsStore) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix=config.prefix, intents=intents, help_command=None)
        self.config = config
        self.engine = engine
        self.store = store
        self.settings = settings
        self._heartbeat_task: asyncio.Task[None] | None = None
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
        if self.alerter is not None:
            await self.alerter.close()
        await super().close()

    async def setup_hook(self) -> None:
        self._start_monitoring()

        from .cogs.dictionary import DictionaryCog
        from .cogs.misc import MiscCog
        from .cogs.settings import SettingsCog
        from .cogs.voice import VoiceCog
        from .session import ffmpeg_source_factory, find_ffmpeg

        ffmpeg_path = find_ffmpeg()
        logger.debug("ffmpeg: %s", ffmpeg_path)
        await self.add_cog(
            VoiceCog(self, self.config, self.engine, self.store, self.settings, ffmpeg_source_factory(ffmpeg_path))
        )
        await self.add_cog(SettingsCog(self, self.config, self.engine, self.settings))
        await self.add_cog(DictionaryCog(self, self.config, self.store))
        await self.add_cog(MiscCog(self, self.config))

    async def on_ready(self) -> None:
        self._gateway_down = False
        if self.alerter is not None:
            self.alerter.connected()
        logger.info("Launch complete! Logged in as [%s]. PREFIX is [%s].", self.user.name, self.config.prefix)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.NoPrivateMessage):
            await ctx.send("このコマンドはサーバー内でのみ使用できます")
            return
        logger.error("コマンド処理中にエラーが発生しました", exc_info=error)


def main(config_path: str = "config.ini") -> None:
    from .logging_setup import setup_logging

    setup_logging("log")

    try:
        config = load_config(config_path)
    except ConfigError as e:
        logger.error("%s", e)
        sys.exit(1)

    try:
        engine = build_engine(config)
    except Exception:
        logger.exception(
            "音声合成エンジンの初期化に失敗しました。"
            "`voicevox_core`フォルダ内にモデルや辞書データが正しく配置されているか確認してください。"
        )
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
