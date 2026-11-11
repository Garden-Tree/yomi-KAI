"""稼働状況の送信（ステータスページ用のハートビート）。

``STATUS_PUSH_URL`` と ``STATUS_PUSH_TOKEN`` を設定したときだけ、Discord に接続している間、
``STATUS_PUSH_INTERVAL`` 秒（既定 300、60 以上）ごとに次の JSON を POST する
（``Authorization: Bearer <token>``）::

    {"version": "abc1234", "ts": 1767225600, "latency_median_ms": 420, "latency_p95_ms": 910,
     "latency_samples": 87, "voice_connections": 3}

``latency_*`` は読み上げの開始までの時間（メッセージの受信から再生の開始まで、``LatencyStats``）で、
記録が無ければ ``null`` / 0。メッセージの本文・サーバー ID などは送らない。

送信は aiohttp（discord.py の依存）で行い、タイムアウトは短く、失敗しても Bot には影響しない
（WARNING ログは ``WARN_INTERVAL`` 秒に1回まで、それ以外は DEBUG）。このモジュールは discord を import しない。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL = 300
MIN_INTERVAL = 60
# 未接続のときに接続を確かめ直す間隔（復帰後すぐに送るため）
IDLE_INTERVAL = 30
TIMEOUT = 10
WARN_INTERVAL = 1800

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[object]]
Payload = dict[str, Any]
Poster = Callable[[Payload], Awaitable[None]]


class StatusPushError(Exception):
    """送信の失敗。メッセージに URL・トークンを含まない。"""


def build_payload(
    version: str,
    summary: Any | None,
    voice_connections: int,
    now: Clock = time.time,
) -> Payload:
    """送信する JSON。``summary`` は :class:`yomi_kai.session.LatencySummary`（秒）または None。"""
    if summary is None or summary.count == 0:
        median_ms = p95_ms = None
        samples = 0
    else:
        median_ms = round(summary.median * 1000)
        p95_ms = round(summary.p95 * 1000)
        samples = summary.count
    return {
        "version": version,
        "ts": int(now()),
        "latency_median_ms": median_ms,
        "latency_p95_ms": p95_ms,
        "latency_samples": samples,
        "voice_connections": voice_connections,
    }


def http_poster(url: str, token: str, *, timeout: float = TIMEOUT) -> Poster:
    """``url`` に JSON を POST する関数を返す。失敗時は :class:`StatusPushError`（URL・トークンは含めない）。"""

    async def post(payload: Payload) -> None:
        import aiohttp  # discord.py の依存。使うときに初めて読み込む

        headers = {"Authorization": f"Bearer {token}"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.post(url, json=payload, headers=headers) as response:
                    if response.status >= 400:
                        raise StatusPushError(f"HTTP {response.status}")
        except StatusPushError:
            raise
        except TimeoutError:
            raise StatusPushError("タイムアウト") from None
        except Exception as e:
            # 例外のメッセージに URL が含まれることがあるので、種類だけを残す
            raise StatusPushError(type(e).__name__) from None

    return post


class StatusPusher:
    """``is_alive()`` が真の間、``interval`` 秒ごとに ``payload()`` を ``post`` で送る。

    ``post`` や ``payload`` が例外を送出しても、ループは止まらず呼び出し側にも伝播しない。
    """

    def __init__(
        self,
        post: Poster,
        payload: Callable[[], Payload],
        is_alive: Callable[[], bool],
        *,
        interval: float = DEFAULT_INTERVAL,
        idle_interval: float = IDLE_INTERVAL,
        warn_interval: float = WARN_INTERVAL,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._post = post
        self._payload = payload
        self._is_alive = is_alive
        self.interval = interval
        self.idle_interval = idle_interval
        self.warn_interval = warn_interval
        self._clock = clock
        self._sleep = sleep
        self._last_warning: float | None = None
        self._failing = False

    async def push_once(self) -> bool:
        """1回送る。成功したら True（例外は送出しない）。"""
        try:
            await self._post(self._payload())
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._failed(e)
            return False
        if self._failing:
            logger.info("稼働状況の送信が復旧しました")
            self._failing = False
        return True

    async def run(self) -> None:
        """キャンセルされるまで送り続ける。未接続の間は送らず、``idle_interval`` 秒ごとに確かめ直す。"""
        while True:
            try:
                alive = self._is_alive()
            except Exception:
                alive = False
            if alive:
                await self.push_once()
                await self._sleep(self.interval)
            else:
                await self._sleep(self.idle_interval)

    def _failed(self, error: BaseException) -> None:
        self._failing = True
        description = str(error) if isinstance(error, StatusPushError) else type(error).__name__
        now = self._clock()
        if self._last_warning is None or now - self._last_warning >= self.warn_interval:
            self._last_warning = now
            logger.warning(
                "稼働状況の送信に失敗しました（%s）。このあと%d分間は同じ警告を出しません",
                description,
                self.warn_interval // 60,
            )
        else:
            logger.debug("稼働状況の送信に失敗しました（%s）", description)
