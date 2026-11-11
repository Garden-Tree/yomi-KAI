"""サーバーごとのプラン（フリー / プレミアム）と上限。

上限はすべて :meth:`PlanManager.limits_for` で決める。フリーの値は config.ini の従来のキー
（MAX_READ_LENGTH / MAX_DICT_ENTRIES / MAX_USER_DICT_ENTRIES / SPEAKER_ALLOWLIST）、
プレミアムの値は PREMIUM_* のキー（空・0 で無制限、話者は空なら読み込んだ全話者）から取る。

プレミアムのサーバーは ``settings/premium.json``（PREMIUM_GUILDS_FILE）に運営者が書く::

    {
      "123456789012345678": null,
      "234567890123456789": "2026-12-31",
      "345678901234567890": {"expires": "2027-01-31", "note": "メモは自由に書ける"}
    }

値が null（または空文字列）なら期限なし、日付（YYYY-MM-DD、その日まで有効）なら期限付き。
PREMIUM_ENABLED = False（既定）のときはファイルを読まず、すべてのサーバーがフリーの上限を使う
（セルフホストの既定の設定では、従来どおりすべて無制限）。

ファイルは更新時刻が変わったときだけ読み直す（Bot は30秒ごとに ``asyncio.to_thread`` で確認する）。
壊れたファイルは読み込まず、前回の内容のまま使う（書き間違いでプレミアムが外れないように）。
``limits_for`` はメモリ上の値を引くだけで I/O をしない。決済の処理は持たない。discord を import しない。
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config
from .tts import TTSEngine

logger = logging.getLogger(__name__)

DEFAULT_PATH = "settings/premium.json"
CHECK_INTERVAL = 30.0
FREE = "free"
PREMIUM = "premium"


@dataclass(frozen=True)
class Limits:
    """サーバーの上限。数値の 0 は無制限。"""

    plan: str
    # 1メッセージで読み上げる最大文字数
    max_read_length: int
    # サーバー辞書の登録数
    max_dict_entries: int
    # 個人辞書（1人あたり）の登録数
    max_user_dict_entries: int
    # プレミアムの期限（その日まで有効。None なら期限なし。フリーでは None）
    expires: dt.date | None = None

    @property
    def is_premium(self) -> bool:
        return self.plan == PREMIUM


def parse_premium(data: Mapping[str, Any], source: str = "premium") -> dict[int, dt.date | None]:
    """premium.json の中身をサーバー ID → 期限に変換する。読めない項目は警告して飛ばす。"""
    guilds: dict[int, dt.date | None] = {}
    for key, value in data.items():
        if not (isinstance(key, str) and key.isascii() and key.isdigit()):
            logger.warning("%s の %r はサーバー ID ではないため無視します", source, key)
            continue
        if isinstance(value, dict):
            value = value.get("expires")
        if value is None or value == "":
            guilds[int(key)] = None
            continue
        try:
            if not isinstance(value, str):
                raise ValueError(value)
            guilds[int(key)] = dt.date.fromisoformat(value)
        except ValueError:
            logger.warning("%s の %s の期限 %r は日付（YYYY-MM-DD）ではないため無視します", source, key, value)
    return guilds


class PremiumGuilds:
    """プレミアムのサーバーの一覧（ファイルの更新時刻が変わったら読み直す）。"""

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self._guilds: dict[int, dt.date | None] = {}
        self._mtime: float | None = None
        self._failed_mtime: float | None = None
        self._lock = threading.Lock()

    def expiry(self, guild_id: int) -> tuple[bool, dt.date | None]:
        """(登録されているか, 期限)。"""
        if guild_id not in self._guilds:
            return False, None
        return True, self._guilds[guild_id]

    def reload_if_changed(self) -> bool:
        """更新時刻が変わっていれば読み直す（ブロッキング）。読み直したら True。"""
        with self._lock:
            try:
                mtime: float | None = self.path.stat().st_mtime
            except FileNotFoundError:
                mtime = None
            except OSError:
                logger.warning("プレミアムの一覧 %s を確認できません", self.path, exc_info=True)
                return False
            if mtime == self._mtime:
                return False
            if mtime is None:
                guilds: dict[int, dt.date | None] = {}
            else:
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8-sig"))
                    if not isinstance(data, dict):
                        raise ValueError("オブジェクトではありません")
                except (OSError, ValueError):
                    # 前回の内容のまま使う。同じ壊れたファイルで警告を出し続けない
                    if self._failed_mtime != mtime:
                        logger.warning(
                            "プレミアムの一覧 %s を読み込めません（前回の内容を使います）", self.path, exc_info=True
                        )
                        self._failed_mtime = mtime
                    return False
                guilds = parse_premium(data, str(self.path))
            if guilds != self._guilds:
                logger.info("プレミアムの一覧を読み込みました（%d件）: %s", len(guilds), self.path)
            self._guilds = guilds
            self._mtime = mtime
            self._failed_mtime = None
            return True


class PlanManager:
    """サーバーごとの上限と、そのサーバーで使える話者（エンジン）を決める。

    ``engine`` はフリーで使える話者に絞ったエンジン（SPEAKER_ALLOWLIST）、``premium_engine`` はプレミアムで
    使える話者に絞ったエンジン、``base_engine`` は読み込んだ全話者のエンジン（合成に使う）。
    省略するとすべて ``engine`` になる。
    """

    def __init__(
        self,
        config: Config,
        engine: TTSEngine,
        *,
        premium_engine: TTSEngine | None = None,
        base_engine: TTSEngine | None = None,
        premium_guilds: PremiumGuilds | None = None,
        today: Callable[[], dt.date] = dt.date.today,
    ) -> None:
        self.config = config
        self.free_engine = engine
        self.premium_engine = premium_engine if premium_engine is not None else engine
        self.base_engine = base_engine if base_engine is not None else engine
        if premium_guilds is None and config.premium_enabled:
            premium_guilds = PremiumGuilds(config.premium_guilds_file)
        self.premium_guilds = premium_guilds if config.premium_enabled else None
        self._today = today
        # id(エンジン) -> 使える話者の名前（エンジンは起動後に変わらない）
        self._names: dict[int, frozenset[str]] = {}
        self.free = Limits(
            FREE,
            max_read_length=config.max_read_length,
            max_dict_entries=config.max_dict_entries,
            max_user_dict_entries=config.max_user_dict_entries,
        )
        self.premium = Limits(
            PREMIUM,
            max_read_length=config.premium_max_read_length,
            max_dict_entries=config.premium_max_dict_entries,
            max_user_dict_entries=config.premium_max_user_dict_entries,
        )

    @property
    def enabled(self) -> bool:
        """プレミアムプランを使うか（PREMIUM_ENABLED）。"""
        return self.premium_guilds is not None

    def is_premium(self, guild_id: int | None) -> bool:
        return self.limits_for(guild_id).is_premium

    def limits_for(self, guild_id: int | None) -> Limits:
        """サーバーの上限。プレミアムでない・期限切れ・PREMIUM_ENABLED = False ならフリーの上限。"""
        if self.premium_guilds is None or guild_id is None:
            return self.free
        listed, expires = self.premium_guilds.expiry(guild_id)
        if not listed or (expires is not None and expires < self._today()):
            return self.free
        if expires is None:
            return self.premium
        return Limits(
            PREMIUM,
            max_read_length=self.premium.max_read_length,
            max_dict_entries=self.premium.max_dict_entries,
            max_user_dict_entries=self.premium.max_user_dict_entries,
            expires=expires,
        )

    def engine_for(self, guild_id: int | None) -> TTSEngine:
        """サーバーで使える話者に絞ったエンジン（話者の一覧・既定話者に使う）。"""
        return self.premium_engine if self.is_premium(guild_id) else self.free_engine

    def speakers_for(self, guild_id: int | None) -> tuple[TTSEngine, frozenset[str]]:
        """(サーバーで使える話者に絞ったエンジン, 使える話者の名前)。名前の集合はエンジンごとに一度だけ作る。"""
        engine = self.engine_for(guild_id)
        names = self._names.get(id(engine))
        if names is None:
            names = self._names[id(engine)] = frozenset(s.name for s in engine.speakers())
        return engine, names

    def reload(self) -> bool:
        """プレミアムの一覧を読み直す（ブロッキング。PREMIUM_ENABLED = False なら何もしない）。"""
        if self.premium_guilds is None:
            return False
        return self.premium_guilds.reload_if_changed()


def format_limit(value: int, unit: str) -> str:
    """0 → 「無制限」、100 → 「100文字」など。"""
    return "無制限" if value <= 0 else f"{value}{unit}"


def format_usage(count: int, limit: int) -> str:
    """登録数と上限（例: 「12 / 200件」「12件（無制限）」）。"""
    return f"{count}件（無制限）" if limit <= 0 else f"{count} / {limit}件"
