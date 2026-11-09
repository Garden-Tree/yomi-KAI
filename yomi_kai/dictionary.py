"""サーバーごとの読み辞書 ``dict/{guild_id}.json`` の読み書き。

ファイル形式は従来どおり ``{"単語": "読み", ...}``（indent=2, ensure_ascii=False）。
読み込んだ辞書はコンパイル済み正規表現と一緒にメモリへキャッシュし、追加・削除時は書き込んだ内容で差し替える（破棄しない）。
同期メソッドはファイル I/O を伴うため、イベントループからは ``asyncio.to_thread`` 経由で呼ぶこと
（``aget`` はキャッシュがあれば I/O なしで返す）。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from .jsonfile import read_json_object, write_json_atomic
from .text import WordDictionary, compile_dictionary

logger = logging.getLogger(__name__)

# 登録できる単語・読みの長さ（文字数）の上限
MAX_WORD_LENGTH = 50
MAX_READING_LENGTH = 100


class InvalidEntryError(ValueError):
    """登録できない単語・読み（空、長すぎる）。メッセージはそのまま利用者に表示できる。"""


def validate_entry(word: str, reading: str) -> None:
    """単語と読みを登録できるか確かめる。できなければ理由を添えて :class:`InvalidEntryError`。

    空の単語はすべての文字の間に一致してしまい、サーバー全体の読み上げが壊れるため受け付けない。
    """
    if not word.strip():
        raise InvalidEntryError("単語が空です")
    if not reading.strip():
        raise InvalidEntryError(f"「{word}」の読みが空です")
    if len(word) > MAX_WORD_LENGTH:
        raise InvalidEntryError(f"単語は{MAX_WORD_LENGTH}文字までです")
    if len(reading) > MAX_READING_LENGTH:
        raise InvalidEntryError(f"「{word}」の読みが長すぎます（{MAX_READING_LENGTH}文字まで）")


class DictionaryStore:
    def __init__(self, directory: str | Path = "dict") -> None:
        self._dir = Path(directory)
        self._cache: dict[int, WordDictionary] = {}
        self._lock = threading.Lock()

    def path_for(self, guild_id: int) -> Path:
        return self._dir / f"{guild_id}.json"

    # --- 読み込み ---
    def _read_words(self, guild_id: int) -> dict[str, str]:
        """ファイルから辞書を読む。ファイルが無ければ空。壊れていれば例外を送出する（読みが文字列でない場合を含む）。"""
        path = self.path_for(guild_id)
        data = read_json_object(path)
        for word, reading in data.items():
            if not isinstance(reading, str):
                raise ValueError(f"{path} の「{word}」の読みが文字列ではありません")
        return data

    def get_cached(self, guild_id: int) -> WordDictionary | None:
        return self._cache.get(guild_id)

    def get(self, guild_id: int) -> WordDictionary:
        """コンパイル済み辞書を返す（初回のみファイルを読む）。読み込みに失敗した場合は空の辞書。"""
        cached = self._cache.get(guild_id)
        if cached is not None:
            return cached
        with self._lock:
            cached = self._cache.get(guild_id)
            if cached is not None:
                return cached
            try:
                compiled = compile_dictionary(self._read_words(guild_id))
            except (OSError, ValueError):
                # 壊れたファイルはキャッシュせず、次回また読み直す
                logger.exception("辞書ファイルの読み込みに失敗しました: %s", self.path_for(guild_id))
                return compile_dictionary({})
            self._cache[guild_id] = compiled
            return compiled

    async def aget(self, guild_id: int) -> WordDictionary:
        """キャッシュ済みならそのまま、未ロードならスレッドで読み込んで返す。"""
        cached = self._cache.get(guild_id)
        if cached is not None:
            return cached
        return await asyncio.to_thread(self.get, guild_id)

    def words(self, guild_id: int) -> dict[str, str]:
        """登録済みの単語一覧（コピー）を返す。"""
        return dict(self.get(guild_id).words)

    # --- 書き込み（一時ファイルに書いてから os.replace で差し替える。書き込み途中で壊れない） ---
    def add(self, guild_id: int, word: str, reading: str, *, max_entries: int = 0) -> bool:
        """単語を登録する（登録済みなら読みを更新する）。

        単語・読みが空、または長すぎる場合は :class:`InvalidEntryError` を送出する。
        ``max_entries`` が 1 以上で、未登録の単語を追加すると件数がそれを超える場合は何もせず False を返す
        （登録済みの単語の読みの更新はいつでもできる）。0 なら無制限。
        """
        validate_entry(word, reading)
        with self._lock:
            words = self._read_words(guild_id)
            if max_entries > 0 and word not in words and len(words) >= max_entries:
                return False
            words[word] = reading
            write_json_atomic(self.path_for(guild_id), words)
            # キャッシュを破棄せず差し替える。破棄すると次の発言でファイル読み込みの待ちが発生し、
            # その間に届いた発言と読み上げ順が入れ替わる可能性がある
            self._cache[guild_id] = compile_dictionary(words)
            return True

    def merge(self, guild_id: int, entries: dict[str, str], *, max_entries: int = 0) -> tuple[int, int, int]:
        """複数の単語をまとめて登録する（同じ単語は ``entries`` の読みで上書き）。(追加, 更新, 上限で飛ばした) を返す。

        ``max_entries`` が 1 以上なら、新しい単語は上限に収まる分だけ（``entries`` の順に）追加する。
        登録できない単語・読みが含まれていれば、何も変えずに :class:`InvalidEntryError` を送出する。
        """
        for word, reading in entries.items():
            validate_entry(word, reading)
        with self._lock:
            words = self._read_words(guild_id)
            added = updated = skipped = 0
            for word, reading in entries.items():
                if word in words:
                    if words[word] != reading:
                        updated += 1
                    words[word] = reading
                elif max_entries > 0 and len(words) >= max_entries:
                    skipped += 1
                else:
                    words[word] = reading
                    added += 1
            if added or updated:
                write_json_atomic(self.path_for(guild_id), words)
                self._cache[guild_id] = compile_dictionary(words)
            return added, updated, skipped

    def remove(self, guild_id: int, word: str) -> bool:
        """単語を削除する。未登録なら False を返し、ファイルは変更しない。"""
        with self._lock:
            words = self._read_words(guild_id)
            if word not in words:
                return False
            del words[word]
            write_json_atomic(self.path_for(guild_id), words)
            self._cache[guild_id] = compile_dictionary(words)
            return True
