"""config.ini の読み込み。

キーは従来どおり ``[DEFAULT]`` セクションの
DISCORD_TOKEN / USE_GOOGLE_TTS / GOOGLE_API_KEY / PREFIX / AFK_TIME / SD_MSG。
任意キーとして VOICEVOX_LOAD_ALL_MODELS（既定 False）、MAX_READ_LENGTH（既定 100、0 で無制限）、
ALERT_WEBHOOK_URL（切断通知用の Discord Webhook、空なら通知しない）、ALERT_AFTER（既定 300 秒、30 以上）、
VOICEVOX_WORKERS（並行して合成する数、1〜8、既定 1）、
LOG_READ_TEXT（読み上げた本文をログに出すか、既定 False）、RECORD_USAGE（使用量を記録するか、既定 False）、
USAGE_DIR（使用量の保存先、既定 usage）、
MAX_DICT_ENTRIES（辞書の登録数の上限、既定 0 = 無制限）、SPEAKER_ALLOWLIST（使える話者、既定は全話者）、
BLOCKED_GUILDS_FILE（利用を断るサーバーの一覧、既定 settings/blocked_guilds.txt）、
ENABLE_SUDDEN_DEATH（「突然の死」コマンド sd を使えるようにするか、既定 True。False なら SD_MSG は省略可）
を追加している。
"""

from __future__ import annotations

import configparser
import re
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
    max_read_length: int = 100
    alert_webhook_url: str | None = None
    alert_after: int = 300
    voicevox_workers: int = 1
    # True のときだけ、読み上げた本文を DEBUG ログ（ファイル）に出す
    log_read_text: bool = False
    # True のときだけ、月ごとの使用量（文字数・メッセージ数）を記録する
    record_usage: bool = False
    # 使用量の保存先
    usage_dir: str = "usage"
    # サーバーごとの辞書の登録数の上限（0 で無制限）
    max_dict_entries: int = 0
    # 使える話者の名前（空なら全話者）
    speaker_allowlist: tuple[str, ...] = ()
    # 利用を断るサーバーの ID を1行に1つ書いたファイル（無ければ何もブロックしない）
    blocked_guilds_file: str = "settings/blocked_guilds.txt"
    # False なら「突然の死」コマンド（y.sd・/sd）を登録しない
    enable_sudden_death: bool = True

    def __repr__(self) -> str:
        # トークン・APIキー・Webhook の URL がログ等に出ないようにする
        return (
            f"Config(prefix={self.prefix!r}, afk_time={self.afk_time}, use_google_tts={self.use_google_tts}, "
            f"max_read_length={self.max_read_length}, alert={'on' if self.alert_webhook_url else 'off'}, "
            f"alert_after={self.alert_after}, voicevox_workers={self.voicevox_workers}, "
            f"log_read_text={self.log_read_text}, record_usage={self.record_usage}, usage_dir={self.usage_dir!r}, "
            f"max_dict_entries={self.max_dict_entries}, speaker_allowlist={list(self.speaker_allowlist)!r}, "
            f"blocked_guilds_file={self.blocked_guilds_file!r}, enable_sudden_death={self.enable_sudden_death})"
        )


def split_names(raw: str) -> list[str]:
    """カンマ区切りの名前を分ける（半角「,」・読点「、」・全角「，」のいずれでも区切る）。前後の空白は除く。"""
    return [name.strip() for name in re.split("[,、，]", raw) if name.strip()]


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


def _int(
    section: configparser.SectionProxy,
    key: str,
    default: int | None,
    lo: int,
    hi: int | None = None,
    kind: str = "整数",
) -> int:
    """整数のキーを読む。``default`` が None なら必須、それ以外は未設定・空なら ``default``。

    ``lo`` 〜 ``hi`` の範囲（両端を含む、``hi`` が None なら上限なし）でなければ ConfigError。
    """
    if default is None:
        raw = _required(section, key).strip()
    else:
        raw = (section.get(key) or "").strip()
        if not raw:
            return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"config.ini の `{key}` は{kind}で指定してください（現在値: {raw!r}）。") from None
    if value < lo or (hi is not None and value > hi):
        if hi is not None:
            expected = f"{lo}〜{hi}"
        else:
            expected = "正の整数" if lo == 1 else f"{lo} 以上"
        raise ConfigError(f"config.ini の `{key}` は {expected} で指定してください（現在値: {raw!r}）。")
    return value


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
        afk_time = _int(section, "AFK_TIME", None, 1, None, "秒数（整数）")
        enable_sudden_death = _boolean(section, "ENABLE_SUDDEN_DEATH", True)
        # sd を使わないなら SD_MSG は無くてもよい
        sd_msg = _required(section, "SD_MSG") if enable_sudden_death else (section.get("SD_MSG") or "").strip()
        load_all = _boolean(section, "VOICEVOX_LOAD_ALL_MODELS", False)
        max_read_length = _int(section, "MAX_READ_LENGTH", 100, 0, None, "整数（0 で無制限）")
        alert_webhook_url = (section.get("ALERT_WEBHOOK_URL") or "").strip() or None
        if alert_webhook_url is not None and not alert_webhook_url.startswith("https://"):
            # 値そのものは秘密情報なので表示しない
            raise ConfigError(
                "config.ini の `ALERT_WEBHOOK_URL` は https:// で始まる Webhook の URL を指定してください。"
            )
        alert_after = _int(section, "ALERT_AFTER", 300, 30, None, "秒数（整数）")
        voicevox_workers = _int(section, "VOICEVOX_WORKERS", 1, 1, 8)
        log_read_text = _boolean(section, "LOG_READ_TEXT", False)
        record_usage = _boolean(section, "RECORD_USAGE", False)
        usage_dir = (section.get("USAGE_DIR") or "").strip() or "usage"
        max_dict_entries = _int(section, "MAX_DICT_ENTRIES", 0, 0, None, "整数（0 で無制限）")
        speaker_allowlist = tuple(dict.fromkeys(split_names(section.get("SPEAKER_ALLOWLIST") or "")))
        blocked_guilds_file = (section.get("BLOCKED_GUILDS_FILE") or "").strip() or "settings/blocked_guilds.txt"
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
        max_read_length=max_read_length,
        alert_webhook_url=alert_webhook_url,
        alert_after=alert_after,
        voicevox_workers=voicevox_workers,
        log_read_text=log_read_text,
        record_usage=record_usage,
        usage_dir=usage_dir,
        max_dict_entries=max_dict_entries,
        speaker_allowlist=speaker_allowlist,
        blocked_guilds_file=blocked_guilds_file,
        enable_sudden_death=enable_sudden_death,
    )
