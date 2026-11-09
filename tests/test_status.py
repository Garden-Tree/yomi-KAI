"""y.status（Bot の状態）のテスト。"""

import asyncio
import math
from types import SimpleNamespace

from test_voice_cog import FakeChannel, FakeVoiceClient, make_cog, make_config, wait_until

from yomi_kai.session import SynthesisStats
from yomi_kai.status import UNKNOWN, bot_version, format_duration, format_latency, status_fields
from yomi_kai.tts import Speaker


def test_bot_version_from_env():
    assert bot_version({"YOMI_KAI_VERSION": "abc1234"}) == "abc1234"
    assert bot_version({"YOMI_KAI_VERSION": " "}) == UNKNOWN
    assert bot_version({}) == UNKNOWN


def test_format_duration_and_latency():
    assert format_duration(30) == "1分未満"
    assert format_duration(5 * 60) == "5分"
    assert format_duration(2 * 3600 + 60) == "2時間 1分"
    assert format_duration(3 * 86400 + 4 * 3600 + 5 * 60) == "3日 4時間 5分"
    assert format_latency(0.0423) == "42ms"
    assert format_latency(math.inf) == UNKNOWN


def test_synthesis_stats_health():
    now = [100.0]
    stats = SynthesisStats(clock=lambda: now[0])
    assert stats.healthy
    stats.failure()
    assert not stats.healthy and stats.errors == 1
    now[0] = 101.0
    stats.success()
    assert stats.healthy and stats.last_error == 100.0


def test_status_fields():
    fields = dict(
        status_fields(
            latency=0.05, sessions=2, queue=None, workers=1, healthy=True, last_error=None, errors=0,
            uptime=None, version=UNKNOWN,
        )
    )
    assert fields["Discord の応答時間"] == "50ms"
    assert fields["このサーバーの読み上げ待ち"] == "-"
    assert fields["音声合成"] == "VOICEVOX の合成ワーカー 1個・正常"
    assert fields["稼働時間"] == UNKNOWN and fields["バージョン"] == UNKNOWN


def test_status_synthesis_label_names_google_when_it_is_the_default():
    fields = dict(
        status_fields(
            latency=0.05, sessions=0, queue=0, workers=1, healthy=True, last_error=None, errors=0,
            uptime=None, version=UNKNOWN, google=True,
        )
    )
    assert fields["音声合成"] == "Google TTS（VOICEVOX の合成ワーカー 1個）・正常"


class FailingEngine:
    default_speaker = "ずんだもん"

    def speakers(self):
        return [Speaker("ずんだもん", "fake")]

    def synthesize(self, text, speaker, speed=1.0):
        raise RuntimeError("boom")


def test_status_command_reports_failed_synthesis(tmp_path, monkeypatch):
    monkeypatch.setenv("YOMI_KAI_VERSION", "deadbee")
    cog = make_cog(tmp_path, make_config(voicevox_workers=2), FailingEngine())
    cog.bot = SimpleNamespace(user=None, latency=0.1, started_at=0.0)
    sent = []

    class Ctx:
        interaction = None
        guild = SimpleNamespace(id=1)

        async def send(self, content=None, **kwargs):
            sent.append(kwargs["embed"])

    async def run():
        session = cog.get_session(1)
        await session.start(FakeVoiceClient(), FakeChannel())
        session.enqueue("失敗する", "ずんだもん")
        await wait_until(lambda: cog.stats.errors == 1 and not session.is_busy)
        await cog.status.callback(cog, Ctx())
        await session.close()

    asyncio.run(run())
    fields = {f.name: f.value for f in sent[0].fields}
    assert fields["接続中のボイスチャンネル"] == "1件（この Bot 全体）"
    assert fields["このサーバーの読み上げ待ち"] == "0件"
    assert fields["音声合成"].startswith("VOICEVOX の合成ワーカー 2個・直近の合成に失敗しています（最後の失敗:")
    assert fields["バージョン"] == "deadbee"
    assert fields["稼働時間"] != UNKNOWN
