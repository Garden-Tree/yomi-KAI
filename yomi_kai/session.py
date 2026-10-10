"""サーバー（ギルド）ごとの読み上げセッション。

1サーバーにつき1つの :class:`GuildSession` が、ボイスクライアント・選択中の話者・
読み上げキュー・ワーカータスク・AFKタイマーを保持する。

ワーカーはキューから取り出したテキストをスレッドで合成し（``asyncio.to_thread``）、
1件ずつ順番に再生する。再生中に次の1件だけ先行して合成するが、再生順は必ずキュー投入順になる。
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import shutil
from collections.abc import Callable
from typing import Any, Protocol

from .tts import TTSEngine

logger = logging.getLogger(__name__)

AFK_NOTICE = "自動切断しました"
SYNTH_FAILED_NOTICE = "読み上げに失敗しました"


class VoiceClientLike(Protocol):
    def play(self, source: Any, *, after: Callable[[Exception | None], Any] | None = None) -> None: ...
    def is_playing(self) -> bool: ...
    def stop(self) -> None: ...
    async def disconnect(self, *, force: bool = False) -> None: ...


class MessageableLike(Protocol):
    async def send(self, content: str) -> Any: ...


SourceFactory = Callable[[bytes], Any]


def find_ffmpeg() -> str:
    """ffmpeg の実行ファイル: ./lib/ffmpeg.exe → ./ffmpeg.exe → PATH 上の ffmpeg の順に探す。"""
    for candidate in ("./lib/ffmpeg.exe", "./ffmpeg.exe"):
        if os.path.isfile(candidate):
            return candidate
    return shutil.which("ffmpeg") or "ffmpeg"


def ffmpeg_source_factory(ffmpeg_path: str) -> SourceFactory:
    """WAV のバイト列から FFmpegPCMAudio を作るファクトリを返す（一時ファイルは使わない）。"""
    import discord

    def factory(wav: bytes) -> Any:
        return discord.FFmpegPCMAudio(io.BytesIO(wav), pipe=True, executable=ffmpeg_path)

    return factory


class GuildSession:
    def __init__(
        self,
        guild_id: int,
        engine: TTSEngine,
        *,
        afk_time: float,
        source_factory: SourceFactory,
    ) -> None:
        self.guild_id = guild_id
        self.engine = engine
        self.afk_time = afk_time
        self.speaker: str = engine.default_speaker
        self.voice_client: VoiceClientLike | None = None
        self.text_channel: MessageableLike | None = None
        self._source_factory = source_factory
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        self._afk_task: asyncio.Task[None] | None = None
        # ワーカーがキュー待ちで何も抱えていないとき True（合成中・再生中は False）
        self._idle = True

    # --- 状態 ---
    @property
    def is_connected(self) -> bool:
        return self.voice_client is not None

    @property
    def pending(self) -> int:
        """未再生（合成待ち）のテキスト数。"""
        return self._queue.qsize()

    # --- 接続・切断 ---
    async def start(self, voice_client: VoiceClientLike, text_channel: MessageableLike) -> None:
        """接続（または移動）直後に呼ぶ。ワーカーを起動し、AFKタイマーを張る。"""
        self.voice_client = voice_client
        self.text_channel = text_channel
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker(), name=f"yomi-kai-worker-{self.guild_id}")
        self._reset_afk_timer()

    async def close(self, notice: str | None = None) -> None:
        """ワーカー停止・キュー破棄・再生停止・切断をまとめて行う。複数回呼んでも安全。"""
        voice_client, text_channel = self.voice_client, self.text_channel
        self.voice_client = None
        self.text_channel = None

        afk_task, self._afk_task = self._afk_task, None
        if afk_task is not None and afk_task is not asyncio.current_task():
            afk_task.cancel()

        worker, self._worker = self._worker, None
        if worker is not None and worker is not asyncio.current_task():
            worker.cancel()
            # asyncio.wait はワーカー側の例外を送出しない（close 自体のキャンセルは伝播する）
            await asyncio.wait([worker])

        dropped = 0
        while not self._queue.empty():
            self._queue.get_nowait()
            dropped += 1
        if dropped:
            logger.info("未読み上げのメッセージ%d件を破棄しました (guild=%s)", dropped, self.guild_id)

        if voice_client is None:
            return

        try:
            if voice_client.is_playing():
                voice_client.stop()
        except Exception:
            logger.exception("再生の停止に失敗しました (guild=%s)", self.guild_id)
        try:
            await voice_client.disconnect(force=True)
        except Exception:
            logger.exception("ボイスチャンネルからの切断に失敗しました (guild=%s)", self.guild_id)

        if notice:
            logger.info(notice)
            if text_channel is not None:
                try:
                    await text_channel.send(notice)
                except Exception:
                    logger.exception("切断通知の送信に失敗しました (guild=%s)", self.guild_id)

    # --- 読み上げ ---
    def enqueue(self, text: str) -> bool:
        """読み上げキューに追加する。未接続なら何もせず False。AFKタイマーを張り直す。"""
        if not self.is_connected:
            return False
        self._queue.put_nowait((text, self.speaker))
        self._reset_afk_timer()
        return True

    async def _synthesize(self, text: str, speaker: str) -> bytes:
        return await asyncio.to_thread(self.engine.synthesize, text, speaker)

    async def _run_worker(self) -> None:
        prefetched: asyncio.Task[bytes] | None = None
        try:
            while True:
                if prefetched is None:
                    self._idle = True
                    text, speaker = await self._queue.get()
                    self._idle = False
                    prefetched = asyncio.create_task(self._synthesize(text, speaker))
                current, prefetched = prefetched, None
                try:
                    wav = await current
                except Exception:
                    logger.exception("音声合成に失敗しました (guild=%s)", self.guild_id)
                    await self._notify(SYNTH_FAILED_NOTICE)
                    wav = None

                # 再生中に次の1件だけ先行して合成しておく（再生順はキュー順のまま）
                if not self._queue.empty():
                    next_text, next_speaker = self._queue.get_nowait()
                    prefetched = asyncio.create_task(self._synthesize(next_text, next_speaker))

                if wav is not None:
                    await self._play(wav)
        finally:
            self._idle = True
            if prefetched is not None:
                prefetched.cancel()

    async def _play(self, wav: bytes) -> None:
        voice_client = self.voice_client
        if voice_client is None:
            return
        loop = asyncio.get_running_loop()
        finished = asyncio.Event()

        def after(error: Exception | None) -> None:
            # 音声スレッドから呼ばれるので、イベントループ側に処理を戻す
            if error:
                logger.error("再生中にエラーが発生しました: %s", error)
            with contextlib.suppress(RuntimeError):  # ループ終了後に呼ばれた場合
                loop.call_soon_threadsafe(finished.set)

        # FFmpeg プロセスは再生直前にだけ生成する
        source = self._source_factory(wav)
        try:
            voice_client.play(source, after=after)
        except Exception:
            logger.exception("再生の開始に失敗しました (guild=%s)", self.guild_id)
            cleanup = getattr(source, "cleanup", None)
            if cleanup is not None:
                cleanup()
            return
        await finished.wait()

    async def _notify(self, content: str) -> None:
        if self.text_channel is None:
            return
        try:
            await self.text_channel.send(content)
        except Exception:
            logger.exception("メッセージの送信に失敗しました (guild=%s)", self.guild_id)

    # --- AFK ---
    def _reset_afk_timer(self) -> None:
        if self._afk_task is not None:
            self._afk_task.cancel()
        self._afk_task = asyncio.create_task(self._afk_timer(), name=f"yomi-kai-afk-{self.guild_id}")

    @property
    def is_busy(self) -> bool:
        """合成待ち・合成中・再生中のいずれかなら True。"""
        voice_client = self.voice_client
        playing = voice_client is not None and voice_client.is_playing()
        return playing or not self._idle or not self._queue.empty()

    async def _afk_timer(self) -> None:
        # 読み上げ中は切断せず、終わってからさらに AFK_TIME 待つ
        await asyncio.sleep(self.afk_time)
        while self.is_busy:
            await asyncio.sleep(self.afk_time)
        await self.close(notice=AFK_NOTICE)
