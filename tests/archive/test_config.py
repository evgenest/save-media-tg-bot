from pathlib import Path

import pytest

from mediasaver.archive.config import load_archive_config, parse_chats
from mediasaver.core.config import ConfigError


def test_archive_off_without_chats():
    assert load_archive_config({}) is None
    assert load_archive_config({"ARCHIVE_CHATS": "  "}) is None


def test_parse_chats():
    assert parse_chats("-1001=/a/b; -1002=c") == {-1001: Path("/a/b"), -1002: Path("c")}


@pytest.mark.parametrize("raw", ["-1001", "abc=/x", "-1001="])
def test_parse_chats_rejects_bad_entries(raw):
    with pytest.raises(ConfigError):
        parse_chats(raw)


def test_defaults():
    config = load_archive_config({"ARCHIVE_CHATS": "-1001=/x"})

    assert config.timezone.key == "Europe/Berlin"
    assert config.elevenlabs_api_key is None
    assert config.transcribe_max_seconds == 1800
    assert config.session_dir == Path("sessions")


def test_bad_timezone():
    with pytest.raises(ConfigError):
        load_archive_config({"ARCHIVE_CHATS": "-1001=/x", "ARCHIVE_TZ": "Mars/Base"})
