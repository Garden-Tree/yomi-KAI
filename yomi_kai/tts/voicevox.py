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
同期の ``synthesize()`` はそのループへ合成を依頼して結果を待つ。読み上げが使う ``synthesize_async()`` は
呼び出し側のイベントループで結果を待つので、スキップで取り消すと合成のタスクも取り消され、Synthesizer がすぐ空く。
ONNX Runtime は1つ、Open JTalk（内部で ``Mutex<Resources>`` を持つ ``Arc``）は全 Synthesizer で共有する。

モデルの読み込み方は3通り:

* 既定: 0.vvm だけを起動時に読み込む。
* ``extra_models``（VOICEVOX_EXTRA_MODELS）: 指定した vvm はメタ情報（話者・スタイル）だけを起動時に読み、
  その話者で初めて合成するときに読み込む。使われなくなったら解放する（:class:`~yomi_kai.tts.models.ModelManager`）。
* ``load_all_models``（VOICEVOX_LOAD_ALL_MODELS）: すべての vvm を起動時に読み込む（従来どおり）。
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import re
from collections.abc import Iterable
from pathlib import Path

from voicevox_core.asyncio import Onnxruntime, OpenJtalk, Synthesizer, VoiceModelFile

from . import Speaker
from .models import (
    DEFAULT_MAX_EXTRA_MODELS,
    DEFAULT_MODEL_IDLE,
    ModelEntry,
    ModelManager,
    current_rss_mb,
    memory_note,
)
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
# 既定は1（メモリの少ない環境でも動くように。1つ増やすごとに RSS が約250MB増える）
DEFAULT_WORKERS = 1
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


# 「VOICEVOX:キャラクター名」と違う表記を求めているキャラクター（各キャラクターの利用規約による）
CREDIT_OVERRIDES = {
    "もち子さん": "VOICEVOX:もち子(cv 明日葉よもぎ)",
    "Voidoll": "VOICEVOX:Voidoll(CV:丹下桜)",
}
# VOICEVOX Nemo（n0.vvm）の話者。クレジットは「VOICEVOX Nemo」
_NEMO_NAME = re.compile(r"[女男]声\d+")


def credit_line(character_name: str) -> str:
    """VOICEVOX の利用規約で求められるクレジット表記（例: ``VOICEVOX:ずんだもん``）。"""
    if _NEMO_NAME.fullmatch(character_name):
        return "VOICEVOX Nemo"
    return CREDIT_OVERRIDES.get(character_name, f"VOICEVOX:{character_name}")


def vvm_sort_key(path: Path) -> tuple[str, int, str]:
    """vvm ファイルを番号の順に並べるキー（0, 1, 2, …, 10, …, n0）。"""
    match = re.fullmatch(r"(\D*)(\d*)", path.stem)
    if match is None:
        return (path.stem, -1, path.name)
    return (match[1], int(match[2]) if match[2] else -1, path.name)


def select_vvms(
    vvm_dir: Path, *, load_all_models: bool, extra_models: Iterable[str]
) -> tuple[list[Path], list[Path]]:
    """(起動時に読み込む vvm, 話者の指定で読み込む vvm)。

    ``extra_models`` は vvm のファイル名（``1.vvm`` または ``1``）、または ``*``（ほかのすべての vvm）。
    ``load_all_models`` なら、すべてを起動時に読み込む（``extra_models`` は使わない）。
    """
    extra_models = list(extra_models)
    if load_all_models:
        eager = sorted(vvm_dir.glob("*.vvm"), key=vvm_sort_key)
        if not eager:
            raise FileNotFoundError(f"音声モデル(*.vvm)が見つかりません: {vvm_dir}")
        if extra_models:
            logger.warning("VOICEVOX_LOAD_ALL_MODELS = True のため、VOICEVOX_EXTRA_MODELS は使いません")
        return eager, []
    default_path = vvm_dir / DEFAULT_MODEL
    if not default_path.is_file():
        raise FileNotFoundError(f"音声モデルが見つかりません: {default_path}")
    if "*" in extra_models:
        extra = [p for p in vvm_dir.glob("*.vvm") if p.name != DEFAULT_MODEL]
    else:
        extra = []
        for name in dict.fromkeys(extra_models):
            filename = name if name.endswith(".vvm") else f"{name}.vvm"
            path = vvm_dir / filename
            if filename == DEFAULT_MODEL:
                continue
            if not path.is_file():
                logger.warning("VOICEVOX_EXTRA_MODELS の %s が見つからないため無視します: %s", name, path)
                continue
            extra.append(path)
    return [default_path], sorted(extra, key=vvm_sort_key)


async def synthesize_with(
    synthesizer: Synthesizer, text: str, style_id: int, speed: float, pitch: float = 0.0
) -> bytes:
    """速度が 1.0・高さが 0.0 なら ``tts``、それ以外は AudioQuery の ``speed_scale`` / ``pitch_scale`` を変えて
    ``synthesis`` する。"""
    if speed == 1.0 and pitch == 0.0:
        return await synthesizer.tts(text, style_id)
    query = await synthesizer.create_audio_query(text, style_id)
    query.speed_scale = speed
    query.pitch_scale = pitch
    return await synthesizer.synthesis(query, style_id)


def worker_threads(workers: int, cpu_count: int | None = None) -> int:
    """ワーカー1つあたりの ONNX Runtime のスレッド数（全体で CPU 数を超えないようにする）。"""
    cpu_count = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
    return max(1, cpu_count // max(1, workers))


class VoicevoxEngine:
    """VOICEVOX CORE による合成。モデルのメタ情報から話者一覧を構築する。

    話者一覧には、まだ読み込んでいない追加のモデル（``extra_models``）の話者も含める。
    """

    def __init__(
        self,
        voicevox_dir: Path | None = None,
        *,
        load_all_models: bool = False,
        workers: int = DEFAULT_WORKERS,
        extra_models: Iterable[str] = (),
        max_extra_models: int = DEFAULT_MAX_EXTRA_MODELS,
        model_idle: float = DEFAULT_MODEL_IDLE,
    ) -> None:
        if not 1 <= workers <= MAX_WORKERS:
            raise ValueError(f"workers は 1〜{MAX_WORKERS} で指定してください: {workers}")
        voicevox_dir = voicevox_dir or find_voicevox_dir()
        rss_before = current_rss_mb()

        eager_paths, extra_paths = select_vvms(
            voicevox_dir / "models/vvms", load_all_models=load_all_models, extra_models=extra_models
        )

        threads = worker_threads(workers)
        self._loop = LoopThread("yomi-kai-voicevox")
        self._sweeper: asyncio.Task[None] | None = None
        try:
            synthesizers = self._loop.run(_new_synthesizers(voicevox_dir, workers, threads))
            self._models = ModelManager(
                synthesizers, VoiceModelFile.open, max_extra=max_extra_models, idle=model_idle
            )
            catalog = self._loop.run(self._read_models(eager_paths, extra_paths))
            if not catalog.style_ids:
                raise RuntimeError("読み込んだモデルに利用可能な話者がありません")
            # Queue はループ上で作る（そのループからだけ使う）
            self._pool = self._loop.run(_make_pool(synthesizers))
            if self._models.has_extra:
                self._sweeper = self._loop.run(_start(self._models.run_sweeper()))
        except BaseException:
            self._loop.stop()
            raise

        style_ids = catalog.style_ids
        # 旧来の話者を先頭に、それ以外は起動時に読み込むモデル → 追加のモデルの順（モデルのメタ情報の順）で並べる
        ordered = [name for name in LEGACY_SPEAKERS if name in style_ids]
        ordered += [name for name in style_ids if name not in LEGACY_SPEAKERS]
        if len(ordered) > MAX_OPTIONS:
            logger.warning("VOICEVOXの話者が%d件あるため、先頭%d件のみ選択肢に表示します", len(ordered), MAX_OPTIONS)
        self._style_ids = style_ids
        self._style_model = catalog.style_model
        # クレジット表記（VOICEVOX:キャラクター名）はモデルのメタ情報のキャラクター名から作る。
        # 0.vvm 以外のモデルの話者は「追加の話者」（EXTRA_VOICES_PREMIUM_ONLY でプレミアムだけにできる）
        self._speakers = [
            Speaker(
                name,
                f"VOICEVOX {name}",
                credit_line(catalog.characters[name]),
                extra=self._style_model[style_ids[name]] != DEFAULT_MODEL,
            )
            for name in ordered
        ]
        self._default = ordered[0]

        rss_after = current_rss_mb()
        memory = memory_note(rss_before, rss_after)
        logger.info("VOICEVOXの合成ワーカー: %d個（1つあたり%dスレッド）%s", workers, threads, memory)
        if extra_paths:
            logger.info(
                "VOICEVOXの追加のモデル: %d個（%s）。話者の指定で読み込み、"
                "同時に%d個まで、%d秒使われなければ解放します",
                len(extra_paths),
                "、".join(p.name for p in extra_paths),
                max_extra_models,
                model_idle,
            )

    async def _read_models(self, eager_paths: list[Path], extra_paths: list[Path]) -> _Catalog:
        """vvm のメタ情報を読む。起動時に読み込むモデルは、このとき全 Synthesizer へ読み込む。"""
        catalog = _Catalog()
        for vvm_path, eager in [(p, True) for p in eager_paths] + [(p, False) for p in extra_paths]:
            async with await VoiceModelFile.open(vvm_path) as model:
                if not catalog.add(vvm_path.name, model.metas):
                    # 歌唱用（s0.vvm）など、読み上げに使えるスタイルがないモデル
                    logger.info("読み上げに使える話者がないため使いません: %s", vvm_path.name)
                    continue
                entry = ModelEntry(vvm_path.name, vvm_path, model.id, eager=eager)
                self._models.add(entry)
                if eager:
                    await self._models.load_eager(entry, model)
                    logger.info("VOICEVOXモデルを読み込みました: %s", vvm_path.name)
        return catalog

    @property
    def workers(self) -> int:
        return self._pool.size

    @property
    def default_speaker(self) -> str:
        return self._default

    def speakers(self) -> list[Speaker]:
        return list(self._speakers)

    def synthesize(self, text: str, speaker: str, speed: float = 1.0, pitch: float = 0.0) -> bytes:
        """合成する（ブロッキング。合成中は GIL を解放しているので、他のスレッドやイベントループは止まらない）。"""
        style_id = self._style_ids.get(speaker, self._style_ids[self._default])
        wav = self._loop.run(self._synthesize(text, style_id, speed, pitch))
        logger.debug("The audio was successfully generated by VOICEVOX. (%d bytes)", len(wav))
        return wav

    async def synthesize_async(self, text: str, speaker: str, speed: float = 1.0, pitch: float = 0.0) -> bytes:
        """合成する（コルーチン版。スレッドを使わず、取り消すと合成も取り消して Synthesizer をプールへ返す）。"""
        style_id = self._style_ids.get(speaker, self._style_ids[self._default])
        wav = await self._loop.run_async(self._synthesize(text, style_id, speed, pitch))
        logger.debug("The audio was successfully generated by VOICEVOX. (%d bytes)", len(wav))
        return wav

    async def _synthesize(self, text: str, style_id: int, speed: float, pitch: float) -> bytes:
        # 追加のモデルの話者なら、読み込んでから合成する（合成中は解放されない）
        async with self._models.use(self._style_model.get(style_id)):
            async with self._pool.borrow() as synthesizer:
                return await synthesize_with(synthesizer, text, style_id, speed, pitch)


class _Catalog:
    """vvm のメタ情報から作る話者の一覧。"""

    def __init__(self) -> None:
        self.style_ids: dict[str, int] = {}  # 話者のラベル -> スタイル ID
        self.characters: dict[str, str] = {}  # 話者のラベル -> キャラクター名
        self.style_model: dict[int, str] = {}  # スタイル ID -> vvm のファイル名

    def add(self, vvm_name: str, metas: Iterable) -> bool:
        """モデルの話者を加える。読み上げに使えるスタイルが1つもなければ False。"""
        found = False
        for character in metas:
            for style in character.styles:
                if getattr(style, "type", "talk") != "talk":
                    continue
                found = True
                label = style_label(character.name, style.name)
                # 同じラベル・スタイル ID は先に読んだモデル（起動時に読み込むモデル）を使う
                if label not in self.style_ids:
                    self.style_ids[label] = int(style.id)
                    self.characters[label] = character.name
                self.style_model.setdefault(int(style.id), vvm_name)
        return found


async def _new_synthesizers(voicevox_dir: Path, workers: int, threads: int) -> list[Synthesizer]:
    onnx = Onnxruntime.get()
    if onnx is None:
        onnx_path = voicevox_dir / "onnxruntime/lib" / _onnxruntime_filename(voicevox_dir)
        onnx = await Onnxruntime.load_once(filename=str(onnx_path))
    open_jtalk = await OpenJtalk.new(voicevox_dir / OPEN_JTALK_DICT)
    return [Synthesizer(onnx, open_jtalk, cpu_num_threads=threads) for _ in range(workers)]


async def _start(coro) -> asyncio.Task[None]:
    """``coro`` をこのループのタスクとして始める。"""
    return asyncio.create_task(coro, name="yomi-kai-voicevox-models")


async def _make_pool(synthesizers: list[Synthesizer]) -> InstancePool[Synthesizer]:
    return InstancePool(synthesizers)
