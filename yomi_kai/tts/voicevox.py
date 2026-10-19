"""VOICEVOX CORE エンジン。

並行合成のために、モデルを読み込んだ ``Synthesizer`` を ``workers`` 個用意し、
:class:`~yomi_kai.tts.pool.InstancePool` で「1つの Synthesizer は同時に1つの合成だけが使う」ように貸し出す。

voicevox_core 0.16.4 の調査結果（ソース: https://github.com/VOICEVOX/voicevox_core/tree/0.16.4 ）:

* ONNX のセッションは ``async_lock::Mutex<ort::session::Session>`` で守られている
  （crates/voicevox_core/src/core/infer/runtimes/onnxruntime.rs）。1つの Synthesizer を複数スレッドから
  呼んでも安全だが、推論はセッションごとに直列になり並行にならない。
* Python の blocking API（``voicevox_core.blocking.Synthesizer.tts`` など）は GIL を握ったまま合成する
  （crates/voicevox_core_python_api/src/lib.rs の blocking ``tts`` は ``py.allow_threads`` なしで ``perform()``）。
  スレッドを増やしても並行にならないうえ、合成中は Bot のイベントループ（Discord の通信・音声送信）も止まる。
* asyncio API（``voicevox_core.asyncio``）は GIL を解放して合成する。

そこで asyncio API の Synthesizer を専用スレッドのイベントループ（:class:`~yomi_kai.tts.pool.LoopThread`）で動かし、
同期の ``synthesize()`` はそのループへ合成を依頼して結果を待つ（呼び出し側は従来どおり ``asyncio.to_thread``）。
ONNX Runtime は1つ、Open JTalk（内部で ``Mutex<Resources>`` を持つ ``Arc``）は全 Synthesizer で共有する。
"""

from __future__ import annotations

import logging
import os
import platform
from pathlib import Path

from voicevox_core.asyncio import Onnxruntime, OpenJtalk, Synthesizer, VoiceModelFile

from . import Speaker
from .pool import InstancePool, LoopThread

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "0.vvm"
OPEN_JTALK_DICT = "dict/open_jtalk_dic_utf_8-1.11"

# 旧バージョンでハードコードされていた話者。ドロップダウンの先頭に並べ、同じラベルを維持する。
LEGACY_SPEAKERS = ("ずんだもん", "春日部つむぎ")
NORMAL_STYLE = "ノーマル"
# Discord の Select は25択まで
MAX_OPTIONS = 25
# 並行して合成できる数（Synthesizer の数）
DEFAULT_WORKERS = 2
MAX_WORKERS = 8


def find_voicevox_dir() -> Path:
    """配布版は ./lib/voicevox_core、それ以外は ./voicevox_core を使う。"""
    lib_dir = Path("./lib/voicevox_core")
    return lib_dir if lib_dir.exists() else Path("./voicevox_core")


def _onnxruntime_filename(voicevox_dir: Path) -> str:
    os_name = platform.system()
    if os_name == "Windows":
        return "voicevox_onnxruntime.dll"
    pattern, fallback = (
        ("libvoicevox_onnxruntime.dylib*", "libvoicevox_onnxruntime.dylib")
        if os_name == "Darwin"
        else ("libvoicevox_onnxruntime.so*", "libvoicevox_onnxruntime.so")
    )
    candidates = sorted((voicevox_dir / "onnxruntime/lib").glob(pattern))
    return candidates[0].name if candidates else fallback


def style_label(character_name: str, style_name: str) -> str:
    """ノーマルスタイルはキャラクター名のみ、それ以外は「名前（スタイル）」。"""
    return character_name if style_name == NORMAL_STYLE else f"{character_name}（{style_name}）"


async def synthesize_with(synthesizer: Synthesizer, text: str, style_id: int, speed: float) -> bytes:
    """速度が 1.0 なら ``tts``、それ以外は AudioQuery の ``speed_scale`` を変えて ``synthesis`` する。"""
    if speed == 1.0:
        return await synthesizer.tts(text, style_id)
    query = await synthesizer.create_audio_query(text, style_id)
    query.speed_scale = speed
    return await synthesizer.synthesis(query, style_id)


def worker_threads(workers: int, cpu_count: int | None = None) -> int:
    """ワーカー1つあたりの ONNX Runtime のスレッド数（全体で CPU 数を超えないようにする）。"""
    cpu_count = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    return max(1, cpu_count // max(1, workers))


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


class VoicevoxEngine:
    """VOICEVOX CORE による合成。モデルのメタ情報から話者一覧を構築する。"""

    def __init__(
        self, voicevox_dir: Path | None = None, *, load_all_models: bool = False, workers: int = DEFAULT_WORKERS
    ) -> None:
        if not 1 <= workers <= MAX_WORKERS:
            raise ValueError(f"workers は 1〜{MAX_WORKERS} で指定してください: {workers}")
        voicevox_dir = voicevox_dir or find_voicevox_dir()
        rss_before = current_rss_mb()

        vvm_dir = voicevox_dir / "models/vvms"
        if load_all_models:
            vvm_paths = sorted(vvm_dir.glob("*.vvm"))
            if not vvm_paths:
                raise FileNotFoundError(f"音声モデル(*.vvm)が見つかりません: {vvm_dir}")
        else:
            default_path = vvm_dir / DEFAULT_MODEL
            if not default_path.is_file():
                raise FileNotFoundError(f"音声モデルが見つかりません: {default_path}")
            vvm_paths = [default_path]

        threads = worker_threads(workers)
        self._loop = LoopThread("yomi-kai-voicevox")
        try:
            synthesizers, style_ids = self._loop.run(self._load(voicevox_dir, vvm_paths, workers, threads))
            if not style_ids:
                raise RuntimeError("読み込んだモデルに利用可能な話者がありません")
            # Queue はループ上で作る（そのループからだけ使う）
            self._pool = self._loop.run(_make_pool(synthesizers))
        except BaseException:
            self._loop.stop()
            raise

        # 旧来の話者を先頭に、それ以外はモデルのメタ情報の順で並べる
        ordered = [name for name in LEGACY_SPEAKERS if name in style_ids]
        ordered += [name for name in style_ids if name not in LEGACY_SPEAKERS]
        if len(ordered) > MAX_OPTIONS:
            logger.warning("VOICEVOXの話者が%d件あるため、先頭%d件のみ選択肢に表示します", len(ordered), MAX_OPTIONS)
        self._style_ids = style_ids
        self._speakers = [Speaker(name, f"VOICEVOX {name}") for name in ordered]
        self._default = ordered[0]

        rss_after = current_rss_mb()
        memory = f"、メモリ {rss_before:.0f}MB → {rss_after:.0f}MB" if rss_before and rss_after else ""
        logger.info("VOICEVOXの合成ワーカー: %d個（1つあたり%dスレッド）%s", workers, threads, memory)

    @staticmethod
    async def _load(
        voicevox_dir: Path, vvm_paths: list[Path], workers: int, threads: int
    ) -> tuple[list[Synthesizer], dict[str, int]]:
        onnx = Onnxruntime.get()
        if onnx is None:
            onnx_path = voicevox_dir / "onnxruntime/lib" / _onnxruntime_filename(voicevox_dir)
            onnx = await Onnxruntime.load_once(filename=str(onnx_path))
        open_jtalk = await OpenJtalk.new(voicevox_dir / OPEN_JTALK_DICT)
        synthesizers = [Synthesizer(onnx, open_jtalk, cpu_num_threads=threads) for _ in range(workers)]

        style_ids: dict[str, int] = {}
        for vvm_path in vvm_paths:
            async with await VoiceModelFile.open(vvm_path) as model:
                for synthesizer in synthesizers:
                    await synthesizer.load_voice_model(model)
                for character in model.metas:
                    for style in character.styles:
                        if getattr(style, "type", "talk") != "talk":
                            continue
                        style_ids.setdefault(style_label(character.name, style.name), int(style.id))
            logger.info("VOICEVOXモデルを読み込みました: %s", vvm_path.name)
        return synthesizers, style_ids

    @property
    def workers(self) -> int:
        return self._pool.size

    @property
    def default_speaker(self) -> str:
        return self._default

    def speakers(self) -> list[Speaker]:
        return list(self._speakers)

    def synthesize(self, text: str, speaker: str, speed: float = 1.0) -> bytes:
        """合成する（ブロッキング。合成中は GIL を解放しているので、他のスレッドやイベントループは止まらない）。"""
        style_id = self._style_ids.get(speaker, self._style_ids[self._default])
        wav = self._loop.run(self._synthesize(text, style_id, speed))
        logger.debug("The audio was successfully generated by VOICEVOX. (%d bytes)", len(wav))
        return wav

    async def _synthesize(self, text: str, style_id: int, speed: float) -> bytes:
        async with self._pool.borrow() as synthesizer:
            return await synthesize_with(synthesizer, text, style_id, speed)


async def _make_pool(synthesizers: list[Synthesizer]) -> InstancePool[Synthesizer]:
    return InstancePool(synthesizers)
