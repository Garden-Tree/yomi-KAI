"""個人辞書（y.mydict / UserDictionaryStore）のテスト。"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from yomi_kai.cogs.dictionary import DictionaryCog, mydict_full_notice
from yomi_kai.config import Config
from yomi_kai.dictionary import MAX_WORD_LENGTH, DictionaryStore, InvalidEntryError, UserDictionaryStore
from yomi_kai.plans import PlanManager
from yomi_kai.text import build_read_text, compile_dictionary
from yomi_kai.tts import Speaker


def test_add_remove_and_isolated_per_user(tmp_path):
    store = UserDictionaryStore(tmp_path)
    assert store.add(1, 7, "yomi", "よみ")
    assert store.add(1, 8, "yomi", "ヨミ")
    assert store.words(1, 7) == {"yomi": "よみ"}
    assert store.words(1, 8) == {"yomi": "ヨミ"}
    assert store.words(2, 7) == {}  # サーバーごと
    assert json.loads((tmp_path / "1.json").read_text(encoding="utf-8")) == {
        "7": {"yomi": "よみ"},
        "8": {"yomi": "ヨミ"},
    }
    assert store.remove(1, 7, "yomi")
    assert not store.remove(1, 7, "yomi")
    assert json.loads((tmp_path / "1.json").read_text(encoding="utf-8")) == {"8": {"yomi": "ヨミ"}}
    # 新しいストアで読み直しても同じ
    assert UserDictionaryStore(tmp_path).words(1, 8) == {"yomi": "ヨミ"}


def test_limit_counts_per_user_and_allows_updates(tmp_path):
    store = UserDictionaryStore(tmp_path)
    assert store.add(1, 7, "a", "え", max_entries=2)
    assert store.add(1, 7, "b", "び", max_entries=2)
    assert not store.add(1, 7, "c", "し", max_entries=2)
    assert store.add(1, 7, "a", "えー", max_entries=2)  # 登録済みの単語の更新はできる
    assert store.add(1, 8, "c", "し", max_entries=2)  # 別の人は別に数える
    assert store.words(1, 7) == {"a": "えー", "b": "び"}


@pytest.mark.parametrize(
    ("word", "reading"),
    [("", "よみ"), ("  ", "よみ"), ("単語", ""), ("あ" * (MAX_WORD_LENGTH + 1), "よみ"), ("単語", "あ" * 101)],
)
def test_uses_server_dictionary_validation(tmp_path, word, reading):
    store = UserDictionaryStore(tmp_path)
    with pytest.raises(InvalidEntryError):
        store.add(1, 7, word, reading)
    assert not (tmp_path / "1.json").exists()


def test_broken_file_reads_as_empty_and_is_not_overwritten(tmp_path):
    path = tmp_path / "1.json"
    path.write_text('{"7": {"a": 1}}', encoding="utf-8")
    store = UserDictionaryStore(tmp_path)
    assert store.words(1, 7) == {}
    with pytest.raises(ValueError):
        store.add(1, 7, "b", "び")
    assert path.read_text(encoding="utf-8") == '{"7": {"a": 1}}'


def test_user_dictionary_is_applied_before_server_dictionary():
    server = compile_dictionary({"yomi": "よみ", "kai": "かい"})
    user = compile_dictionary({"yomi": "ヨミ", "1": "いち"})
    out = build_read_text("yomi kai <@1>", server, user_dictionary=user, user_name={1: "太郎"}.get)
    # 個人辞書が先に当たる。メンションの数字は辞書で壊れない
    assert out == "ヨミ かい アット太郎"
    assert build_read_text("yomi", None, user_dictionary=user) == "ヨミ"


class Engine:
    def __init__(self):
        self.calls = []

    @property
    def default_speaker(self):
        return "ずんだもん"

    def speakers(self):
        return [Speaker("ずんだもん", "fake")]

    def synthesize(self, text, speaker, speed=1.0, pitch=0.0):
        self.calls.append(text)
        return b"wav"


class FakeCtx:
    def __init__(self, user_id=7):
        self.interaction = None
        self.guild = SimpleNamespace(id=1)
        self.author = SimpleNamespace(id=user_id)
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append(content)


def test_mydict_commands_respect_plan_limit(tmp_path):
    config = Config(discord_token="d", prefix="y.", afk_time=60, sd_msg="x", max_user_dict_entries=1)
    user_store = UserDictionaryStore(tmp_path / "users")
    cog = DictionaryCog(
        SimpleNamespace(),
        config,
        DictionaryStore(tmp_path / "dict"),
        plans=PlanManager(config, Engine()),
        user_store=user_store,
    )

    async def run(callback, *args):
        ctx = FakeCtx()
        await callback(cog, ctx, *args)
        return ctx.sent

    added = asyncio.run(run(DictionaryCog.mydict_add.callback, "yomi", "よみ"))
    assert added == ["個人辞書に登録しました: yomi → よみ"]
    assert asyncio.run(run(DictionaryCog.mydict_add.callback, "kai", "かい")) == [mydict_full_notice(1)]
    assert asyncio.run(run(DictionaryCog.mydict_add.callback, "", "かい"))[0].startswith("個人辞書に登録できません")
    assert asyncio.run(run(DictionaryCog.mydict_list.callback)) == ["個人辞書（1件 / 上限1件）:\nyomi → よみ"]
    assert asyncio.run(run(DictionaryCog.mydict_del.callback, "yomi")) == ["個人辞書から`yomi`を削除しました"]
    assert asyncio.run(run(DictionaryCog.mydict_del.callback, "yomi")) == ["`yomi`は個人辞書に登録されていません"]
    assert asyncio.run(run(DictionaryCog.mydict_list.callback)) == ["個人辞書（0件 / 上限1件）: まだ登録がありません"]


def test_voice_cog_applies_only_the_speakers_dictionary(tmp_path):
    from test_voice_cog import make_cog, make_config, read_message

    engine = Engine()
    user_store = UserDictionaryStore(tmp_path / "users")
    user_store.add(1, 7, "yomi", "よみかい")  # USER_ID = 7 の辞書
    user_store.add(1, 8, "yomi", "べつのひと")
    cog = make_cog(tmp_path, make_config(), engine, user_store=user_store)
    asyncio.run(read_message(cog, engine, "yomi"))
    assert engine.calls == ["よみかい"]
