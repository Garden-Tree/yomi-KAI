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


# --- 読み込みに失敗したファイル（負のキャッシュ） ---


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _stores(tmp_path, clock):
    from yomi_kai.dictionary import DictionaryStore
    from yomi_kai.jsonfile import FailedLoads
    from yomi_kai.settings import SettingsStore

    return [
        ("dict", DictionaryStore(tmp_path / "dict", failed_loads=FailedLoads(60, clock))),
        ("settings", SettingsStore(tmp_path / "settings", failed_loads=FailedLoads(60, clock))),
    ]


@pytest.mark.parametrize("kind", ["dict", "settings"])
def test_corrupt_file_is_not_reread_on_every_message(tmp_path, monkeypatch, caplog, kind):
    import logging

    clock = Clock()
    store = dict(_stores(tmp_path, clock))[kind]
    path = store.path_for(1)
    path.parent.mkdir()
    path.write_text("{broken", encoding="UTF-8")
    reads = []
    original = jsonfile.read_json_object

    def counting(p):
        reads.append(p)
        return original(p)

    module = __import__(f"yomi_kai.{'dictionary' if kind == 'dict' else 'settings'}", fromlist=["x"])
    monkeypatch.setattr(module, "read_json_object", counting)

    with caplog.at_level(logging.DEBUG):
        for _ in range(5):
            store.get(1)
        assert len(reads) == 1  # 発言のたびには読み直さない
        assert len([r for r in caplog.records if r.exc_info]) == 1

        clock.now += 61
        store.get(1)
        assert len(reads) == 2  # しばらくしたら読み直す
        assert len([r for r in caplog.records if r.exc_info]) == 1  # 同じファイルならトレースバックは1回だけ

        clock.now += 61
        path.write_text("{still broken", encoding="UTF-8")
        os.utime(path, ns=(1, 1))  # 更新時刻を確実に変える
        store.get(1)
        assert len([r for r in caplog.records if r.exc_info]) == 2  # ファイルが変わったらまた出す

        clock.now += 61
        path.write_text("{}", encoding="UTF-8")
        store.get(1)
        assert store.get_cached(1) is not None  # 直ったら通常どおりキャッシュする


@pytest.mark.parametrize("kind", ["dict", "settings"])
def test_aget_skips_thread_while_waiting(tmp_path, monkeypatch, kind):
    import asyncio

    clock = Clock()
    store = dict(_stores(tmp_path, clock))[kind]
    path = store.path_for(1)
    path.parent.mkdir()
    path.write_text("[]", encoding="UTF-8")
    store.get(1)

    async def fail(*args, **kwargs):
        raise AssertionError("読み直さない")

    monkeypatch.setattr(asyncio, "to_thread", fail)
    asyncio.run(store.aget(1))


@pytest.mark.parametrize("kind", ["dict", "settings"])
def test_evict_drops_only_that_guild(tmp_path, kind):
    store = dict(_stores(tmp_path, Clock()))[kind]
    for guild_id in (1, 2):
        store.get(guild_id)
    assert store.get_cached(1) is not None and store.get_cached(2) is not None
    store.evict(1)
    store.evict(3)  # 読み込んでいないサーバーでも例外にしない
    assert store.get_cached(1) is None
    assert store.get_cached(2) is not None
