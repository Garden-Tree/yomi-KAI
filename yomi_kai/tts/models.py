"""音声モデル（*.vvm）の読み込みと解放を管理する（voicevox_core を import しない）。

起動時に読み込むモデル（既定は 0.vvm）は最後まで解放しない。それ以外の「追加のモデル」は、その話者で
初めて合成するときに全 Synthesizer へ読み込み、しばらく使われなければ解放する。同時に読み込んでおく
追加のモデルの数には上限があり、超えるときは最も長く使われていないモデル（合成中でないもの）を解放する。

このクラスのメソッドは、すべて VOICEVOX 専用スレッドのイベントループ上で呼ぶ
（:class:`~yomi_kai.tts.pool.LoopThread`）。状態の変更は ``await`` をまたがないので、
読み込み中の二重読み込みだけをモデルごとの ``asyncio.Lock`` で防ぐ。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 追加のモデルを同時に読み込んでおく数の既定値（1つあたり、ワーカーごとに約120〜200MB増える）
DEFAULT_MAX_EXTRA_MODELS = 1
# 追加のモデルを解放するまでの、使われていない時間の既定値（秒）
DEFAULT_MODEL_IDLE = 600
# 使われていないモデルを確認する間隔の上限（秒）
SWEEP_INTERVAL = 60.0


def current_rss_mb() -> float | None:
    """このプロセスの常駐メモリ（MB）。psutil も /proc も使えなければ None（ログを省略する）。"""
    try:
        import psutil  # 任意。依存には加えない
    except ImportError:
        pass
    else:
        return psutil.Process().memory_info().rss / 2**20
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except (OSError, ValueError):
        pass
    return None


def memory_note(before: float | None, after: float | None) -> str:
    """ログに付けるメモリの変化（例: 「、メモリ 250MB → 380MB」）。測れなければ空文字列。"""
    return f"、メモリ {before:.0f}MB → {after:.0f}MB" if before and after else ""


@dataclass
class ModelEntry:
    """1つの vvm ファイル。"""

    name: str
    path: Path
    # VoiceModelFile.id（解放に使う）
    model_id: Any
    # True なら起動時に読み込み、解放しない
    eager: bool
    loaded: bool = False
    # このモデルで合成中（または読み込みを待っている）数。0 でなければ解放しない
    users: int = 0
    last_used: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class ModelManager:
    """vvm ファイルを Synthesizer へ読み込み・解放する。

    ``opener(path)`` は ``VoiceModelFile.open`` と同じく、``async with`` で使えるモデルを返すコルーチン。
    """

    def __init__(
        self,
        synthesizers: Sequence[Any],
        opener: Callable[[Path], Awaitable[Any]] | None,
        *,
        max_extra: int = DEFAULT_MAX_EXTRA_MODELS,
        idle: float = DEFAULT_MODEL_IDLE,
        clock: Callable[[], float] = time.monotonic,
        rss: Callable[[], float | None] = current_rss_mb,
    ) -> None:
        if max_extra < 1:
            raise ValueError(f"max_extra は1以上で指定してください: {max_extra}")
        self._synthesizers = list(synthesizers)
        self._open = opener
        self.max_extra = max_extra
        self.idle = idle
        self._clock = clock
        self._rss = rss
        self._entries: dict[str, ModelEntry] = {}

    def add(self, entry: ModelEntry) -> None:
        self._entries[entry.name] = entry

    def entry(self, name: str) -> ModelEntry:
        return self._entries[name]

    @property
    def has_extra(self) -> bool:
        return any(not e.eager for e in self._entries.values())

    def loaded_extra(self) -> list[str]:
        """読み込み済みの追加のモデル（最も長く使われていない順）。"""
        loaded = [e for e in self._entries.values() if e.loaded and not e.eager]
        return [e.name for e in sorted(loaded, key=lambda e: e.last_used)]

    async def _load_into_all(self, entry: ModelEntry, model: Any) -> None:
        loaded: list[Any] = []
        try:
            for synthesizer in self._synthesizers:
                await synthesizer.load_voice_model(model)
                loaded.append(synthesizer)
        except BaseException:
            # 一部の Synthesizer にだけ読み込まれた状態を残さない
            for synthesizer in loaded:
                try:
                    synthesizer.unload_voice_model(entry.model_id)
                except Exception:  # noqa: BLE001 - 元の例外を優先する
                    logger.debug("読み込みに失敗したモデルを解放できませんでした: %s", entry.name, exc_info=True)
            raise
        entry.loaded = True

    async def load_eager(self, entry: ModelEntry, model: Any) -> None:
        """起動時に読み込むモデルを（開いてある ``model`` から）全 Synthesizer へ読み込む。"""
        await self._load_into_all(entry, model)
        entry.last_used = self._clock()

    @asynccontextmanager
    async def use(self, name: str | None) -> AsyncIterator[None]:
        """``name`` のモデルを読み込んだ状態で使う（使っている間は解放しない）。None や不明な名前なら何もしない。"""
        entry = self._entries.get(name) if name is not None else None
        if entry is None:
            yield
            return
        entry.users += 1
        try:
            if not entry.loaded:
                await self._ensure_loaded(entry)
            yield
        finally:
            entry.users -= 1
            entry.last_used = self._clock()

    async def _ensure_loaded(self, entry: ModelEntry) -> None:
        async with entry.lock:
            if entry.loaded:
                return
            if self._open is None:
                raise RuntimeError(f"モデルを開けません: {entry.name}")
            self._make_room()
            before = self._rss()
            started = time.perf_counter()
            async with await self._open(entry.path) as model:
                await self._load_into_all(entry, model)
            elapsed = time.perf_counter() - started
            logger.info(
                "VOICEVOXモデルを読み込みました（話者の指定による）: %s（%.1f秒%s、追加のモデル %d/%d）",
                entry.name,
                elapsed,
                memory_note(before, self._rss()),
                len(self.loaded_extra()),
                self.max_extra,
            )

    def _make_room(self) -> None:
        """追加のモデルが上限に達していれば、合成中でない最も古いモデルを解放する。"""
        while len(self.loaded_extra()) >= self.max_extra:
            idle = [self._entries[n] for n in self.loaded_extra() if self._entries[n].users == 0]
            if not idle:
                logger.warning(
                    "追加のモデルがすべて合成中のため、上限（%d）を超えて読み込みます", self.max_extra
                )
                return
            self._unload(idle[0], "上限に達したため")

    def _unload(self, entry: ModelEntry, reason: str) -> None:
        before = self._rss()
        for synthesizer in self._synthesizers:
            synthesizer.unload_voice_model(entry.model_id)
        entry.loaded = False
        logger.info("VOICEVOXモデルを解放しました（%s）: %s%s", reason, entry.name, memory_note(before, self._rss()))

    def sweep(self) -> list[str]:
        """``idle`` 秒以上使われていない追加のモデルを解放する。解放したモデルの名前を返す。"""
        now = self._clock()
        released = []
        for name in self.loaded_extra():
            entry = self._entries[name]
            if entry.users == 0 and now - entry.last_used >= self.idle:
                self._unload(entry, f"{self.idle:.0f}秒使われなかったため")
                released.append(name)
        return released

    async def run_sweeper(self) -> None:
        """使われていない追加のモデルを定期的に解放し続ける（取り消されるまで）。"""
        interval = max(1.0, min(SWEEP_INTERVAL, self.idle / 2))
        while True:
            await asyncio.sleep(interval)
            try:
                self.sweep()
            except Exception:
                logger.exception("VOICEVOXモデルの解放に失敗しました")
