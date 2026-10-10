"""音声合成エンジンの共通インターフェース。

このモジュールは voicevox_core / google-cloud-texttospeech を import しない。
各エンジンの実装は ``yomi_kai.tts.voicevox`` / ``yomi_kai.tts.google`` にあり、
``yomi_kai.main`` から必要なときだけ遅延 import される。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Speaker:
    """話者。``name`` がドロップダウンのラベル兼 ID になる。"""

    name: str
    description: str


@runtime_checkable
class TTSEngine(Protocol):
    """音声合成エンジン。``synthesize`` はブロッキングで、WAV のバイト列を返す。"""

    @property
    def default_speaker(self) -> str: ...

    def speakers(self) -> list[Speaker]: ...

    def synthesize(self, text: str, speaker: str) -> bytes: ...


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

    def synthesize(self, text: str, speaker: str) -> bytes:
        engine = self._owner.get(speaker)
        if engine is None:
            logger.warning("未知の話者 %s が指定されたため %s で読み上げます", speaker, self._default)
            speaker = self._default
            engine = self._owner[speaker]
        return engine.synthesize(text, speaker)
