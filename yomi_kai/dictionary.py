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

from .jsonfile import FailedLoads, read_json_object, write_json_atomic
from .text import EMPTY_DICTIONARY, WordDictionary, compile_dictionary

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
    def __init__(self, directory: str | Path = "dict", *, failed_loads: FailedLoads | None = None) -> None:
        self._dir = Path(directory)
        self._cache: dict[int, WordDictionary] = {}
        # 読み込みに失敗したサーバー（しばらくは読み直さず空の辞書を返す）
        self._failed = failed_loads if failed_loads is not None else FailedLoads()
        self._lock = threading.Lock()

    @property
    def directory(self) -> Path:
        return self._dir

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
        """コンパイル済み辞書を返す（初回のみファイルを読む）。読み込みに失敗した場合は空の辞書。

        壊れたファイルはキャッシュせずに読み直すが、発言のたびに読み直さないよう、失敗してから
        しばらく（``FailedLoads`` の間隔）は読み直さずに空の辞書を返す。
        """
        cached = self._cache.get(guild_id)
        if cached is not None:
            return cached
        if self._failed.is_waiting(guild_id):
            return compile_dictionary({})
        with self._lock:
            cached = self._cache.get(guild_id)
            if cached is not None:
                return cached
            if self._failed.is_waiting(guild_id):
                return compile_dictionary({})
            path = self.path_for(guild_id)
            try:
                compiled = compile_dictionary(self._read_words(guild_id))
            except (OSError, ValueError):
                # トレースバックはファイルが変わったときだけ出す（同じ壊れたファイルで出し続けない）
                if self._failed.record(guild_id, path):
                    logger.exception("辞書ファイルの読み込みに失敗しました: %s", path)
                else:
                    logger.debug("辞書ファイルは引き続き読み込めません: %s", path)
                return compile_dictionary({})
            self._failed.clear(guild_id)
            self._cache[guild_id] = compiled
            return compiled

    async def aget(self, guild_id: int) -> WordDictionary:
        """キャッシュ済みならそのまま、未ロードならスレッドで読み込んで返す。"""
        cached = self._cache.get(guild_id)
        if cached is not None:
            return cached
        if self._failed.is_waiting(guild_id):
            return compile_dictionary({})
        return await asyncio.to_thread(self.get, guild_id)

    def evict(self, guild_id: int) -> None:
        """サーバーのキャッシュを捨てる（Bot がサーバーから外れたとき用。ファイルはそのまま残す）。"""
        with self._lock:
            self._cache.pop(guild_id, None)
            self._failed.clear(guild_id)

    def words(self, guild_id: int) -> dict[str, str]:
        """登録済みの単語一覧（コピー）を返す。"""
        return dict(self.get(guild_id).words)

    # --- 書き込み（一時ファイルに書いてから os.replace で差し替える。書き込み途中で壊れない） ---
    def _replace_cache(self, guild_id: int, words: dict[str, str]) -> None:
        """書き込んだ内容でキャッシュを差し替える（ロックを持った状態で呼ぶ）。"""
        self._cache[guild_id] = compile_dictionary(words)
        self._failed.clear(guild_id)

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
            self._replace_cache(guild_id, words)
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
                self._replace_cache(guild_id, words)
            return added, updated, skipped

    def remove(self, guild_id: int, word: str) -> bool:
        """単語を削除する。未登録なら False を返し、ファイルは変更しない。"""
        with self._lock:
            words = self._read_words(guild_id)
            if word not in words:
                return False
            del words[word]
            write_json_atomic(self.path_for(guild_id), words)
            self._replace_cache(guild_id, words)
            return True


class UserDictionaryStore:
    """個人辞書 ``dict/users/{guild_id}.json``（``{"<user_id>": {"単語": "読み"}}``）の読み書き。

    サーバーごとに1ファイルで、その人の発言にだけ（サーバー辞書より先に）使う。
    キャッシュ・壊れたファイルの扱い・アトミックな書き込みは :class:`DictionaryStore` と同じ。
    同期メソッドはイベントループから ``asyncio.to_thread`` 経由で呼ぶこと（``aget`` はキャッシュがあれば I/O なし）。
    """

    def __init__(self, directory: str | Path = "dict/users", *, failed_loads: FailedLoads | None = None) -> None:
        self._dir = Path(directory)
        # guild_id -> user_id -> コンパイル済み辞書（登録のないユーザーは入れない）
        self._cache: dict[int, dict[int, WordDictionary]] = {}
        self._failed = failed_loads if failed_loads is not None else FailedLoads()
        self._lock = threading.Lock()

    def path_for(self, guild_id: int) -> Path:
        return self._dir / f"{guild_id}.json"

    def _read_all(self, guild_id: int) -> dict[str, dict[str, str]]:
        """ファイルから全員の辞書を読む。ファイルが無ければ空。壊れていれば例外を送出する。"""
        path = self.path_for(guild_id)
        data = read_json_object(path)
        for user, words in data.items():
            if not (isinstance(user, str) and user.isascii() and user.isdigit()):
                raise ValueError(f"{path} のキー {user!r} がユーザーIDではありません")
            if not isinstance(words, dict):
                raise ValueError(f"{path} の {user} の辞書がオブジェクトではありません")
            for word, reading in words.items():
                if not isinstance(reading, str):
                    raise ValueError(f"{path} の {user} の「{word}」の読みが文字列ではありません")
        return data

    @staticmethod
    def _compile_all(data: dict[str, dict[str, str]]) -> dict[int, WordDictionary]:
        return {int(user): compile_dictionary(words) for user, words in data.items() if words}

    def _guild(self, guild_id: int) -> dict[int, WordDictionary]:
        cached = self._cache.get(guild_id)
        if cached is not None:
            return cached
        if self._failed.is_waiting(guild_id):
            return {}
        with self._lock:
            cached = self._cache.get(guild_id)
            if cached is not None:
                return cached
            if self._failed.is_waiting(guild_id):
                return {}
            path = self.path_for(guild_id)
            try:
                compiled = self._compile_all(self._read_all(guild_id))
            except (OSError, ValueError):
                if self._failed.record(guild_id, path):
                    logger.exception("個人辞書ファイルの読み込みに失敗しました: %s", path)
                else:
                    logger.debug("個人辞書ファイルは引き続き読み込めません: %s", path)
                return {}
            self._failed.clear(guild_id)
            self._cache[guild_id] = compiled
            return compiled

    def get(self, guild_id: int, user_id: int) -> WordDictionary:
        """その人のコンパイル済み辞書（登録がなければ空）。初回のみファイルを読む。"""
        return self._guild(guild_id).get(user_id, EMPTY_DICTIONARY)

    async def aget(self, guild_id: int, user_id: int) -> WordDictionary:
        cached = self._cache.get(guild_id)
        if cached is not None:
            return cached.get(user_id, EMPTY_DICTIONARY)
        if self._failed.is_waiting(guild_id):
            return EMPTY_DICTIONARY
        return await asyncio.to_thread(self.get, guild_id, user_id)

    def words(self, guild_id: int, user_id: int) -> dict[str, str]:
        return dict(self.get(guild_id, user_id).words)

    async def awords(self, guild_id: int, user_id: int) -> dict[str, str]:
        return dict((await self.aget(guild_id, user_id)).words)

    def evict(self, guild_id: int) -> None:
        with self._lock:
            self._cache.pop(guild_id, None)
            self._failed.clear(guild_id)

    def _write(self, guild_id: int, data: dict[str, dict[str, str]]) -> None:
        """書き込んでキャッシュを差し替える（ロックを持った状態で呼ぶ）。"""
        data = {user: words for user, words in data.items() if words}
        write_json_atomic(self.path_for(guild_id), data)
        self._cache[guild_id] = self._compile_all(data)
        self._failed.clear(guild_id)

    def add(self, guild_id: int, user_id: int, word: str, reading: str, *, max_entries: int = 0) -> bool:
        """単語を登録する（登録済みなら読みを更新）。規則はサーバー辞書と同じ（:func:`validate_entry`）。

        ``max_entries`` が 1 以上で、未登録の単語を追加するとその人の件数がそれを超える場合は False。0 なら無制限。
        """
        validate_entry(word, reading)
        with self._lock:
            data = self._read_all(guild_id)
            words = data.setdefault(str(user_id), {})
            if max_entries > 0 and word not in words and len(words) >= max_entries:
                return False
            words[word] = reading
            self._write(guild_id, data)
            return True

    def remove(self, guild_id: int, user_id: int, word: str) -> bool:
        """単語を削除する。未登録なら False（ファイルは変更しない）。"""
        with self._lock:
            data = self._read_all(guild_id)
            words = data.get(str(user_id), {})
            if word not in words:
                return False
            del words[word]
            self._write(guild_id, data)
            return True
