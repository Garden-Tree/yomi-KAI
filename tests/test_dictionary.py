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


@pytest.mark.parametrize("content", ['{"a": {"x": 1}}', '{"a": null}', '{"a": 1}', '{"a": ["あ"]}'])
def test_non_string_readings_are_corrupt(tmp_path, content):
    # 文字列以外の読みを文字列にして読み込まない（壊れたファイルとして扱い、上書きもしない）
    path = tmp_path / f"{GUILD}.json"
    path.write_text(content, encoding="UTF-8")
    store = DictionaryStore(tmp_path)
    assert store.words(GUILD) == {}
    with pytest.raises(ValueError):
        store.add(GUILD, "b", "び")
    assert path.read_text(encoding="UTF-8") == content


# --- 登録できない単語・読み ---

INVALID_ENTRIES = [
    ("", "あ"),
    ("   ", "あ"),
    ("　", "あ"),
    ("a", ""),
    ("a", " \n"),
    ("x" * 51, "あ"),
    ("a", "あ" * 101),
]


@pytest.mark.parametrize(("word", "reading"), INVALID_ENTRIES)
def test_add_rejects_invalid_entries(tmp_path, word, reading):
    from yomi_kai.dictionary import InvalidEntryError

    store = DictionaryStore(tmp_path)
    store.add(GUILD, "a", "えー")
    with pytest.raises(InvalidEntryError):
        store.add(GUILD, word, reading)
    with pytest.raises(InvalidEntryError):
        store.merge(GUILD, {"b": "びー", word: reading})
    assert read_json(store.path_for(GUILD)) == {"a": "えー"}
    assert store.get(GUILD).apply("abc") == "えーbc"


def test_add_accepts_entries_at_the_length_limit(tmp_path):
    from yomi_kai.dictionary import MAX_READING_LENGTH, MAX_WORD_LENGTH

    store = DictionaryStore(tmp_path)
    assert store.add(GUILD, "x" * MAX_WORD_LENGTH, "あ" * MAX_READING_LENGTH)


@pytest.mark.parametrize("slash", [False, True])
def test_dict_add_command_rejects_empty_word(tmp_path, slash):
    from yomi_kai.cogs.dictionary import DictionaryCog
    from yomi_kai.config import Config

    store = DictionaryStore(tmp_path / "dict")
    cog = DictionaryCog(SimpleNamespace(), Config(discord_token="d", prefix="y.", afk_time=60, sd_msg="x"), store)
    ctx = FakeCtx(slash)
    asyncio.run(DictionaryCog.dict_add.callback(cog, ctx, "", "ばくはつ"))
    assert ctx.sent == [("辞書に登録できません: 単語が空です", {"ephemeral": slash})]
    assert store.words(GUILD) == {}
    assert not store.path_for(GUILD).exists()


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


# --- エクスポート・インポート ---


def test_merge_respects_limit_and_overwrites(tmp_path):
    store = DictionaryStore(tmp_path / "dict")
    store.add(GUILD, "a", "えー")
    store.add(GUILD, "b", "びー")
    added, updated, skipped = store.merge(GUILD, {"a": "あ", "b": "びー", "c": "しー", "d": "でぃー"}, max_entries=3)
    assert (added, updated, skipped) == (1, 1, 1)
    assert store.words(GUILD) == {"a": "あ", "b": "びー", "c": "しー"}
    assert read_json(store.path_for(GUILD)) == {"a": "あ", "b": "びー", "c": "しー"}
    assert store.merge(GUILD, {"d": "でぃー"}) == (1, 0, 0)  # 0 は無制限


def test_export_matches_store_format_and_round_trips(tmp_path):
    from yomi_kai.cogs.dictionary import export_dictionary, parse_dictionary_import

    store = DictionaryStore(tmp_path / "dict")
    store.add(GUILD, "yomi-KAI", "よみかい")
    data = export_dictionary(store.words(GUILD))
    # 改行コード以外はストアのファイルと同じ（ストアはテキストモードで書くため Windows では CRLF）
    assert data.decode("utf-8") == store.path_for(GUILD).read_text(encoding="utf-8")
    assert parse_dictionary_import(data) == {"yomi-KAI": "よみかい"}


@pytest.mark.parametrize(
    "data",
    [
        b"{broken",
        b"[]",
        b'{"a": 1}',
        b'{"a": ""}',
        b'{" ": "x"}',
        b'{"": "x"}',
        b'{"' + b"x" * 51 + b'": "x"}',
        b'{"a": "' + b"x" * 101 + b'"}',
        b"\xff\xfe",
        "too-large",
    ],
)
def test_parse_import_rejects_invalid(data):
    from yomi_kai.cogs.dictionary import parse_dictionary_import

    if data == "too-large":
        data = b'{"a": "' + b"x" * 102400 + b'"}'
    with pytest.raises(ValueError):
        parse_dictionary_import(data)


class FakeAttachment:
    def __init__(self, data):
        self.data = data
        self.size = len(data)

    async def read(self):
        return self.data


class ImportCtx(FakeCtx):
    async def defer(self):
        pass


@pytest.mark.parametrize("slash", [False, True])
def test_dict_import_command(tmp_path, slash):
    from yomi_kai.cogs.dictionary import DictionaryCog
    from yomi_kai.config import Config

    config = Config(discord_token="dummy", prefix="y.", afk_time=60, sd_msg="x", max_dict_entries=2)
    store = DictionaryStore(tmp_path / "dict")
    store.add(GUILD, "a", "えー")
    cog = DictionaryCog(SimpleNamespace(), config, store)

    async def run():
        ok, bad = ImportCtx(slash), ImportCtx(slash)
        payload = '{"a": "あ", "b": "びー", "c": "しー"}'.encode()
        await DictionaryCog.dict_import.callback(cog, ok, FakeAttachment(payload))
        await DictionaryCog.dict_import.callback(cog, bad, FakeAttachment(b"[]"))
        return ok, bad

    ok, bad = asyncio.run(run())
    expected = "辞書を読み込みました（追加 1件、更新 1件）。登録数の上限（2件）のため 1件は追加しませんでした"
    assert ok.sent == [(expected, {"ephemeral": False})]
    assert bad.sent[0][0].startswith("辞書を読み込めませんでした")
    assert bad.sent[0][1] == {"ephemeral": slash}
    assert store.words(GUILD) == {"a": "あ", "b": "びー"}


def test_dict_export_command(tmp_path):
    from yomi_kai.cogs.dictionary import DictionaryCog
    from yomi_kai.config import Config

    store = DictionaryStore(tmp_path / "dict")
    store.add(GUILD, "a", "えー")
    cog = DictionaryCog(SimpleNamespace(), Config(discord_token="d", prefix="y.", afk_time=60, sd_msg="x"), store)
    ctx = FakeCtx(True)
    asyncio.run(DictionaryCog.dict_export.callback(cog, ctx))
    content, kwargs = ctx.sent[0]
    assert content == "辞書を書き出しました（1件）"
    assert kwargs["file"].filename == f"dict-{GUILD}.json"
    assert json.loads(kwargs["file"].fp.read()) == {"a": "えー"}


def test_dict_list_command_sends_one_entry_per_line_without_splitting(tmp_path):
    from yomi_kai.cogs.dictionary import DictionaryCog
    from yomi_kai.config import Config

    store = DictionaryStore(tmp_path / "dict")
    words = {f"単語{i:03d}_{'w' * 40}": f"よみ{i:03d}{'あ' * 40}" for i in range(120)}
    store.merge(GUILD, words)
    cog = DictionaryCog(SimpleNamespace(), Config(discord_token="d", prefix="y.", afk_time=60, sd_msg="x"), store)
    ctx = FakeCtx(False)
    asyncio.run(DictionaryCog.dict_list.callback(cog, ctx))
    contents = [content for content, _ in ctx.sent]
    assert contents[0] == "辞書を表示します（120件）"
    bodies = contents[1:]
    assert len(bodies) > 1  # 1通に収まらない量
    lines = []
    for body in bodies:
        assert len(body) < 2000
        assert body.startswith("```\n") and body.endswith("\n```")
        lines.extend(body[4:-4].split("\n"))
    # どの行も「単語 → 読み」の形で、途中で切れていない
    assert lines == [f"{w} → {r}" for w, r in words.items()]


def test_dict_list_command_on_empty_dictionary(tmp_path):
    from yomi_kai.cogs.dictionary import DictionaryCog
    from yomi_kai.config import Config

    store = DictionaryStore(tmp_path / "dict")
    cog = DictionaryCog(SimpleNamespace(), Config(discord_token="d", prefix="y.", afk_time=60, sd_msg="x"), store)
    ctx = FakeCtx(False)
    asyncio.run(DictionaryCog.dict_list.callback(cog, ctx))
    assert [content for content, _ in ctx.sent] == ["辞書（0件）: まだ登録がありません"]
