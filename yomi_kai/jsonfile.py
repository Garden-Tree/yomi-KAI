"""JSON ファイルの読み書きヘルパー（辞書・設定ストアで共有）。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

# 書き込んだファイルの権限（mkstemp は 0600 で作るため、通常のファイルと同じにそろえる）
FILE_MODE = 0o644


def read_json_object(path: Path) -> dict[str, Any]:
    """JSON オブジェクトを読む。ファイルが無ければ空。壊れていれば例外（OSError / ValueError）を送出する。"""
    if not path.is_file():
        return {}
    with path.open("r", encoding="UTF-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} の形式が不正です（オブジェクトではありません）")
    return data


def _fsync_directory(directory: Path) -> None:
    """os.replace による差し替えをディスクへ確実に反映する（POSIX のみ。Windows ではディレクトリを開けない）。"""
    if os.name != "posix":
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # 差し替え自体は済んでいるので、ここで失敗しても保存の失敗とはしない
    finally:
        os.close(fd)


def write_json_atomic(path: Path, data: Any) -> None:
    """一時ファイルに書いてから os.replace で差し替える（書き込み途中で壊れない）。

    形式は indent=2, ensure_ascii=False。親ディレクトリが無ければ作成する。
    """
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="UTF-8") as f:
            f.write(json.dumps(data, indent=2, ensure_ascii=False))
            f.flush()
            os.fsync(f.fileno())
        if os.name == "posix":
            os.chmod(tmp_name, FILE_MODE)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    _fsync_directory(directory)
