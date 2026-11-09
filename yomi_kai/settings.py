"""サーバーごとの設定 ``settings/{guild_id}.json`` の読み書きと、読み上げ音声の決定。

ファイル形式（indent=2, ensure_ascii=False）::

    {
      "speaker": "サーバーの話者（未設定なら省略）",
      "announce_voice": true,
      "read_name": false,
      "queue_speedup": true,
      "read_bots": false,
      "ignored_users": ["<user_id>"],
      "ignored_roles": ["<role_id>"],
      "autojoin": {"<voice_channel_id>": "<text_channel_id>"},
      "users": {
        "<user_id>": {"speaker": "個人の話者", "speed": 1.2}
      }
    }

``DictionaryStore`` と同じく、読み込んだ設定はメモリへキャッシュし、書き込みはアトミックに行う。
壊れたファイルは読み込み時は空として扱い、書き込み時は上書きせずに例外を送出する。
同期メソッドはファイル I/O を伴うため、イベントループからは ``asyncio.to_thread`` 経由で呼ぶこと
（``aget`` はキャッシュがあれば I/O なしで返す）。
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .jsonfile import read_json_object, write_json_atomic

logger = logging.getLogger(__name__)

# 読み上げないユーザー・ロールの登録数の上限（それぞれ）
MAX_IGNORED = 25
IGNORED_KEYS = ("ignored_users", "ignored_roles")
# 自動接続するボイスチャンネルの登録数の上限
MAX_AUTOJOIN = 5

SPEED_MIN = 0.5
SPEED_MAX = 2.0
DEFAULT_SPEED = 1.0


@dataclass(frozen=True)
class UserSettings:
    speaker: str | None = None
    speed: float | None = None


@dataclass(frozen=True)
class GuildSettings:
    speaker: str | None = None
    users: Mapping[int, UserSettings] = field(default_factory=dict)
    # ボイスチャンネルへの入退室を読み上げるか（未設定なら読み上げる）
    announce_voice: bool = True
    # 発言者の名前を本文の前に読み上げるか（未設定なら読み上げない）
    read_name: bool = False
    # 読み上げ待ちが溜まったら速く読むか（未設定なら速く読む）
    queue_speedup: bool = True
    # ほかの Bot・Webhook の発言も読み上げるか（未設定なら読み上げない）
    read_bots: bool = False
    # 読み上げないユーザー・ロールの ID
    ignored_users: frozenset[int] = frozenset()
    ignored_roles: frozenset[int] = frozenset()
    # 自動接続: ボイスチャンネルの ID -> 読み上げるテキストチャンネルの ID
    autojoin: Mapping[int, int] = field(default_factory=dict)

    def user(self, user_id: int) -> UserSettings:
        return self.users.get(user_id) or UserSettings()

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> GuildSettings:
        """JSON オブジェクトから構築する。型が不正なら ValueError（未知のキーは無視）。"""
        speaker = data.get("speaker")
        if speaker is not None and not isinstance(speaker, str):
            raise ValueError("speaker は文字列で指定してください")
        announce_voice = _flag(data, "announce_voice", True)
        read_name = _flag(data, "read_name", False)
        queue_speedup = _flag(data, "queue_speedup", True)
        read_bots = _flag(data, "read_bots", False)
        ignored_users = frozenset(_id_list(data, "ignored_users"))
        ignored_roles = frozenset(_id_list(data, "ignored_roles"))
        autojoin = _id_map(data, "autojoin")
        raw_users = data.get("users", {})
        if not isinstance(raw_users, dict):
            raise ValueError("users はオブジェクトで指定してください")
        users: dict[int, UserSettings] = {}
        for key, entry in raw_users.items():
            try:
                user_id = int(key)
            except ValueError:
                raise ValueError(f"users のキー {key!r} がユーザーIDではありません") from None
            if not isinstance(entry, dict):
                raise ValueError(f"users[{key!r}] はオブジェクトで指定してください")
            user_speaker = entry.get("speaker")
            if user_speaker is not None and not isinstance(user_speaker, str):
                raise ValueError(f"users[{key!r}].speaker は文字列で指定してください")
            speed = entry.get("speed")
            if speed is not None and (isinstance(speed, bool) or not isinstance(speed, (int, float))):
                raise ValueError(f"users[{key!r}].speed は数値で指定してください")
            users[user_id] = UserSettings(user_speaker, None if speed is None else float(speed))
        return cls(
            speaker=speaker,
            users=users,
            announce_voice=announce_voice,
            read_name=read_name,
            queue_speedup=queue_speedup,
            read_bots=read_bots,
            ignored_users=ignored_users,
            ignored_roles=ignored_roles,
            autojoin=autojoin,
        )


def _snowflake(value: Any, where: str) -> int:
    """ID（数字の文字列または整数）を int にする。"""
    if isinstance(value, bool):
        raise ValueError(f"{where} は ID で指定してください")
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdigit():
        return int(value)
    raise ValueError(f"{where} は ID で指定してください")


def _id_list(data: Mapping[str, Any], key: str) -> list[int]:
    raw = data.get(key, [])
    if not isinstance(raw, list):
        raise ValueError(f"{key} は配列で指定してください")
    return [_snowflake(v, f"{key}[{i}]") for i, v in enumerate(raw)]


def _id_map(data: Mapping[str, Any], key: str) -> dict[int, int]:
    raw = data.get(key, {})
    if not isinstance(raw, dict):
        raise ValueError(f"{key} はオブジェクトで指定してください")
    return {_snowflake(k, f"{key} のキー"): _snowflake(v, f"{key}[{k!r}]") for k, v in raw.items()}


def should_read_author(*, is_self: bool, is_bot: bool, is_webhook: bool, read_bots: bool) -> bool:
    """発言者の種類で読み上げるか決める。

    自分（この Bot）の発言は読まない。ほかの Bot・Webhook の発言は read_bots のときだけ読む。
    """
    if is_self:
        return False
    if is_bot or is_webhook:
        return read_bots
    return True


def is_ignored(settings: GuildSettings, user_id: int, role_ids: Iterable[int]) -> bool:
    """読み上げないユーザー、または読み上げないロールを持つメンバーなら True。"""
    if user_id in settings.ignored_users:
        return True
    return not settings.ignored_roles.isdisjoint(role_ids)


def _flag(data: Mapping[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} は true / false で指定してください")
    return value


EMPTY_SETTINGS = GuildSettings()


def server_speaker(
    settings: GuildSettings, available_speakers: set[str] | frozenset[str], engine_default: str
) -> str:
    """サーバーの話者（未設定・利用できない場合はエンジンの既定話者）。"""
    return settings.speaker if settings.speaker in available_speakers else engine_default


def resolve_voice(
    settings: GuildSettings,
    user_id: int,
    available_speakers: set[str] | frozenset[str],
    engine_default: str,
) -> tuple[str, float]:
    """読み上げに使う (話者, 速度) を決める。

    話者は「ユーザー個人の話者 → サーバーの話者 → エンジンの既定話者」の順で、
    利用できない（モデル未読み込みなど）話者は飛ばす。速度は個人設定、なければ 1.0。
    """
    user = settings.user(user_id)
    speaker = engine_default
    for candidate in (user.speaker, settings.speaker):
        if candidate is None:
            continue
        if candidate in available_speakers:
            speaker = candidate
            break
        logger.debug("保存された話者 %s は利用できないため使用しません (user=%s)", candidate, user_id)

    speed = DEFAULT_SPEED if user.speed is None else user.speed
    if not math.isfinite(speed) or not SPEED_MIN <= speed <= SPEED_MAX:
        # 手で編集されたファイルなどで範囲外の値が入っていた場合
        clamped = DEFAULT_SPEED if not math.isfinite(speed) else min(max(speed, SPEED_MIN), SPEED_MAX)
        logger.debug("保存された速度 %s は範囲外のため %s を使用します (user=%s)", speed, clamped, user_id)
        speed = clamped
    return speaker, speed


class SettingsStore:
    def __init__(self, directory: str | Path = "settings") -> None:
        self._dir = Path(directory)
        self._cache: dict[int, GuildSettings] = {}
        self._lock = threading.Lock()

    def path_for(self, guild_id: int) -> Path:
        return self._dir / f"{guild_id}.json"

    # --- 読み込み ---
    def get_cached(self, guild_id: int) -> GuildSettings | None:
        return self._cache.get(guild_id)

    def get(self, guild_id: int) -> GuildSettings:
        """サーバーの設定を返す（初回のみファイルを読む）。読み込みに失敗した場合は空の設定。"""
        cached = self._cache.get(guild_id)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._cache.get(guild_id)
            if cached is not None:
                return cached
            try:
                settings = GuildSettings.from_json(read_json_object(self.path_for(guild_id)))
            except (OSError, ValueError):
                # 壊れたファイルはキャッシュせず、次回また読み直す
                logger.exception("設定ファイルの読み込みに失敗しました: %s", self.path_for(guild_id))
                return EMPTY_SETTINGS
            self._cache[guild_id] = settings
            return settings

    async def aget(self, guild_id: int) -> GuildSettings:
        """キャッシュ済みならそのまま、未ロードならスレッドで読み込んで返す。"""
        cached = self._cache.get(guild_id)
        if cached is not None:
            return cached
        return await asyncio.to_thread(self.get, guild_id)

    # --- 書き込み ---
    def _update(self, guild_id: int, mutate: Callable[[dict[str, Any]], bool]) -> bool:
        """ファイルを読み、``mutate`` で書き換え、変更があれば保存する。変更の有無を返す。

        壊れたファイルは上書きせずに例外を送出する。未知のキーはそのまま残す。
        """
        with self._lock:
            raw = read_json_object(self.path_for(guild_id))
            GuildSettings.from_json(raw)  # 形式が不正なら書き換える前に例外
            changed = mutate(raw)
            if changed:
                write_json_atomic(self.path_for(guild_id), raw)
            self._cache[guild_id] = GuildSettings.from_json(raw)
            return changed

    def set_guild_speaker(self, guild_id: int, speaker: str) -> None:
        def mutate(raw: dict[str, Any]) -> bool:
            raw["speaker"] = speaker
            return True

        self._update(guild_id, mutate)

    def clear_guild_speaker(self, guild_id: int) -> bool:
        """サーバーの話者設定を消す。未設定なら False。"""

        def mutate(raw: dict[str, Any]) -> bool:
            return raw.pop("speaker", None) is not None

        return self._update(guild_id, mutate)

    def set_flag(self, guild_id: int, key: str, enabled: bool) -> None:
        """サーバー全体のオン/オフの設定（announce_voice / read_name など）を保存する。"""

        def mutate(raw: dict[str, Any]) -> bool:
            raw[key] = bool(enabled)
            return True

        self._update(guild_id, mutate)

    def set_announce_voice(self, guild_id: int, enabled: bool) -> None:
        self.set_flag(guild_id, "announce_voice", enabled)

    def set_read_name(self, guild_id: int, enabled: bool) -> None:
        self.set_flag(guild_id, "read_name", enabled)

    def add_ignored(self, guild_id: int, key: str, target_id: int, limit: int = MAX_IGNORED) -> str:
        """読み上げないユーザー・ロールを登録する。"added" / "exists"（登録済み）/ "full"（上限）を返す。"""
        if key not in IGNORED_KEYS:
            raise ValueError(f"unknown key: {key}")
        result = "added"

        def mutate(raw: dict[str, Any]) -> bool:
            nonlocal result
            ids = _id_list(raw, key)
            if target_id in ids:
                result = "exists"
                return False
            if len(ids) >= limit:
                result = "full"
                return False
            raw[key] = [str(i) for i in [*ids, target_id]]
            return True

        self._update(guild_id, mutate)
        return result

    def remove_ignored(self, guild_id: int, key: str, target_id: int) -> bool:
        """読み上げないユーザー・ロールの登録を外す。登録されていなければ False。"""
        if key not in IGNORED_KEYS:
            raise ValueError(f"unknown key: {key}")

        def mutate(raw: dict[str, Any]) -> bool:
            ids = _id_list(raw, key)
            if target_id not in ids:
                return False
            remaining = [str(i) for i in ids if i != target_id]
            if remaining:
                raw[key] = remaining
            else:
                del raw[key]
            return True

        return self._update(guild_id, mutate)

    def set_autojoin(self, guild_id: int, voice_id: int, text_id: int, limit: int = MAX_AUTOJOIN) -> str:
        """自動接続を登録する。"added" / "updated"（登録済みのボイスチャンネル）/ "full"（上限）を返す。"""
        result = "added"

        def mutate(raw: dict[str, Any]) -> bool:
            nonlocal result
            mapping = _id_map(raw, "autojoin")
            if voice_id in mapping:
                result = "updated"
            elif len(mapping) >= limit:
                result = "full"
                return False
            mapping[voice_id] = text_id
            raw["autojoin"] = {str(k): str(v) for k, v in mapping.items()}
            return True

        self._update(guild_id, mutate)
        return result

    def remove_autojoin(self, guild_id: int, voice_id: int) -> bool:
        """自動接続の登録を外す。登録されていなければ False。"""

        def mutate(raw: dict[str, Any]) -> bool:
            mapping = _id_map(raw, "autojoin")
            if voice_id not in mapping:
                return False
            del mapping[voice_id]
            if mapping:
                raw["autojoin"] = {str(k): str(v) for k, v in mapping.items()}
            else:
                del raw["autojoin"]
            return True

        return self._update(guild_id, mutate)

    def _set_user_field(self, guild_id: int, user_id: int, key: str, value: Any) -> None:
        def mutate(raw: dict[str, Any]) -> bool:
            users = raw.setdefault("users", {})
            users.setdefault(str(user_id), {})[key] = value
            return True

        self._update(guild_id, mutate)

    def _clear_user_field(self, guild_id: int, user_id: int, key: str) -> bool:
        def mutate(raw: dict[str, Any]) -> bool:
            users = raw.get("users")
            if not users:
                return False
            entry = users.get(str(user_id))
            if entry is None or key not in entry:
                return False
            del entry[key]
            if not entry:  # 空になったユーザーは消す
                del users[str(user_id)]
            if not users:
                del raw["users"]
            return True

        return self._update(guild_id, mutate)

    def set_user_speaker(self, guild_id: int, user_id: int, speaker: str) -> None:
        self._set_user_field(guild_id, user_id, "speaker", speaker)

    def clear_user_speaker(self, guild_id: int, user_id: int) -> bool:
        """個人の話者設定を消す。未設定なら False。"""
        return self._clear_user_field(guild_id, user_id, "speaker")

    def set_user_speed(self, guild_id: int, user_id: int, speed: float) -> None:
        self._set_user_field(guild_id, user_id, "speed", float(speed))

    def clear_user_speed(self, guild_id: int, user_id: int) -> bool:
        """個人の速度設定を消す。未設定なら False。"""
        return self._clear_user_field(guild_id, user_id, "speed")
