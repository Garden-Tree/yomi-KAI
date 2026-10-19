"""接続監視（ハートビート・切断通知）のテスト。Discord には接続しない。"""

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

from yomi_kai import health
from yomi_kai.health import (
    DisconnectAlerter,
    format_time,
    heartbeat_age,
    is_healthy,
    mask_url,
    write_heartbeat,
)

ROOT = Path(__file__).resolve().parent.parent

# --- ハートビート ---


def test_heartbeat_fresh_and_stale(tmp_path):
    path = tmp_path / "log" / "heartbeat"
    assert not is_healthy(path)  # ファイルが無い
    write_heartbeat(path, now=lambda: 1000.0)
    assert path.read_text(encoding="ascii").strip() == "1000"
    assert heartbeat_age(path, now=lambda: 1030.0) == 30.0
    assert is_healthy(path, now=lambda: 1000.0 + health.HEARTBEAT_MAX_AGE)
    assert not is_healthy(path, now=lambda: 1001.0 + health.HEARTBEAT_MAX_AGE)
    assert sorted(p.name for p in path.parent.iterdir()) == ["heartbeat"]  # 一時ファイルが残らない


def test_heartbeat_falls_back_to_mtime(tmp_path):
    path = tmp_path / "heartbeat"
    path.write_text("garbage", encoding="ascii")
    os.utime(path, (500.0, 500.0))
    assert heartbeat_age(path, now=lambda: 600.0) == 100.0


def test_heartbeat_loop_writes_only_while_alive(tmp_path):
    path = tmp_path / "heartbeat"
    alive = iter([False, True, False])
    clock = iter([10.0, 20.0, 30.0])
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise asyncio.CancelledError

    async def run():
        try:
            await health.heartbeat_loop(lambda: next(alive), path, 30, now=lambda: next(clock), sleep=sleep)
        except asyncio.CancelledError:
            pass

    asyncio.run(run())
    assert sleeps == [30, 30, 30]
    assert path.read_text(encoding="ascii").strip() == "10"  # 生きていた1回だけ書いた


def test_health_cli_exit_codes(tmp_path):
    def run():
        env = {**os.environ, "PYTHONPATH": str(ROOT)}
        return subprocess.run([sys.executable, "-m", "yomi_kai.health"], cwd=tmp_path, env=env).returncode

    assert run() == 1
    write_heartbeat(tmp_path / "log" / "heartbeat")
    assert run() == 0
    write_heartbeat(tmp_path / "log" / "heartbeat", now=lambda: time.time() - 1000)
    assert run() == 1


def test_health_module_does_not_import_discord():
    code = "import sys, yomi_kai.health; print('discord' in sys.modules, 'voicevox_core' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    assert out == ["False", "False"]


def test_mask_url():
    url = "https://discord.com/api/webhooks/123/secret-token"
    assert mask_url(url) == "https://discord.com/***"
    assert "secret" not in mask_url(url)


# --- 切断通知 ---


class FakeClock:
    def __init__(self, start=1_000_000.0):
        self.t = start

    def __call__(self):
        return self.t


class Harness:
    """偽の時計・sleep・送信関数で DisconnectAlerter を動かす。

    sleep は実際には待たず、時計を進めてから制御を返す。
    """

    def __init__(self, alert_after=300, fail_times=0):
        self.clock = FakeClock()
        self.sent = []
        self.attempts = 0
        self.fail_times = fail_times
        self.sleeps = []
        self.alerter = DisconnectAlerter(
            self.send, alert_after=alert_after, now=self.clock, sleep=self.sleep, retry_initial=10, retry_max=40
        )

    async def send(self, content):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise OSError("network is unreachable")
        self.sent.append(content)

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.clock.t += seconds
        await asyncio.sleep(0)

    async def settle(self):
        for _ in range(50):
            await asyncio.sleep(0)


def at(h: Harness, offset: float) -> str:
    return format_time(1_000_000.0 + offset)


def test_short_disconnect_sends_nothing():
    async def run():
        h = Harness()
        # 閾値（300秒）の待機中に復帰させるため、sleep は止めたままにしておく
        gate = asyncio.Event()

        async def blocking_sleep(seconds):
            h.sleeps.append(seconds)
            await gate.wait()

        h.alerter._sleep = blocking_sleep
        h.alerter.disconnected()
        await h.settle()
        h.clock.t += 120
        h.alerter.connected()
        await h.settle()
        assert h.sent == []
        assert h.sleeps == [300]
        assert not h.alerter.is_down
        await h.alerter.close()

    asyncio.run(run())


def test_repeated_disconnect_keeps_first_time():
    async def run():
        h = Harness()
        h.alerter._sleep = lambda s: asyncio.Event().wait()
        h.alerter.disconnected()
        start = h.alerter.down_since
        h.clock.t += 50
        h.alerter.disconnected()
        assert h.alerter.down_since == start
        await h.alerter.close()

    asyncio.run(run())


def test_alert_after_threshold_then_recovery():
    async def run():
        h = Harness(alert_after=300)
        h.alerter.disconnected()
        await h.settle()
        assert h.sleeps == [300]
        assert h.sent == [f"yomi-KAI が 5分以上 Discord から切断されています（{at(h, 0)}〜）"]

        h.clock.t += 600  # 合計 900 秒後に復帰
        h.alerter.connected()
        await h.settle()
        assert h.sent[1] == f"yomi-KAI が復旧しました（停止: {at(h, 0)}〜{at(h, 900)}、15分）"
        assert len(h.sent) == 2
        await h.alerter.close()

    asyncio.run(run())


def test_alert_retries_with_backoff_until_sent():
    async def run():
        h = Harness(alert_after=300, fail_times=3)
        h.alerter.disconnected()
        await h.settle()
        # 300秒待ってから送信、失敗のたびに 10, 20, 40(上限) 秒待って再送
        assert h.sleeps == [300, 10, 20, 40]
        assert h.attempts == 4
        # 再送時点の経過時間（370秒 → 6分）で文面を作る
        assert h.sent == [f"yomi-KAI が 6分以上 Discord から切断されています（{at(h, 0)}〜）"]
        await h.alerter.close()

    asyncio.run(run())


def test_recovery_reports_full_window_even_if_alert_never_sent():
    async def run():
        h = Harness(alert_after=300, fail_times=10**6)
        gate = asyncio.Event()
        real_sleep = h.sleep

        async def sleep(seconds):
            await real_sleep(seconds)
            if len(h.sleeps) >= 4:  # 何度か失敗した後、ネットワーク復旧まで待つ
                await gate.wait()

        h.alerter._sleep = sleep
        h.alerter.disconnected()
        await h.settle()
        assert h.attempts >= 3 and h.sent == []

        h.clock.t = 1_000_000.0 + 1800  # 30分後に復帰
        h.fail_times = 0
        h.attempts = 0
        h.alerter.connected()
        await h.settle()
        assert h.sent == [f"yomi-KAI が復旧しました（停止: {at(h, 0)}〜{at(h, 1800)}、30分）"]
        await h.alerter.close()

    asyncio.run(run())


def test_recovery_message_retries():
    async def run():
        h = Harness(alert_after=300)
        h.alerter.disconnected()
        await h.settle()
        assert len(h.sent) == 1
        h.fail_times = h.attempts + 2  # 復旧通知を2回失敗させる
        h.clock.t += 60
        h.alerter.connected()
        await h.settle()
        assert h.sent[-1].startswith("yomi-KAI が復旧しました")
        assert h.attempts == 4
        await h.alerter.close()

    asyncio.run(run())


def test_second_outage_is_tracked_separately():
    async def run():
        h = Harness(alert_after=300)
        h.alerter.disconnected()
        await h.settle()
        h.clock.t += 10
        h.alerter.connected()
        await h.settle()
        assert len(h.sent) == 2

        h.clock.t += 1000
        second_start = h.clock.t - 1_000_000.0
        h.alerter.disconnected()
        await h.settle()
        assert h.sent[2] == f"yomi-KAI が 5分以上 Discord から切断されています（{at(h, second_start)}〜）"
        await h.alerter.close()

    asyncio.run(run())


def test_close_cancels_pending_tasks():
    async def run():
        h = Harness(fail_times=10**6)
        h.alerter.disconnected()
        await asyncio.sleep(0)
        await h.alerter.close()
        assert not h.alerter._tasks

    asyncio.run(run())
