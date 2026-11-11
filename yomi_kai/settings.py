"""サーバーごとの設定 ``settings/{guild_id}.json`` の読み書きと、読み上げ音声の決定。

ファイル形式（indent=2, ensure_ascii=False）::

    {
      "speaker": "サーバーの話者（未設定なら省略）",
      "announce_voice": true,
      "read_name": false,
      "queue_speedup": true,
      "read_bots": false,
      "read_url": true,
      "read_emoji": true,
      "read_code": false,
      "read_spoiler": false,
      "ignored_users": ["<user_id>"],
      "ignored_roles": ["<role_id>"],
      "autojoin": {"<voice_channel_id>": "<text_channel_id>"},
      "users": {
        "<user_id>": {"speaker": "個人の話者", "speed": 1.2, "pitch": 3}
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

from .jsonfile import FailedLoads, read_json_object, write_json_atomic
from .text import ReadOptions

logger = logging.getLogger(__name__)

# 読み上げないユーザー・ロールの登録数の上限（それぞれ）
MAX_IGNORED = 25
IGNORED_KEYS = ("ignored_users", "ignored_roles")
# 読み上げの切り替え（y.read）: 対象の名前 -> 設定のキー
READ_KEYS = {"url": "read_url", "emoji": "read_emoji", "code": "read_code", "spoiler": "read_spoiler"}
# 自動接続するボイスチャンネルの登録数の上限
MAX_AUTOJOIN = 5

SPEED_MIN = 0.5
SPEED_MAX = 2.0
DEFAULT_SPEED = 1.0

# 声の高さ（y.pitch）。利用者には -10〜+10 の整数で見せ、1段階あたり PITCH_STEP を
# VOICEVOX の pitch_scale（対数の基本周波数に足す値）に足す。±10 で ±0.15（約 ±2.6 半音）
PITCH_MIN = -10
PITCH_MAX = 10
PITCH_STEP = 0.015


@dataclass(frozen=True)
class UserSettings:
    speaker: str | None = None
    speed: float | None = None
    # 声の高さ（PITCH_MIN〜PITCH_MAX の段階。None なら標準）
    pitch: int | None = None


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
    # URL を「URL」と読むか（オフなら読まない）・サーバー絵文字の名前を読むか（オフなら読まない）
    read_url: bool = True
    read_emoji: bool = True
    # コードブロック・ネタバレの中身を読むか（オフなら「コード省略」「ネタバレ」と読む）
    read_code: bool = False
    read_spoiler: bool = False
    # 読み上げないユーザー・ロールの ID
    ignored_users: frozenset[int] = frozenset()
    ignored_roles: frozenset[int] = frozenset()
    # 自動接続: ボイスチャンネルの ID -> 読み上げるテキストチャンネルの ID
    autojoin: Mapping[int, int] = field(default_factory=dict)

    def user(self, user_id: int) -> UserSettings:
        return self.users.get(user_id) or UserSettings()

    def read_options(self) -> ReadOptions:
        return ReadOptions(url=self.read_url, emoji=self.read_emoji, code=self.read_code, spoiler=self.read_spoiler)

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
        read_url = _flag(data, "read_url", True)
        read_emoji = _flag(data, "read_emoji", True)
        read_code = _flag(data, "read_code", False)
        read_spoiler = _flag(data, "read_spoiler", False)
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
            pitch = entry.get("pitch")
            if pitch is not None and (isinstance(pitch, bool) or not isinstance(pitch, (int, float))):
                raise ValueError(f"users[{key!r}].pitch は数値で指定してください")
            users[user_id] = UserSettings(
                user_speaker,
                None if speed is None else float(speed),
                None if pitch is None else _clamp_pitch(pitch),
            )
        return cls(
            speaker=speaker,
            users=users,
            announce_voice=announce_voice,
            read_name=read_name,
            queue_speedup=queue_speedup,
            read_bots=read_bots,
            read_url=read_url,
            read_emoji=read_emoji,
            read_code=read_code,
            read_spoiler=read_spoiler,
            ignored_users=ignored_users,
            ignored_roles=ignored_roles,
            autojoin=autojoin,
        )


def _clamp_pitch(value: float) -> int:
    """保存された高さを PITCH_MIN〜PITCH_MAX の整数にする（手で編集されたファイルなど）。"""
    if not math.isfinite(value):
        return 0
    return int(min(max(round(value), PITCH_MIN), PITCH_MAX))


def pitch_scale(level: int | None) -> float:
    """声の高さの段階を TTSEngine の ``pitch``（VOICEVOX の pitch_scale と同じ単位）にする。"""
    if not level:
        return 0.0
    return min(max(level, PITCH_MIN), PITCH_MAX) * PITCH_STEP


def resolve_pitch(settings: GuildSettings, user_id: int) -> float:
    """読み上げに使う声の高さ（TTSEngine の ``pitch``）。個人設定がなければ 0.0（標準）。"""
    return pitch_scale(settings.user(user_id).pitch)


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


def _dump_ids(raw: dict[str, Any], key: str, ids: Iterable[int]) -> None:
    """ID の一覧を文字列の配列として ``raw`` に書く（空ならキーごと消す）。"""
    values = [str(i) for i in ids]
    if values:
        raw[key] = values
    else:
        raw.pop(key, None)


def _dump_map(raw: dict[str, Any], key: str, mapping: Mapping[int, int]) -> None:
    """ID -> ID の対応を文字列のオブジェクトとして ``raw`` に書く（空ならキーごと消す）。"""
    if mapping:
        raw[key] = {str(k): str(v) for k, v in mapping.items()}
    else:
        raw.pop(key, None)


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
    def __init__(self, directory: str | Path = "settings", *, failed_loads: FailedLoads | None = None) -> None:
        self._dir = Path(directory)
        self._cache: dict[int, GuildSettings] = {}
        # 読み込みに失敗したサーバー（しばらくは読み直さず空の設定を返す）
        self._failed = failed_loads if failed_loads is not None else FailedLoads()
        self._lock = threading.Lock()

    @property
    def directory(self) -> Path:
        return self._dir

    def path_for(self, guild_id: int) -> Path:
        return self._dir / f"{guild_id}.json"

    # --- 読み込み ---
    def get_cached(self, guild_id: int) -> GuildSettings | None:
        return self._cache.get(guild_id)

    def get(self, guild_id: int) -> GuildSettings:
        """サーバーの設定を返す（初回のみファイルを読む）。読み込みに失敗した場合は空の設定。

        壊れたファイルはキャッシュせずに読み直すが、発言のたびに読み直さないよう、失敗してから
        しばらく（``FailedLoads`` の間隔）は読み直さずに空の設定を返す。
        """
        cached = self._cache.get(guild_id)
        if cached is not None:
            return cached
        if self._failed.is_waiting(guild_id):
            return EMPTY_SETTINGS
        with self._lock:
            cached = self._cache.get(guild_id)
            if cached is not None:
                return cached
            if self._failed.is_waiting(guild_id):
                return EMPTY_SETTINGS
            path = self.path_for(guild_id)
            try:
                settings = GuildSettings.from_json(read_json_object(path))
            except (OSError, ValueError):
                # トレースバックはファイルが変わったときだけ出す（同じ壊れたファイルで出し続けない）
                if self._failed.record(guild_id, path):
                    logger.exception("設定ファイルの読み込みに失敗しました: %s", path)
                else:
                    logger.debug("設定ファイルは引き続き読み込めません: %s", path)
                return EMPTY_SETTINGS
            self._failed.clear(guild_id)
            self._cache[guild_id] = settings
            return settings

    async def aget(self, guild_id: int) -> GuildSettings:
        """キャッシュ済みならそのまま、未ロードならスレッドで読み込んで返す。"""
        cached = self._cache.get(guild_id)
        if cached is not None:
            return cached
        if self._failed.is_waiting(guild_id):
            return EMPTY_SETTINGS
        return await asyncio.to_thread(self.get, guild_id)

    def evict(self, guild_id: int) -> None:
        """サーバーのキャッシュを捨てる（Bot がサーバーから外れたとき用。ファイルはそのまま残す）。"""
        with self._lock:
            self._cache.pop(guild_id, None)
            self._failed.clear(guild_id)

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
            self._failed.clear(guild_id)
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
            _dump_ids(raw, key, [*ids, target_id])
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
            _dump_ids(raw, key, [i for i in ids if i != target_id])
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
            _dump_map(raw, "autojoin", mapping)
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
            _dump_map(raw, "autojoin", mapping)
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

    def set_user_pitch(self, guild_id: int, user_id: int, level: int) -> None:
        if not PITCH_MIN <= level <= PITCH_MAX:
            raise ValueError(f"pitch は {PITCH_MIN}〜{PITCH_MAX} で指定してください")
        self._set_user_field(guild_id, user_id, "pitch", int(level))

    def clear_user_pitch(self, guild_id: int, user_id: int) -> bool:
        """個人の声の高さの設定を消す。未設定なら False。"""
        return self._clear_user_field(guild_id, user_id, "pitch")
