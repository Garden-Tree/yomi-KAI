"""利用を断るサーバー（ギルド）のブラックリスト。

ファイル（既定 ``settings/blocked_guilds.txt``、config.ini の BLOCKED_GUILDS_FILE）には1行に1つサーバー ID を書く。
``#`` 以降はコメント、空行は無視する。ファイルが無ければ何もブロックしない。

ファイルの更新時刻が変わったら読み直す（Bot は30秒ごとに ``asyncio.to_thread`` で確認する）。
``is_blocked`` はメモリ上の集合を引くだけで I/O をしない。discord を import しない。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = "settings/blocked_guilds.txt"
CHECK_INTERVAL = 30.0


def parse_blocklist(text: str, source: str = "blocklist") -> frozenset[int]:
    """ファイルの中身からサーバー ID の集合を作る。ID として読めない行は警告して飛ばす。"""
    ids: set[int] = set()
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if not line.isascii() or not line.isdigit():
            logger.warning("%s の %d 行目はサーバー ID ではないため無視します: %r", source, lineno, line)
            continue
        ids.add(int(line))
    return frozenset(ids)


def guilds_to_leave(guild_ids: Iterable[int], blocked: frozenset[int] | set[int]) -> list[int]:
    """参加中のサーバーのうち、退出すべきもの（元の順序）。"""
    return [guild_id for guild_id in guild_ids if guild_id in blocked]


class GuildBlocklist:
    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self._blocked: frozenset[int] = frozenset()
        self._mtime: float | None = None  # 最後に読んだときの更新時刻（ファイルが無ければ None）
        self._lock = threading.Lock()

    @property
    def blocked(self) -> frozenset[int]:
        return self._blocked

    def is_blocked(self, guild_id: int | None) -> bool:
        return guild_id is not None and guild_id in self._blocked

    def reload_if_changed(self) -> frozenset[int]:
        """更新時刻が変わっていれば読み直す（ブロッキング）。新たにブロックされたサーバー ID を返す。"""
        with self._lock:
            try:
                mtime: float | None = self.path.stat().st_mtime
            except FileNotFoundError:
                mtime = None
            except OSError:
                logger.warning("ブロックリスト %s を確認できません", self.path, exc_info=True)
                return frozenset()
            if mtime == self._mtime:
                return frozenset()
            if mtime is None:
                blocked: frozenset[int] = frozenset()
            else:
                try:
                    text = self.path.read_text(encoding="utf-8-sig")
                except OSError:
                    # 読めなかった場合は前回の内容のまま。次回また試す
                    logger.warning("ブロックリスト %s を読み込めません", self.path, exc_info=True)
                    return frozenset()
                blocked = parse_blocklist(text, str(self.path))
            added = blocked - self._blocked
            if blocked != self._blocked:
                logger.info("ブロックリストを読み込みました（%d件）: %s", len(blocked), self.path)
            self._blocked = blocked
            self._mtime = mtime
            return added
