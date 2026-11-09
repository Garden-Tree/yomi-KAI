"""話者の許可リスト（SPEAKER_ALLOWLIST / FilteredEngine）のテスト。"""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from yomi_kai.cogs.settings import SettingsCog
from yomi_kai.config import Config, ConfigError
from yomi_kai.main import apply_speaker_allowlist
from yomi_kai.settings import GuildSettings, SettingsStore, UserSettings, resolve_voice
from yomi_kai.tts import CompositeEngine, FilteredEngine, Speaker

NAMES = ["ずんだもん", "春日部つむぎ", "四国めたん", "ずんだもん（あまあま）", "雨晴はう"]


class RecordingEngine:
    def __init__(self, names, default=None):
        self.names = names
        self.default = default or names[0]
        self.calls = []

    @property
    def default_speaker(self):
        return self.default

    def speakers(self):
        return [Speaker(n, "fake") for n in self.names]

    def synthesize(self, text, speaker, speed=1.0):
        self.calls.append((text, speaker, speed))
        return b"wav"


def test_filters_speakers_in_engine_order():
    engine = FilteredEngine(RecordingEngine(NAMES), ["雨晴はう", "ずんだもん", "四国めたん"])
    assert [s.name for s in engine.speakers()] == ["ずんだもん", "四国めたん", "雨晴はう"]
    assert engine.default_speaker == "ずんだもん"
    assert engine.unknown == []


def test_default_falls_back_to_first_allowed_speaker():
    engine = FilteredEngine(RecordingEngine(NAMES), ["四国めたん", "春日部つむぎ"])
    assert engine.default_speaker == "春日部つむぎ"


def test_disallowed_speaker_is_synthesized_with_default():
    inner = RecordingEngine(NAMES)
    engine = FilteredEngine(inner, ["春日部つむぎ"])
    engine.synthesize("やあ", "ずんだもん", 1.5)
    engine.synthesize("こんにちは", "春日部つむぎ")
    assert inner.calls == [("やあ", "春日部つむぎ", 1.5), ("こんにちは", "春日部つむぎ", 1.0)]


def test_wraps_composite_engine():
    google = RecordingEngine(["ja-JP-Wavenet-A", "ja-JP-Wavenet-D"])
    voicevox = RecordingEngine(NAMES)
    engine = FilteredEngine(CompositeEngine([google, voicevox]), ["ja-JP-Wavenet-D", "ずんだもん"])
    assert [s.name for s in engine.speakers()] == ["ja-JP-Wavenet-D", "ずんだもん"]
    assert engine.default_speaker == "ja-JP-Wavenet-D"
    engine.synthesize("x", "ずんだもん")
    assert voicevox.calls == [("x", "ずんだもん", 1.0)]


def test_apply_allowlist_warns_about_unknown_names(caplog):
    inner = RecordingEngine(NAMES)
    assert apply_speaker_allowlist(inner, ()) is inner  # 未設定なら何もしない
    with caplog.at_level(logging.WARNING):
        engine = apply_speaker_allowlist(inner, ("ずんだもん", "存在しない話者"))
    assert [s.name for s in engine.speakers()] == ["ずんだもん"]
    assert "存在しない話者" in caplog.text


def test_apply_allowlist_without_any_valid_name_is_config_error():
    with pytest.raises(ConfigError, match="SPEAKER_ALLOWLIST"):
        apply_speaker_allowlist(RecordingEngine(NAMES), ("いない", "いない2"))


def test_saved_disallowed_speaker_falls_back():
    engine = FilteredEngine(RecordingEngine(NAMES), ["春日部つむぎ", "四国めたん"])
    available = frozenset(s.name for s in engine.speakers())
    settings = GuildSettings(speaker="ずんだもん", users={1: UserSettings(speaker="ずんだもん（あまあま）")})
    assert resolve_voice(settings, 1, available, engine.default_speaker) == ("春日部つむぎ", 1.0)
    settings = GuildSettings(speaker="四国めたん", users={1: UserSettings(speaker="雨晴はう")})
    assert resolve_voice(settings, 1, available, engine.default_speaker) == ("四国めたん", 1.0)


class FakeCtx:
    def __init__(self):
        self.interaction = None
        self.guild = SimpleNamespace(id=1)
        self.author = SimpleNamespace(id=7, display_name="alice")
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


def test_voice_commands_only_see_allowed_speakers(tmp_path):
    engine = FilteredEngine(RecordingEngine(NAMES), ["ずんだもん", "春日部つむぎ"])
    config = Config(discord_token="dummy", prefix="y.", afk_time=60, sd_msg="x")
    store = SettingsStore(tmp_path / "settings")
    cog = SettingsCog(SimpleNamespace(), config, engine, store)

    async def run():
        listing, denied, allowed = FakeCtx(), FakeCtx(), FakeCtx()
        await cog._send_speaker_list(listing)
        await cog._set_speaker_by_name(denied, "四国めたん")
        await cog._set_speaker_by_name(allowed, "ずんだもん")  # 「ずんだもん（あまあま）」は候補にならない
        return listing, denied, allowed

    listing, denied, allowed = asyncio.run(run())
    assert listing.sent == ["使える話者（2件）:\nずんだもん\n春日部つむぎ"]
    assert "見つかりません" in denied.sent[0]
    assert allowed.sent == ["aliceさんの話者をずんだもんにしました"]
    assert cog.available_speakers == {"ずんだもん", "春日部つむぎ"}
