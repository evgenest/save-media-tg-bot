from pathlib import Path

from bot import BotState, create_app
from config import Config


def make_test_config(tmp_path: Path) -> Config:
    return Config(
        api_id=12345,
        api_hash="hash",
        bot_token="111:AAAA",
        allowed_user_ids=frozenset({111}),
        storage_dir=tmp_path,
        batch_timeout=30.0,
    )


def test_create_app_builds_without_network_access(tmp_path):
    config = make_test_config(tmp_path)

    app = create_app(config, BotState())

    assert app.name == "mediasaver_bot"
