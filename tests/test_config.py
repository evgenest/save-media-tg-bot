from pathlib import Path

import pytest

from config import Config, ConfigError, load_config

VALID_ENV = {
    "API_ID": "12345",
    "API_HASH": "abc123hash",
    "BOT_TOKEN": "111:AAAA",
    "ALLOWED_USER_IDS": "111, 222",
    "STORAGE_DIR": "/data/storage",
}


def test_load_config_success():
    config = load_config(VALID_ENV)

    assert config == Config(
        api_id=12345,
        api_hash="abc123hash",
        bot_token="111:AAAA",
        allowed_user_ids=frozenset({111, 222}),
        storage_dir=Path("/data/storage"),
        batch_timeout=30.0,
    )


@pytest.mark.parametrize(
    "missing_key", ["API_ID", "API_HASH", "BOT_TOKEN", "ALLOWED_USER_IDS", "STORAGE_DIR"]
)
def test_load_config_missing_required_var_raises(missing_key):
    env = dict(VALID_ENV)
    del env[missing_key]

    with pytest.raises(ConfigError, match=missing_key):
        load_config(env)


def test_load_config_invalid_api_id_raises():
    env = dict(VALID_ENV, API_ID="not-a-number")

    with pytest.raises(ConfigError, match="API_ID"):
        load_config(env)


def test_load_config_invalid_allowed_user_ids_raises():
    env = dict(VALID_ENV, ALLOWED_USER_IDS="abc")

    with pytest.raises(ConfigError, match="ALLOWED_USER_IDS"):
        load_config(env)


def test_load_config_empty_after_strip_allowed_user_ids_raises():
    env = dict(VALID_ENV, ALLOWED_USER_IDS=" , ")

    with pytest.raises(ConfigError, match="ALLOWED_USER_IDS"):
        load_config(env)


def test_load_config_default_batch_timeout():
    env = dict(VALID_ENV)
    env.pop("BATCH_TIMEOUT", None)

    config = load_config(env)

    assert config.batch_timeout == 30.0


def test_load_config_custom_batch_timeout():
    env = dict(VALID_ENV, BATCH_TIMEOUT="45")

    config = load_config(env)

    assert config.batch_timeout == 45.0


def test_load_config_non_positive_batch_timeout_raises():
    env = dict(VALID_ENV, BATCH_TIMEOUT="0")

    with pytest.raises(ConfigError, match="BATCH_TIMEOUT"):
        load_config(env)
