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


@pytest.mark.parametrize("value", ["0", "9", "two"])
def test_invalid_voicevox_workers(tmp_path, value):
    with pytest.raises(ConfigError, match="VOICEVOX_WORKERS"):
        load(tmp_path, f"VOICEVOX_WORKERS = {value}\n")
