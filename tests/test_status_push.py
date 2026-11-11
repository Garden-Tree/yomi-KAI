"""稼働状況の送信（status_push）のテスト。HTTP はモックし、Discord には接続しない。"""

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest
from test_commands import StubEngine
from test_config import load

from yomi_kai.config import Config, ConfigError
from yomi_kai.dictionary import DictionaryStore
from yomi_kai.main import YomiBot
from yomi_kai.session import LatencyStats, LatencySummary
from yomi_kai.settings import SettingsStore
from yomi_kai.status_push import StatusPusher, StatusPushError, build_payload, http_poster, next_delay

URL = "https://status.example.com/api/heartbeat"
TOKEN = "secret-push-token"

# --- 設定 ---


def test_status_push_is_off_by_default(tmp_path):
    config = load(tmp_path)
    assert config.status_push_url is None and config.status_push_token is None
    assert config.status_push_interval == 300


def test_status_push_settings(tmp_path):
    config = load(tmp_path, f"STATUS_PUSH_URL = {URL}\nSTATUS_PUSH_TOKEN = {TOKEN}\nSTATUS_PUSH_INTERVAL = 120\n")
    assert (config.status_push_url, config.status_push_token, config.status_push_interval) == (URL, TOKEN, 120)
    assert TOKEN not in repr(config) and "example.com" not in repr(config)


def test_empty_status_push_keys_disable_it(tmp_path):
    config = load(tmp_path, "STATUS_PUSH_URL =\nSTATUS_PUSH_TOKEN =\nSTATUS_PUSH_INTERVAL =\n")
    assert config.status_push_url is None and config.status_push_interval == 300


@pytest.mark.parametrize(
    ("extra", "key"),
    [
        (f"STATUS_PUSH_URL = {URL}\n", "STATUS_PUSH_TOKEN"),
        (f"STATUS_PUSH_TOKEN = {TOKEN}\n", "STATUS_PUSH_URL"),
        (f"STATUS_PUSH_URL = http://example.com/hb\nSTATUS_PUSH_TOKEN = {TOKEN}\n", "STATUS_PUSH_URL"),
        ("STATUS_PUSH_INTERVAL = 59\n", "STATUS_PUSH_INTERVAL"),
    ],
)
def test_invalid_status_push_settings(tmp_path, extra, key):
    with pytest.raises(ConfigError, match=key) as excinfo:
        load(tmp_path, extra)
    assert TOKEN not in str(excinfo.value) and "example.com" not in str(excinfo.value)


# --- 送信内容 ---


def test_build_payload():
    payload = build_payload("abc1234", LatencySummary(87, 0.4204, 0.9106), 3, now=lambda: 1767225600.7)
    assert payload == {
        "version": "abc1234",
        "ts": 1767225600,
        "latency_median_ms": 420,
        "latency_p95_ms": 911,
        "latency_samples": 87,
        "voice_connections": 3,
    }


def test_build_payload_without_samples():
    payload = build_payload("不明", None, 0, now=lambda: 10.0)
    assert payload["latency_median_ms"] is None and payload["latency_p95_ms"] is None
    assert payload["latency_samples"] == 0


# --- HTTP（aiohttp をモック） ---


class FakeResponse:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, calls, status=204, error=None, **kwargs):
        self.calls = calls
        self.status = status
        self.error = error
        calls.append(("session", kwargs))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, json, headers):
        self.calls.append(("post", url, json, headers))
        if self.error is not None:
            raise self.error
        return FakeResponse(self.status)


def run_poster(status=204, error=None):
    calls = []
    with patch("aiohttp.ClientSession", lambda **kw: FakeSession(calls, status, error, **kw)):
        asyncio.run(http_poster(URL, TOKEN, timeout=5)({"ts": 1}))
    return calls


def test_http_poster_sends_bearer_token():
    calls = run_poster()
    (_, session_kwargs), (_, url, payload, headers) = calls
    assert session_kwargs["timeout"].total == 5
    assert url == URL and payload == {"ts": 1}
    assert headers == {"Authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize(
    ("status", "error", "message"),
    [
        (401, None, "HTTP 401"),
        (204, TimeoutError(), "タイムアウト"),
        (204, OSError(f"cannot connect to {URL}"), "OSError"),
    ],
)
def test_http_poster_errors_hide_url_and_token(status, error, message):
    with pytest.raises(StatusPushError) as excinfo:
        run_poster(status, error)
    assert str(excinfo.value) == message
    assert "example.com" not in str(excinfo.value) and TOKEN not in str(excinfo.value)


# --- 送信ループ ---


@pytest.mark.parametrize(
    ("now", "delay"),
    [
        (150.0, 300.0),  # 区間の中ほどで送ったら、次の区間の中ほどまで
        (150.4, 299.6),
        (1000.0, 50.0 + 300.0),  # 1000 は区間 [900, 1200) の中ほど（1050）より前。同じ区間には送らない
        (1100.0, 250.0),  # 中ほどを過ぎていれば次の区間の中ほど（1350）
        (1199.0, 151.0),
        (1200.0, 450.0),
    ],
)
def test_next_delay_aligns_to_the_middle_of_each_slot(now, delay):
    assert next_delay(now, 300) == pytest.approx(delay)
    assert (now + next_delay(now, 300)) % 300 == pytest.approx(150)


class Cancel(Exception):
    pass


def run_pusher(alive, post, *, clock=None, sleeps_until_stop=None):
    """``alive`` の値を順に返し、すべて使い切ったら止める。sleep した秒数のリストを返す。"""
    alive = list(alive)
    sleeps = []
    limit = sleeps_until_stop or len(alive)

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= limit:
            raise asyncio.CancelledError

    pusher = StatusPusher(
        post,
        lambda: {"ts": len(sleeps)},
        lambda: alive.pop(0),
        interval=300,
        idle_interval=30,
        warn_interval=1800,
        clock=clock or (lambda: 0.0),
        wall=lambda: 150.0,
        sleep=sleep,
    )

    async def main():
        with pytest.raises(asyncio.CancelledError):
            await pusher.run()

    asyncio.run(main())
    return sleeps


def test_pusher_sends_only_while_connected():
    sent = []

    async def post(payload):
        sent.append(payload)

    sleeps = run_pusher([False, True, True, False, True], post)
    assert sleeps == [30, 300, 300, 30, 300]
    assert sent == [{"ts": 1}, {"ts": 2}, {"ts": 4}]


def test_pusher_survives_failures_and_rate_limits_warnings(caplog):
    times = iter([0.0, 600.0, 1799.0, 1800.0])

    async def post(payload):
        raise StatusPushError("HTTP 500")

    with caplog.at_level(logging.DEBUG, logger="yomi_kai.status_push"):
        sleeps = run_pusher([True] * 4, post, clock=lambda: next(times))
    assert sleeps == [300] * 4  # 失敗しても間隔は変えず、ループも止まらない
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(warnings) == 2  # 0秒と1800秒の時点だけ
    assert len(debugs) == 2
    assert all("HTTP 500" in r.getMessage() for r in warnings)


def test_pusher_survives_unexpected_errors_and_logs_recovery(caplog):
    results = iter([RuntimeError(f"boom {TOKEN}"), None])

    async def post(payload):
        error = next(results)
        if error is not None:
            raise error

    with caplog.at_level(logging.INFO, logger="yomi_kai.status_push"):
        run_pusher([True, True], post)
    messages = [r.getMessage() for r in caplog.records]
    assert any("RuntimeError" in m for m in messages)
    assert all(TOKEN not in m for m in messages)  # 想定外の例外のメッセージは出さない
    assert any("復旧" in m for m in messages)


def test_pusher_treats_is_alive_errors_as_disconnected():
    sent = []

    async def post(payload):
        sent.append(payload)

    def broken():
        raise RuntimeError

    async def sleep(seconds):
        raise asyncio.CancelledError

    pusher = StatusPusher(post, dict, broken, sleep=sleep)

    async def main():
        with pytest.raises(asyncio.CancelledError):
            await pusher.run()

    asyncio.run(main())
    assert sent == []


# --- Bot との結線 ---


def make_bot(tmp_path, **kwargs):
    config = Config(discord_token="dummy", prefix="y.", afk_time=60, sd_msg="x", **kwargs)
    return YomiBot(config, StubEngine(), DictionaryStore(tmp_path / "dict"), SettingsStore(tmp_path / "settings"))


def test_bot_has_no_pusher_by_default(tmp_path):
    assert make_bot(tmp_path).status_pusher is None


def test_bot_payload_uses_voice_cog_latency(tmp_path, monkeypatch):
    bot = make_bot(tmp_path, status_push_url=URL, status_push_token=TOKEN, status_push_interval=60)
    assert bot.status_pusher is not None and bot.status_pusher.interval == 60
    monkeypatch.setenv("YOMI_KAI_VERSION", "abc1234")
    payload = bot.status_payload()  # VoiceCog が無くても送れる
    assert payload["version"] == "abc1234" and payload["latency_samples"] == 0
    assert payload["voice_connections"] == 0

    latency = LatencyStats()
    latency.record(1, 0.25)
    monkeypatch.setattr(bot, "get_cog", MagicMock(return_value=MagicMock(latency=latency)))
    payload = bot.status_payload()
    assert payload["latency_median_ms"] == 250 and payload["latency_samples"] == 1
