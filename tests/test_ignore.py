"""読み上げないユーザー・ロール（y.ignore）のテスト。"""

import asyncio
from types import SimpleNamespace

import pytest
from test_voice_cog import FakeChannel, FakeEngine, FakeVoiceClient, make_cog, make_config, make_message, wait_until

from yomi_kai.settings import MAX_IGNORED, GuildSettings, SettingsStore, is_ignored

GUILD_ID = 1


def test_is_ignored():
    settings = GuildSettings(ignored_users=frozenset({7}), ignored_roles=frozenset({50}))
    assert is_ignored(settings, 7, [])
    assert is_ignored(settings, 8, [49, 50])
    assert not is_ignored(settings, 8, [49])
    assert not is_ignored(GuildSettings(), 7, [50])


def test_add_and_remove_ignored(tmp_path):
    store = SettingsStore(tmp_path)
    assert store.add_ignored(GUILD_ID, "ignored_users", 7) == "added"
    assert store.add_ignored(GUILD_ID, "ignored_users", 7) == "exists"
    assert store.add_ignored(GUILD_ID, "ignored_roles", 50) == "added"
    assert store.get(GUILD_ID).ignored_users == {7}
    assert store.get(GUILD_ID).ignored_roles == {50}
    assert store.remove_ignored(GUILD_ID, "ignored_users", 7)
    assert not store.remove_ignored(GUILD_ID, "ignored_users", 7)
    assert store.get(GUILD_ID).ignored_users == frozenset()
    with pytest.raises(ValueError):
        store.add_ignored(GUILD_ID, "speaker", 1)


def test_ignored_limit(tmp_path):
    store = SettingsStore(tmp_path)
    for i in range(MAX_IGNORED):
        assert store.add_ignored(GUILD_ID, "ignored_roles", 100 + i) == "added"
    assert store.add_ignored(GUILD_ID, "ignored_roles", 999) == "full"
    assert store.add_ignored(GUILD_ID, "ignored_users", 999) == "added"  # 上限はそれぞれ


@pytest.mark.parametrize("data", [{"ignored_users": "7"}, {"ignored_roles": ["abc"]}, {"ignored_users": [True]}])
def test_invalid_ignored_values(data):
    with pytest.raises(ValueError):
        GuildSettings.from_json(data)


def test_on_message_skips_ignored_user_and_role(tmp_path):
    engine = FakeEngine()
    cog = make_cog(tmp_path, make_config(), engine)
    cog.settings.add_ignored(GUILD_ID, "ignored_users", 8)
    cog.settings.add_ignored(GUILD_ID, "ignored_roles", 50)

    def message(content, user_id, role_ids=()):
        m = make_message(content, user_id=user_id)
        m.author.roles = [SimpleNamespace(id=r) for r in role_ids]
        return m

    async def run():
        session = cog.get_session(GUILD_ID)
        await session.start(FakeVoiceClient(), FakeChannel())
        await cog.on_message(message("無視されるユーザー", 8))
        await cog.on_message(message("無視されるロール", 9, [49, 50]))
        await cog.on_message(message("読まれる", 9, [49]))
        await wait_until(lambda: engine.calls and not session.is_busy)
        await session.close()

    asyncio.run(run())
    assert engine.calls == ["読まれる"]


def test_missing_permission_reply_is_private_for_slash(tmp_path):
    from discord.ext import commands

    from yomi_kai.cogs.admin import MANAGE_GUILD_NOTICE, AdminCog

    sent = []

    class Ctx:
        interaction = object()

        async def send(self, content=None, **kwargs):
            sent.append((content, kwargs))

    cog = AdminCog(SimpleNamespace(), make_config(), SettingsStore(tmp_path))
    error = commands.HybridCommandError(commands.MissingPermissions(["manage_guild"]))
    asyncio.run(cog.cog_command_error(Ctx(), error))
    assert sent == [(MANAGE_GUILD_NOTICE, {"ephemeral": True})]
