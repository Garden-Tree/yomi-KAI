import pytest

from yomi_kai.text import (
    JOIN,
    LEAVE,
    announce_text,
    build_read_text,
    chunk_lines,
    compile_dictionary,
    gen_sd_text,
    strip_markdown,
    voice_transition,
)

# --- 辞書置換 ---


def test_dictionary_replacement():
    d = compile_dictionary({"w": "わら", "yomi": "よみ"})
    assert build_read_text("yomiw", d) == "よみわら"


def test_dictionary_longest_key_first():
    d = compile_dictionary({"東京": "とうきょう", "東京都": "とうきょうと"})
    assert build_read_text("東京都と東京", d) == "とうきょうとととうきょう"


def test_dictionary_keys_are_literal_not_regex():
    d = compile_dictionary({"a.b": "えーびー", "{0}": "ぜろ"})
    assert build_read_text("axb a.b {0}", d) == "axb えーびー ぜろ"


def test_empty_dictionary_is_noop():
    assert build_read_text("hello", compile_dictionary({})) == "hello"
    assert build_read_text("hello", None) == "hello"


def test_dictionary_applied_before_other_rules():
    # 辞書で生成された URL も URL 置換の対象になる（従来の処理順）
    d = compile_dictionary({"公式": "https://example.com"})
    assert build_read_text("公式", d) == "URL"


# --- URL ---


def test_url_at_end():
    assert build_read_text("見て https://example.com/a?b=c") == "見て URL"


def test_url_keeps_following_whitespace():
    # URL 直後の空白は残す（旧実装は空白まで置換していた）
    assert build_read_text("https://example.com これ") == "URL これ"


def test_multiple_urls():
    assert build_read_text("http://a.jp と https://b.jp") == "URL と URL"


# --- ネタバレ ---


def test_spoiler():
    assert build_read_text("犯人は||ヤス||です") == "犯人はネタバレです"


def test_multiline_spoiler():
    assert build_read_text("||一行目\n二行目||") == "ネタバレ"


# --- メンション ---


def test_user_mention_resolved():
    names = {1: "太郎"}
    assert build_read_text("<@1> <@!1>", user_name=names.get) == "アット太郎 アット太郎"


def test_user_mention_unknown():
    assert build_read_text("<@999>", user_name=lambda _: None) == "アット不明なユーザー"
    assert build_read_text("<@999>") == "アット不明なユーザー"


def test_role_and_channel_mentions():
    out = build_read_text(
        "<@&5> <#7> <@&6> <#8>",
        role_name={5: "管理者"}.get,
        channel_name={7: "雑談"}.get,
    )
    assert out == "アット管理者 雑談 アット不明なロール 不明なチャンネル"


# --- 絵文字 ---


def test_custom_emoji():
    assert build_read_text("<:smile:123>と<a:party_parrot:456>") == "smileとparty_parrot"


# --- マークダウン ---


def test_asterisk_italic_and_bold():
    assert build_read_text("*斜体* と **太字** と ***両方***") == "斜体 と 太字 と 両方"


def test_asterisk_inside_japanese_sentence():
    assert build_read_text("これは*強調*です") == "これは強調です"


def test_asterisk_arithmetic_untouched():
    assert build_read_text("2 * 3 * 4") == "2 * 3 * 4"


def test_underscore_italic_and_underline():
    assert build_read_text("_斜体_ と __下線__") == "斜体 と 下線"


def test_underscore_italic_in_japanese_sentence():
    assert build_read_text("これは_強調_です") == "これは強調です"


def test_snake_case_untouched():
    assert build_read_text("snake_case_name") == "snake_case_name"
    assert build_read_text("変数 my_var_name を使う") == "変数 my_var_name を使う"
    assert strip_markdown("a_b_c d_e_f") == "a_b_c d_e_f"


def test_emoji_name_with_underscores_untouched():
    assert build_read_text("<:my_cool_emoji:1>") == "my_cool_emoji"


# --- 突然の死 ---


def test_gen_sd_text():
    assert gen_sd_text("word") == "＿人人人人＿\n＞　word　＜\n￣Y^Y^Y^Y￣"
    assert gen_sd_text("突然の死") == "＿人人人人人人＿\n＞　突然の死　＜\n￣Y^Y^Y^Y^Y^Y￣"


# --- 入退室 ---

BOT_VC = 10
OTHER_VC = 20


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (None, BOT_VC, JOIN),  # 接続
        (BOT_VC, None, LEAVE),  # 切断
        (OTHER_VC, BOT_VC, JOIN),  # 別のチャンネルから移動してきた
        (BOT_VC, OTHER_VC, LEAVE),  # 別のチャンネルへ移動した
        (None, OTHER_VC, None),  # 関係ないチャンネル
        (OTHER_VC, None, None),
        (OTHER_VC, 30, None),
        (BOT_VC, BOT_VC, None),  # ミュート・配信などの変更
        (None, None, None),
    ],
)
def test_voice_transition(before, after, expected):
    assert voice_transition(before, after, BOT_VC, is_bot=False) == expected


def test_voice_transition_ignores_bots_and_unconnected():
    assert voice_transition(None, BOT_VC, BOT_VC, is_bot=True) is None
    assert voice_transition(None, BOT_VC, None, is_bot=False) is None


def test_announce_text():
    assert announce_text(JOIN, "たろう") == "たろうさんが入室しました"
    assert announce_text(LEAVE, "たろう") == "たろうさんが退室しました"


# --- 長いメッセージの分割 ---


def test_chunk_lines():
    assert chunk_lines(["a", "b", "c"], limit=3) == ["a\nb", "c"]
    assert chunk_lines(["abc", "d"], limit=3) == ["abc", "d"]
    assert chunk_lines(["abcdefg", "h"], limit=3) == ["abc", "def", "g\nh"]
    assert chunk_lines([]) == []
    lines = [f"話者{i}" for i in range(1000)]
    chunks = chunk_lines(lines)
    assert all(len(c) <= 1900 for c in chunks)
    assert "\n".join(chunks).split("\n") == lines
