"""追加の VOICEVOX モデル（話者の指定で読み込み・使われなければ解放）のテスト。voicevox_core は偽物を使う。"""

import asyncio
import logging
import types
import uuid

import pytest
from test_config import BASE
from test_plans import Engine, make_config, premium_config, write_premium
from test_tts import _import_voicevox_module

from yomi_kai.config import ConfigError, load_config
from yomi_kai.main import build_plans
from yomi_kai.tts import Speaker
from yomi_kai.tts.models import ModelEntry, ModelManager


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeModel:
    def __init__(self, path, model_id=None, metas=()):
        self.path = path
        self.id = model_id or uuid.uuid5(uuid.NAMESPACE_URL, str(path))
        self.metas = list(metas)
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True


class FakeSynth:
    def __init__(self, fail_on=None):
        self.loaded = set()
        self.load_calls = 0
        self.fail_on = fail_on

    async def load_voice_model(self, model):
        self.load_calls += 1
        await asyncio.sleep(0.01)
        if self.fail_on is not None and model.path.name == self.fail_on:
            raise RuntimeError("壊れたモデル")
        assert model.id not in self.loaded
        self.loaded.add(model.id)

    def unload_voice_model(self, model_id):
        self.loaded.remove(model_id)


def model_id(name):
    return uuid.uuid5(uuid.NAMESPACE_URL, name)


def make_manager(tmp_path, synths, *, max_extra=1, idle=600, extras=("1.vvm", "2.vvm", "3.vvm")):
    clock = Clock()

    async def opener(path):
        return FakeModel(path, model_id(path.name))

    manager = ModelManager(synths, opener, max_extra=max_extra, idle=idle, clock=clock, rss=lambda: None)
    eager = ModelEntry("0.vvm", tmp_path / "0.vvm", model_id("0.vvm"), eager=True)
    manager.add(eager)

    async def load_eager():
        await manager.load_eager(eager, FakeModel(tmp_path / "0.vvm", model_id("0.vvm")))

    asyncio.run(load_eager())
    for name in extras:
        manager.add(ModelEntry(name, tmp_path / name, model_id(name), eager=False))
    return manager, clock


async def use(manager, name):
    async with manager.use(name):
        pass


def test_loads_extra_model_into_every_synthesizer_once(tmp_path):
    synths = [FakeSynth(), FakeSynth()]
    manager, _ = make_manager(tmp_path, synths)

    async def run():
        # 同時に3つの合成が同じモデルを求めても、読み込みは1回だけ
        await asyncio.gather(*(use(manager, "1.vvm") for _ in range(3)))

    asyncio.run(run())
    assert all(s.loaded == {model_id("0.vvm"), model_id("1.vvm")} for s in synths)
    assert all(s.load_calls == 2 for s in synths)
    assert manager.loaded_extra() == ["1.vvm"]


def test_unknown_or_eager_model_needs_no_loading(tmp_path):
    synths = [FakeSynth()]
    manager, _ = make_manager(tmp_path, synths)
    asyncio.run(use(manager, None))
    asyncio.run(use(manager, "0.vvm"))
    asyncio.run(use(manager, "99.vvm"))
    assert synths[0].load_calls == 1


def test_cap_unloads_least_recently_used(tmp_path):
    synths = [FakeSynth()]
    manager, clock = make_manager(tmp_path, synths, max_extra=2)
    for t, name in [(1, "1.vvm"), (2, "2.vvm"), (3, "1.vvm"), (4, "3.vvm")]:
        clock.now = t
        asyncio.run(use(manager, name))
    # 2.vvm が最も長く使われていない
    assert manager.loaded_extra() == ["1.vvm", "3.vvm"]
    assert synths[0].loaded == {model_id("0.vvm"), model_id("1.vvm"), model_id("3.vvm")}


def test_model_in_use_is_not_unloaded(tmp_path):
    synths = [FakeSynth()]
    manager, clock = make_manager(tmp_path, synths, max_extra=1, idle=60)

    async def run():
        async with manager.use("1.vvm"):
            clock.now = 1000
            # 合成中は上限を超えても・時間が経っても解放しない
            await use(manager, "2.vvm")
            clock.now = 2000
            assert manager.sweep() == ["2.vvm"]
            assert manager.loaded_extra() == ["1.vvm"]

    asyncio.run(run())


def test_sweep_unloads_idle_models_but_never_eager(tmp_path):
    synths = [FakeSynth()]
    manager, clock = make_manager(tmp_path, synths, max_extra=3, idle=600)
    clock.now = 0
    asyncio.run(use(manager, "1.vvm"))
    clock.now = 500
    asyncio.run(use(manager, "2.vvm"))
    clock.now = 700
    assert manager.sweep() == ["1.vvm"]
    clock.now = 10_000
    assert manager.sweep() == ["2.vvm"]
    assert synths[0].loaded == {model_id("0.vvm")}
    assert manager.entry("0.vvm").loaded
    # 解放したモデルも、次に使うときにまた読み込む
    asyncio.run(use(manager, "1.vvm"))
    assert manager.loaded_extra() == ["1.vvm"]


def test_failed_load_is_rolled_back(tmp_path):
    synths = [FakeSynth(), FakeSynth(fail_on="1.vvm")]
    manager, _ = make_manager(tmp_path, synths)
    with pytest.raises(RuntimeError):
        asyncio.run(use(manager, "1.vvm"))
    assert all(s.loaded == {model_id("0.vvm")} for s in synths)
    assert manager.loaded_extra() == []
    assert manager.entry("1.vvm").users == 0


def test_run_sweeper_releases_idle_models(tmp_path, monkeypatch):
    synths = [FakeSynth()]
    manager, clock = make_manager(tmp_path, synths, idle=2)
    asyncio.run(use(manager, "1.vvm"))
    clock.now = 10

    async def run():
        task = asyncio.create_task(manager.run_sweeper())
        while manager.loaded_extra():
            await asyncio.sleep(0.05)
        task.cancel()

    asyncio.run(asyncio.wait_for(run(), timeout=5))
    assert synths[0].loaded == {model_id("0.vvm")}


def test_max_extra_must_be_positive():
    with pytest.raises(ValueError):
        ModelManager([], None, max_extra=0)


# --- vvm の選び方・クレジット ---


def touch_vvms(tmp_path, names):
    vvms = tmp_path / "models/vvms"
    vvms.mkdir(parents=True)
    for name in names:
        (vvms / name).write_bytes(b"")
    return vvms


def test_select_vvms(tmp_path, caplog):
    voicevox = _import_voicevox_module()
    vvms = touch_vvms(tmp_path, ["0.vvm", "1.vvm", "2.vvm", "10.vvm", "n0.vvm", "s0.vvm"])

    def names(paths):
        return [p.name for p in paths]

    eager, extra = voicevox.select_vvms(vvms, load_all_models=False, extra_models=())
    assert (names(eager), extra) == (["0.vvm"], [])
    eager, extra = voicevox.select_vvms(vvms, load_all_models=False, extra_models=["10.vvm", "1", "0.vvm", "9.vvm"])
    assert (names(eager), names(extra)) == (["0.vvm"], ["1.vvm", "10.vvm"])
    assert "9.vvm" in caplog.text
    _, extra = voicevox.select_vvms(vvms, load_all_models=False, extra_models=["*"])
    assert names(extra) == ["1.vvm", "2.vvm", "10.vvm", "n0.vvm", "s0.vvm"]
    caplog.clear()
    eager, extra = voicevox.select_vvms(vvms, load_all_models=True, extra_models=["*"])
    assert (names(eager), extra) == (["0.vvm", "1.vvm", "2.vvm", "10.vvm", "n0.vvm", "s0.vvm"], [])
    assert "VOICEVOX_EXTRA_MODELS" in caplog.text


def test_select_vvms_requires_default_model(tmp_path):
    voicevox = _import_voicevox_module()
    vvms = touch_vvms(tmp_path, ["1.vvm"])
    with pytest.raises(FileNotFoundError):
        voicevox.select_vvms(vvms, load_all_models=False, extra_models=["*"])


def test_credit_line_special_formats():
    voicevox = _import_voicevox_module()
    assert voicevox.credit_line("ずんだもん") == "VOICEVOX:ずんだもん"
    assert voicevox.credit_line("女声3") == "VOICEVOX Nemo"
    assert voicevox.credit_line("Voidoll") == "VOICEVOX:Voidoll(CV:丹下桜)"


# --- VoicevoxEngine（voicevox_core の偽物で起動から合成まで） ---


def meta(name, *styles):
    return types.SimpleNamespace(
        name=name, styles=[types.SimpleNamespace(name=s, id=i, type=t) for s, i, t in styles]
    )


METAS = {
    "0.vvm": [meta("ずんだもん", ("ノーマル", 3, "talk")), meta("春日部つむぎ", ("ノーマル", 8, "talk"))],
    "1.vvm": [meta("冥鳴ひまり", ("ノーマル", 14, "talk"))],
    "2.vvm": [meta("九州そら", ("ノーマル", 16, "talk"), ("ささやき", 19, "talk"))],
    "s0.vvm": [meta("ずんだもん", ("ノーマル", 3001, "sing"))],
}


class EngineSynth(FakeSynth):
    def __init__(self, onnx, open_jtalk, cpu_num_threads):
        super().__init__()
        self.tts_calls = []

    async def tts(self, text, style_id):
        self.tts_calls.append((text, style_id))
        # 読み込んでいないモデルのスタイルでは合成できない
        assert any(style_id in ids for name, ids in STYLE_IDS.items() if model_id(name) in self.loaded)
        return b"wav"


STYLE_IDS = {name: {s.id for m in metas for s in m.styles} for name, metas in METAS.items()}


@pytest.fixture
def fake_core(monkeypatch):
    voicevox = _import_voicevox_module()

    class FakeVoiceModelFile:
        @staticmethod
        async def open(path):
            return FakeModel(path, model_id(path.name), METAS[path.name])

    class FakeOnnx:
        @staticmethod
        def get():
            return object()

    class FakeOpenJtalk:
        @staticmethod
        async def new(path):
            return object()

    monkeypatch.setattr(voicevox, "VoiceModelFile", FakeVoiceModelFile)
    monkeypatch.setattr(voicevox, "Onnxruntime", FakeOnnx)
    monkeypatch.setattr(voicevox, "OpenJtalk", FakeOpenJtalk)
    monkeypatch.setattr(voicevox, "Synthesizer", EngineSynth)
    return voicevox


def test_engine_lists_unloaded_speakers_and_loads_on_demand(tmp_path, fake_core, caplog):
    caplog.set_level(logging.INFO)
    touch_vvms(tmp_path, METAS)
    engine = fake_core.VoicevoxEngine(tmp_path, workers=2, extra_models=["*"], max_extra_models=1)
    try:
        speakers = engine.speakers()
        names = ["ずんだもん", "春日部つむぎ", "冥鳴ひまり", "九州そら", "九州そら（ささやき）"]
        assert [s.name for s in speakers] == names
        assert [s.extra for s in speakers] == [False, False, True, True, True]
        assert speakers[2].credit == "VOICEVOX:冥鳴ひまり"
        assert "s0.vvm" in caplog.text  # 読み上げに使える話者がないモデルは使わない

        synths = engine._models._synthesizers
        assert all(s.loaded == {model_id("0.vvm")} for s in synths)
        assert engine.synthesize("こんにちは", "冥鳴ひまり") == b"wav"
        assert all(s.loaded == {model_id("0.vvm"), model_id("1.vvm")} for s in synths)
        assert asyncio.run(engine.synthesize_async("やあ", "九州そら（ささやき）")) == b"wav"
        # 上限は1つなので 1.vvm は解放される
        assert all(s.loaded == {model_id("0.vvm"), model_id("2.vvm")} for s in synths)
        assert engine.synthesize("また", "ずんだもん") == b"wav"
        assert engine._sweeper is not None
    finally:
        engine._loop.stop()


def test_engine_default_loads_only_default_model(tmp_path, fake_core):
    touch_vvms(tmp_path, METAS)
    engine = fake_core.VoicevoxEngine(tmp_path)
    try:
        assert [s.name for s in engine.speakers()] == ["ずんだもん", "春日部つむぎ"]
        assert engine._sweeper is None
    finally:
        engine._loop.stop()


def test_engine_load_all_models_is_unchanged(tmp_path, fake_core):
    touch_vvms(tmp_path, METAS)
    engine = fake_core.VoicevoxEngine(tmp_path, load_all_models=True, extra_models=["1.vvm"])
    try:
        synth = engine._models._synthesizers[0]
        assert synth.loaded == {model_id("0.vvm"), model_id("1.vvm"), model_id("2.vvm")}
        assert len(engine.speakers()) == 5
        assert engine._models.loaded_extra() == []
        assert engine._sweeper is None
    finally:
        engine._loop.stop()


# --- 設定 ---


def load(tmp_path, extra=""):
    path = tmp_path / "config.ini"
    path.write_text(BASE + extra, encoding="utf-8")
    return load_config(path)


def test_config_defaults(tmp_path):
    config = load(tmp_path)
    assert config.voicevox_extra_models == ()
    assert config.voicevox_max_extra_models == 1
    assert config.voicevox_model_idle == 600
    assert config.extra_voices_premium_only is False
    assert config.voicevox_load_all_models is False


def test_config_extra_models(tmp_path):
    config = load(
        tmp_path,
        "VOICEVOX_EXTRA_MODELS = 1.vvm、2.vvm, 1.vvm\nVOICEVOX_MAX_EXTRA_MODELS = 2\nVOICEVOX_MODEL_IDLE = 120\n"
        "EXTRA_VOICES_PREMIUM_ONLY = True\n",
    )
    assert config.voicevox_extra_models == ("1.vvm", "2.vvm")
    assert (config.voicevox_max_extra_models, config.voicevox_model_idle) == (2, 120)
    assert config.extra_voices_premium_only is True
    assert load(tmp_path, "VOICEVOX_EXTRA_MODELS = *\n").voicevox_extra_models == ("*",)


@pytest.mark.parametrize(
    ("line", "key"),
    [
        ("VOICEVOX_EXTRA_MODELS = ../secret.vvm", "VOICEVOX_EXTRA_MODELS"),
        ("VOICEVOX_MAX_EXTRA_MODELS = 0", "VOICEVOX_MAX_EXTRA_MODELS"),
        ("VOICEVOX_MODEL_IDLE = 30", "VOICEVOX_MODEL_IDLE"),
        ("EXTRA_VOICES_PREMIUM_ONLY = maybe", "EXTRA_VOICES_PREMIUM_ONLY"),
    ],
)
def test_config_invalid(tmp_path, line, key):
    with pytest.raises(ConfigError, match=key):
        load(tmp_path, line + "\n")


# --- プラン（追加の話者をプレミアムだけにする） ---


class ExtraEngine(Engine):
    def speakers(self):
        return [Speaker(n, "fake", extra=n in ("冥鳴ひまり", "九州そら")) for n in self.names]


EXTRA_NAMES = ["ずんだもん", "春日部つむぎ", "冥鳴ひまり", "九州そら"]


def test_extra_voices_are_free_by_default():
    plans = build_plans(make_config(), ExtraEngine(EXTRA_NAMES))
    assert plans.speakers_for(1)[1] == frozenset(EXTRA_NAMES)


def test_extra_voices_premium_only(tmp_path):
    write_premium(tmp_path / "premium.json", {"1": None})
    config = premium_config(tmp_path, extra_voices_premium_only=True)
    config = config.__class__(**{**config.__dict__, "speaker_allowlist": ()})
    plans = build_plans(config, ExtraEngine(EXTRA_NAMES))
    plans.reload()
    assert plans.speakers_for(2)[1] == frozenset(["ずんだもん", "春日部つむぎ"])
    assert plans.speakers_for(1)[1] == frozenset(EXTRA_NAMES)


def test_extra_voices_premium_only_without_premium_warns(caplog):
    plans = build_plans(make_config(extra_voices_premium_only=True), ExtraEngine(EXTRA_NAMES))
    assert plans.speakers_for(1)[1] == frozenset(["ずんだもん", "春日部つむぎ"])
    assert "PREMIUM_ENABLED" in caplog.text
