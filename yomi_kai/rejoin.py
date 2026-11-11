"""再起動後の自動再接続のための、接続中のボイスチャンネルの記録。

``settings/voice_sessions.json`` に ``{"<guild_id>": {"voice": "<voice_channel_id>", "text": "<text_channel_id>"}}``
の形式で保存する。接続（移動を含む）したときに書き、``dc``・AFK・無人・キックなど通常の切断で消す。
Bot の終了（docker stop・デプロイ）では消さないので、次の起動の ``on_ready`` で一度だけ、記録したボイスチャンネルに
Bot 以外のメンバーがいて、接続・発言の権限があれば接続し直す（判定は :func:`rejoin_skip_reason`）。

同期メソッドはファイル I/O を伴うため、イベントループからは ``asyncio.to_thread`` 経由で呼ぶこと。
書き込みの失敗は例外として呼び出し側に伝える（VoiceCog がログに残して読み上げは続ける）。discord を import しない。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from .jsonfile import read_json_object, write_json_atomic

logger = logging.getLogger(__name__)

DEFAULT_PATH = "settings/voice_sessions.json"


def parse_sessions(data: dict, source: str = "voice_sessions") -> dict[int, tuple[int, int]]:
    """ファイルの中身をサーバー ID → (ボイスチャンネル ID, テキストチャンネル ID) にする。読めない項目は飛ばす。"""
    sessions: dict[int, tuple[int, int]] = {}
    for key, entry in data.items():
        try:
            guild_id = int(key)
            voice_id = int(entry["voice"])
            text_id = int(entry["text"])
        except (TypeError, ValueError, KeyError):
            logger.warning("%s の %r は読み込めないため無視します", source, key)
            continue
        sessions[guild_id] = (voice_id, text_id)
    return sessions


def rejoin_skip_reason(
    *,
    guild_found: bool,
    voice_found: bool,
    text_found: bool,
    blocked: bool,
    connected: bool,
    members: int,
    missing_permissions: list[str],
) -> str | None:
    """再接続しない理由（再接続するなら None）。

    ``members`` はボイスチャンネルにいる Bot 以外のメンバーの数、``missing_permissions`` は Bot に足りない権限。
    """
    if not guild_found:
        return "サーバーが見つかりません"
    if blocked:
        return "ブロックリストにあるサーバーです"
    if connected:
        return "すでに接続しています"
    if not voice_found or not text_found:
        return "チャンネルが見つかりません"
    if members == 0:
        return "ボイスチャンネルに誰もいません"
    if missing_permissions:
        return f"権限がありません（{'・'.join(missing_permissions)}）"
    return None


class VoiceSessionRegistry:
    """接続中のボイスチャンネルの記録（``settings/voice_sessions.json``）。"""

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> dict[int, tuple[int, int]]:
        """記録を読む（ブロッキング）。ファイルが無い・壊れている場合は空（壊れていればログに残す）。"""
        with self._lock:
            try:
                data = read_json_object(self.path)
            except (OSError, ValueError):
                logger.warning("接続の記録 %s を読み込めません", self.path, exc_info=True)
                return {}
            return parse_sessions(data, str(self.path))

    def _update(self, guild_id: int, entry: dict[str, str] | None) -> bool:
        with self._lock:
            try:
                data = read_json_object(self.path)
            except ValueError:
                # 壊れた記録は作り直す（再接続できないだけで、設定などは失われない）
                logger.warning("接続の記録 %s が壊れているため作り直します", self.path)
                data = {}
            key = str(guild_id)
            if entry is None:
                if key not in data:
                    return False
                del data[key]
            else:
                if data.get(key) == entry:
                    return False
                data[key] = entry
            write_json_atomic(self.path, data)
            return True

    def record(self, guild_id: int, voice_id: int, text_id: int) -> bool:
        """接続したボイスチャンネルとテキストチャンネルを記録する。変わらなければ書き込まずに False。"""
        return self._update(guild_id, {"voice": str(voice_id), "text": str(text_id)})

    def forget(self, guild_id: int) -> bool:
        """記録を消す。記録がなければ False。"""
        return self._update(guild_id, None)
