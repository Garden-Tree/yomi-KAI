"""プラン（フリー / プレミアム）と上限のテスト。"""

import asyncio
import datetime as dt
import json
import os
from types import SimpleNamespace

import pytest

from yomi_kai.cogs.plan import PlanCog, premium_summary
from yomi_kai.config import Config, ConfigError, load_config
from yomi_kai.dictionary import DictionaryStore
from yomi_kai.main import build_plans
from yomi_kai.plans import FREE, PREMIUM, PlanManager, PremiumGuilds, format_limit, format_usage, parse_premium
from yomi_kai.tts import Speaker

NAMES = ["ずんだもん", "春日部つむぎ", "四国めたん", "雨晴はう"]
TODAY = dt.date(2026, 11, 11)


class Engine:
    def __init__(self, names=NAMES):
        self.names = names

    @property
    def default_speaker(self):
        return self.names[0]

    def speakers(self):
        return [Speaker(n, "fake") for n in self.names]

    def synthesize(self, text, speaker, speed=1.0, pitch=0.0):
        return b"wav"


def make_config(**kwargs):
    return Config(discord_token="d", prefix="y.", afk_time=60, sd_msg="x", **kwargs)


def write_premium(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    # 同じ秒に書き直しても更新時刻が変わるようにする
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def premium_config(tmp_path, **kwargs):
    return make_config(
        premium_enabled=True,
        premium_guilds_file=str(tmp_path / "premium.json"),
        max_read_length=100,
        max_dict_entries=200,
        max_user_dict_entries=10,
        speaker_allowlist=("ずんだもん", "春日部つむぎ"),
        **kwargs,
    )


def test_default_config_is_unlimited_except_read_length():
    plans = PlanManager(make_config(), Engine())
    limits = plans.limits_for(1)
    assert limits.plan == FREE
    assert (limits.max_read_length, limits.max_dict_entries, limits.max_user_dict_entries) == (100, 0, 0)
    assert not plans.enabled
    assert plans.speakers_for(1)[1] == frozenset(NAMES)


def test_premium_file_is_ignored_unless_enabled(tmp_path):
    write_premium(tmp_path / "premium.json", {"1": None})
    config = make_config(premium_guilds_file=str(tmp_path / "premium.json"), premium_max_read_length=500)
    plans = PlanManager(config, Engine())
    assert plans.reload() is False
    assert plans.limits_for(1).plan == FREE


def test_premium_guild_gets_premium_limits(tmp_path):
    write_premium(tmp_path / "premium.json", {"1": None, "2": "2026-11-11", "3": "2026-11-10", "4": {"expires": ""}})
    config = premium_config(tmp_path, premium_max_read_length=300, premium_max_dict_entries=1000)
    plans = PlanManager(config, Engine(), today=lambda: TODAY)
    assert plans.reload()
    assert plans.limits_for(1).plan == PREMIUM
    assert plans.limits_for(1).max_read_length == 300
    assert plans.limits_for(1).max_dict_entries == 1000
    assert plans.limits_for(1).max_user_dict_entries == 0  # 空 = 無制限
    assert plans.limits_for(2).is_premium and plans.limits_for(2).expires == TODAY  # 期限の日までは有効
    assert plans.limits_for(3).plan == FREE  # 期限切れ
    assert plans.limits_for(4).is_premium and plans.limits_for(4).expires is None
    assert plans.limits_for(5) == plans.free
    assert plans.limits_for(None) == plans.free


def test_premium_reload_and_broken_file_keeps_previous(tmp_path, caplog):
    path = tmp_path / "premium.json"
    plans = PlanManager(premium_config(tmp_path), Engine())
    plans.reload()  # ファイルが無い = プレミアムなし
    assert not plans.is_premium(1)
    write_premium(path, {"1": None})
    assert plans.reload() and plans.is_premium(1)
    assert plans.reload() is False  # 変わっていなければ読まない
    path.write_text("{broken", encoding="utf-8")
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    assert plans.reload() is False
    assert plans.is_premium(1)  # 書き間違いでプレミアムが外れない
    assert "premium.json" in caplog.text
    path.unlink()
    assert plans.reload() and not plans.is_premium(1)


def test_parse_premium_skips_invalid_entries(caplog):
    parsed = parse_premium({"1": None, "abc": None, "2": "2026-13-01", "3": 5, "4": "2027-01-31"})
    assert parsed == {1: None, 4: dt.date(2027, 1, 31)}
    assert "abc" in caplog.text


def test_premium_guilds_without_file(tmp_path):
    guilds = PremiumGuilds(tmp_path / "missing.json")
    assert guilds.reload_if_changed() is False  # 最初から無い（前回も無い）
    assert guilds.expiry(1) == (False, None)


def test_build_plans_uses_separate_speaker_lists(tmp_path):
    config = premium_config(tmp_path)
    plans = build_plans(config, Engine())
    write_premium(tmp_path / "premium.json", {"1": None})
    plans.reload()
    assert plans.speakers_for(2)[1] == {"ずんだもん", "春日部つむぎ"}
    assert plans.speakers_for(1)[1] == frozenset(NAMES)  # 空 = 読み込んだ全話者
    assert len(plans.base_engine.speakers()) == 4


def test_build_plans_rejects_unknown_premium_speakers(tmp_path):
    config = premium_config(tmp_path, premium_speaker_allowlist=("いない",))
    with pytest.raises(ConfigError, match="PREMIUM_SPEAKER_ALLOWLIST"):
        build_plans(config, Engine())


def test_build_plans_without_premium_shares_free_engine():
    plans = build_plans(make_config(speaker_allowlist=("四国めたん",)), Engine())
    assert plans.premium_engine is plans.free_engine
    assert [s.name for s in plans.base_engine.speakers()] == NAMES


def test_config_reads_premium_keys(tmp_path):
    path = tmp_path / "config.ini"
    path.write_text(
        "[DEFAULT]\nDISCORD_TOKEN = t\nPREFIX = y.\nAFK_TIME = 60\nSD_MSG = x\n"
        "MAX_USER_DICT_ENTRIES = 10\nPREMIUM_ENABLED = True\nPREMIUM_MAX_READ_LENGTH = 300\n"
        "PREMIUM_MAX_DICT_ENTRIES =\nPREMIUM_SPEAKER_ALLOWLIST = ずんだもん、四国めたん\n",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.max_user_dict_entries == 10
    assert config.premium_enabled
    assert config.premium_guilds_file == "settings/premium.json"
    assert config.premium_max_read_length == 300
    assert config.premium_max_dict_entries == 0
    assert config.premium_speaker_allowlist == ("ずんだもん", "四国めたん")


def test_config_rejects_negative_user_dict_limit(tmp_path):
    path = tmp_path / "config.ini"
    path.write_text(
        "[DEFAULT]\nDISCORD_TOKEN = t\nPREFIX = y.\nAFK_TIME = 60\nSD_MSG = x\nMAX_USER_DICT_ENTRIES = -1\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="MAX_USER_DICT_ENTRIES"):
        load_config(path)


def test_formatting():
    assert format_limit(0, "文字") == "無制限"
    assert format_limit(100, "文字") == "100文字"
    assert format_usage(3, 0) == "3件（無制限）"
    assert format_usage(3, 10) == "3 / 10件"


class FakeCtx:
    def __init__(self, guild_id=1):
        self.interaction = None
        self.guild = SimpleNamespace(id=guild_id)
        self.author = SimpleNamespace(id=7)
        self.sent = []

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


def run_plan(cog, guild_id):
    ctx = FakeCtx(guild_id)
    asyncio.run(PlanCog.plan.callback(cog, ctx))
    embed = ctx.sent[0][1]["embed"]
    return {field.name: field.value for field in embed.fields}


def test_plan_command_hides_premium_when_disabled(tmp_path):
    store = DictionaryStore(tmp_path / "dict")
    store.add(1, "単語", "たんご")
    config = make_config(max_dict_entries=200)
    cog = PlanCog(SimpleNamespace(), config, PlanManager(config, Engine()), store)
    fields = run_plan(cog, 1)
    assert "プラン" not in fields and "プレミアムでは" not in fields
    assert fields["読み上げる文字数"] == "100文字"
    assert fields["サーバー辞書"] == "1 / 200件"
    assert fields["個人辞書（あなた）"] == "無制限"
    assert fields["話者"] == "4種類"


def test_plan_command_shows_premium_when_enabled(tmp_path):
    config = premium_config(tmp_path, premium_max_dict_entries=1000)
    plans = build_plans(config, Engine())
    write_premium(tmp_path / "premium.json", {"2": "2026-12-31"})
    plans.reload()
    cog = PlanCog(SimpleNamespace(), config, plans, DictionaryStore(tmp_path / "dict"))
    free = run_plan(cog, 1)
    assert free["プラン"] == "フリー"
    assert free["話者"] == "2種類"
    assert free["プレミアムでは"] == premium_summary(plans.premium, 4)
    assert "1000件" in free["プレミアムでは"]
    premium = run_plan(cog, 2)
    assert premium["プラン"] == "プレミアム（2026-12-31 まで）"
    assert "プレミアムでは" not in premium
