import asyncio
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
    # Drain pending handler-registration tasks scheduled during Client construction.
    # The @app.on_message and @app.on_callback_query decorators schedule small
    # bookkeeping coroutines on the event loop; give them one tick to complete
    # so they don't get garbage-collected later and trigger RuntimeWarning.
    app.loop.run_until_complete(asyncio.sleep(0))
