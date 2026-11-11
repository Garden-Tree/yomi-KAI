"""Stripe の契約の同期（premium_sync）と、申し込み・契約の管理のリンク（premium_link）のテスト。

HTTP はモックし、Stripe・サイト・Discord には接続しない。
"""

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import patch

import discord
import pytest
from test_commands import StubEngine
from test_config import load

from yomi_kai.cogs.plan import DM_FAILED_NOTICE, DM_SENT_NOTICE, PREMIUM_LINK_LABEL, PlanCog
from yomi_kai.config import Config, ConfigError
from yomi_kai.dictionary import DictionaryStore
from yomi_kai.main import YomiBot
from yomi_kai.plans import PlanManager
from yomi_kai.premium_link import premium_url, sign_link_token
from yomi_kai.premium_sync import (
    PremiumSyncer,
    PremiumSyncError,
    apply_sync,
    http_fetcher,
    merge_premium,
    parse_sync_response,
)
from yomi_kai.settings import SettingsStore

SYNC_URL = "https://site.example.com/api/premium-guilds"
SYNC_TOKEN = "sync-token-0123456789abcdef0123456789"
SITE_URL = "https://site.example.com"
LINK_SECRET = "test-link-secret-0123456789abcdef"

# --- 署名付きリンク（サイト側の tests/premium-token.test.ts と同じテストベクトル） ---


def test_link_token_matches_the_site_test_vector():
    token = sign_link_token(LINK_SECRET, 123456789012345678, now=lambda: 1767225600.9)
    assert token == "v1.123456789012345678.1767229200.4AYTGHjGOk35L8n2NWtEKdW2lHOXUjmFAQX41sxaWTk"


def test_link_token_depends_on_guild_expiry_and_secret():
    now = lambda: 1767225600  # noqa: E731
    base = sign_link_token(LINK_SECRET, 1, now=now)
    assert sign_link_token(LINK_SECRET, 2, now=now).split(".")[3] != base.split(".")[3]
    assert sign_link_token(LINK_SECRET, 1, ttl=60, now=now) != base
    assert sign_link_token(LINK_SECRET + "x", 1, now=now) != base
    assert LINK_SECRET not in base


def test_link_token_rejects_short_secret_and_bad_guild():
    with pytest.raises(ValueError):
        sign_link_token("short", 1)
    with pytest.raises(ValueError):
        sign_link_token(LINK_SECRET, 0)


def test_premium_url():
    url = premium_url(SITE_URL + "/", LINK_SECRET, 42, now=lambda: 1000)
    assert url.startswith("https://site.example.com/premium/?t=v1.42.4600.")


# --- 設定 ---


def test_premium_sync_and_link_are_off_by_default(tmp_path):
    config = load(tmp_path)
    assert config.premium_sync_url is None and config.premium_sync_token is None
    assert config.premium_site_url is None and config.premium_link_secret is None


def test_premium_sync_and_link_settings(tmp_path):
    config = load(
        tmp_path,
        f"PREMIUM_SYNC_URL = {SYNC_URL}\nPREMIUM_SYNC_TOKEN = {SYNC_TOKEN}\n"
        f"PREMIUM_SITE_URL = {SITE_URL}\nPREMIUM_LINK_SECRET = {LINK_SECRET}\n",
    )
    assert (config.premium_sync_url, config.premium_sync_token) == (SYNC_URL, SYNC_TOKEN)
    assert (config.premium_site_url, config.premium_link_secret) == (SITE_URL, LINK_SECRET)
    text = repr(config)
    assert SYNC_TOKEN not in text and LINK_SECRET not in text and "example.com" not in text
    assert "premium_sync=on" in text and "premium_link=on" in text


@pytest.mark.parametrize(
    ("extra", "key"),
    [
        (f"PREMIUM_SYNC_URL = {SYNC_URL}\n", "PREMIUM_SYNC_TOKEN"),
        (f"PREMIUM_SYNC_TOKEN = {SYNC_TOKEN}\n", "PREMIUM_SYNC_URL"),
        (f"PREMIUM_SYNC_URL = http://site.example.com/x\nPREMIUM_SYNC_TOKEN = {SYNC_TOKEN}\n", "PREMIUM_SYNC_URL"),
        (f"PREMIUM_SYNC_URL = {SYNC_URL}\nPREMIUM_SYNC_TOKEN = short\n", "PREMIUM_SYNC_TOKEN"),
        (f"PREMIUM_SITE_URL = {SITE_URL}\n", "PREMIUM_LINK_SECRET"),
        (f"PREMIUM_LINK_SECRET = {LINK_SECRET}\n", "PREMIUM_SITE_URL"),
        (f"PREMIUM_SITE_URL = {SITE_URL}\nPREMIUM_LINK_SECRET = tooshort\n", "PREMIUM_LINK_SECRET"),
    ],
)
def test_invalid_premium_sync_and_link_settings(tmp_path, extra, key):
    with pytest.raises(ConfigError, match=key) as excinfo:
        load(tmp_path, extra)
    message = str(excinfo.value)
    assert SYNC_TOKEN not in message and LINK_SECRET not in message and "example.com" not in message


# --- 応答の読み取りと premium.json への反映 ---


def test_parse_sync_response():
    data = {
        "guilds": [
            {"guild_id": "1", "expires": "2026-12-31", "status": "active"},
            {"guild_id": "2", "expires": "2026-12-03", "status": "past_due"},
            {"guild_id": "1", "expires": "2026-12-01", "status": "active"},  # 同じサーバーは期限の遅いほう
        ],
        "generated_at": 1,
    }
    assert parse_sync_response(data) == {
        "1": {"expires": "2026-12-31", "source": "stripe", "status": "active"},
        "2": {"expires": "2026-12-03", "source": "stripe", "status": "past_due"},
    }
    assert parse_sync_response({"guilds": []}) == {}


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        {"guilds": None},
        {"guilds": ["1"]},
        {"guilds": [{"guild_id": 1, "expires": "2026-12-31"}]},
        {"guilds": [{"guild_id": "abc", "expires": "2026-12-31"}]},
        {"guilds": [{"guild_id": "1", "expires": None}]},
        {"guilds": [{"guild_id": "1", "expires": "2026-13-01"}]},
        {"guilds": [{"guild_id": "1", "expires": "2026-12-31"}, {"guild_id": "x", "expires": "2026-12-31"}]},
    ],
)
def test_parse_sync_response_rejects_malformed(data):
    with pytest.raises(PremiumSyncError):
        parse_sync_response(data)


def test_merge_keeps_manual_entries_and_replaces_synced_ones():
    existing = {
        "10": None,  # 手で書いた（期限なし）
        "11": {"expires": "2027-01-31", "note": "手で書いた"},
        "12": {"expires": "2026-11-30", "source": "stripe", "status": "active"},  # 解約された
        "13": {"expires": "2026-11-30", "source": "stripe", "status": "active"},  # 更新された
    }
    synced = {
        "10": {"expires": "2026-12-31", "source": "stripe"},  # 手で書いた項目を優先する
        "13": {"expires": "2026-12-30", "source": "stripe", "status": "active"},
        "14": {"expires": "2026-12-15", "source": "stripe", "status": "trialing"},
    }
    assert merge_premium(existing, synced) == {
        "10": None,
        "11": {"expires": "2027-01-31", "note": "手で書いた"},
        "13": {"expires": "2026-12-30", "source": "stripe", "status": "active"},
        "14": {"expires": "2026-12-15", "source": "stripe", "status": "trialing"},
    }


def test_apply_sync_writes_only_when_changed(tmp_path):
    path = tmp_path / "settings" / "premium.json"
    synced = {"1": {"expires": "2026-12-31", "source": "stripe", "status": "active"}}
    assert apply_sync(path, synced) is True  # ファイルもディレクトリも無ければ作る
    assert json.loads(path.read_text(encoding="utf-8")) == synced
    mtime = path.stat().st_mtime_ns
    assert apply_sync(path, synced) is False
    assert path.stat().st_mtime_ns == mtime


def test_apply_sync_reads_bom_and_keeps_manual_entries(tmp_path):
    path = tmp_path / "premium.json"
    path.write_text('{"5": "2027-03-31"}', encoding="utf-8-sig")
    assert apply_sync(path, {"1": {"expires": "2026-12-31", "source": "stripe"}})
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "5": "2027-03-31",
        "1": {"expires": "2026-12-31", "source": "stripe"},
    }


@pytest.mark.parametrize("content", ["{broken", "[1, 2]"])
def test_apply_sync_does_not_touch_a_broken_file(tmp_path, content):
    path = tmp_path / "premium.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        apply_sync(path, {"1": {"expires": "2026-12-31", "source": "stripe"}})
    assert path.read_text(encoding="utf-8") == content


def test_synced_file_is_read_by_plan_manager(tmp_path):
    path = tmp_path / "premium.json"
    config = Config(
        discord_token="d", prefix="y.", afk_time=60, sd_msg="x", premium_enabled=True, premium_guilds_file=str(path)
    )
    plans = PlanManager(config, StubEngine())
    apply_sync(path, parse_sync_response({"guilds": [{"guild_id": "7", "expires": "2999-12-31", "status": "active"}]}))
    plans.reload()
    assert plans.is_premium(7) and plans.limits_for(7).expires.isoformat() == "2999-12-31"
    assert not plans.is_premium(8)


# --- HTTP（aiohttp をモック） ---


class FakeResponse:
    def __init__(self, status, body, content_length=None):
        self.status = status
        self.body = body
        self.content_length = content_length

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read(self):
        return self.body


class FakeSession:
    def __init__(self, calls, response, error=None, **kwargs):
        self.calls = calls
        self.response = response
        self.error = error
        calls.append(("session", kwargs))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, headers, allow_redirects):
        self.calls.append(("get", url, headers, allow_redirects))
        if self.error is not None:
            raise self.error
        return self.response


def run_fetcher(response=None, error=None):
    calls = []
    response = response or FakeResponse(200, b'{"guilds": []}')
    with patch("aiohttp.ClientSession", lambda **kw: FakeSession(calls, response, error, **kw)):
        result = asyncio.run(http_fetcher(SYNC_URL, SYNC_TOKEN, timeout=5)())
    return result, calls


def test_http_fetcher_sends_bearer_token():
    result, calls = run_fetcher()
    (_, session_kwargs), (_, url, headers, allow_redirects) = calls
    assert result == {"guilds": []}
    assert session_kwargs["timeout"].total == 5
    assert url == SYNC_URL and allow_redirects is False
    assert headers["Authorization"] == f"Bearer {SYNC_TOKEN}"


@pytest.mark.parametrize(
    ("response", "error", "message"),
    [
        (FakeResponse(401, b""), None, "HTTP 401"),
        (FakeResponse(302, b""), None, "HTTP 302"),
        (FakeResponse(200, b"<html>"), None, "応答が JSON ではありません"),
        (FakeResponse(200, b"{}", content_length=10_000_000), None, "応答が大きすぎます"),
        (None, TimeoutError(), "タイムアウト"),
        (None, OSError(f"cannot connect to {SYNC_URL}"), "OSError"),
    ],
)
def test_http_fetcher_errors_hide_url_and_token(response, error, message):
    with pytest.raises(PremiumSyncError) as excinfo:
        run_fetcher(response, error)
    assert str(excinfo.value) == message
    assert "example.com" not in str(excinfo.value) and SYNC_TOKEN not in str(excinfo.value)


# --- 同期ループ ---


def make_syncer(tmp_path, responses, reloads, clock=None):
    responses = list(responses)

    async def fetch():
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return PremiumSyncer(
        fetch,
        tmp_path / "premium.json",
        on_change=lambda: reloads.append(1),
        clock=clock or (lambda: 0.0),
        warn_interval=1800,
    )


def guilds(*ids):
    return {"guilds": [{"guild_id": g, "expires": "2026-12-31", "status": "active"} for g in ids]}


def test_syncer_writes_and_reloads_only_on_change(tmp_path):
    reloads = []
    syncer = make_syncer(tmp_path, [guilds("1"), guilds("1"), guilds()], reloads)
    assert asyncio.run(syncer.sync_once())
    assert reloads == [1]
    assert asyncio.run(syncer.sync_once())
    assert reloads == [1]  # 変わらなければ書かない・読み直さない
    assert asyncio.run(syncer.sync_once())
    assert reloads == [1, 1]
    assert json.loads((tmp_path / "premium.json").read_text(encoding="utf-8")) == {}


def test_syncer_keeps_file_on_failure_and_rate_limits_warnings(tmp_path, caplog):
    caplog.set_level(logging.DEBUG, logger="yomi_kai.premium_sync")
    now = [0.0]
    reloads = []
    syncer = make_syncer(
        tmp_path,
        [guilds("1"), PremiumSyncError("HTTP 500"), {"bad": 1}, PremiumSyncError("HTTP 500"), guilds("1", "2")],
        reloads,
        clock=lambda: now[0],
    )
    assert asyncio.run(syncer.sync_once())
    before = (tmp_path / "premium.json").read_text(encoding="utf-8")
    assert not asyncio.run(syncer.sync_once())
    assert not asyncio.run(syncer.sync_once())  # 形の正しくない応答でも書き換えない
    assert (tmp_path / "premium.json").read_text(encoding="utf-8") == before
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "HTTP 500" in warnings[0].getMessage()
    now[0] = 1800.0
    assert not asyncio.run(syncer.sync_once())
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 2
    assert asyncio.run(syncer.sync_once())
    assert "復旧しました" in caplog.text
    assert reloads == [1, 1]


def test_syncer_does_not_overwrite_broken_file(tmp_path, caplog):
    path = tmp_path / "premium.json"
    path.write_text("{broken", encoding="utf-8")
    syncer = make_syncer(tmp_path, [guilds("1")], [])
    assert not asyncio.run(syncer.sync_once())
    assert path.read_text(encoding="utf-8") == "{broken"
    assert "読み書きできません" in caplog.text


def test_syncer_run_syncs_immediately_then_every_interval(tmp_path):
    class Stop(Exception):
        pass

    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise Stop

    syncer = make_syncer(tmp_path, [guilds("1"), PremiumSyncError("HTTP 500")], [])
    syncer._sleep = sleep
    with pytest.raises(Stop):
        asyncio.run(syncer.run())
    assert sleeps == [300, 300]


# --- Bot との結線 ---


def make_bot(tmp_path, **kwargs):
    config = Config(discord_token="dummy", prefix="y.", afk_time=60, sd_msg="x", **kwargs)
    return YomiBot(config, StubEngine(), DictionaryStore(tmp_path / "dict"), SettingsStore(tmp_path / "settings"))


def test_bot_has_no_syncer_by_default(tmp_path):
    assert make_bot(tmp_path).premium_syncer is None


def test_bot_syncer_requires_premium_enabled(tmp_path, caplog):
    bot = make_bot(tmp_path, premium_sync_url=SYNC_URL, premium_sync_token=SYNC_TOKEN)
    assert bot.premium_syncer is None
    assert "PREMIUM_ENABLED" in caplog.text
    path = tmp_path / "premium.json"
    bot = make_bot(
        tmp_path,
        premium_enabled=True,
        premium_guilds_file=str(path),
        premium_sync_url=SYNC_URL,
        premium_sync_token=SYNC_TOKEN,
    )
    assert bot.premium_syncer is not None and bot.premium_syncer.path == path
    assert SYNC_TOKEN not in caplog.text


# --- y.plan のリンク ---


class LinkCtx:
    def __init__(self, *, manage_guild, slash, dm_error=None):
        self.interaction = object() if slash else None
        self.guild = SimpleNamespace(id=42)
        self.sent = []
        self.dms = []
        dms = self.dms

        class Author:
            id = 7
            guild_permissions = SimpleNamespace(manage_guild=manage_guild)

            async def send(self, content=None, **kwargs):
                if dm_error is not None:
                    raise dm_error
                dms.append((content, kwargs))

        self.author = Author()

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


def link_cog(tmp_path, **kwargs):
    config = Config(
        discord_token="d",
        prefix="y.",
        afk_time=60,
        sd_msg="x",
        premium_guilds_file=str(tmp_path / "premium.json"),
        **kwargs,
    )
    return PlanCog(SimpleNamespace(), config, PlanManager(config, StubEngine()), DictionaryStore(tmp_path / "dict"))


LINK_SETTINGS = {"premium_enabled": True, "premium_site_url": SITE_URL, "premium_link_secret": LINK_SECRET}


def run_link(cog, ctx):
    asyncio.run(PlanCog.plan.callback(cog, ctx))
    return ctx


def test_plan_link_is_ephemeral_for_slash_managers(tmp_path):
    ctx = run_link(link_cog(tmp_path, **LINK_SETTINGS), LinkCtx(manage_guild=True, slash=True))
    assert len(ctx.sent) == 2
    content, kwargs = ctx.sent[1]
    assert PREMIUM_LINK_LABEL in content
    assert "https://site.example.com/premium/?t=v1.42." in content
    assert kwargs["ephemeral"] is True
    assert LINK_SECRET not in content
    assert "ephemeral" not in ctx.sent[0][1] or ctx.sent[0][1]["ephemeral"] is False  # 上限の表示は従来どおり


def test_plan_link_is_sent_by_dm_for_prefix_command(tmp_path):
    ctx = run_link(link_cog(tmp_path, **LINK_SETTINGS), LinkCtx(manage_guild=True, slash=False))
    assert len(ctx.dms) == 1 and "/premium/?t=v1.42." in ctx.dms[0][0]
    assert [c for c, _ in ctx.sent[1:]] == [DM_SENT_NOTICE]
    assert all("/premium/" not in (c or "") for c, _ in ctx.sent)  # チャンネルにはリンクを出さない


def test_plan_link_dm_failure(tmp_path):
    error = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Cannot send messages to this user")
    ctx = run_link(link_cog(tmp_path, **LINK_SETTINGS), LinkCtx(manage_guild=True, slash=False, dm_error=error))
    assert [c for c, _ in ctx.sent[1:]] == [DM_FAILED_NOTICE]


@pytest.mark.parametrize(
    "settings",
    [
        {**LINK_SETTINGS, "premium_enabled": False},
        {"premium_enabled": True},
    ],
)
def test_plan_link_requires_settings(tmp_path, settings):
    ctx = run_link(link_cog(tmp_path, **settings), LinkCtx(manage_guild=True, slash=True))
    assert len(ctx.sent) == 1


def test_plan_link_is_only_for_managers(tmp_path):
    ctx = run_link(link_cog(tmp_path, **LINK_SETTINGS), LinkCtx(manage_guild=False, slash=True))
    assert len(ctx.sent) == 1 and not ctx.dms
