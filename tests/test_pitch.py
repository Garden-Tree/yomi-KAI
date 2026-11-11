"""声の高さ（y.pitch）のテスト。"""

import asyncio
import math
import types
from types import SimpleNamespace

import pytest

from yomi_kai.cogs.settings import PITCH_INVALID_NOTICE, SettingsCog, format_pitch, parse_pitch
from yomi_kai.config import Config
from yomi_kai.settings import (
    PITCH_STEP,
    GuildSettings,
    SettingsStore,
    UserSettings,
    pitch_scale,
    resolve_pitch,
)
from yomi_kai.tts import CompositeEngine, FilteredEngine, Speaker, google_semitones, synthesize_async


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("3", 3), ("+3", 3), ("-10", -10), ("10", 10), ("0", 0), ("＋２", 2), ("−4", -4), (" 5 ", 5)],
)
def test_parse_pitch_accepts_integers_in_range(raw, expected):
    assert parse_pitch(raw) == expected


@pytest.mark.parametrize("raw", ["11", "-11", "1.5", "abc", "", "nan"])
def test_parse_pitch_rejects_invalid(raw):
    assert parse_pitch(raw) is None


def test_format_pitch():
    assert format_pitch(3) == "+3"
    assert format_pitch(-2) == "-2"
    assert format_pitch(0) == "0（標準）"


def test_pitch_scale_maps_levels_to_voicevox_units():
    assert pitch_scale(None) == 0.0
    assert pitch_scale(0) == 0.0
    assert pitch_scale(10) == pytest.approx(0.15)
    assert pitch_scale(-10) == pytest.approx(-0.15)
    assert pitch_scale(99) == pytest.approx(10 * PITCH_STEP)  # 範囲外は丸める


def test_google_semitones_matches_voicevox_pitch():
    # pitch_scale 0.15 は基本周波数を e^0.15 倍 = 約2.6半音
    assert google_semitones(0.15) == pytest.approx(12 * math.log2(math.exp(0.15)))
    assert google_semitones(0.0) == 0.0
    assert google_semitones(5.0) == 20.0  # Google の上限


def test_resolve_pitch_uses_user_setting():
    settings = GuildSettings(users={1: UserSettings(pitch=4)})
    assert resolve_pitch(settings, 1) == pytest.approx(4 * PITCH_STEP)
    assert resolve_pitch(settings, 2) == 0.0


def test_store_roundtrip_and_clamps_hand_edited_values(tmp_path):
    store = SettingsStore(tmp_path)
    store.set_user_pitch(1, 7, -3)
    assert store.get(1).user(7).pitch == -3
    assert store.clear_user_pitch(1, 7)
    assert store.get(1).user(7).pitch is None
    assert not store.clear_user_pitch(1, 7)
    with pytest.raises(ValueError):
        store.set_user_pitch(1, 7, 11)
    parsed = GuildSettings.from_json({"users": {"7": {"pitch": 25}}, "speaker": "x"})
    assert parsed.user(7).pitch == 10
    with pytest.raises(ValueError):
        GuildSettings.from_json({"users": {"7": {"pitch": "high"}}})


class RecordingEngine:
    def __init__(self, names):
        self.names = names
        self.calls = []

    @property
    def default_speaker(self):
        return self.names[0]

    def speakers(self):
        return [Speaker(n, "fake") for n in self.names]

    def synthesize(self, text, speaker, speed=1.0, pitch=0.0):
        self.calls.append((text, speaker, speed, pitch))
        return b"wav"


def test_wrappers_forward_pitch():
    inner = RecordingEngine(["ずんだもん", "春日部つむぎ"])
    engine = FilteredEngine(CompositeEngine([inner]), ["春日部つむぎ"])
    engine.synthesize("a", "春日部つむぎ", 1.2, 0.06)
    asyncio.run(synthesize_async(engine, "b", "春日部つむぎ", 1.0, -0.03))
    assert inner.calls == [("a", "春日部つむぎ", 1.2, 0.06), ("b", "春日部つむぎ", 1.0, -0.03)]


class FakeSynthesizer:
    def __init__(self):
        self.used = []

    async def tts(self, text, style_id):
        self.used.append("tts")
        return b"wav"

    async def create_audio_query(self, text, style_id):
        return types.SimpleNamespace(speed_scale=1.0, pitch_scale=0.0)

    async def synthesis(self, query, style_id):
        self.used.append(("synthesis", query.speed_scale, query.pitch_scale))
        return b"wav"


def test_voicevox_sets_pitch_scale():
    from test_tts import _import_voicevox_module

    voicevox = _import_voicevox_module()
    synthesizer = FakeSynthesizer()

    async def run():
        await voicevox.synthesize_with(synthesizer, "a", 3, 1.0)
        await voicevox.synthesize_with(synthesizer, "a", 3, 1.0, 0.09)

    asyncio.run(run())
    assert synthesizer.used == ["tts", ("synthesis", 1.0, 0.09)]


class FakeCtx:
    def __init__(self):
        self.interaction = None
        self.guild = SimpleNamespace(id=1)
        self.author = SimpleNamespace(id=7, display_name="alice")
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


def test_pitch_command(tmp_path):
    store = SettingsStore(tmp_path)
    config = Config(discord_token="d", prefix="y.", afk_time=60, sd_msg="x")
    cog = SettingsCog(SimpleNamespace(), config, RecordingEngine(["ずんだもん"]), store)

    async def run(value):
        ctx = FakeCtx()
        await SettingsCog.pitch.callback(cog, ctx, value)
        return ctx.sent[0]

    assert asyncio.run(run(None)) == "現在の声の高さは0（標準）です"
    assert asyncio.run(run("+4")) == "aliceさんの声の高さを+4にしました"
    assert store.get(1).user(7).pitch == 4
    assert asyncio.run(run(None)) == "現在の声の高さは+4です"
    assert asyncio.run(run("20")) == PITCH_INVALID_NOTICE
    assert asyncio.run(run("reset")) == "声の高さの個人設定を解除しました"
    assert store.get(1).user(7).pitch is None


def test_session_passes_pitch_to_engine():
    from test_session import FakeChannel, FakeVoiceClient, make_session, wait_until

    engine = RecordingEngine(["ずんだもん"])

    async def run():
        session, created = make_session(engine)
        await session.start(FakeVoiceClient(), FakeChannel())
        session.enqueue("こんにちは", "ずんだもん", 1.0, pitch=0.045)
        session.enqueue("やあ", "ずんだもん")
        await wait_until(lambda: len(created) == 2)
        await session.close()

    asyncio.run(run())
    assert [call[3] for call in engine.calls] == [0.045, 0.0]
