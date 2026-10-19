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
# ```コードブロック```（言語指定付きも含む）。閉じていない ``` は対象外
_CODE_BLOCK_RE = re.compile(r"```.*?```", flags=re.DOTALL)
# `インラインコード` / ``インラインコード``
_INLINE_CODE_RE = re.compile(r"(?<!`)(`{1,2})(?!`)([^\n]+?)(?<!`)\1(?!`)")
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


CODE_BLOCK_TEXT = "コード省略"
TRUNCATED_SUFFIX = " 以下略"


def replace_code(text: str) -> str:
    """コードブロックは「コード省略」に、インラインコードは中身だけにする。"""
    text = _CODE_BLOCK_RE.sub(CODE_BLOCK_TEXT, text)
    text = _INLINE_CODE_RE.sub(r"\2", text)
    return text


def truncate(text: str, max_length: int) -> str:
    """``max_length`` 文字を超えたら切り詰めて「以下略」を付ける。0 以下なら無制限。"""
    if max_length <= 0 or len(text) <= max_length:
        return text
    return text[:max_length].rstrip() + TRUNCATED_SUFFIX


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
    max_length: int = 0,
) -> str:
    """メッセージ本文を読み上げ用に整形する。

    処理順: コード → 辞書置換 → URL → ネタバレ → メンション → サーバー絵文字 → マークダウン → 文字数制限。
    コードを最初に処理するのは、コード内の文字列が辞書や URL として読まれないようにするため。
    ``max_length`` が正なら、整形後の文字数で切り詰める（0 以下で無制限）。
    """
    text = replace_code(text)
    if dictionary is not None:
        text = dictionary.apply(text)
    text = replace_urls(text)
    text = replace_spoilers(text)
    text = replace_mentions(text, user_name=user_name, role_name=role_name, channel_name=channel_name)
    text = replace_custom_emojis(text)
    text = strip_markdown(text)
    return truncate(text, max_length)


# --- 入退室 ---
JOIN = "join"
LEAVE = "leave"


def voice_transition(
    before_channel_id: int | None,
    after_channel_id: int | None,
    bot_channel_id: int | None,
    *,
    is_bot: bool,
) -> str | None:
    """Bot のいるボイスチャンネルへの入室なら ``JOIN``、退室なら ``LEAVE``、それ以外は None。

    チャンネル間の移動は、Bot のいるチャンネルから出れば退室、入れば入室として扱う。
    ミュート・配信など、チャンネルが変わらない更新は None。Bot（他の Bot を含む）は対象外。
    """
    if is_bot or bot_channel_id is None or before_channel_id == after_channel_id:
        return None
    if after_channel_id == bot_channel_id:
        return JOIN
    if before_channel_id == bot_channel_id:
        return LEAVE
    return None


def announce_text(kind: str, display_name: str) -> str:
    return f"{display_name}さんが{'入室' if kind == JOIN else '退室'}しました"


def gen_sd_text(msg: str) -> str:
    """「突然の死」風のテキストを生成する。"""
    length = sum(2 if east_asian_width(ch) in "FWA" else 1 for ch in msg)
    top = "＿人" + "人" * (length // 2) + "人＿"
    bottom = "￣Y" + "^Y" * (length // 2) + "^Y￣"
    return f"{top}\n＞　{msg}　＜\n{bottom}"
