"""音声合成エンジンの共通インターフェース。

このモジュールは voicevox_core / google-cloud-texttospeech を import しない。
各エンジンの実装は ``yomi_kai.tts.voicevox`` / ``yomi_kai.tts.google`` にあり、
``yomi_kai.main`` から必要なときだけ遅延 import される。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# 同期の合成（Google TTS など）を動かすスレッドの数。設定・辞書のファイル I/O が使う既定の executor を
# 合成で埋めてしまわないよう、合成には専用の executor を使う
SYNTHESIS_THREADS = 8
_executor: ThreadPoolExecutor | None = None
_executor_lock = threading.Lock()


def synthesis_executor() -> ThreadPoolExecutor:
    """同期の合成専用の executor（初めて使うときに作る）。"""
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=SYNTHESIS_THREADS, thread_name_prefix="yomi-kai-tts")
        return _executor


async def run_in_synthesis_thread[R](func: Callable[..., R], *args: Any) -> R:
    """``func(*args)`` を合成専用のスレッドで実行して待つ。"""
    return await asyncio.get_running_loop().run_in_executor(synthesis_executor(), func, *args)


async def synthesize_async(engine: TTSEngine, text: str, speaker: str, speed: float = 1.0) -> bytes:
    """``engine`` で非同期に合成する。

    ``synthesize_async`` を持つエンジンはそれを使う（取り消すと合成も取り消される）。持たないエンジンは
    同期の ``synthesize`` を合成専用のスレッドで呼ぶ（この場合、取り消しても実行中の合成は最後まで動く）。
    """
    method = getattr(engine, "synthesize_async", None)
    if method is not None:
        return await method(text, speaker, speed)
    return await run_in_synthesis_thread(engine.synthesize, text, speaker, speed)


@dataclass(frozen=True)
class Speaker:
    """話者。``name`` がドロップダウンのラベル兼 ID になる。

    ``credit`` は利用規約で求められるクレジット表記（VOICEVOX の話者なら ``VOICEVOX:ずんだもん`` のように
    スタイル名を含まないキャラクター名）。不要なエンジン（Google など）は None。
    """

    name: str
    description: str
    credit: str | None = None


def _normalize(name: str) -> str:
    # 全角・半角や大文字・小文字の違いを無視する（「（」と「(」、「Ａ」と「a」など）
    return unicodedata.normalize("NFKC", name).casefold().strip()


def find_speakers(query: str, names: Iterable[str]) -> list[str]:
    """話者名を検索する。要素が1つなら一意に決まった、2つ以上なら曖昧、空なら該当なし。

    完全一致 → 前方一致 → 部分一致の順に探し、最初に見つかった段階の候補を（元の順序で）返す。
    """
    names = list(names)
    key = _normalize(query)
    if not key:
        return []
    normalized = [(name, _normalize(name)) for name in names]
    for matches in (
        lambda n: n == key,
        lambda n: n.startswith(key),
        lambda n: key in n,
    ):
        found = [name for name, n in normalized if matches(n)]
        if found:
            return found
    return []


@runtime_checkable
class TTSEngine(Protocol):
    """音声合成エンジン。``synthesize`` はブロッキングで、WAV のバイト列を返す。

    ``synthesize_async`` は同じ合成のコルーチン版で、取り消し（``Task.cancel``）に応じる。
    読み上げ（``GuildSession``）はこちらを :func:`synthesize_async` 経由で使う。
    ``speed`` は話速の倍率（1.0 が標準）。
    """

    @property
    def default_speaker(self) -> str: ...

    def speakers(self) -> list[Speaker]: ...

    def synthesize(self, text: str, speaker: str, speed: float = 1.0) -> bytes: ...

    async def synthesize_async(self, text: str, speaker: str, speed: float = 1.0) -> bytes: ...


class CompositeEngine:
    """複数エンジンを束ね、話者名で振り分けるエンジン。

    未知の話者名は ``default_speaker`` にフォールバックする。
    """

    def __init__(self, engines: Sequence[TTSEngine], default_speaker: str | None = None) -> None:
        if not engines:
            raise ValueError("engines must not be empty")
        self._engines = list(engines)
        self._speakers: list[Speaker] = []
        self._owner: dict[str, TTSEngine] = {}
        for engine in self._engines:
            for speaker in engine.speakers():
                if speaker.name in self._owner:
                    continue
                self._owner[speaker.name] = engine
                self._speakers.append(speaker)
        self._default = default_speaker if default_speaker is not None else self._engines[0].default_speaker
        if self._default not in self._owner:
            raise ValueError(f"unknown default speaker: {self._default}")

    @property
    def default_speaker(self) -> str:
        return self._default

    def speakers(self) -> list[Speaker]:
        return list(self._speakers)

    def _route(self, speaker: str) -> tuple[TTSEngine, str]:
        engine = self._owner.get(speaker)
        if engine is None:
            logger.warning("未知の話者 %s が指定されたため %s で読み上げます", speaker, self._default)
            speaker = self._default
            engine = self._owner[speaker]
        return engine, speaker

    def synthesize(self, text: str, speaker: str, speed: float = 1.0) -> bytes:
        engine, speaker = self._route(speaker)
        return engine.synthesize(text, speaker, speed)

    async def synthesize_async(self, text: str, speaker: str, speed: float = 1.0) -> bytes:
        engine, speaker = self._route(speaker)
        return await synthesize_async(engine, text, speaker, speed)


class FilteredEngine:
    """話者を許可リスト（config.ini の SPEAKER_ALLOWLIST）に絞るエンジン。合成は元のエンジンに任せる。

    話者の並びは元のエンジンの順のまま。元の既定話者が許可されていなければ、許可された最初の話者を既定にする。
    許可されていない話者が指定された場合は既定話者で読み上げる（保存済みの設定は ``resolve_voice`` が先に飛ばす）。
    """

    def __init__(self, engine: TTSEngine, allowed: Iterable[str]) -> None:
        allowed = list(dict.fromkeys(allowed))
        # スタイルは「ずんだもん（あまあま）」のように y.voice list の名前で書く。
        # 全角・半角の違い（「（」と「(」など）は y.voice と同じく無視する
        available = {_normalize(s.name) for s in engine.speakers()}
        allowed_set = {_normalize(name) for name in allowed}
        #: 許可リストにあるが、エンジンに存在しない名前（指定順）
        self.unknown = [name for name in allowed if _normalize(name) not in available]
        self._engine = engine
        self._speakers = [s for s in engine.speakers() if _normalize(s.name) in allowed_set]
        if not self._speakers:
            raise ValueError("許可された話者がありません")
        names = {s.name for s in self._speakers}
        self._default = engine.default_speaker if engine.default_speaker in names else self._speakers[0].name
        self._names = frozenset(names)

    @property
    def default_speaker(self) -> str:
        return self._default

    def speakers(self) -> list[Speaker]:
        return list(self._speakers)

    def _allowed(self, speaker: str) -> str:
        if speaker not in self._names:
            logger.warning("許可されていない話者 %s が指定されたため %s で読み上げます", speaker, self._default)
            return self._default
        return speaker

    def synthesize(self, text: str, speaker: str, speed: float = 1.0) -> bytes:
        return self._engine.synthesize(text, self._allowed(speaker), speed)

    async def synthesize_async(self, text: str, speaker: str, speed: float = 1.0) -> bytes:
        return await synthesize_async(self._engine, text, self._allowed(speaker), speed)
