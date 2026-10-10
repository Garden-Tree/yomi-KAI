"""config.ini の読み込み。

キーは従来どおり ``[DEFAULT]`` セクションの
DISCORD_TOKEN / USE_GOOGLE_TTS / GOOGLE_API_KEY / PREFIX / AFK_TIME / SD_MSG。
任意キーとして VOICEVOX_LOAD_ALL_MODELS（既定 False）を追加している。
"""

from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

SECTION = "DEFAULT"


class ConfigError(Exception):
    """設定ファイルの不備。メッセージには問題のあるキー名を含める。"""


@dataclass(frozen=True)
class Config:
    discord_token: str
    prefix: str
    afk_time: int
    sd_msg: str
    use_google_tts: bool = False
    google_api_key: str | None = None
    voicevox_load_all_models: bool = False


def _required(section: configparser.SectionProxy, key: str) -> str:
    value = section.get(key)
    if value is None:
        raise ConfigError(f"config.ini に必須項目 `{key}` がありません。")
    if not value.strip():
        raise ConfigError(f"config.ini の `{key}` が空です。")
    return value


def _boolean(section: configparser.SectionProxy, key: str, default: bool) -> bool:
    try:
        return section.getboolean(key, fallback=default)
    except ValueError:
        raise ConfigError(
            f"config.ini の `{key}` は True / False のいずれかで指定してください（現在値: {section.get(key)!r}）。"
        ) from None


def load_config(path: str | Path = "config.ini") -> Config:
    """config.ini を読み込んで :class:`Config` を返す。不備があれば :class:`ConfigError` を送出する。"""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"{path} が見つかりません。config.ini.example をコピーして作成してください。")

    parser = configparser.ConfigParser()
    try:
        # utf-8-sig: メモ帳などで BOM 付き保存された場合も読めるようにする
        parser.read(path, encoding="utf-8-sig")
    except configparser.Error as e:
        raise ConfigError(f"{path} の形式が不正です: {e}") from None

    section = parser[SECTION]
    try:
        discord_token = _required(section, "DISCORD_TOKEN")
        use_google_tts = _boolean(section, "USE_GOOGLE_TTS", False)
        google_api_key = _required(section, "GOOGLE_API_KEY") if use_google_tts else section.get("GOOGLE_API_KEY")
        prefix = _required(section, "PREFIX")
        afk_raw = _required(section, "AFK_TIME")
        try:
            afk_time = int(afk_raw)
        except ValueError:
            raise ConfigError(
                f"config.ini の `AFK_TIME` は秒数（整数）で指定してください（現在値: {afk_raw!r}）。"
            ) from None
        if afk_time <= 0:
            raise ConfigError(f"config.ini の `AFK_TIME` は正の整数で指定してください（現在値: {afk_raw!r}）。")
        sd_msg = _required(section, "SD_MSG")
        load_all = _boolean(section, "VOICEVOX_LOAD_ALL_MODELS", False)
    except configparser.Error as e:
        # 値に `%` が含まれる場合の InterpolationError など
        raise ConfigError(f"config.ini の読み込みに失敗しました: {e}") from None

    return Config(
        discord_token=discord_token,
        prefix=prefix,
        afk_time=afk_time,
        sd_msg=sd_msg,
        use_google_tts=use_google_tts,
        google_api_key=google_api_key,
        voicevox_load_all_models=load_all,
    )
