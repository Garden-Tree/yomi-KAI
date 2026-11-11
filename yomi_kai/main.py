"""起動処理。import しただけでは何も起きない（ログ設定・モデル読み込み等は main() 内で行う）。"""

from __future__ import annotations

import asyncio
import logging
import math
import signal
import sys
import time
from collections.abc import Iterable
from pathlib import Path

import discord
from discord.ext import commands

from .blocklist import CHECK_INTERVAL, GuildBlocklist
from .cogs.common import original_error
from .config import Config, ConfigError, load_config
from .dictionary import DictionaryStore, UserDictionaryStore
from .health import DisconnectAlerter, heartbeat_loop, mask_url, webhook_sender
from .plans import CHECK_INTERVAL as PREMIUM_CHECK_INTERVAL
from .plans import PlanManager
from .rejoin import VoiceSessionRegistry
from .settings import SettingsStore
from .status import bot_version
from .status_push import StatusPusher, build_payload, http_poster
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


def apply_speaker_allowlist(
    engine: TTSEngine, allowlist: tuple[str, ...], key: str = "SPEAKER_ALLOWLIST"
) -> TTSEngine:
    """SPEAKER_ALLOWLIST（``key``）が指定されていれば、話者をその名前に絞ったエンジンを返す。

    存在しない名前は警告し、使える話者が1つも残らなければ :class:`ConfigError` を送出する。
    """
    if not allowlist:
        return engine
    from .tts import FilteredEngine

    try:
        filtered = FilteredEngine(engine, allowlist)
    except ValueError:
        raise ConfigError(
            f"config.ini の `{key}` に使える話者がありません。"
            f"`{'` `'.join(allowlist)}` はいずれも見つかりませんでした"
            "（話者の名前は `voice list` の表示どおりにカンマ区切りで指定してください）。"
        ) from None
    if filtered.unknown:
        logger.warning("%s の次の話者は見つからないため無視します: %s", key, "、".join(filtered.unknown))
    logger.info(
        "%s: 話者を%d件に絞りました（既定の話者: %s）", key, len(filtered.speakers()), filtered.default_speaker
    )
    return filtered


def build_plans(config: Config, engine: TTSEngine) -> PlanManager:
    """読み込んだ全話者のエンジンから、フリー・プレミアムで使える話者に絞ったエンジンを作り、プランを組み立てる。

    使える話者が1つも残らなければ :class:`ConfigError` を送出する。
    """
    free_engine = apply_speaker_allowlist(engine, config.speaker_allowlist)
    premium_engine = None
    if config.premium_enabled:
        premium_engine = apply_speaker_allowlist(
            engine, config.premium_speaker_allowlist, key="PREMIUM_SPEAKER_ALLOWLIST"
        )
    return PlanManager(config, free_engine, premium_engine=premium_engine, base_engine=engine)


class YomiBot(commands.Bot):
    def __init__(
        self,
        config: Config,
        engine: TTSEngine,
        store: DictionaryStore,
        settings: SettingsStore,
        usage: UsageStore | None = None,
        plans: PlanManager | None = None,
        user_store: UserDictionaryStore | None = None,
        voice_sessions: VoiceSessionRegistry | None = None,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        # コマンドは利用者の入力を返すことがある（例: y.sd @everyone）ので、メンションで通知しない
        super().__init__(
            command_prefix=config.prefix,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.config = config
        self.engine = engine
        self.store = store
        # 個人辞書（既定はサーバー辞書のディレクトリの users/）
        self.user_store = user_store if user_store is not None else UserDictionaryStore(store.directory / "users")
        self.settings = settings
        # 接続中のボイスチャンネルの記録（再起動後の自動再接続。既定は settings/voice_sessions.json）
        self.voice_sessions = (
            voice_sessions
            if voice_sessions is not None
            else VoiceSessionRegistry(settings.directory / "voice_sessions.json")
        )
        # サーバーごとの上限と使える話者（engine はフリーで使える話者に絞ったエンジン）
        self.plans = plans if plans is not None else PlanManager(config, engine)
        self._premium_task: asyncio.Task[None] | None = None
        # 起動した時刻（y.status の稼働時間）
        self.started_at = time.time()
        # 使用量の計測（RECORD_USAGE = True のときだけ。本文は保存しない）
        if usage is None and config.record_usage:
            usage = UsageStore(config.usage_dir)
        self.usage: UsageStore | None = usage
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._usage_task: asyncio.Task[None] | None = None
        # 利用を断るサーバー。ブロックされたサーバーのコマンド・スラッシュコマンドはグローバルチェックで無視する
        self.blocklist = GuildBlocklist(config.blocked_guilds_file)
        self._blocklist_task: asyncio.Task[None] | None = None
        # SIGTERM を受けて始めた終了処理
        self._close_task: asyncio.Task[None] | None = None
        self.add_check(self._not_blocked)
        # on_disconnect で True、on_ready / on_resumed で False（is_ready() は切断中も True のままのことがある）
        self._gateway_down = True
        self.alerter: DisconnectAlerter | None = None
        if config.alert_webhook_url:
            self.alerter = DisconnectAlerter(webhook_sender(config.alert_webhook_url), alert_after=config.alert_after)
            logger.info("切断通知を有効にしました (%s, %d秒)", mask_url(config.alert_webhook_url), config.alert_after)
        self.status_pusher: StatusPusher | None = None
        self._status_push_task: asyncio.Task[None] | None = None
        if config.status_push_url and config.status_push_token:
            self.status_pusher = StatusPusher(
                http_poster(config.status_push_url, config.status_push_token),
                self.status_payload,
                self.is_gateway_alive,
                interval=config.status_push_interval,
            )
            logger.info(
                "稼働状況の送信を有効にしました (%s, %d秒ごと)",
                mask_url(config.status_push_url),
                config.status_push_interval,
            )

    # --- 接続監視（yomi_kai.health） ---
    def is_gateway_alive(self) -> bool:
        if self._gateway_down:
            return False
        return self.is_ready() and not self.is_closed() and math.isfinite(self.latency)

    def status_payload(self) -> dict:
        """ステータスページに送る稼働状況（yomi_kai.status_push）。"""
        latency = getattr(self.get_cog("VoiceCog"), "latency", None)
        summary = latency.summary() if latency is not None else None
        return build_payload(bot_version(), summary, len(self.voice_clients))

    def _start_monitoring(self) -> None:
        self._heartbeat_task = asyncio.create_task(heartbeat_loop(self.is_gateway_alive), name="yomi-kai-heartbeat")
        if self.status_pusher is not None:
            self._status_push_task = asyncio.create_task(self.status_pusher.run(), name="yomi-kai-status-push")
        if self.usage is not None:
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
        if self._status_push_task is not None:
            self._status_push_task.cancel()
        if self._blocklist_task is not None:
            self._blocklist_task.cancel()
        if self._premium_task is not None:
            self._premium_task.cancel()
        if self.alerter is not None:
            await self.alerter.close()
        # Cog の取り外し（読み上げの停止）まで済んでから、残りの使用量を書き込む
        await super().close()
        if self.usage is None:
            return
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

    async def _watch_premium(self, interval: float = PREMIUM_CHECK_INTERVAL) -> None:
        """プレミアムの一覧（settings/premium.json）の更新を interval 秒ごとに確認する。"""
        while True:
            await asyncio.sleep(interval)
            try:
                await asyncio.to_thread(self.plans.reload)
            except Exception:
                logger.exception("プレミアムの一覧の確認に失敗しました")

    # --- 終了（docker stop の SIGTERM） ---
    def install_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> bool:
        """SIGTERM で ``close()`` するようにする。登録できなければ（Windows など）False。

        Docker では Python が PID 1 で動くため、ハンドラが無いと SIGTERM が無視され、docker stop が
        10秒後に SIGKILL して終了処理（読み上げの停止・使用量の書き込み）が行われない。
        """
        try:
            loop.add_signal_handler(signal.SIGTERM, self._on_sigterm)
        except (NotImplementedError, RuntimeError, AttributeError, ValueError):
            logger.debug("SIGTERM のハンドラを登録できませんでした（この環境では使えません）")
            return False
        return True

    def _on_sigterm(self) -> None:
        if self._close_task is not None:
            return
        logger.info("SIGTERM を受け取ったため終了します")
        self._close_task = asyncio.create_task(self.close(), name="yomi-kai-close")

    async def setup_hook(self) -> None:
        self.install_signal_handlers(asyncio.get_running_loop())
        self._start_monitoring()
        await asyncio.to_thread(self.blocklist.reload_if_changed)
        self._blocklist_task = asyncio.create_task(self._watch_blocklist(), name="yomi-kai-blocklist")
        if self.plans.enabled:
            await asyncio.to_thread(self.plans.reload)
            self._premium_task = asyncio.create_task(self._watch_premium(), name="yomi-kai-premium")

        from .cogs.admin import AdminCog
        from .cogs.dictionary import DictionaryCog
        from .cogs.misc import MiscCog, SuddenDeathCog
        from .cogs.plan import PlanCog
        from .cogs.settings import SettingsCog
        from .cogs.voice import VoiceCog
        from .session import ffmpeg_available, ffmpeg_source_factory, find_ffmpeg

        ffmpeg_path = find_ffmpeg()
        logger.debug("ffmpeg: %s", ffmpeg_path)
        if not ffmpeg_available(ffmpeg_path):
            logger.warning(
                "ffmpeg が見つかりません。./lib/ffmpeg.exe・./ffmpeg.exe に置くか、PATH に追加してください"
                "（このままでは読み上げを再生できません）"
            )
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
                plans=self.plans,
                user_store=self.user_store,
                voice_sessions=self.voice_sessions,
            )
        )
        await self.add_cog(SettingsCog(self, self.config, self.engine, self.settings, plans=self.plans))
        await self.add_cog(
            DictionaryCog(self, self.config, self.store, plans=self.plans, user_store=self.user_store)
        )
        await self.add_cog(MiscCog(self, self.config, self.engine, plans=self.plans))
        if self.config.enable_sudden_death:
            # 読み上げと関係ない機能なので、公開版では ENABLE_SUDDEN_DEATH = False で外せる
            await self.add_cog(SuddenDeathCog(self, self.config))
        await self.add_cog(AdminCog(self, self.config, self.settings))
        await self.add_cog(PlanCog(self, self.config, self.plans, self.store, self.user_store))
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
        if isinstance(original_error(error), commands.CheckFailure):
            # 権限不足などは各 Cog の cog_command_error が案内を返す
            logger.info("コマンドの実行条件を満たしていません: %s", original_error(error))
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
        plans = build_plans(config, engine)
    except ConfigError as e:
        logger.error("%s", e)
        sys.exit(1)

    Path("dict").mkdir(exist_ok=True)
    Path("settings").mkdir(exist_ok=True)
    bot = YomiBot(config, plans.free_engine, DictionaryStore("dict"), SettingsStore("settings"), plans=plans)
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
