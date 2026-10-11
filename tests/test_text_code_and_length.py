"""B-8 コードブロック除去と B-3 文字数制限のテスト。"""

from yomi_kai.text import (
    CODE_BLOCK_TEXT,
    TRUNCATED_SUFFIX,
    build_read_text,
    compile_dictionary,
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
