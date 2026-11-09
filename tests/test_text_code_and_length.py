"""B-8 コードブロック除去・B-3 文字数制限・B-9 添付ファイルのテスト。"""

from yomi_kai.text import (
    CODE_BLOCK_TEXT,
    TRUNCATED_SUFFIX,
    build_read_text,
    compile_dictionary,
    describe_attachments,
    replace_code,
    truncate,
)

# --- B-8 コードブロック ---


def test_code_block_is_replaced():
    assert build_read_text("見て\n```\nprint('hi')\n```\nどう？") == f"見て\n{CODE_BLOCK_TEXT}\nどう？"


def test_code_block_with_language():
    assert replace_code("```python\nx = 1\n```") == CODE_BLOCK_TEXT


def test_multiple_code_blocks_are_replaced_separately():
    assert replace_code("```a```と```b```") == f"{CODE_BLOCK_TEXT}と{CODE_BLOCK_TEXT}"


def test_unclosed_code_block_is_left_alone():
    assert replace_code("```閉じてない") == "```閉じてない"


def test_inline_code_keeps_content():
    assert build_read_text("`git status` を打って") == "git status を打って"
    assert replace_code("``a`b``") == "a`b"  # 二重バッククォートの中の ` は中身
    assert replace_code("`a` と `b`") == "a と b"


def test_code_is_not_touched_by_dictionary_or_url_rules():
    d = compile_dictionary({"print": "ぷりんと"})
    text = "```\nprint('https://example.com')\n```"
    assert build_read_text(text, d) == CODE_BLOCK_TEXT


# --- B-3 文字数制限 ---


def test_truncate_over_limit():
    assert truncate("あいうえおかきくけこ", 5) == "あいうえお" + TRUNCATED_SUFFIX


def test_truncate_exact_limit_is_kept():
    assert truncate("あいうえお", 5) == "あいうえお"


def test_truncate_zero_means_unlimited():
    long = "あ" * 1000
    assert truncate(long, 0) == long


def test_truncate_strips_trailing_space_before_suffix():
    assert truncate("ab   cd", 4) == "ab" + TRUNCATED_SUFFIX


def test_limit_counts_text_after_processing():
    # URL は「URL」の3文字として数える
    text = "https://example.com/very/long/path/that/is/long あいう"
    assert build_read_text(text, max_length=7) == "URL あいう"


def test_build_read_text_default_is_unlimited():
    long = "あ" * 500
    assert build_read_text(long) == long


# --- B-9 添付ファイル・スタンプ ---


def test_attachment_kinds():
    assert describe_attachments([("image/png", "a.png")]) == "画像"
    assert describe_attachments([("video/mp4", "a.mp4")]) == "動画"
    assert describe_attachments([("audio/ogg", "voice-message.ogg")]) == "音声"
    assert describe_attachments([("application/pdf", "a.pdf")]) == "ファイル"
    assert describe_attachments([]) == ""


def test_attachment_counts_and_order():
    attachments = [
        ("application/zip", "a.zip"),
        ("image/png", "1.png"),
        ("video/mp4", "v.mp4"),
        ("image/jpeg", "2.jpg"),
        ("image/gif", "3.gif"),
        ("text/plain", "b.txt"),
    ]
    assert describe_attachments(attachments) == "画像3枚 動画 ファイル2件"


def test_attachment_kind_falls_back_to_extension():
    assert describe_attachments([(None, "photo.JPG"), (None, "clip.mov"), (None, "noext")]) == "画像 動画 ファイル"


def test_stickers():
    assert describe_attachments([], ["ねこ"]) == "スタンプ ねこ"
    assert describe_attachments([("image/png", "a.png")], ["ねこ", "いぬ"]) == "画像 スタンプ ねこ スタンプ いぬ"


def test_attachments_are_appended_to_text():
    assert build_read_text("見て", attachments="画像") == "見て 画像"


def test_attachments_only_message():
    assert build_read_text("", attachments="画像2枚") == "画像2枚"
    assert build_read_text("  ", attachments="スタンプ ねこ") == "スタンプ ねこ"


def test_truncation_applies_to_text_with_attachments():
    assert build_read_text("あいうえお", max_length=7, attachments="画像2枚") == "あいうえお 画" + TRUNCATED_SUFFIX
    assert build_read_text("あ" * 10, max_length=5, attachments="画像") == "あ" * 5 + TRUNCATED_SUFFIX
