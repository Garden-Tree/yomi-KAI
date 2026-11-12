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


def test_dictionary_does_not_break_mentions_or_custom_emojis():
    d = compile_dictionary({"1": "いち", "@": "あっと", "smile": "すまいる", "<": "しょうなり"})
    names = {123: "alice"}
    text = build_read_text(
        "<@123> <@!123> <@&1> <#1> <:smile:111> 1 smile",
        d,
        user_name=names.get,
        role_name=lambda i: "role",
        channel_name=lambda i: "general",
    )
    assert text == "アットalice アットalice アットrole general smile いち すまいる"


def test_empty_or_blank_dictionary_keys_are_ignored():
    d = compile_dictionary({"": "から", "  ": "くうはく", "w": "わら"})
    assert build_read_text("abw", d) == "abわら"
    assert compile_dictionary({"": "から"}).pattern is None


def test_dictionary_expansion_is_bounded():
    from yomi_kai.text import MAX_EXPANDED_LENGTH

    d = compile_dictionary({"あ": "い" * 2000})
    expanded = d.apply("あ" * 4000, limit=MAX_EXPANDED_LENGTH)
    assert len(expanded) == MAX_EXPANDED_LENGTH
    # 最終的な長さは max_length で切り詰める
    assert build_read_text("あ" * 4000, d, max_length=100) == "い" * 100 + " 以下略"
    assert len(build_read_text("あ" * 4000, d)) == MAX_EXPANDED_LENGTH


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


ZWJ = "\N{ZERO WIDTH JOINER}"
VS16 = "\N{VARIATION SELECTOR-16}"
KEYCAP = "\N{COMBINING ENCLOSING KEYCAP}"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("おはよう😀", "おはよう"),
        ("いいね👍🏻", "いいね"),  # 肌の色の修飾子
        ("好き❤" + VS16, "好き"),  # 異体字セレクタ付きの ❤️
        ("日本🇯🇵", "日本"),  # 国旗（地域指示文字2つ）
        ("家族👨" + ZWJ + "👩" + ZWJ + "👧", "家族"),  # ZWJ でつないだ組み合わせ
        ("完了✅ すごい✨ 注意⚠" + VS16, "完了 すごい 注意"),
        ("1" + VS16 + KEYCAP + "番", "1番"),  # キーキャップは数字だけ残す
    ],
)
def test_unicode_emoji_are_removed(text, expected):
    assert build_read_text(text) == expected


def test_text_symbols_without_emoji_presentation_are_kept():
    # ★ や ♪、矢印などは絵文字として表示されないので残す
    assert build_read_text("★きらきら♪ →次へ") == "★きらきら♪ →次へ"


def test_emoji_only_message_becomes_empty():
    assert build_read_text("😀👍🏻🇯🇵").strip() == ""
    # 添付ファイルがあれば説明だけを読む
    assert build_read_text("😀", attachments="画像") == "画像"


def test_dictionary_can_still_read_emoji():
    # 絵文字は辞書のあとに取り除くので、辞書に登録した絵文字は読みが使われる
    d = compile_dictionary({"😀": "にっこり"})
    assert build_read_text("おはよう😀👍", d) == "おはようにっこり"


# --- タイムスタンプ・スラッシュコマンド ---


@pytest.mark.parametrize("style", ["", ":t", ":T", ":d", ":D", ":f", ":F", ":R"])
def test_timestamp_is_read_as_jikoku(style):
    assert build_read_text(f"開始は<t:1700000000{style}>です") == "開始は時刻です"


def test_command_mentions_read_command_name():
    assert build_read_text("</help:123456>を見て") == "helpを見て"
    assert build_read_text("</dict list:123456>で確認") == "dict listで確認"
    assert build_read_text("</my_cmd sub-a:1>") == "my_cmd sub-a"


def test_dictionary_does_not_break_timestamps_or_command_mentions():
    d = compile_dictionary({"1": "いち", "t": "てぃー", "help": "へるぷ", ":": "ころん", "/": "すらっしゅ"})
    text = build_read_text("<t:1700000000:R> </help:123> help 1", d)
    assert text == "時刻 help へるぷ いち"


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
