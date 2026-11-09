import os
import stat

import pytest

import yomi_kai.jsonfile as jsonfile
from yomi_kai.jsonfile import read_json_object, write_json_atomic


def test_directory_is_synced_after_replace(tmp_path, monkeypatch):
    calls = []

    def record(directory):
        # 差し替えが済んでからディレクトリを同期する
        calls.append((directory, read_json_object(directory / "a.json")))

    monkeypatch.setattr(jsonfile, "_fsync_directory", record)
    write_json_atomic(tmp_path / "a.json", {"x": 1})
    assert calls == [(tmp_path, {"x": 1})]


@pytest.mark.skipif(os.name != "posix", reason="POSIX の権限のみ")
def test_written_file_is_world_readable(tmp_path):
    # mkstemp は 0600 で作るが、通常のファイルと同じ 0644 にする（ホスト側の別ユーザーからも読める）
    path = tmp_path / "a.json"
    write_json_atomic(path, {})
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


@pytest.mark.skipif(os.name != "posix", reason="POSIX のみ")
def test_directory_fsync_failure_does_not_fail_write(tmp_path, monkeypatch):
    real_fsync = os.fsync

    def fsync(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("not supported")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fsync)
    write_json_atomic(tmp_path / "a.json", {"x": 1})
    assert read_json_object(tmp_path / "a.json") == {"x": 1}
