import asyncio
from pathlib import Path

from bot import BotState, LiveProgress, build_help_text, create_app, make_progress_callback
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


async def test_make_progress_callback_updates_live_state():
    live = LiveProgress()
    callback = make_progress_callback(live, "video.mp4")

    await callback(50, 200)

    assert live.current_name == "video.mp4"
    assert live.current_bytes == 50
    assert live.current_total == 200


def test_build_help_text_includes_batch_timeout():
    text = build_help_text(45.0)
    assert "45 секунд" in text


def test_build_help_text_mentions_finish_button_and_help_command():
    text = build_help_text(30.0)
    assert "Завершить пакет" in text
    assert "/help" in text


def test_create_app_builds_without_network_access(tmp_path):
    config = make_test_config(tmp_path)

    app = create_app(config, BotState())

    assert app.name == "mediasaver_bot"
    # Drain pending handler-registration tasks scheduled during Client construction.
    # The @app.on_message and @app.on_callback_query decorators schedule small
    # bookkeeping coroutines on the event loop; give them one tick to complete
    # so they don't get garbage-collected later and trigger RuntimeWarning.
    app.loop.run_until_complete(asyncio.sleep(0))
