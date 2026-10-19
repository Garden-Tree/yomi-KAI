import importlib
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor

import pytest

from yomi_kai.tts import CompositeEngine, Speaker


class RecordingEngine:
    def __init__(self, names):
        self.names = names
        self.calls = []

    @property
    def default_speaker(self):
        return self.names[0]

    def speakers(self):
        return [Speaker(n, "fake") for n in self.names]

    def synthesize(self, text, speaker, speed=1.0):
        self.calls.append((text, speaker, speed))
        return b"wav"


def test_composite_routes_by_speaker_and_passes_speed():
    google = RecordingEngine(["ja-JP-Wavenet-A", "ja-JP-Wavenet-D"])
    voicevox = RecordingEngine(["ずんだもん", "春日部つむぎ"])
    engine = CompositeEngine([google, voicevox])

    engine.synthesize("一", "春日部つむぎ", 1.5)
    engine.synthesize("二", "ja-JP-Wavenet-D", 0.75)
    engine.synthesize("三", "ずんだもん")

    assert voicevox.calls == [("一", "春日部つむぎ", 1.5), ("三", "ずんだもん", 1.0)]
    assert google.calls == [("二", "ja-JP-Wavenet-D", 0.75)]


def test_composite_unknown_speaker_falls_back_with_speed():
    google = RecordingEngine(["ja-JP-Wavenet-A"])
    voicevox = RecordingEngine(["ずんだもん"])
    engine = CompositeEngine([google, voicevox], default_speaker="ずんだもん")
    engine.synthesize("やあ", "存在しない話者", 1.25)
    assert voicevox.calls == [("やあ", "ずんだもん", 1.25)]
    assert google.calls == []


def test_composite_rejects_unknown_default():
    with pytest.raises(ValueError):
        CompositeEngine([RecordingEngine(["ずんだもん"])], default_speaker="nobody")


# --- VoicevoxEngine の直列化 ---


def _import_voicevox_engine():
    """voicevox_core が無い環境（CI）でもテストできるよう、無ければ空のスタブを差し込んで import する。"""
    try:
        importlib.import_module("voicevox_core.blocking")
    except ImportError:
        package = types.ModuleType("voicevox_core")
        blocking = types.ModuleType("voicevox_core.blocking")
        for name in ("Onnxruntime", "OpenJtalk", "Synthesizer", "VoiceModelFile"):
            setattr(blocking, name, type(name, (), {}))
        package.blocking = blocking
        sys.modules.setdefault("voicevox_core", package)
        sys.modules.setdefault("voicevox_core.blocking", blocking)
    return importlib.import_module("yomi_kai.tts.voicevox").VoicevoxEngine


class OverlapDetectingSynthesizer:
    """同時に呼ばれたら記録する偽の Synthesizer。"""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.calls = 0
        self._guard = threading.Lock()

    def _enter(self):
        with self._guard:
            self.active += 1
            self.calls += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.02)
        with self._guard:
            self.active -= 1

    def tts(self, text, style_id):
        self._enter()
        return b"wav"

    def create_audio_query(self, text, style_id):
        self._enter()
        return types.SimpleNamespace(speed_scale=1.0)

    def synthesis(self, query, style_id):
        self._enter()
        return b"wav"


def test_voicevox_engine_serializes_synthesis():
    VoicevoxEngine = _import_voicevox_engine()
    engine = object.__new__(VoicevoxEngine)  # モデルを読み込まずに合成部分だけ試す
    fake = OverlapDetectingSynthesizer()
    engine._synthesizer = fake
    engine._lock = threading.Lock()
    engine._style_ids = {"ずんだもん": 3}
    engine._default = "ずんだもん"

    with ThreadPoolExecutor(max_workers=8) as pool:
        speeds = [1.0, 1.5] * 6
        results = list(pool.map(lambda s: engine.synthesize("テスト", "ずんだもん", s), speeds))

    assert results == [b"wav"] * len(speeds)
    assert fake.calls == 6 + 6 * 2  # 速度 1.0 は tts、それ以外は create_audio_query + synthesis
    assert fake.max_active == 1
