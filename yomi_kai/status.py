"""y.status で表示する Bot の状態の整形（discord を import しない純粋関数）。"""

from __future__ import annotations

import math
import os
from datetime import datetime

UNKNOWN = "不明"
VERSION_ENV = "YOMI_KAI_VERSION"


def bot_version(environ: dict[str, str] | None = None) -> str:
    """Docker のビルド時に埋め込んだバージョン（git の短いハッシュ）。分からなければ「不明」。"""
    value = (os.environ if environ is None else environ).get(VERSION_ENV, "").strip()
    return value or UNKNOWN


def format_duration(seconds: float) -> str:
    """稼働時間の表示（例: 3日 4時間 5分、1分未満は「1分未満」）。"""
    minutes = int(max(0.0, seconds) // 60)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    parts = [f"{days}日"] if days else []
    if days or hours:
        parts.append(f"{hours}時間")
    parts.append(f"{minutes}分")
    return "1分未満" if minutes == 0 and not days and not hours else " ".join(parts)


def format_latency(latency: float) -> str:
    return f"{latency * 1000:.0f}ms" if math.isfinite(latency) else UNKNOWN


def format_synthesis(workers: int, healthy: bool, last_error: float | None, errors: int) -> str:
    state = "正常" if healthy else "直近の合成に失敗しています"
    text = f"VOICEVOX の合成ワーカー {workers}個・{state}"
    if last_error is not None:
        text += f"（最後の失敗: {datetime.fromtimestamp(last_error):%m/%d %H:%M}、起動後 {errors}回）"
    return text


def status_fields(
    *,
    latency: float,
    sessions: int,
    queue: int | None,
    workers: int,
    healthy: bool,
    last_error: float | None,
    errors: int,
    uptime: float | None,
    version: str,
) -> list[tuple[str, str]]:
    """(項目名, 値) の一覧。``queue`` が None ならサーバー外（DM）。"""
    return [
        ("Discord の応答時間", format_latency(latency)),
        ("接続中のボイスチャンネル", f"{sessions}件（この Bot 全体）"),
        ("このサーバーの読み上げ待ち", "-" if queue is None else f"{queue}件"),
        ("音声合成", format_synthesis(workers, healthy, last_error, errors)),
        ("稼働時間", UNKNOWN if uptime is None else format_duration(uptime)),
        ("バージョン", version),
    ]
