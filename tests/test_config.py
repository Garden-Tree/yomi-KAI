import pytest

from yomi_kai.config import ConfigError, load_config

BASE = """[DEFAULT]
DISCORD_TOKEN = token
USE_GOOGLE_TTS = False
GOOGLE_API_KEY = key
PREFIX = y.
AFK_TIME = 1800
SD_MSG = 突然の死
"""
WEBHOOK = "https://discord.com/api/webhooks/123/secret-token"


def load(tmp_path, extra=""):
    path = tmp_path / "config.ini"
    path.write_text(BASE + extra, encoding="utf-8")
    return load_config(path)


def test_alert_defaults_when_missing(tmp_path):
    config = load(tmp_path)
    assert config.alert_webhook_url is None
    assert config.alert_after == 300


def test_alert_settings(tmp_path):
    config = load(tmp_path, f"ALERT_WEBHOOK_URL = {WEBHOOK}\nALERT_AFTER = 600\n")
    assert config.alert_webhook_url == WEBHOOK
    assert config.alert_after == 600
    assert "secret" not in repr(config) and "token" not in repr(config)


def test_empty_webhook_disables_alert(tmp_path):
    assert load(tmp_path, "ALERT_WEBHOOK_URL =\nALERT_AFTER =\n").alert_webhook_url is None


@pytest.mark.parametrize(
    ("extra", "key"),
    [
        ("ALERT_AFTER = 29\n", "ALERT_AFTER"),
        ("ALERT_AFTER = 5分\n", "ALERT_AFTER"),
        ("ALERT_WEBHOOK_URL = http://example.com/hook\n", "ALERT_WEBHOOK_URL"),
    ],
)
def test_invalid_alert_settings(tmp_path, extra, key):
    with pytest.raises(ConfigError, match=key) as excinfo:
        load(tmp_path, extra)
    assert "example.com" not in str(excinfo.value)  # URL は表示しない


def test_voicevox_workers(tmp_path):
    assert load(tmp_path).voicevox_workers == 1
    assert load(tmp_path, "VOICEVOX_WORKERS = 4\n").voicevox_workers == 4


def test_log_read_text(tmp_path):
    assert load(tmp_path).log_read_text is False
    assert load(tmp_path, "LOG_READ_TEXT = True\n").log_read_text is True
    with pytest.raises(ConfigError, match="LOG_READ_TEXT"):
        load(tmp_path, "LOG_READ_TEXT = maybe\n")


@pytest.mark.parametrize("value", ["0", "9", "two"])
def test_invalid_voicevox_workers(tmp_path, value):
    with pytest.raises(ConfigError, match="VOICEVOX_WORKERS"):
        load(tmp_path, f"VOICEVOX_WORKERS = {value}\n")


def test_max_dict_entries(tmp_path):
    assert load(tmp_path).max_dict_entries == 0
    assert load(tmp_path, "MAX_DICT_ENTRIES = 50\n").max_dict_entries == 50
    for value in ("-1", "many"):
        with pytest.raises(ConfigError, match="MAX_DICT_ENTRIES"):
            load(tmp_path, f"MAX_DICT_ENTRIES = {value}\n")


def test_speaker_allowlist(tmp_path):
    assert load(tmp_path).speaker_allowlist == ()
    assert load(tmp_path, "SPEAKER_ALLOWLIST =\n").speaker_allowlist == ()
    config = load(tmp_path, "SPEAKER_ALLOWLIST = ずんだもん, 春日部つむぎ ,,四国めたん（あまあま）,ずんだもん\n")
    assert config.speaker_allowlist == ("ずんだもん", "春日部つむぎ", "四国めたん（あまあま）")


def test_blocked_guilds_file(tmp_path):
    assert load(tmp_path).blocked_guilds_file == "settings/blocked_guilds.txt"
    assert load(tmp_path, "BLOCKED_GUILDS_FILE = data/block.txt\n").blocked_guilds_file == "data/block.txt"


def test_speaker_allowlist_accepts_japanese_separators(tmp_path):
    config = load(tmp_path, "SPEAKER_ALLOWLIST = ずんだもん、 春日部つむぎ ，四国めたん,雨晴はう\n")
    assert config.speaker_allowlist == ("ずんだもん", "春日部つむぎ", "四国めたん", "雨晴はう")


def test_record_usage(tmp_path):
    assert load(tmp_path).record_usage is False
    assert load(tmp_path, "RECORD_USAGE = True\n").record_usage is True
    with pytest.raises(ConfigError, match="RECORD_USAGE"):
        load(tmp_path, "RECORD_USAGE = maybe\n")


def test_empty_optional_ints_use_defaults(tmp_path):
    # 任意の整数キーは、空欄なら既定値（MAX_READ_LENGTH も他のキーと同じ扱い）
    config = load(tmp_path, "MAX_READ_LENGTH =\nALERT_AFTER =\nVOICEVOX_WORKERS =\nMAX_DICT_ENTRIES =\n")
    assert config.max_read_length == 100
    assert config.alert_after == 300
    assert config.voicevox_workers == 1
    assert config.max_dict_entries == 0


@pytest.mark.parametrize(
    ("line", "key"),
    [
        ("MAX_READ_LENGTH = -1", "MAX_READ_LENGTH"),
        ("MAX_READ_LENGTH = long", "MAX_READ_LENGTH"),
        ("AFK_TIME = 0", "AFK_TIME"),
        ("AFK_TIME = soon", "AFK_TIME"),
        ("AFK_TIME =", "AFK_TIME"),  # 必須
    ],
)
def test_invalid_ints(tmp_path, line, key):
    path = tmp_path / "config.ini"
    text = BASE.replace("AFK_TIME = 1800", line) if key == "AFK_TIME" else BASE + line + "\n"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match=key):
        load_config(path)


def test_enable_sudden_death(tmp_path):
    assert load(tmp_path).enable_sudden_death is True
    assert load(tmp_path, "ENABLE_SUDDEN_DEATH = False\n").enable_sudden_death is False
    with pytest.raises(ConfigError, match="ENABLE_SUDDEN_DEATH"):
        load(tmp_path, "ENABLE_SUDDEN_DEATH = maybe\n")


def test_sd_msg_optional_when_sudden_death_disabled(tmp_path):
    path = tmp_path / "config.ini"
    base = BASE.replace("SD_MSG = 突然の死\n", "")
    path.write_text(base + "ENABLE_SUDDEN_DEATH = False\n", encoding="utf-8")
    assert load_config(path).enable_sudden_death is False
    path.write_text(base, encoding="utf-8")
    with pytest.raises(ConfigError, match="SD_MSG"):
        load_config(path)
