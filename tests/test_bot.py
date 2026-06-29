import asyncio
from datetime import datetime
from pathlib import Path

from batch_manager import Batch
from bot import BotState, LiveProgress, build_help_keyboard, build_help_text, build_status_text, create_app, make_progress_callback, refresh_status_message
from config import Config


def make_batch(**overrides):
    defaults = dict(user_id=1, started_at=datetime(2026, 6, 28, 12, 0, 0))
    defaults.update(overrides)
    return Batch(**defaults)


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


def test_build_status_text_without_live_progress_is_unchanged():
    batch = make_batch(file_count=3, total_bytes=1024 * 1024)
    assert build_status_text(batch) == "Сохранено: 3 файлов, 1.0 МБ"


def test_build_status_text_with_live_progress_shows_current_file():
    batch = make_batch(file_count=3, total_bytes=1024 * 1024)
    live = LiveProgress(current_name="a.jpg", current_bytes=50, current_total=200)

    text = build_status_text(batch, live)

    assert "Сейчас: a.jpg — 25%" in text


def test_build_status_text_omits_current_file_line_when_none():
    batch = make_batch(file_count=1, total_bytes=0)
    live = LiveProgress()

    text = build_status_text(batch, live)

    assert "Сейчас:" not in text


def test_build_help_keyboard_has_show_help_button():
    keyboard = build_help_keyboard()
    button = keyboard.inline_keyboard[0][0]
    assert button.callback_data == "show_help"


def test_create_app_builds_without_network_access(tmp_path):
    config = make_test_config(tmp_path)

    app = create_app(config, BotState())

    assert app.name == "mediasaver_bot"
    # Drain pending handler-registration tasks scheduled during Client construction.
    # The @app.on_message and @app.on_callback_query decorators schedule small
    # bookkeeping coroutines on the event loop; give them one tick to complete
    # so they don't get garbage-collected later and trigger RuntimeWarning.
    app.loop.run_until_complete(asyncio.sleep(0))


class FakeStatusMessage:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.edits: "list[tuple]" = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))
        if self.fail:
            raise RuntimeError("flood wait")
        return self


async def test_refresh_status_message_edits_with_latest_text():
    batch = make_batch(file_count=2, total_bytes=2048)
    live = LiveProgress(current_name="a.jpg", current_bytes=10, current_total=20)
    message = FakeStatusMessage()

    result = await refresh_status_message(batch, live, message)

    assert result is message
    assert len(message.edits) == 1
    assert "a.jpg — 50%" in message.edits[0][0]


async def test_refresh_status_message_swallows_edit_errors():
    batch = make_batch(file_count=1, total_bytes=0)
    live = LiveProgress()
    message = FakeStatusMessage(fail=True)

    result = await refresh_status_message(batch, live, message)

    assert result is message


def test_get_lock_returns_same_lock_for_same_user():
    state = BotState()

    lock1 = state.get_lock(1)
    lock2 = state.get_lock(1)

    assert lock1 is lock2


def test_get_lock_returns_different_locks_for_different_users():
    state = BotState()

    assert state.get_lock(1) is not state.get_lock(2)
