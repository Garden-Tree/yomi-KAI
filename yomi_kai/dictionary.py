"""サーバーごとの読み辞書 ``dict/{guild_id}.json`` の読み書き。

ファイル形式は従来どおり ``{"単語": "読み", ...}``（indent=2, ensure_ascii=False）。
読み込んだ辞書はコンパイル済み正規表現と一緒にメモリへキャッシュし、追加・削除で破棄する。
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


class DictionaryStore:
    def __init__(self, directory: str | Path = "dict") -> None:
        self._dir = Path(directory)
        self._cache: dict[int, WordDictionary] = {}
        self._lock = threading.Lock()

    def path_for(self, guild_id: int) -> Path:
        return self._dir / f"{guild_id}.json"

    # --- 読み込み ---
    def _read_words(self, guild_id: int) -> dict[str, str]:
        """ファイルから辞書を読む。ファイルが無ければ空。壊れていれば例外を送出する。"""
        data = read_json_object(self.path_for(guild_id))
        return {str(k): str(v) for k, v in data.items()}

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

    # --- 書き込み ---
    def _write_words(self, guild_id: int, words: dict[str, str]) -> None:
        """一時ファイルに書いてから os.replace で差し替える（書き込み途中で壊れない）。"""
        write_json_atomic(self.path_for(guild_id), words)

    def add(self, guild_id: int, word: str, reading: str) -> None:
        with self._lock:
            words = self._read_words(guild_id)
            words[word] = reading
            self._write_words(guild_id, words)
            self._cache.pop(guild_id, None)

    def remove(self, guild_id: int, word: str) -> bool:
        """単語を削除する。未登録なら False を返し、ファイルは変更しない。"""
        with self._lock:
            words = self._read_words(guild_id)
            if word not in words:
                return False
            del words[word]
            self._write_words(guild_id, words)
            self._cache.pop(guild_id, None)
            return True
