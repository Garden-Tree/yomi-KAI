"""使用量の計測（上限なし）。月ごとの読み上げ文字数・メッセージ数を ``usage/{YYYY-MM}.json`` に記録する。

ファイル形式（indent=2, ensure_ascii=False）::

    {
      "guilds": {
        "<guild_id>": {
          "chars": 1234, "messages": 56, "system_chars": 78,
          "users": {"<user_id>": {"chars": 1000, "messages": 50}}
        }
      }
    }

* ``chars`` / ``messages``: ユーザーの発言として読み上げを受け付けた数（切り詰め後の読み上げテキストの文字数）。
* ``system_chars``: 入退室の読み上げなど、Bot が自分で読み上げた文字数（ユーザーの使用量には含めない）。
* 本文は一切保存しない。月はサーバーのローカル時刻（Docker では TZ=Asia/Tokyo）で決める。

``record`` はメモリ上の増分（前回の書き込み以降に増えた分）に足すだけで I/O をしない。
``flush`` がファイルを読んで増分を足し、アトミックに書き込む（イベントループからは ``asyncio.to_thread`` で呼ぶ）。
壊れたファイルは上書きせず ``{name}.corrupt-{時刻}`` に名前を変えてから、その月を新しく数え直す。

``python -m yomi_kai.usage [YYYY-MM] [--dir DIR]`` で集計を表示する（discord / voicevox_core を import しない）。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from .jsonfile import read_json_object, write_json_atomic

logger = logging.getLogger(__name__)

DEFAULT_DIR = "usage"
FLUSH_INTERVAL = 60.0
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
TOP_GUILDS = 10

Clock = Callable[[], datetime]


def month_key(moment: datetime) -> str:
    return f"{moment:%Y-%m}"


def _empty_month() -> dict[str, Any]:
    return {"guilds": {}}


def _count(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{where} は 0 以上の整数で指定してください")
    return value


def validate_month(data: Any) -> dict[str, Any]:
    """読み込んだ月のデータの形を確かめる。不正なら ValueError。"""
    if not isinstance(data, dict):
        raise ValueError("オブジェクトではありません")
    guilds = data.get("guilds", {})
    if not isinstance(guilds, dict):
        raise ValueError("guilds はオブジェクトで指定してください")
    for guild_id, guild in guilds.items():
        if not isinstance(guild, dict):
            raise ValueError(f"guilds[{guild_id!r}] はオブジェクトで指定してください")
        for key in ("chars", "messages", "system_chars"):
            _count(guild.get(key, 0), f"guilds[{guild_id!r}].{key}")
        users = guild.get("users", {})
        if not isinstance(users, dict):
            raise ValueError(f"guilds[{guild_id!r}].users はオブジェクトで指定してください")
        for user_id, user in users.items():
            if not isinstance(user, dict):
                raise ValueError(f"guilds[{guild_id!r}].users[{user_id!r}] はオブジェクトで指定してください")
            for key in ("chars", "messages"):
                _count(user.get(key, 0), f"guilds[{guild_id!r}].users[{user_id!r}].{key}")
    data.setdefault("guilds", guilds)
    return data


def _add(target: dict[str, Any], key: str, amount: int) -> None:
    if amount:
        target[key] = target.get(key, 0) + amount


def merge_month(base: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    """``base`` に ``delta`` の数を足す（``base`` を書き換えて返す）。"""
    guilds = base.setdefault("guilds", {})
    for guild_id, d_guild in delta.get("guilds", {}).items():
        guild = guilds.setdefault(guild_id, {"chars": 0, "messages": 0, "users": {}})
        for key in ("chars", "messages", "system_chars"):
            _add(guild, key, d_guild.get(key, 0))
        users = guild.setdefault("users", {})
        for user_id, d_user in d_guild.get("users", {}).items():
            user = users.setdefault(user_id, {"chars": 0, "messages": 0})
            for key in ("chars", "messages"):
                _add(user, key, d_user.get(key, 0))
    return base


class UsageStore:
    def __init__(self, directory: str | Path = DEFAULT_DIR, *, clock: Clock = datetime.now) -> None:
        self._dir = Path(directory)
        self._clock = clock
        # 月 -> まだファイルに書いていない増分
        self._pending: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()  # _pending を守る
        self._flush_lock = threading.Lock()  # ファイルの読み書きを1つずつにする

    @property
    def directory(self) -> Path:
        return self._dir

    def path_for(self, month: str) -> Path:
        return self._dir / f"{month}.json"

    @property
    def dirty(self) -> bool:
        return bool(self._pending)

    # --- 記録（イベントループから呼ぶ。I/O なし） ---
    def _guild_delta(self, guild_id: int) -> dict[str, Any]:
        month = self._pending.setdefault(month_key(self._clock()), _empty_month())
        return month["guilds"].setdefault(str(guild_id), {"chars": 0, "messages": 0, "users": {}})

    def record(self, guild_id: int, user_id: int, chars: int) -> None:
        """ユーザーの発言1件を読み上げとして受け付けたことを記録する。"""
        with self._lock:
            guild = self._guild_delta(guild_id)
            guild["chars"] += chars
            guild["messages"] += 1
            user = guild["users"].setdefault(str(user_id), {"chars": 0, "messages": 0})
            user["chars"] += chars
            user["messages"] += 1

    def record_system(self, guild_id: int, chars: int) -> None:
        """入退室の読み上げなど、ユーザーの発言ではない読み上げの文字数を記録する。"""
        with self._lock:
            guild = self._guild_delta(guild_id)
            guild["system_chars"] = guild.get("system_chars", 0) + chars

    # --- 書き込み（ブロッキング） ---
    def _load_for_merge(self, month: str) -> dict[str, Any]:
        path = self.path_for(month)
        try:
            return validate_month(read_json_object(path)) if path.is_file() else _empty_month()
        except (OSError, ValueError):
            corrupt = path.with_name(f"{path.name}.corrupt-{self._clock():%Y%m%d-%H%M%S}")
            logger.exception("使用量のファイルが壊れているため %s に退避し、この月を数え直します", corrupt.name)
            path.replace(corrupt)
            return _empty_month()

    def flush(self) -> None:
        """増分をファイルへ書き込む。失敗した分は増分に戻し、次回また書き込む。"""
        with self._flush_lock:
            with self._lock:
                pending, self._pending = self._pending, {}
            for month in sorted(pending):
                delta = pending[month]
                try:
                    merged = merge_month(self._load_for_merge(month), delta)
                    write_json_atomic(self.path_for(month), merged)
                except Exception:
                    logger.exception("使用量の書き込みに失敗しました: %s", self.path_for(month))
                    with self._lock:
                        current = self._pending.setdefault(month, _empty_month())
                        self._pending[month] = merge_month(delta, current)

    async def aflush(self) -> None:
        if self.dirty:
            await asyncio.to_thread(self.flush)

    async def run_flusher(self, interval: float = FLUSH_INTERVAL) -> None:
        """``interval`` 秒ごとに、増分があれば書き込む（バックグラウンドタスク用）。"""
        while True:
            await asyncio.sleep(interval)
            try:
                await self.aflush()
            except Exception:
                logger.exception("使用量の書き込みに失敗しました")


# --- 集計（python -m yomi_kai.usage） ---


def load_month(directory: str | Path, month: str) -> dict[str, Any] | None:
    """月のデータを読む。ファイルが無ければ None。壊れていれば例外（OSError / ValueError）。"""
    path = Path(directory) / f"{month}.json"
    if not path.is_file():
        return None
    return validate_month(read_json_object(path))


def summarize(data: dict[str, Any], top: int = TOP_GUILDS) -> dict[str, Any]:
    guilds = data.get("guilds", {})
    users: set[str] = set()
    for guild in guilds.values():
        users.update(uid for uid, u in guild.get("users", {}).items() if u.get("messages", 0) or u.get("chars", 0))
    ranking = sorted(guilds.items(), key=lambda item: (-item[1].get("chars", 0), item[0]))
    return {
        "guilds": len(guilds),
        "users": len(users),
        "chars": sum(g.get("chars", 0) for g in guilds.values()),
        "messages": sum(g.get("messages", 0) for g in guilds.values()),
        "system_chars": sum(g.get("system_chars", 0) for g in guilds.values()),
        "top": [
            (gid, g.get("chars", 0), g.get("messages", 0), len(g.get("users", {}))) for gid, g in ranking[:top]
        ],
    }


def format_report(month: str, data: dict[str, Any] | None) -> str:
    if data is None:
        return f"{month}: 記録はありません"
    s = summarize(data)
    lines = [
        f"{month} の使用量",
        f"サーバー数: {s['guilds']}",
        f"利用ユーザー数: {s['users']}",
        f"読み上げ文字数: {s['chars']}",
        f"読み上げメッセージ数: {s['messages']}",
        f"入退室などの読み上げ文字数: {s['system_chars']}",
    ]
    if s["top"]:
        lines.append(f"文字数の多いサーバー（上位{TOP_GUILDS}件）:")
        for rank, (gid, chars, messages, users) in enumerate(s["top"], 1):
            lines.append(f"{rank:>2}. {gid}  {chars}文字 / {messages}件 / {users}人")
    return "\n".join(lines)


def main(argv: list[str] | None = None, *, now: Clock = datetime.now) -> int:
    parser = argparse.ArgumentParser(prog="python -m yomi_kai.usage", description="月ごとの使用量を表示します")
    parser.add_argument("month", nargs="?", help="YYYY-MM（省略すると今月）")
    parser.add_argument("--dir", default=DEFAULT_DIR, help=f"使用量のディレクトリ（既定: {DEFAULT_DIR}）")
    args = parser.parse_args(argv)
    month = args.month or month_key(now())
    if not MONTH_PATTERN.match(month):
        print(f"月は YYYY-MM の形式で指定してください: {month}", file=sys.stderr)
        return 2
    try:
        data = load_month(args.dir, month)
    except (OSError, ValueError) as e:
        print(f"{Path(args.dir) / (month + '.json')} を読み込めません: {e}", file=sys.stderr)
        return 1
    print(format_report(month, data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
