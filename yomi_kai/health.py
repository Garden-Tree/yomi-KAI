"""接続監視: ハートビートファイルと、Discord からの長時間切断の通知（Webhook）。

このモジュールは discord / voicevox_core を import しない（``python -m yomi_kai.health`` を
Docker の HEALTHCHECK から高速・副作用なしで実行するため）。Webhook の送信に使う aiohttp も
送信時に初めて import する。Bot との結線は ``yomi_kai.main.YomiBot`` が行う。

* ハートビート: Gateway に接続している間、``HEARTBEAT_PATH`` に UNIX 時刻を30秒ごとに書き込む。
  ``python -m yomi_kai.health`` は、それが ``HEARTBEAT_MAX_AGE`` 秒以内なら 0、そうでなければ 1 で終了する。
* 切断通知: :class:`DisconnectAlerter` が切断・復帰を受け取り、``alert_after`` 秒以上切断が続いたら通知、
  復帰したら停止期間を通知する。ネットワーク自体が落ちている場合に備え、送れるまでバックオフしながら再送する。
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import sys
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

HEARTBEAT_PATH = Path("log/heartbeat")
HEARTBEAT_INTERVAL = 30
HEARTBEAT_MAX_AGE = 180

DEFAULT_ALERT_AFTER = 300
MIN_ALERT_AFTER = 30
RETRY_INITIAL = 10
RETRY_MAX = 300
WEBHOOK_TIMEOUT = 10
TIME_FORMAT = "%m/%d %H:%M"

Clock = Callable[[], float]
Sleep = Callable[[float], Awaitable[object]]
Sender = Callable[[str], Awaitable[None]]


# --- ハートビート ---


def write_heartbeat(path: Path = HEARTBEAT_PATH, now: Clock = time.time) -> None:
    """現在の UNIX 時刻を書き込む（一時ファイル経由で差し替えるので、読み手が途中の内容を見ることはない）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(f"{now():.0f}\n", encoding="ascii")
    os.replace(tmp, path)


def heartbeat_age(path: Path = HEARTBEAT_PATH, now: Clock = time.time) -> float | None:
    """最後のハートビートからの経過秒数。ファイルが無ければ None。内容が読めなければ更新時刻で判断する。"""
    try:
        raw = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        raw = None
    try:
        stamp = float(raw) if raw else math.nan
    except ValueError:
        stamp = math.nan
    if not math.isfinite(stamp):
        try:
            stamp = path.stat().st_mtime
        except OSError:
            return None
    return now() - stamp


def is_healthy(path: Path = HEARTBEAT_PATH, max_age: float = HEARTBEAT_MAX_AGE, now: Clock = time.time) -> bool:
    age = heartbeat_age(path, now)
    return age is not None and age <= max_age


async def heartbeat_loop(
    is_alive: Callable[[], bool],
    path: Path = HEARTBEAT_PATH,
    interval: float = HEARTBEAT_INTERVAL,
    *,
    now: Clock = time.time,
    sleep: Sleep = asyncio.sleep,
) -> None:
    """``is_alive()`` が真の間だけ、``interval`` 秒ごとにハートビートを書き込む（キャンセルされるまで続く）。"""
    while True:
        try:
            if is_alive():
                write_heartbeat(path, now)
        except Exception:
            logger.warning("ハートビートの書き込みに失敗しました: %s", path, exc_info=True)
        await sleep(interval)


# --- 切断通知 ---


def format_time(timestamp: float) -> str:
    """ローカル時刻（コンテナでは TZ=Asia/Tokyo）で ``%m/%d %H:%M`` 表記にする。"""
    return datetime.fromtimestamp(timestamp).strftime(TIME_FORMAT)


def outage_message(start: float, now: float) -> str:
    minutes = int((now - start) // 60)
    return f"yomi-KAI が {minutes}分以上 Discord から切断されています（{format_time(start)}〜）"


def recovery_message(start: float, end: float) -> str:
    minutes = max(1, round((end - start) / 60))
    return f"yomi-KAI が復旧しました（停止: {format_time(start)}〜{format_time(end)}、{minutes}分）"


def mask_url(url: str) -> str:
    """Webhook の URL（秘密情報）をログ用に伏せる。ホスト名までは残す。"""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return "***"
    return f"{scheme}://{rest.split('/', 1)[0]}/***"


class DisconnectAlerter:
    """Gateway の切断・復帰を受け取り、長時間の切断と復旧を ``send`` で通知する。

    Discord から独立しており、``now`` / ``sleep`` / ``send`` を差し替えてテストできる。
    ``send`` が例外を送出した場合はバックオフしながら再送する（最大 ``retry_max`` 秒間隔）。
    例外が呼び出し側（Bot のイベントハンドラ）へ伝播することはない。
    """

    def __init__(
        self,
        send: Sender,
        *,
        alert_after: float = DEFAULT_ALERT_AFTER,
        now: Clock = time.time,
        sleep: Sleep = asyncio.sleep,
        retry_initial: float = RETRY_INITIAL,
        retry_max: float = RETRY_MAX,
        describe_error: Callable[[BaseException], str] = lambda e: f"{type(e).__name__}: {e}",
    ) -> None:
        self._send = send
        self.alert_after = alert_after
        self._now = now
        self._sleep = sleep
        self._retry_initial = retry_initial
        self._retry_max = retry_max
        self._describe_error = describe_error
        self.down_since: float | None = None
        # 今回の切断について通知を試みたか（送れたかどうかに関わらず、復旧時に停止期間を知らせる）
        self._alert_attempted = False
        self._outage_task: asyncio.Task[None] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def is_down(self) -> bool:
        return self.down_since is not None

    def disconnected(self) -> None:
        """切断を記録する。切断中に何度呼ばれても、最初の時刻を保持する。"""
        if self.down_since is not None:
            return
        self.down_since = self._now()
        self._alert_attempted = False
        self._outage_task = self._spawn(self._alert_outage(self.down_since))

    def connected(self) -> None:
        """接続（on_ready / on_resumed）を記録する。通知済みの切断があれば復旧を通知する。"""
        start = self.down_since
        if start is None:
            return
        self.down_since = None
        outage_task, self._outage_task = self._outage_task, None
        if outage_task is not None:
            outage_task.cancel()
        if self._alert_attempted:
            end = self._now()
            self._spawn(self._deliver(recovery_message(start, end), "復旧通知"))
            logger.info("Discord との接続が復旧しました（%s〜%s）", format_time(start), format_time(end))
        self._alert_attempted = False

    async def close(self) -> None:
        """送信中・待機中のタスクをすべて止める。"""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait(tasks)
        self._outage_task = None

    def _spawn(self, coro: Awaitable[None]) -> asyncio.Task[None]:
        task = asyncio.create_task(coro)  # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _alert_outage(self, start: float) -> None:
        delay = start + self.alert_after - self._now()
        if delay > 0:
            await self._sleep(delay)
        if self.down_since != start:
            return
        self._alert_attempted = True
        logger.warning("Discord から %d 秒以上切断されています", self.alert_after)
        # 再送のたびに経過時間を計算し直す
        await self._deliver(lambda: outage_message(start, self._now()), "切断通知")

    async def _deliver(self, content: str | Callable[[], str], label: str) -> None:
        delay = self._retry_initial
        while True:
            message = content() if callable(content) else content
            try:
                await self._send(message)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(
                    "%sの送信に失敗しました。%d秒後に再送します（%s）", label, delay, self._describe_error(e)
                )
                await self._sleep(delay)
                delay = min(delay * 2, self._retry_max)
                continue
            logger.info("%sを送信しました", label)
            return


def webhook_sender(url: str, *, timeout: float = WEBHOOK_TIMEOUT) -> Sender:
    """Discord の Webhook にメッセージを送る関数を返す。失敗時は例外（URL はメッセージに含めない）。"""

    async def send(content: str) -> None:
        import aiohttp  # discord.py の依存。--health 実行時に読み込まないよう遅延 import

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                payload = {"content": content, "allowed_mentions": {"parse": []}}
                async with session.post(url, json=payload) as response:
                    if response.status >= 400:
                        raise WebhookError(f"HTTP {response.status}")
        except WebhookError:
            raise
        except TimeoutError:
            raise WebhookError("タイムアウト") from None
        except Exception as e:
            # 例外のメッセージに URL が含まれることがあるので伏せる
            raise WebhookError(f"{type(e).__name__}: {str(e).replace(url, mask_url(url))}") from None

    return send


class WebhookError(Exception):
    """Webhook の送信失敗。メッセージに URL を含まない。"""


def main() -> int:
    """Docker の HEALTHCHECK 用。ハートビートが新しければ 0、古い・無ければ 1。"""
    return 0 if is_healthy() else 1


if __name__ == "__main__":
    sys.exit(main())
