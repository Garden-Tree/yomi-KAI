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
