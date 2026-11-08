import asyncio
import json

import pytest

from yomi_kai.settings import GuildSettings, SettingsStore, UserSettings, resolve_voice, server_speaker

GUILD = 1234
ALICE = 111
BOB = 222
ZUNDA = "ずんだもん"
TSUMUGI = "春日部つむぎ"
GOOGLE = "ja-JP-Wavenet-A"
AVAILABLE = {ZUNDA, TSUMUGI, GOOGLE}


def read_json(path):
    with open(path, encoding="UTF-8") as f:
        return json.load(f)


# --- SettingsStore ---


def test_missing_file_is_empty(tmp_path):
    store = SettingsStore(tmp_path / "settings")
    settings = store.get(GUILD)
    assert settings.speaker is None
    assert settings.user(ALICE) == UserSettings()
    assert not (tmp_path / "settings").exists()  # 読むだけではディレクトリを作らない


def test_persists_across_instances(tmp_path):
    store = SettingsStore(tmp_path)
    store.set_guild_speaker(GUILD, TSUMUGI)
    store.set_user_speaker(GUILD, ALICE, ZUNDA)
    store.set_user_speed(GUILD, ALICE, 1.2)
    store.set_user_speed(GUILD, BOB, 0.8)

    assert read_json(tmp_path / f"{GUILD}.json") == {
        "speaker": TSUMUGI,
        "users": {str(ALICE): {"speaker": ZUNDA, "speed": 1.2}, str(BOB): {"speed": 0.8}},
    }

    reloaded = SettingsStore(tmp_path).get(GUILD)
    assert reloaded.speaker == TSUMUGI
    assert reloaded.user(ALICE) == UserSettings(ZUNDA, 1.2)
    assert reloaded.user(BOB) == UserSettings(None, 0.8)
    assert reloaded.user(999) == UserSettings()


def test_cache_reflects_writes(tmp_path):
    store = SettingsStore(tmp_path)
    assert store.get(GUILD).speaker is None
    store.set_guild_speaker(GUILD, TSUMUGI)
    assert store.get(GUILD).speaker == TSUMUGI
    assert store.get_cached(GUILD) is store.get(GUILD)


def test_clear_guild_speaker(tmp_path):
    store = SettingsStore(tmp_path)
    assert store.clear_guild_speaker(GUILD) is False
    assert not (tmp_path / f"{GUILD}.json").exists()  # 変更がなければファイルを作らない
    store.set_guild_speaker(GUILD, TSUMUGI)
    assert store.clear_guild_speaker(GUILD) is True
    assert SettingsStore(tmp_path).get(GUILD).speaker is None


def test_user_entry_removed_when_empty(tmp_path):
    store = SettingsStore(tmp_path)
    store.set_guild_speaker(GUILD, TSUMUGI)
    store.set_user_speaker(GUILD, ALICE, ZUNDA)
    store.set_user_speed(GUILD, ALICE, 1.5)
    store.set_user_speed(GUILD, BOB, 0.7)
    path = tmp_path / f"{GUILD}.json"

    assert store.clear_user_speaker(GUILD, ALICE) is True
    assert read_json(path)["users"][str(ALICE)] == {"speed": 1.5}
    assert store.clear_user_speed(GUILD, ALICE) is True
    assert str(ALICE) not in read_json(path)["users"]
    assert store.get(GUILD).user(ALICE) == UserSettings()

    assert store.clear_user_speed(GUILD, ALICE) is False  # もう何もない
    assert store.clear_user_speed(GUILD, BOB) is True
    assert read_json(path) == {"speaker": TSUMUGI}


def test_unknown_keys_are_kept(tmp_path):
    path = tmp_path / f"{GUILD}.json"
    path.write_text(json.dumps({"read_name": True}), encoding="UTF-8")
    store = SettingsStore(tmp_path)
    store.set_user_speed(GUILD, ALICE, 1.1)
    assert read_json(path) == {"read_name": True, "users": {str(ALICE): {"speed": 1.1}}}


def test_atomic_write_leaves_no_temp_files(tmp_path):
    store = SettingsStore(tmp_path)
    store.set_guild_speaker(GUILD, ZUNDA)
    store.set_user_speed(GUILD, ALICE, 1.3)
    store.clear_user_speed(GUILD, ALICE)
    assert sorted(p.name for p in tmp_path.iterdir()) == [f"{GUILD}.json"]


@pytest.mark.parametrize(
    "content",
    [
        "{not json",
        "[]",
        json.dumps({"speaker": 1}),
        json.dumps({"users": []}),
        json.dumps({"users": {"abc": {}}}),
        json.dumps({"users": {"1": {"speed": "fast"}}}),
        json.dumps({"announce_voice": "yes"}),
    ],
)
def test_corrupt_file_reads_as_empty_but_writes_fail(tmp_path, content):
    path = tmp_path / f"{GUILD}.json"
    path.write_text(content, encoding="UTF-8")
    store = SettingsStore(tmp_path)
    assert store.get(GUILD) == GuildSettings()
    assert store.get_cached(GUILD) is None  # 壊れたファイルはキャッシュしない
    # 壊れたファイルを空の設定で上書きしてしまわない
    with pytest.raises(ValueError):
        store.set_user_speed(GUILD, ALICE, 1.2)
    with pytest.raises(ValueError):
        store.clear_guild_speaker(GUILD)
    assert path.read_text(encoding="UTF-8") == content


def test_aget_uses_cache(tmp_path):
    store = SettingsStore(tmp_path)
    store.set_guild_speaker(GUILD, ZUNDA)

    async def run():
        return await store.aget(GUILD), await store.aget(GUILD)

    first, second = asyncio.run(run())
    assert first is second
    assert first.speaker == ZUNDA


# --- resolve_voice ---


def test_resolve_defaults():
    assert resolve_voice(GuildSettings(), ALICE, AVAILABLE, ZUNDA) == (ZUNDA, 1.0)


def test_resolve_precedence():
    settings = GuildSettings(TSUMUGI, {ALICE: UserSettings(GOOGLE, 1.5), BOB: UserSettings(speed=0.8)})
    assert resolve_voice(settings, ALICE, AVAILABLE, ZUNDA) == (GOOGLE, 1.5)  # 個人設定
    assert resolve_voice(settings, BOB, AVAILABLE, ZUNDA) == (TSUMUGI, 0.8)  # サーバー設定
    assert resolve_voice(GuildSettings(), BOB, AVAILABLE, ZUNDA) == (ZUNDA, 1.0)  # エンジン既定


def test_resolve_skips_unavailable_speakers():
    # 例: Google TTS を無効にした・モデルを減らした後に古い設定が残っている
    settings = GuildSettings(TSUMUGI, {ALICE: UserSettings(GOOGLE, 1.2)})
    available = {ZUNDA, TSUMUGI}
    assert resolve_voice(settings, ALICE, available, ZUNDA) == (TSUMUGI, 1.2)
    settings = GuildSettings(GOOGLE, {ALICE: UserSettings(GOOGLE)})
    assert resolve_voice(settings, ALICE, available, ZUNDA) == (ZUNDA, 1.0)


def test_resolve_clamps_out_of_range_speed():
    settings = GuildSettings(users={ALICE: UserSettings(speed=5.0), BOB: UserSettings(speed=0.1)})
    assert resolve_voice(settings, ALICE, AVAILABLE, ZUNDA) == (ZUNDA, 2.0)
    assert resolve_voice(settings, BOB, AVAILABLE, ZUNDA) == (ZUNDA, 0.5)


# --- 入退室の読み上げ設定 ---


def test_announce_voice_defaults_to_on_and_persists(tmp_path):
    store = SettingsStore(tmp_path)
    assert store.get(GUILD).announce_voice is True
    store.set_user_speed(GUILD, ALICE, 1.2)
    store.set_announce_voice(GUILD, False)
    assert store.get(GUILD).announce_voice is False
    assert read_json(tmp_path / f"{GUILD}.json") == {"users": {str(ALICE): {"speed": 1.2}}, "announce_voice": False}
    assert SettingsStore(tmp_path).get(GUILD).announce_voice is False
    store.set_announce_voice(GUILD, True)
    assert SettingsStore(tmp_path).get(GUILD).announce_voice is True


def test_server_speaker():
    assert server_speaker(GuildSettings(TSUMUGI), AVAILABLE, ZUNDA) == TSUMUGI
    assert server_speaker(GuildSettings(), AVAILABLE, ZUNDA) == ZUNDA
    assert server_speaker(GuildSettings("消えた話者"), AVAILABLE, ZUNDA) == ZUNDA
