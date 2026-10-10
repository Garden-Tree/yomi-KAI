"""読み上げ用テキストの整形（I/O を行わない純粋関数のみ）。"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from unicodedata import east_asian_width

# id -> 表示名。見つからなければ None を返す。
NameResolver = Callable[[int], "str | None"]


@dataclass(frozen=True)
class WordDictionary:
    """コンパイル済みのサーバー辞書。"""

    words: Mapping[str, str] = field(default_factory=dict)
    pattern: re.Pattern[str] | None = None

    def apply(self, text: str) -> str:
        if self.pattern is None:
            return text
        return self.pattern.sub(lambda m: self.words[m.group(0)], text)


EMPTY_DICTIONARY = WordDictionary()


def compile_dictionary(words: Mapping[str, str]) -> WordDictionary:
    """単語→読みの辞書から置換用の正規表現を作る。長い単語を優先してマッチさせる。"""
    words = dict(words)
    if not words:
        return EMPTY_DICTIONARY
    pattern = re.compile("|".join(re.escape(k) for k in sorted(words, key=len, reverse=True)))
    return WordDictionary(words, pattern)


# --- 置換規則 ---
_URL_RE = re.compile(r"https?://\S+")
_SPOILER_RE = re.compile(r"\|\|.*?\|\|", flags=re.DOTALL)
_USER_MENTION_RE = re.compile(r"<@!?([0-9]+)>")
_ROLE_MENTION_RE = re.compile(r"<@&([0-9]+)>")
_CHANNEL_MENTION_RE = re.compile(r"<#([0-9]+)>")
_CUSTOM_EMOJI_RE = re.compile(r"<a?:(\w+):[0-9]+>")
# *斜体* / **太字** / ***太字斜体***: 記号の内側が空白で始まる・終わる場合（例: `2 * 3 * 4`）は対象外
_ASTERISK_RE = re.compile(r"(?<!\*)(\*{1,3})(?=\S)(.+?)(?<=\S)\1(?!\*)")
# _斜体_ / __下線__: Discord と同様、英数字に挟まれた `_`（例: snake_case_name）は対象外
_UNDERSCORE_RE = re.compile(r"(?<![A-Za-z0-9_])(_{1,2})([^_\n]+?)\1(?![A-Za-z0-9_])")


def replace_urls(text: str) -> str:
    return _URL_RE.sub("URL", text)


def replace_spoilers(text: str) -> str:
    return _SPOILER_RE.sub("ネタバレ", text)


def replace_mentions(
    text: str,
    *,
    user_name: NameResolver | None = None,
    role_name: NameResolver | None = None,
    channel_name: NameResolver | None = None,
) -> str:
    def resolve(resolver: NameResolver | None, raw_id: str) -> str | None:
        return resolver(int(raw_id)) if resolver is not None else None

    text = _USER_MENTION_RE.sub(lambda m: "アット" + (resolve(user_name, m.group(1)) or "不明なユーザー"), text)
    text = _ROLE_MENTION_RE.sub(lambda m: "アット" + (resolve(role_name, m.group(1)) or "不明なロール"), text)
    text = _CHANNEL_MENTION_RE.sub(lambda m: resolve(channel_name, m.group(1)) or "不明なチャンネル", text)
    return text


def replace_custom_emojis(text: str) -> str:
    """サーバー絵文字（アニメーション絵文字 <a:name:id> も含む）を名前に置換する。"""
    return _CUSTOM_EMOJI_RE.sub(r"\1", text)


def strip_markdown(text: str) -> str:
    text = _ASTERISK_RE.sub(r"\2", text)
    text = _UNDERSCORE_RE.sub(r"\2", text)
    return text


def build_read_text(
    text: str,
    dictionary: WordDictionary | None = None,
    *,
    user_name: NameResolver | None = None,
    role_name: NameResolver | None = None,
    channel_name: NameResolver | None = None,
) -> str:
    """メッセージ本文を読み上げ用に整形する。

    処理順: 辞書置換 → URL → ネタバレ → メンション → サーバー絵文字 → マークダウン。
    """
    if dictionary is not None:
        text = dictionary.apply(text)
    text = replace_urls(text)
    text = replace_spoilers(text)
    text = replace_mentions(text, user_name=user_name, role_name=role_name, channel_name=channel_name)
    text = replace_custom_emojis(text)
    text = strip_markdown(text)
    return text


def gen_sd_text(msg: str) -> str:
    """「突然の死」風のテキストを生成する。"""
    length = sum(2 if east_asian_width(ch) in "FWA" else 1 for ch in msg)
    top = "＿人" + "人" * (length // 2) + "人＿"
    bottom = "￣Y" + "^Y" * (length // 2) + "^Y￣"
    return f"{top}\n＞　{msg}　＜\n{bottom}"
