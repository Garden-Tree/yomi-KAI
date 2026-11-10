"""読み上げの開始までの時間（LatencyStats）のテスト。"""

import asyncio

import pytest
from test_session import TSUMUGI, ZUNDA, FakeChannel, FakeEngine, FakeVoiceClient, wait_until

from yomi_kai.session import GuildSession, LatencyStats, LatencySummary, percentile
from yomi_kai.status import format_speech_latency, status_fields


def test_percentile_nearest_rank():
    values = [float(i) for i in range(1, 101)]
    assert percentile(values, 0.95) == 95.0
    assert percentile(values, 0.5) == 50.0
    assert percentile([3.0], 0.95) == 3.0
    assert percentile([1.0, 2.0], 0.0) == 1.0
    with pytest.raises(ValueError):
        percentile([], 0.5)


def test_summary_is_none_without_samples():
    stats = LatencyStats()
    assert stats.summary() is None
    assert stats.summary(1) is None


def test_summary_median_p95_and_count():
    stats = LatencyStats()
    for i in range(1, 21):
        stats.record(1, i / 10)
    summary = stats.summary()
    assert summary.count == 20
    assert summary.median == pytest.approx(1.05)
    assert summary.p95 == pytest.approx(1.9)


def test_ring_buffer_keeps_latest_per_guild():
    stats = LatencyStats(maxlen=3)
    for value in (10.0, 20.0, 1.0, 2.0, 3.0):
        stats.record(1, value)
    stats.record(2, 5.0)
    assert stats.summary(1) == LatencySummary(3, 2.0, 3.0)
    # 全サーバーをまとめて集計する
    assert stats.summary() == LatencySummary(4, 2.5, 5.0)
    stats.forget(1)
    assert stats.summary() == LatencySummary(1, 5.0, 5.0)


def test_negative_values_are_clamped():
    stats = LatencyStats()
    stats.record(1, -0.5)
    assert stats.summary().median == 0.0


def test_format_speech_latency():
    assert format_speech_latency(None) == "記録なし"
    text = format_speech_latency(LatencySummary(42, 0.4321, 0.9))
    assert "中央値 432ms" in text and "900ms" in text and "42件" in text


def test_status_fields_include_speech_latency():
    common = dict(
        latency=0.05,
        sessions=0,
        queue=0,
        workers=1,
        healthy=True,
        last_error=None,
        errors=0,
        uptime=None,
        version="x",
    )
    assert dict(status_fields(**common))["読み上げの開始までの時間"] == "記録なし"
    fields = dict(status_fields(**common, speech_latency=LatencySummary(1, 0.2, 0.2)))
    assert fields["読み上げの開始までの時間"].startswith("中央値 200ms")


def make_session(engine, latency):
    return GuildSession(1, engine, afk_time=60.0, source_factory=lambda wav: wav, latency=latency)


def test_session_records_once_per_played_message():
    async def run():
        latency = LatencyStats()
        session = make_session(FakeEngine(), latency)
        voice = FakeVoiceClient()
        await session.start(voice, FakeChannel())
        # 3つのかたまりに分かれる長文でも、記録は1メッセージにつき1件
        session.enqueue("一文目。二文目。三文目。", ZUNDA, received_at=latency.clock())
        session.enqueue("次の発言", TSUMUGI, received_at=latency.clock())
        session.enqueue("時刻なし", ZUNDA)  # received_at が無ければ数えない
        await wait_until(lambda: len(voice.played) == 5 and not session.is_busy)
        await session.close()
        return latency.summary()

    summary = asyncio.run(run())
    assert summary.count == 2
    assert 0.0 <= summary.median < 5.0


def test_failed_and_skipped_messages_are_not_counted():
    async def run():
        latency = LatencyStats()
        session = make_session(FakeEngine(delays={"遅い": 0.2}, fail={"失敗"}), latency)
        voice = FakeVoiceClient()
        await session.start(voice, FakeChannel())
        session.enqueue("失敗", ZUNDA, received_at=latency.clock())
        session.enqueue("遅い", ZUNDA, received_at=latency.clock())
        await asyncio.sleep(0.05)
        session.skip_all()  # 合成中の「遅い」は再生されない
        await wait_until(lambda: not session.is_busy)
        await session.close()
        return latency.summary(), voice.played

    summary, played = asyncio.run(run())
    assert played == []
    assert summary is None


def test_voice_cog_records_latency_from_on_message(tmp_path):
    from test_voice_cog import FakeEngine as CogEngine
    from test_voice_cog import make_cog, make_config, read_message

    engine = CogEngine()
    cog = make_cog(tmp_path, make_config(), engine)
    asyncio.run(read_message(cog, engine, "こんにちは"))
    assert cog.latency.summary().count == 1
