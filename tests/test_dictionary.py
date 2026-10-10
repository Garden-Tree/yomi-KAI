import asyncio
import json
import os

import pytest

from yomi_kai.dictionary import DictionaryStore

GUILD = 1234


def read_json(path):
    with open(path, encoding="UTF-8") as f:
        return json.load(f)


def test_missing_file_is_empty(tmp_path):
    store = DictionaryStore(tmp_path / "dict")
    assert store.words(GUILD) == {}
    assert store.get(GUILD).apply("abc") == "abc"
    assert not (tmp_path / "dict").exists()  # 読むだけではディレクトリを作らない


def test_add_writes_same_format(tmp_path):
    store = DictionaryStore(tmp_path)
    store.add(GUILD, "w", "わら")
    store.add(GUILD, "草", "くさ")
    path = tmp_path / f"{GUILD}.json"
    assert read_json(path) == {"w": "わら", "草": "くさ"}
    # 従来どおり indent=2, ensure_ascii=False
    assert path.read_text(encoding="UTF-8") == json.dumps({"w": "わら", "草": "くさ"}, indent=2, ensure_ascii=False)
    assert store.words(GUILD) == {"w": "わら", "草": "くさ"}


def test_add_overwrites_reading(tmp_path):
    store = DictionaryStore(tmp_path)
    store.add(GUILD, "w", "わら")
    store.add(GUILD, "w", "だぶりゅー")
    assert store.words(GUILD) == {"w": "だぶりゅー"}


def test_reads_existing_file(tmp_path):
    (tmp_path / f"{GUILD}.json").write_text(json.dumps({"東京": "とうきょう"}, ensure_ascii=False), encoding="UTF-8")
    store = DictionaryStore(tmp_path)
    assert store.get(GUILD).apply("東京へ") == "とうきょうへ"


def test_cache_is_invalidated_on_add_and_del(tmp_path):
    store = DictionaryStore(tmp_path)
    assert store.get(GUILD).apply("abc") == "abc"
    store.add(GUILD, "abc", "えーびーしー")
    assert store.get_cached(GUILD) is None
    assert store.get(GUILD).apply("abc") == "えーびーしー"
    assert store.get_cached(GUILD) is store.get(GUILD)  # 2回目以降はキャッシュ
    assert store.remove(GUILD, "abc") is True
    assert store.get(GUILD).apply("abc") == "abc"


def test_remove_unknown_word(tmp_path):
    store = DictionaryStore(tmp_path)
    store.add(GUILD, "a", "あ")
    before = (tmp_path / f"{GUILD}.json").read_bytes()
    assert store.remove(GUILD, "zzz") is False
    assert (tmp_path / f"{GUILD}.json").read_bytes() == before


def test_remove_on_missing_file(tmp_path):
    store = DictionaryStore(tmp_path)
    assert store.remove(GUILD, "a") is False
    assert not (tmp_path / f"{GUILD}.json").exists()


def test_guilds_are_independent(tmp_path):
    store = DictionaryStore(tmp_path)
    store.add(1, "a", "あ")
    store.add(2, "b", "び")
    assert store.words(1) == {"a": "あ"}
    assert store.words(2) == {"b": "び"}


def test_atomic_write_leaves_no_temp_files(tmp_path):
    store = DictionaryStore(tmp_path)
    for i in range(5):
        store.add(GUILD, f"w{i}", f"r{i}")
    store.remove(GUILD, "w0")
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{GUILD}.json"]


def test_failed_write_keeps_old_file(tmp_path, monkeypatch):
    store = DictionaryStore(tmp_path)
    store.add(GUILD, "a", "あ")
    path = tmp_path / f"{GUILD}.json"
    before = path.read_bytes()

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.add(GUILD, "b", "び")
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{GUILD}.json"]


def test_corrupt_file_reads_as_empty_but_mutations_fail(tmp_path):
    path = tmp_path / f"{GUILD}.json"
    path.write_text("{not json", encoding="UTF-8")
    store = DictionaryStore(tmp_path)
    assert store.get(GUILD).apply("abc") == "abc"
    # 壊れたファイルを空の辞書で上書きしてしまわない
    with pytest.raises(ValueError):
        store.add(GUILD, "a", "あ")
    assert path.read_text(encoding="UTF-8") == "{not json"


def test_aget_uses_cache(tmp_path):
    store = DictionaryStore(tmp_path)
    store.add(GUILD, "a", "あ")

    async def run():
        first = await store.aget(GUILD)
        second = await store.aget(GUILD)
        return first, second

    first, second = asyncio.run(run())
    assert first is second
    assert first.apply("a") == "あ"
