"""合成用の分割（split_for_synthesis）のテスト。"""

import pytest

from yomi_kai.text import split_for_synthesis

LONG_NO_PUNCT = "あ" * 100


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # 文末で区切り、記号は前のかたまりに含める
        ("こんにちは。元気ですか？はい！", ["こんにちは。", "元気ですか？", "はい！"]),
        ("Hello! How are you? Fine.", ["Hello!", "How are you?", "Fine."]),
        # 「！？」や「…」の連続の途中では区切らない
        ("えっ！？本当に…？うん。", ["えっ！？", "本当に…？", "うん。"]),
        ("まじで！！！！すごい", ["まじで！！！！", "すごい"]),
        ("そう。。。なるほど", ["そう。。。", "なるほど"]),
        ("えーと…それで…", ["えーと…それで…"]),  # 「…」だけでは区切らない
        # 閉じ括弧は前のかたまりに含める
        ("「すごい！」と言った。次。", ["「すごい！」", "と言った。", "次。"]),
        ("（笑）。はい", ["（笑）。", "はい"]),
        ("『終わり。』続き", ["『終わり。』", "続き"]),
        # 改行では必ず区切り、空行・空白だけの行は捨てる
        ("一行目\n\n  \n二行目", ["一行目", "二行目"]),
        ("一行目\r\n二行目", ["一行目", "二行目"]),
        # 記号だけのかたまりは作らない（前に連結する）
        ("そうなんだ\n！？", ["そうなんだ！？"]),
        ("！？", ["！？"]),
        # 句読点も改行もなければ、長くても1つのまま
        (LONG_NO_PUNCT, [LONG_NO_PUNCT]),
        ("", []),
        ("   ", []),
    ],
)
def test_split(text, expected):
    assert split_for_synthesis(text) == expected


def test_short_sentence_keeps_commas():
    # 上限以下なら読点では区切らない（読点ごとに区切るとイントネーションがリセットされる）
    text = "今日は、晴れて、暖かいです。"
    assert split_for_synthesis(text, soft_limit=40) == [text]


def test_long_sentence_splits_at_last_comma_before_limit():
    text = "あいうえお、かきくけこ、さしすせそ、たちつてと。"
    # 上限 13 → 13文字以内で最後の読点（「かきくけこ、」の後、12文字目）で区切る
    assert split_for_synthesis(text, soft_limit=13) == ["あいうえお、かきくけこ、", "さしすせそ、たちつてと。"]


def test_long_sentence_splits_repeatedly():
    text = "あいうえお、かきくけこ、さしすせそ、たちつてと。"
    assert split_for_synthesis(text, soft_limit=7) == ["あいうえお、", "かきくけこ、", "さしすせそ、", "たちつてと。"]


def test_uses_first_comma_after_limit_when_none_before():
    text = "あ" * 10 + "、" + "い" * 3 + "、" + "う" * 3
    # 7文字以内に読点がないので、上限の後の最初の読点で区切る。残り（7文字）は上限以内なのでそのまま
    assert split_for_synthesis(text, soft_limit=7) == ["あ" * 10 + "、", "い" * 3 + "、" + "う" * 3]


def test_comma_at_end_does_not_create_empty_chunk():
    text = "あ" * 10 + "、"
    assert split_for_synthesis(text, soft_limit=5) == [text]


def test_leading_comma_is_not_a_split_point():
    text = "、" + "あ" * 10 + "、いいい"
    assert split_for_synthesis(text, soft_limit=5) == ["、" + "あ" * 10 + "、", "いいい"]


def test_fullwidth_comma_and_closing_bracket_after_comma():
    text = "あ" * 8 + "，」" + "い" * 8
    assert split_for_synthesis(text, soft_limit=5) == ["あ" * 8 + "，」", "い" * 8]


def test_chunks_reassemble_to_original_without_whitespace():
    text = "長い文章を読み上げます。最初の文、二つ目の節、三つ目の節、四つ目の節が続きます！\n次の行です…？終わり"
    chunks = split_for_synthesis(text, soft_limit=12)
    assert "".join(chunks) == text.replace("\n", "")
    assert all(chunks)
