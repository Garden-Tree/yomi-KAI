import asyncio
import importlib
import sys
import threading
import types
from concurrent.futures import ThreadPoolExecutor

import pytest

from yomi_kai.tts import CompositeEngine, Speaker, find_speakers
from yomi_kai.tts.pool import InstancePool, LoopThread


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


# --- VoicevoxEngine の Synthesizer プール ---


def _import_voicevox_module():
    """voicevox_core が無い環境（CI）でもテストできるよう、無ければ空のスタブを差し込んで import する。"""
    try:
        importlib.import_module("voicevox_core.asyncio")
    except ImportError:
        package = types.ModuleType("voicevox_core")
        stub = types.ModuleType("voicevox_core.asyncio")
        for name in ("Onnxruntime", "OpenJtalk", "Synthesizer", "VoiceModelFile"):
            setattr(stub, name, type(name, (), {}))
        package.asyncio = stub
        sys.modules["voicevox_core"] = package
        sys.modules["voicevox_core.asyncio"] = stub
    return importlib.import_module("yomi_kai.tts.voicevox")


class Overlap:
    """同時に実行されている数を数える。"""

    def __init__(self):
        self.active = 0
        self.max_active = 0
        self._guard = threading.Lock()

    def __enter__(self):
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)

    def __exit__(self, *exc):
        with self._guard:
            self.active -= 1


class FakeSynthesizer:
    """asyncio API の偽物。自分自身の同時実行数と、全インスタンス合計の同時実行数を記録する。"""

    def __init__(self, overall):
        self.own = Overlap()
        self.overall = overall
        self.calls = 0

    async def _work(self):
        with self.own, self.overall:
            self.calls += 1
            await asyncio.sleep(0.05)

    async def tts(self, text, style_id):
        await self._work()
        return b"wav"

    async def create_audio_query(self, text, style_id):
        await self._work()
        return types.SimpleNamespace(speed_scale=1.0)

    async def synthesis(self, query, style_id):
        assert query.speed_scale != 1.0
        await self._work()
        return b"wav"


def make_engine(workers):
    voicevox = _import_voicevox_module()
    engine = object.__new__(voicevox.VoicevoxEngine)  # モデルを読み込まずに合成部分だけ試す
    overall = Overlap()
    fakes = [FakeSynthesizer(overall) for _ in range(workers)]
    engine._loop = LoopThread("test-voicevox")

    async def make_pool():
        return InstancePool(fakes)

    engine._pool = engine._loop.run(make_pool())
    engine._style_ids = {"ずんだもん": 3}
    engine._default = "ずんだもん"
    return engine, fakes, overall


@pytest.mark.parametrize("workers", [1, 2, 3])
def test_voicevox_pool_never_shares_an_instance_and_overlaps_up_to_workers(workers):
    engine, fakes, overall = make_engine(workers)
    try:
        speeds = [1.0, 1.5] * 6
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda s: engine.synthesize("テスト", "ずんだもん", s), speeds))
    finally:
        engine._loop.stop()

    assert results == [b"wav"] * len(speeds)
    assert sum(f.calls for f in fakes) == 6 + 6 * 2  # 速度 1.0 は tts、それ以外は create_audio_query + synthesis
    assert all(f.own.max_active == 1 for f in fakes)  # 1つの Synthesizer を同時に2つの合成が使わない
    assert overall.max_active == workers  # ワーカー数までは並行に合成できる
    assert engine.workers == workers


def test_instance_pool_waits_until_returned():
    async def run():
        pool = InstancePool(["a"])
        order = []

        async def use(tag):
            async with pool.borrow() as item:
                order.append(("start", item))
                await asyncio.sleep(0.01)
                order.append(("end", item))

        await asyncio.gather(*(use(i) for i in range(3)))
        return order

    # 開始と終了が必ず交互（重ならない）
    assert [kind for kind, _ in asyncio.run(run())] == ["start", "end"] * 3
    with pytest.raises(ValueError):
        asyncio.run(_make_empty_pool())


async def _make_empty_pool():
    return InstancePool([])


def test_loop_thread_runs_coroutines_from_other_threads():
    loop_thread = LoopThread("test")
    try:

        async def where():
            return threading.current_thread().name

        assert loop_thread.run(where()) == "test"
    finally:
        loop_thread.stop()


def test_worker_threads_split_cpus():
    voicevox = _import_voicevox_module()
    assert voicevox.worker_threads(2, cpu_count=8) == 4
    assert voicevox.worker_threads(4, cpu_count=6) == 1
    assert voicevox.worker_threads(8, cpu_count=2) == 1
    assert voicevox.worker_threads(1, cpu_count=12) == 12


# --- 話者名の検索 ---

NAMES = ["ずんだもん", "春日部つむぎ", "四国めたん", "四国めたん（あまあま）", "ずんだもん（あまあま）", "雨晴はう"]


def test_find_speakers_exact_wins_over_prefix():
    assert find_speakers("ずんだもん", NAMES) == ["ずんだもん"]
    assert find_speakers("四国めたん", NAMES) == ["四国めたん"]


def test_find_speakers_unique_prefix_or_partial():
    assert find_speakers("春日部", NAMES) == ["春日部つむぎ"]
    assert find_speakers("はう", NAMES) == ["雨晴はう"]
    assert find_speakers("つむぎ", NAMES) == ["春日部つむぎ"]


def test_find_speakers_ambiguous_returns_candidates_in_order():
    assert find_speakers("めたん", NAMES) == ["四国めたん", "四国めたん（あまあま）"]
    assert find_speakers("あまあま", NAMES) == ["四国めたん（あまあま）", "ずんだもん（あまあま）"]
    assert find_speakers("四国", NAMES) == ["四国めたん", "四国めたん（あまあま）"]


def test_find_speakers_normalizes_width_and_case():
    assert find_speakers("四国めたん(あまあま)", NAMES) == ["四国めたん（あまあま）"]
    assert find_speakers("JA-JP-WAVENET-a", ["ja-JP-Wavenet-A", "ja-JP-Wavenet-D"]) == ["ja-JP-Wavenet-A"]


def test_find_speakers_not_found():
    assert find_speakers("存在しない", NAMES) == []
    assert find_speakers("  ", NAMES) == []
