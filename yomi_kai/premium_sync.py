"""Stripe の契約の同期（サイトの ``GET /api/premium-guilds`` → ``settings/premium.json``）。

Bot はインターネットから届かない場所で動くため、Stripe からの通知（Webhook）はサイト（Cloudflare Pages Functions）が
受け取って D1 に記録し、Bot はそれを読みに行くだけにする。``PREMIUM_ENABLED`` が True で、``PREMIUM_SYNC_URL`` と
``PREMIUM_SYNC_TOKEN`` を設定したときだけ、``DEFAULT_INTERVAL`` 秒（5分）ごとに次の JSON を GET する
（``Authorization: Bearer <token>``）::

    {"guilds": [{"guild_id": "123456789012345678", "expires": "2026-12-31", "status": "active"}]}

``expires`` はその日まで有効（日本時間。サイト側で契約の期間の終わり、支払いが滞っている間は +3日 にしてある）。

受け取った一覧は、既存の ``premium.json`` のうち ``"source": "stripe"`` の項目だけを置き換えて書き込む
（一時ファイルに書いてから差し替える）。運営者が手で書いた項目（``source`` が無い・違う）はそのまま残し、
同じサーバーが両方にあれば手で書いた項目を優先する。書き込んだ形::

    {"123456789012345678": {"expires": "2026-12-31", "source": "stripe", "status": "active"}}

取得に失敗した・応答の形が正しくない・既存のファイルが壊れている場合は、ファイルを書き換えない
（同期が止まっても、各項目の期限が過ぎればフリーに戻る）。内容が変わらなければ書き込まない。

通信は aiohttp（discord.py の依存）で行う。警告は ``WARN_INTERVAL`` 秒に1回まで、それ以外は DEBUG。
例外のメッセージに URL・トークンは含めない。このモジュールは discord を import しない。
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

from .jsonfile import write_json_atomic

logger = logging.getLogger(__name__)

SOURCE = "stripe"
DEFAULT_INTERVAL = 300
TIMEOUT = 15
WARN_INTERVAL = 1800
# 応答の大きさの上限（異常な応答でメモリを使い切らないように）
MAX_RESPONSE_BYTES = 2_000_000

Entry = dict[str, Any]
Fetcher = Callable[[], Awaitable[Any]]
Sleep = Callable[[float], Awaitable[object]]


class PremiumSyncError(Exception):
    """同期の失敗。メッセージに URL・トークンを含まない。"""


def is_synced_entry(value: object) -> bool:
    """同期で書いた項目（``"source": "stripe"``）か。"""
    return isinstance(value, dict) and value.get("source") == SOURCE


def parse_sync_response(data: object) -> dict[str, Entry]:
    """サイトの応答を premium.json の項目（サーバー ID → 項目）に変える。形が正しくなければ PremiumSyncError。

    1件でもおかしな項目があれば全体を使わない（サイト側の不具合で一部のサーバーだけ外れることがないように）。
    """
    if not isinstance(data, dict) or not isinstance(data.get("guilds"), list):
        raise PremiumSyncError("応答の形が正しくありません")
    entries: dict[str, Entry] = {}
    for item in data["guilds"]:
        if not isinstance(item, dict):
            raise PremiumSyncError("応答の形が正しくありません")
        guild_id = item.get("guild_id")
        expires = item.get("expires")
        status = item.get("status")
        if not (isinstance(guild_id, str) and guild_id.isascii() and guild_id.isdigit() and 0 < len(guild_id) <= 20):
            raise PremiumSyncError("応答にサーバー ID ではない値があります")
        if not isinstance(expires, str):
            raise PremiumSyncError("応答に期限の無い項目があります")
        try:
            dt.date.fromisoformat(expires)
        except ValueError:
            raise PremiumSyncError("応答に日付ではない期限があります") from None
        entry: Entry = {"expires": expires, "source": SOURCE}
        if isinstance(status, str) and status:
            entry["status"] = status
        previous = entries.get(guild_id)
        # 同じサーバーが2回あれば、期限の遅いほう
        if previous is None or previous["expires"] < expires:
            entries[guild_id] = entry
    return entries


def merge_premium(existing: Mapping[str, Any], synced: Mapping[str, Entry]) -> dict[str, Any]:
    """既存の内容のうち同期で書いた項目だけを ``synced`` に置き換える。手で書いた項目は残し、優先する。"""
    merged = {key: value for key, value in existing.items() if not is_synced_entry(value)}
    for key, entry in synced.items():
        if key in merged:
            logger.debug("サーバー %s は premium.json に手で書かれているため、Stripe の契約では上書きしません", key)
            continue
        merged[key] = dict(entry)
    return merged


def read_premium_file(path: Path) -> dict[str, Any]:
    """premium.json を読む。無ければ空。壊れていれば ValueError / OSError（そのときは書き換えない）。"""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path} の形式が不正です（オブジェクトではありません）")
    return data


def apply_sync(path: Path, synced: Mapping[str, Entry]) -> bool:
    """premium.json に同期の結果を書く（ブロッキング）。書き換えたら True、変わらなければ False。"""
    existing = read_premium_file(path)
    merged = merge_premium(existing, synced)
    if merged == existing:
        return False
    write_json_atomic(path, merged)
    return True


def http_fetcher(url: str, token: str, *, timeout: float = TIMEOUT) -> Fetcher:
    """``url`` を GET して JSON を返す関数を返す。失敗時は :class:`PremiumSyncError`（URL・トークンは含めない）。"""

    async def fetch() -> Any:
        import aiohttp  # discord.py の依存。使うときに初めて読み込む

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
                async with session.get(url, headers=headers, allow_redirects=False) as response:
                    if response.status != 200:
                        raise PremiumSyncError(f"HTTP {response.status}")
                    if (response.content_length or 0) > MAX_RESPONSE_BYTES:
                        raise PremiumSyncError("応答が大きすぎます")
                    body = await response.read()
            if len(body) > MAX_RESPONSE_BYTES:
                raise PremiumSyncError("応答が大きすぎます")
            return json.loads(body.decode("utf-8"))
        except PremiumSyncError:
            raise
        except TimeoutError:
            raise PremiumSyncError("タイムアウト") from None
        except ValueError:
            raise PremiumSyncError("応答が JSON ではありません") from None
        except Exception as e:
            # 例外のメッセージに URL が含まれることがあるので、種類だけを残す
            raise PremiumSyncError(type(e).__name__) from None

    return fetch


class PremiumSyncer:
    """``interval`` 秒ごとにサイトから契約中のサーバーを取得し、``path``（premium.json）に書く。

    書き換えたら ``on_change``（PlanManager.reload。ブロッキング）を呼ぶ。``sync_once`` も ``run`` も
    例外を呼び出し側に伝えない（キャンセルを除く）。
    """

    def __init__(
        self,
        fetch: Fetcher,
        path: str | Path,
        *,
        on_change: Callable[[], object] | None = None,
        interval: float = DEFAULT_INTERVAL,
        warn_interval: float = WARN_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._fetch = fetch
        self.path = Path(path)
        self._on_change = on_change
        self.interval = interval
        self.warn_interval = warn_interval
        self._clock = clock
        self._sleep = sleep
        self._last_warning: float | None = None
        self._failing = False

    async def sync_once(self) -> bool:
        """1回同期する。成功したら True（書き換えが無くても True）。"""
        try:
            synced = parse_sync_response(await self._fetch())
            changed = await asyncio.to_thread(apply_sync, self.path, synced)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._failed(e)
            return False
        if changed:
            logger.info("Stripe の契約を %s に反映しました（契約中 %d件）", self.path, len(synced))
            if self._on_change is not None:
                try:
                    await asyncio.to_thread(self._on_change)
                except Exception:
                    logger.exception("プレミアムの一覧の読み直しに失敗しました")
        if self._failing:
            logger.info("Stripe の契約の同期が復旧しました")
            self._failing = False
        return True

    async def run(self) -> None:
        """キャンセルされるまで同期し続ける（最初の1回はすぐ）。"""
        while True:
            await self.sync_once()
            await self._sleep(self.interval)

    def _failed(self, error: BaseException) -> None:
        self._failing = True
        if isinstance(error, PremiumSyncError):
            description = str(error)
        elif isinstance(error, (OSError, ValueError)):
            description = f"{self.path} を読み書きできません（{type(error).__name__}）"
        else:
            description = type(error).__name__
        now = self._clock()
        if self._last_warning is None or now - self._last_warning >= self.warn_interval:
            self._last_warning = now
            logger.warning(
                "Stripe の契約の同期に失敗しました（%s）。premium.json はそのまま使います。"
                "このあと%d分間は同じ警告を出しません",
                description,
                self.warn_interval // 60,
            )
        else:
            logger.debug("Stripe の契約の同期に失敗しました（%s）", description)
