import asyncio
import json
import os
from types import SimpleNamespace

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


def test_cache_is_replaced_on_add_and_del(tmp_path):
    """追加・削除後もキャッシュが残り、読み上げ時にファイル読み込みの待ちが発生しない。"""
    store = DictionaryStore(tmp_path)
    assert store.get(GUILD).apply("abc") == "abc"
    store.add(GUILD, "abc", "えーびーしー")
    cached = store.get_cached(GUILD)
    assert cached is not None and cached.apply("abc") == "えーびーしー"
    assert store.get(GUILD) is cached
    assert store.remove(GUILD, "abc") is True
    cached = store.get_cached(GUILD)
    assert cached is not None and cached.apply("abc") == "abc"
    # 別インスタンス（再起動相当）でもファイルから同じ内容が読める
    assert DictionaryStore(tmp_path).get(GUILD).apply("abc") == "abc"


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


# --- 登録数の上限（MAX_DICT_ENTRIES） ---


def test_add_respects_max_entries(tmp_path):
    store = DictionaryStore(tmp_path / "dict")
    assert store.add(GUILD, "a", "えー", max_entries=2)
    assert store.add(GUILD, "b", "びー", max_entries=2)
    assert not store.add(GUILD, "c", "しー", max_entries=2)  # 新しい単語は追加しない
    assert store.add(GUILD, "a", "あ", max_entries=2)  # 登録済みの単語の更新はできる
    assert read_json(store.path_for(GUILD)) == {"a": "あ", "b": "びー"}
    assert store.get(GUILD).words == {"a": "あ", "b": "びー"}
    assert store.add(GUILD, "c", "しー")  # 0（既定）は無制限


class FakeCtx:
    def __init__(self, slash):
        self.interaction = object() if slash else None
        self.guild = SimpleNamespace(id=GUILD)
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


@pytest.mark.parametrize("slash", [False, True])
def test_dict_add_command_refuses_when_full(tmp_path, slash):
    from yomi_kai.cogs.dictionary import DictionaryCog
    from yomi_kai.config import Config

    config = Config(discord_token="dummy", prefix="y.", afk_time=60, sd_msg="x", max_dict_entries=1)
    store = DictionaryStore(tmp_path / "dict")
    cog = DictionaryCog(SimpleNamespace(), config, store)

    async def run():
        first, second, update = FakeCtx(slash), FakeCtx(slash), FakeCtx(slash)
        await DictionaryCog.dict_add.callback(cog, first, "a", "えー")
        await DictionaryCog.dict_add.callback(cog, second, "b", "びー")
        await DictionaryCog.dict_add.callback(cog, update, "a", "あ")
        return first, second, update

    first, second, update = asyncio.run(run())
    assert "embed" in first.sent[0][1]
    assert second.sent == [
        ("辞書の登録数が上限（1件）に達しています。不要な単語を削除してください", {"ephemeral": slash})
    ]
    assert "embed" in update.sent[0][1]
    assert store.words(GUILD) == {"a": "あ"}
