"""ログ設定。コンソールは INFO 以上、log/ 配下のファイルは DEBUG 以上を出力する。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

LOG_FORMAT = "[%(asctime)s] %(name)s:%(lineno)s %(funcName)s [%(levelname)s]: %(message)s"
KEEP_LOG_FILES = 50


def prune_logs(log_dir: Path, keep: int = KEEP_LOG_FILES) -> None:
    """古いログファイルを削除し、新しいものから ``keep`` 個だけ残す。

    ファイル名は ``%Y-%m-%d_%H%M%S.log`` なので名前順 = 時系列順。
    """
    files = sorted(log_dir.glob("*.log"))
    for old in files[: max(0, len(files) - keep)]:
        try:
            old.unlink()
        except OSError:
            pass


def setup_logging(log_dir: str | Path = "log") -> Path:
    """ルートロガーを設定し、今回のログファイルのパスを返す。"""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"{datetime.now():%Y-%m-%d_%H%M%S}.log"

    formatter = logging.Formatter(LOG_FORMAT)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)

    file_handler = logging.FileHandler(log_file, encoding="UTF-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    logging.basicConfig(level=logging.NOTSET, handlers=[console, file_handler])

    # voicevox_core 内部の動作に影響のない警告ログ（バージョン差異など）を抑制
    logging.getLogger("voicevox_core").setLevel(logging.ERROR)

    # 今回のファイルを含めて最新50個を残す
    prune_logs(log_dir)
    return log_file
