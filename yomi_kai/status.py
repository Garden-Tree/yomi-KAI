"""y.status で表示する Bot の状態の整形（discord を import しない純粋関数）。"""

from __future__ import annotations

import math
import os
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .session import LatencySummary

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


def format_speech_latency(summary: LatencySummary | None) -> str:
    """受信から読み上げの開始までの時間（中央値・95パーセンタイル・件数）。記録が無ければ「記録なし」。"""
    if summary is None:
        return "記録なし"
    return (
        f"中央値 {summary.median * 1000:.0f}ms・95%が {summary.p95 * 1000:.0f}ms 以内"
        f"（直近 {summary.count}件、サーバーごとに最大100件）"
    )


def format_synthesis(
    workers: int, healthy: bool, last_error: float | None, errors: int, *, google: bool = False
) -> str:
    """``google=True`` なら既定の話者が Google TTS（VOICEVOX は追加の話者として使える）。"""
    state = "正常" if healthy else "直近の合成に失敗しています"
    engine = f"Google TTS（VOICEVOX の合成ワーカー {workers}個）" if google else f"VOICEVOX の合成ワーカー {workers}個"
    text = f"{engine}・{state}"
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
    google: bool = False,
    speech_latency: LatencySummary | None = None,
) -> list[tuple[str, str]]:
    """(項目名, 値) の一覧。``queue`` が None ならサーバー外（DM）。``google`` は Google TTS を使っているか。

    ``speech_latency`` はメッセージの受信から読み上げの開始までの時間の集計（全サーバー）。
    """
    return [
        ("Discord の応答時間", format_latency(latency)),
        ("接続中のボイスチャンネル", f"{sessions}件（この Bot 全体）"),
        ("このサーバーの読み上げ待ち", "-" if queue is None else f"{queue}件"),
        ("音声合成", format_synthesis(workers, healthy, last_error, errors, google=google)),
        ("読み上げの開始までの時間", format_speech_latency(speech_latency)),
        ("稼働時間", UNKNOWN if uptime is None else format_duration(uptime)),
        ("バージョン", version),
    ]
