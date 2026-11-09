"""使用量の計測（UsageStore・python -m yomi_kai.usage）のテスト。"""

import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from yomi_kai.config import Config
from yomi_kai.dictionary import DictionaryStore
from yomi_kai.main import YomiBot
from yomi_kai.settings import SettingsStore
from yomi_kai.usage import UsageStore, format_report, main

GUILD_A = 111
GUILD_B = 222
ALICE = 1
BOB = 2


class Clock:
    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment


def read_json(path):
    with open(path, encoding="UTF-8") as f:
        return json.load(f)


def make_store(tmp_path, moment=datetime(2026, 11, 9, 12, 0)):
    clock = Clock(moment)
    return UsageStore(tmp_path / "usage", clock=clock), clock


def test_counts_per_guild_and_user(tmp_path):
    store, _ = make_store(tmp_path)
    store.record(GUILD_A, ALICE, 10)
    store.record(GUILD_A, ALICE, 5)
    store.record(GUILD_A, BOB, 3)
    store.record(GUILD_B, ALICE, 7)
    store.record_system(GUILD_A, 9)
    assert not (tmp_path / "usage").exists()  # 記録だけでは書き込まない

    store.flush()
    data = read_json(tmp_path / "usage" / "2026-11.json")
    assert data == {
        "guilds": {
            str(GUILD_A): {
                "chars": 18,
                "messages": 3,
                "system_chars": 9,
                "users": {str(ALICE): {"chars": 15, "messages": 2}, str(BOB): {"chars": 3, "messages": 1}},
            },
            str(GUILD_B): {"chars": 7, "messages": 1, "users": {str(ALICE): {"chars": 7, "messages": 1}}},
        }
    }


def test_never_stores_text(tmp_path):
    store, _ = make_store(tmp_path)
    store.record(GUILD_A, ALICE, len("ひみつ"))
    store.flush()
    assert "ひみつ" not in (tmp_path / "usage" / "2026-11.json").read_text(encoding="UTF-8")


def test_flush_adds_to_existing_file_and_survives_restart(tmp_path):
    store, _ = make_store(tmp_path)
    store.record(GUILD_A, ALICE, 10)
    store.flush()
    store.record(GUILD_A, ALICE, 1)
    store.flush()
    # 再起動後の新しいストアも、ファイルの値に足していく
    restarted, _ = make_store(tmp_path)
    restarted.record(GUILD_A, BOB, 2)
    restarted.flush()
    guild = read_json(tmp_path / "usage" / "2026-11.json")["guilds"][str(GUILD_A)]
    assert guild["chars"] == 13 and guild["messages"] == 3
    assert guild["users"][str(ALICE)] == {"chars": 11, "messages": 2}


def test_month_rollover(tmp_path):
    store, clock = make_store(tmp_path, datetime(2026, 11, 30, 23, 59))
    store.record(GUILD_A, ALICE, 4)
    clock.moment = datetime(2026, 12, 1, 0, 0)
    store.record(GUILD_A, ALICE, 6)
    store.flush()
    assert read_json(tmp_path / "usage" / "2026-11.json")["guilds"][str(GUILD_A)]["chars"] == 4
    assert read_json(tmp_path / "usage" / "2026-12.json")["guilds"][str(GUILD_A)]["chars"] == 6


def test_flush_without_changes_writes_nothing(tmp_path):
    store, _ = make_store(tmp_path)
    assert not store.dirty
    store.flush()
    assert not (tmp_path / "usage").exists()


def test_failed_write_is_kept_for_next_flush(tmp_path, monkeypatch):
    store, _ = make_store(tmp_path)
    store.record(GUILD_A, ALICE, 5)

    import yomi_kai.usage as usage

    original = usage.write_json_atomic

    def broken(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(usage, "write_json_atomic", broken)
    store.flush()
    assert store.dirty
    store.record(GUILD_A, ALICE, 1)
    monkeypatch.setattr(usage, "write_json_atomic", original)
    store.flush()
    assert not store.dirty
    assert read_json(tmp_path / "usage" / "2026-11.json")["guilds"][str(GUILD_A)]["chars"] == 6


@pytest.mark.parametrize("content", ["{broken", "[]", '{"guilds": {"1": {"chars": "many"}}}'])
def test_corrupt_file_is_renamed_not_overwritten(tmp_path, content):
    store, _ = make_store(tmp_path)
    path = tmp_path / "usage" / "2026-11.json"
    path.parent.mkdir()
    path.write_text(content, encoding="UTF-8")

    store.record(GUILD_A, ALICE, 5)
    store.flush()

    backups = list(path.parent.glob("2026-11.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="UTF-8") == content
    assert read_json(path)["guilds"][str(GUILD_A)]["chars"] == 5


def test_unreadable_file_is_not_renamed_and_delta_is_kept(tmp_path, monkeypatch):
    # 読めないだけ（権限など）のファイルは壊れているとはみなさず、退避も数え直しもしない
    store, _ = make_store(tmp_path)
    path = tmp_path / "usage" / "2026-11.json"
    path.parent.mkdir()
    existing = {"guilds": {str(GUILD_A): {"chars": 10, "messages": 1, "users": {}}}}
    path.write_text(json.dumps(existing), encoding="UTF-8")

    import yomi_kai.usage as usage

    original = usage.read_json_object

    def unreadable(p):
        raise PermissionError("denied")

    monkeypatch.setattr(usage, "read_json_object", unreadable)
    store.record(GUILD_A, ALICE, 5)
    store.flush()
    assert store.dirty
    assert not list(path.parent.glob("2026-11.json.corrupt-*"))

    monkeypatch.setattr(usage, "read_json_object", original)
    store.flush()
    assert not store.dirty
    assert read_json(path)["guilds"][str(GUILD_A)]["chars"] == 15


def test_periodic_flusher_batches_writes(tmp_path):
    store, _ = make_store(tmp_path)
    path = tmp_path / "usage" / "2026-11.json"

    async def run():
        task = asyncio.create_task(store.run_flusher(interval=0.05))
        store.record(GUILD_A, ALICE, 1)
        store.record(GUILD_A, ALICE, 2)
        assert not path.exists()  # 記録した時点では書き込まない
        deadline = asyncio.get_running_loop().time() + 5
        while not path.exists():
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())
    assert read_json(path)["guilds"][str(GUILD_A)] == {
        "chars": 3,
        "messages": 2,
        "users": {str(ALICE): {"chars": 3, "messages": 2}},
    }


# --- 集計 CLI ---


def write_month(tmp_path, month, guilds):
    path = tmp_path / f"{month}.json"
    path.write_text(json.dumps({"guilds": guilds}), encoding="UTF-8")


def test_cli_prints_totals_and_top_guilds(tmp_path, capsys):
    guilds = {
        str(1000 + i): {"chars": i * 10, "messages": i, "users": {str(i): {"chars": i * 10, "messages": i}}}
        for i in range(1, 13)
    }
    guilds["1001"]["users"]["99"] = {"chars": 0, "messages": 0}
    guilds["1002"]["users"]["3"] = {"chars": 1, "messages": 1}  # 別サーバーの同じユーザーは1人と数える
    write_month(tmp_path, "2026-10", guilds)

    assert main(["2026-10", "--dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "サーバー数: 12" in out
    assert "利用ユーザー数: 12" in out
    assert f"読み上げ文字数: {sum(i * 10 for i in range(1, 13))}" in out
    assert f"読み上げメッセージ数: {sum(range(1, 13))}" in out
    ranking = [line for line in out.splitlines() if "文字 /" in line]
    assert len(ranking) == 10
    assert ranking[0].split()[1] == "1012" and ranking[-1].split()[1] == "1003"


def test_cli_defaults_to_current_month_and_handles_missing(tmp_path, capsys):
    assert main(["--dir", str(tmp_path)], now=lambda: datetime(2026, 11, 9)) == 0
    assert capsys.readouterr().out.strip() == "2026-11: 記録はありません"


def test_cli_rejects_bad_month_and_corrupt_file(tmp_path, capsys):
    assert main(["2026-13", "--dir", str(tmp_path)]) == 2
    (tmp_path / "2026-10.json").write_text("{broken", encoding="UTF-8")
    assert main(["2026-10", "--dir", str(tmp_path)]) == 1


def test_format_report_without_data():
    assert format_report("2026-01", None) == "2026-01: 記録はありません"


def test_cli_does_not_import_discord_or_voicevox(tmp_path):
    code = (
        "import sys, yomi_kai.usage as u; u.main(['--dir', sys.argv[1]]); "
        "bad = [m for m in sys.modules if m.split('.')[0] in ('discord', 'voicevox_core')]; "
        "sys.exit(1 if bad else 0)"
    )
    root = Path(__file__).parents[1]
    result = subprocess.run([sys.executable, "-c", code, str(tmp_path)], capture_output=True, cwd=root)
    assert result.returncode == 0, result.stderr


def make_bot(tmp_path, **kwargs):
    config = Config(
        discord_token="dummy", prefix="y.", afk_time=60, sd_msg="x", usage_dir=str(tmp_path / "usage"), **kwargs
    )
    return YomiBot(config, object(), DictionaryStore(tmp_path / "dict"), SettingsStore(tmp_path / "settings"))


def test_bot_does_not_record_usage_by_default(tmp_path):
    bot = make_bot(tmp_path)
    assert bot.usage is None
    asyncio.run(bot.close())
    assert not (tmp_path / "usage").exists()


def test_bot_records_usage_when_enabled(tmp_path):
    bot = make_bot(tmp_path, record_usage=True)
    assert isinstance(bot.usage, UsageStore)
    bot.usage.record(GUILD_A, ALICE, 7)
    asyncio.run(bot.close())
    month = datetime.now().strftime("%Y-%m")
    assert read_json(tmp_path / "usage" / f"{month}.json")["guilds"][str(GUILD_A)]["chars"] == 7
