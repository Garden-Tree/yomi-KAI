"""サーバー（ギルド）ごとの読み上げセッション。

1サーバーにつき1つの :class:`GuildSession` が、ボイスクライアント・
読み上げキュー・ワーカータスク・AFKタイマーを保持する。
話者・速度はメッセージごとに呼び出し側（``settings.resolve_voice``）が決めて ``enqueue`` に渡す。

メッセージは ``text.split_for_synthesis`` で文単位のかたまりに分けてから合成する（長文でも最初の文から
すぐに再生を始められる）。ワーカーは、再生前のかたまりが最大 ``PREFETCH_CHUNKS`` 個になるよう、メッセージを
またいで次のかたまりの合成を並行に始め（``asyncio.to_thread``）、かたまりを必ずキュー投入順に1つずつ再生する。
スキップはメッセージ単位（残りのかたまりもまとめて飛ばす）。
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import os
import shutil
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .text import split_for_synthesis
from .tts import TTSEngine

logger = logging.getLogger(__name__)

AFK_NOTICE = "自動切断しました"
SYNTH_FAILED_NOTICE = "読み上げに失敗しました"
# 1サーバーあたり、再生前に先行して合成しておくかたまりの最大数（= 同時に合成する最大数）
PREFETCH_CHUNKS = 3


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


@dataclass(eq=False)
class _Message:
    """読み上げキューの1メッセージ。合成・再生はかたまり（chunks）ごとに行い、スキップはメッセージ単位。"""

    chunks: list[str]
    speaker: str
    speed: float
    epoch: int
    next_chunk: int = 0  # 次に合成を始めるかたまりの位置
    skipped: bool = False
    failure_notified: bool = False


@dataclass(eq=False)
class _Job:
    """1かたまりの合成タスク。"""

    message: _Message
    task: asyncio.Task[bytes]


def _consume_result(task: asyncio.Task[bytes]) -> None:
    # 捨てたタスクの例外で「Task exception was never retrieved」が出ないようにする
    if not task.cancelled():
        task.exception()


class GuildSession:
    def __init__(
        self,
        guild_id: int,
        engine: TTSEngine,
        *,
        afk_time: float,
        source_factory: SourceFactory,
        prefetch_chunks: int = PREFETCH_CHUNKS,
    ) -> None:
        self.guild_id = guild_id
        self.engine = engine
        self.afk_time = afk_time
        self.prefetch_chunks = prefetch_chunks
        self.voice_client: VoiceClientLike | None = None
        self.text_channel: MessageableLike | None = None
        self._source_factory = source_factory
        # まだ合成を始めていないかたまりが残っているメッセージ（投入順）
        self._messages: deque[_Message] = deque()
        # 合成中・合成済みで、まだ再生を始めていないかたまり（再生順）。長さは prefetch_chunks まで
        self._pipeline: deque[_Job] = deque()
        # 再生中のかたまりのメッセージ
        self._playing: _Message | None = None
        # skip_all で世代を進め、古い世代のメッセージは再生しない
        self._epoch = 0
        self._wakeup = asyncio.Event()
        self._worker: asyncio.Task[None] | None = None
        self._afk_task: asyncio.Task[None] | None = None

    # --- 状態 ---
    @property
    def is_connected(self) -> bool:
        return self.voice_client is not None

    @property
    def pending(self) -> int:
        """合成を始めていないかたまりが残っているメッセージの数。"""
        return len(self._messages)

    @property
    def is_busy(self) -> bool:
        """合成待ち・合成中・再生中のいずれかなら True。"""
        voice_client = self.voice_client
        playing = voice_client is not None and voice_client.is_playing()
        return playing or self._playing is not None or bool(self._pipeline) or bool(self._messages)

    # --- 接続・切断 ---
    async def start(self, voice_client: VoiceClientLike, text_channel: MessageableLike) -> None:
        """接続（または移動）直後に呼ぶ。ワーカーを起動し、AFKタイマーを張る。"""
        self.voice_client = voice_client
        self.text_channel = text_channel
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run_worker(), name=f"yomi-kai-worker-{self.guild_id}")
        self._reset_afk_timer()

    async def close(self, notice: str | None = None) -> None:
        """ワーカー停止・合成中のタスクの取り消し・キュー破棄・再生停止・切断をまとめて行う。複数回呼んでも安全。"""
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
        self._cancel_pipeline()

        dropped = len(self._messages)
        self._messages.clear()
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
    def enqueue(self, text: str, speaker: str, speed: float = 1.0) -> bool:
        """読み上げキューに追加する（合成用のかたまりに分ける）。未接続なら何もせず False。AFKタイマーを張り直す。"""
        if not self.is_connected:
            return False
        chunks = split_for_synthesis(text) or [text]
        self._messages.append(_Message(chunks, speaker, speed, self._epoch))
        self._wakeup.set()
        self._reset_afk_timer()
        return True

    def _current_message(self) -> _Message | None:
        """いま再生中、または次に再生する（合成を待っている）メッセージ。"""
        if self._playing is not None:
            return self._playing
        for job in self._pipeline:
            if self._is_live(job.message):
                return job.message
        return None

    def skip(self) -> bool:
        """いま読み上げている（または合成中の）メッセージの残りをすべて飛ばして次へ進む。対象がなければ False。"""
        message = self._current_message()
        if message is None:
            return False
        message.skipped = True
        for job in self._pipeline:
            if job.message is message:
                job.task.cancel()
        voice_client = self.voice_client
        if self._playing is message and voice_client is not None and voice_client.is_playing():
            voice_client.stop()  # after コールバック経由でワーカーが次へ進む
        return True

    def skip_all(self) -> int:
        """再生中・合成中・未再生のものをすべて読み上げ中止する。中止したメッセージの数を返す。"""
        messages = []
        if self._playing is not None and self._is_live(self._playing):
            messages.append(self._playing)
        messages += [job.message for job in self._pipeline if self._is_live(job.message)]
        messages += [m for m in self._messages if self._is_live(m)]
        dropped = len({id(m) for m in messages})

        self._epoch += 1  # 合成中・合成済みのかたまりは再生前に世代で弾く
        self._messages.clear()
        for job in self._pipeline:
            job.task.cancel()
        voice_client = self.voice_client
        if voice_client is not None and voice_client.is_playing():
            voice_client.stop()
        return dropped

    def _is_live(self, message: _Message) -> bool:
        return not message.skipped and message.epoch == self._epoch

    async def _synthesize(self, text: str, speaker: str, speed: float) -> bytes:
        return await asyncio.to_thread(self.engine.synthesize, text, speaker, speed)

    def _fill_pipeline(self) -> None:
        """再生前のかたまりが prefetch_chunks 個になるまで、次のかたまりの合成を始める（メッセージをまたぐ）。"""
        while len(self._pipeline) < self.prefetch_chunks and self._messages:
            message = self._messages[0]
            if not self._is_live(message) or message.next_chunk >= len(message.chunks):
                self._messages.popleft()
                continue
            text = message.chunks[message.next_chunk]
            message.next_chunk += 1
            task = asyncio.create_task(self._synthesize(text, message.speaker, message.speed))
            task.add_done_callback(_consume_result)
            self._pipeline.append(_Job(message, task))

    def _cancel_pipeline(self) -> None:
        for job in self._pipeline:
            job.task.cancel()
        self._pipeline.clear()
        self._playing = None

    async def _run_worker(self) -> None:
        try:
            while True:
                self._fill_pipeline()
                if not self._pipeline:
                    self._wakeup.clear()
                    await self._wakeup.wait()
                    continue

                job = self._pipeline[0]
                await asyncio.wait([job.task])  # タスクの失敗・取り消しはここでは送出されない
                self._pipeline.popleft()
                message = job.message
                if not self._is_live(message) or job.task.cancelled():
                    continue
                error = job.task.exception()
                if error is not None:
                    logger.error("音声合成に失敗しました (guild=%s)", self.guild_id, exc_info=error)
                    if not message.failure_notified:  # 1メッセージにつき1回だけ通知する
                        message.failure_notified = True
                        await self._notify(SYNTH_FAILED_NOTICE)
                    continue

                self._playing = message
                try:
                    self._fill_pipeline()  # 再生中に次のかたまりを合成しておく
                    await self._play(job.task.result())
                finally:
                    self._playing = None
        finally:
            self._cancel_pipeline()

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

    async def _afk_timer(self) -> None:
        # 読み上げ中は切断せず、終わってからさらに AFK_TIME 待つ
        await asyncio.sleep(self.afk_time)
        while self.is_busy:
            await asyncio.sleep(self.afk_time)
        await self.close(notice=AFK_NOTICE)
