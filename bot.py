from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional

from pyrogram import Client, filters, idle
from pyrogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from batch_manager import Batch, BatchManager, BatchPhase
from config import Config, load_config
from downloader import DownloadResult, download_media_message, extract_media_info
from storage import Manifest, ManifestEntry, create_batch_dir
from text_export import save_text_message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mediasaver")

FINISH_BATCH_DATA = "finish_batch"
SHOW_HELP_DATA = "show_help"

MEDIA_FILTER = (
    filters.photo
    | filters.video
    | filters.audio
    | filters.document
    | filters.voice
    | filters.video_note
    | filters.animation
)

# Plain text messages (no media). Registered after the /start & /help command
# handler, so commands - also `filters.text` matches - are claimed by that
# handler first and never reach this one.
TEXT_FILTER = filters.text

# The bot only saves content that already exists elsewhere in Telegram, not
# text typed directly into the chat - so both media and text handlers require
# a forwarded message. /start & /help stay usable when typed directly since
# their handler doesn't use this filter.
FORWARDED_FILTER = filters.forwarded

STATUS_REFRESH_INTERVAL = 5.0


async def run_status_ticker(batch: Batch, state: BotState, batch_timeout: float) -> None:
    try:
        while True:
            await asyncio.sleep(STATUS_REFRESH_INTERVAL)
            if batch.phase == BatchPhase.CLOSED:
                return
            status_message = state.status_messages.get(batch.batch_id)
            if status_message is None:
                continue
            live = state.live_progress.get(batch.batch_id, LiveProgress())
            state.status_messages[batch.batch_id] = await refresh_status_message(
                batch, live, status_message, batch_timeout
            )
    except asyncio.CancelledError:
        pass


@dataclass
class LiveProgress:
    current_name: Optional[str] = None
    current_bytes: int = 0
    current_total: int = 0


def make_progress_callback(
    live: LiveProgress, file_name: str
) -> Callable[[int, int], Awaitable[None]]:
    async def progress(current: int, total: int) -> None:
        live.current_name = file_name
        live.current_bytes = current
        live.current_total = total

    return progress


class BotState:
    """All dicts below are keyed by Batch.batch_id, not user_id - a user can
    have a PENDING batch collecting files while a previous batch is still
    downloading, each with its own status message and ticker."""

    def __init__(self) -> None:
        self.batch_dirs: Dict[int, Path] = {}
        self.manifests: Dict[int, Manifest] = {}
        self.status_messages: Dict[int, Message] = {}
        self.live_progress: Dict[int, LiveProgress] = {}
        self.ticker_tasks: Dict[int, asyncio.Task] = {}


def is_allowed(user_id: Optional[int], config: Config) -> bool:
    return user_id is not None and user_id in config.allowed_user_ids


def build_pending_text(batch: Batch, batch_timeout: float) -> str:
    elapsed = (datetime.now() - batch.last_activity_at).total_seconds()
    remaining = max(0.0, batch_timeout - elapsed)
    return (
        f"В очереди: {batch.queued_count} файлов\n"
        f"Скачивание начнётся через {remaining:.0f} сек — либо нажмите "
        "«⬇️ Скачать сейчас», чтобы начать сразу."
    )


def build_status_text(batch: Batch, live: Optional[LiveProgress] = None) -> str:
    size_mb = batch.total_bytes / (1024 * 1024)
    text = f"Сохранено: {batch.file_count} файлов, {size_mb:.1f} МБ"
    if live is not None and live.current_name is not None:
        percent = (live.current_bytes * 100 / live.current_total) if live.current_total else 0
        text += f"\nСейчас: {live.current_name} — {percent:.0f}%"
    if batch.error_count:
        text += f"\nОшибок: {batch.error_count}"
    return text


def build_summary_text(batch: Batch, batch_dir: Path) -> str:
    size_mb = batch.total_bytes / (1024 * 1024)
    text = (
        f"Пакет завершён: {batch_dir}\n"
        f"Файлов: {batch.file_count}\n"
        f"Размер: {size_mb:.1f} МБ"
    )
    if batch.error_count:
        text += f"\nОшибок: {batch.error_count}"
    return text


def build_help_text(batch_timeout: float) -> str:
    timeout_seconds = int(batch_timeout)
    return (
        "👋 Привет! Этот бот сохраняет медиафайлы и текстовые сообщения, "
        "которые вы ему пересылаете.\n\n"
        "Как это работает:\n"
        "1. Пришлите (перешлите) одно или несколько сообщений с фото, видео, "
        "аудио, документами или просто текстом — текст сохранится как "
        ".md-файл с сохранением форматирования (жирный, курсив, ссылки и "
        "т.д.).\n"
        "2. Бот покажет, сколько файлов в очереди, и начнёт скачивание через "
        f"{timeout_seconds} секунд после последнего файла — либо сразу, если "
        "нажать «⬇️ Скачать сейчас».\n"
        "3. Во время скачивания статус показывает, сколько файлов уже "
        "сохранено и что скачивается прямо сейчас.\n"
        "4. В конце вы получите итог: количество файлов и общий размер.\n\n"
        "Эта справка доступна в любой момент — командой /help или кнопкой ниже."
    )


def build_finish_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬇️ Скачать сейчас", callback_data=FINISH_BATCH_DATA)]]
    )


def build_help_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❓ Справка", callback_data=SHOW_HELP_DATA)]]
    )


def build_bot_commands() -> "list[BotCommand]":
    return [
        BotCommand("start", "Запустить бота и показать справку"),
        BotCommand("help", "Показать справку по использованию"),
    ]


async def refresh_status_message(
    batch: Batch, live: LiveProgress, status_message, batch_timeout: float
):
    try:
        if batch.phase == BatchPhase.PENDING:
            text = build_pending_text(batch, batch_timeout)
            markup = build_finish_keyboard()
        else:
            text = build_status_text(batch, live)
            markup = None
        return await status_message.edit_text(text, reply_markup=markup)
    except Exception:
        logger.exception("Failed to refresh status message for user %s", batch.user_id)
        return status_message


def create_app(config: Config, state: BotState) -> Client:
    app = Client(
        "mediasaver_bot",
        api_id=config.api_id,
        api_hash=config.api_hash,
        bot_token=config.bot_token,
        in_memory=True,
    )

    async def on_batch_created(batch: Batch) -> None:
        batch_dir = create_batch_dir(config.storage_dir, batch.started_at)
        state.batch_dirs[batch.batch_id] = batch_dir
        state.manifests[batch.batch_id] = Manifest(batch_dir, batch.started_at)
        state.live_progress[batch.batch_id] = LiveProgress()
        status_message = await app.send_message(
            batch.user_id,
            build_pending_text(batch, config.batch_timeout),
            reply_markup=build_finish_keyboard(),
        )
        state.status_messages[batch.batch_id] = status_message
        state.ticker_tasks[batch.batch_id] = asyncio.create_task(
            run_status_ticker(batch, state, config.batch_timeout)
        )

    async def on_batch_sealed(batch: Batch) -> None:
        status_message = state.status_messages.get(batch.batch_id)
        if status_message is None:
            return
        live = state.live_progress.get(batch.batch_id, LiveProgress())
        state.status_messages[batch.batch_id] = await status_message.edit_text(
            build_status_text(batch, live), reply_markup=None
        )

    async def on_download_file(batch: Batch, message: Message) -> DownloadResult:
        batch_dir = state.batch_dirs[batch.batch_id]
        date_str = message.date.strftime("%Y%m%d-%H%M%S")

        if message.text is not None:
            return await save_text_message(message, batch_dir, date_str=date_str)

        media_info = extract_media_info(message)
        display_name = (media_info.file_name if media_info else None) or "файл"
        live = state.live_progress.setdefault(batch.batch_id, LiveProgress())
        progress = make_progress_callback(live, display_name)

        try:
            outcome = await download_media_message(
                app, message, batch_dir, date_str=date_str, progress=progress
            )
        finally:
            live.current_name = None
        return outcome

    async def on_file_downloaded(batch: Batch, outcome: DownloadResult) -> None:
        manifest = state.manifests[batch.batch_id]
        manifest.add_entry(
            ManifestEntry(
                message_id=outcome.message_id,
                original_name=outcome.original_name,
                stored_name=outcome.stored_name,
                media_type=outcome.media_type,
                size_bytes=outcome.size_bytes,
                message_date=outcome.message_date,
                caption=outcome.caption,
                error=outcome.error,
            )
        )

    async def on_batch_closed(batch: Batch) -> None:
        batch_dir = state.batch_dirs.pop(batch.batch_id, None)
        state.manifests.pop(batch.batch_id, None)
        state.live_progress.pop(batch.batch_id, None)
        ticker_task = state.ticker_tasks.pop(batch.batch_id, None)
        if ticker_task is not None:
            ticker_task.cancel()
        status_message = state.status_messages.pop(batch.batch_id, None)
        if status_message is not None and batch_dir is not None:
            await status_message.edit_text(
                build_summary_text(batch, batch_dir), reply_markup=None
            )

    batch_manager = BatchManager(
        batch_timeout=config.batch_timeout,
        on_batch_created=on_batch_created,
        on_batch_sealed=on_batch_sealed,
        on_download_file=on_download_file,
        on_file_downloaded=on_file_downloaded,
        on_batch_closed=on_batch_closed,
    )

    @app.on_message(filters.command(["start", "help"]) & filters.private)
    async def handle_help_commands(client: Client, message: Message) -> None:
        if not is_allowed(message.from_user.id if message.from_user else None, config):
            return

        await message.reply_text(
            build_help_text(config.batch_timeout), reply_markup=build_help_keyboard()
        )

    async def handle_enqueue(message: Message) -> None:
        if not is_allowed(message.from_user.id if message.from_user else None, config):
            return

        user_id = message.from_user.id
        await batch_manager.enqueue(user_id, message)

    @app.on_message(MEDIA_FILTER & FORWARDED_FILTER & filters.private)
    async def handle_media(client: Client, message: Message) -> None:
        await handle_enqueue(message)

    @app.on_message(TEXT_FILTER & FORWARDED_FILTER & filters.private)
    async def handle_text(client: Client, message: Message) -> None:
        await handle_enqueue(message)

    @app.on_callback_query()
    async def handle_callback_query(client: Client, callback_query: CallbackQuery) -> None:
        if callback_query.data not in (FINISH_BATCH_DATA, SHOW_HELP_DATA):
            return

        user_id = callback_query.from_user.id
        if not is_allowed(user_id, config):
            await callback_query.answer()
            return

        if callback_query.data == SHOW_HELP_DATA:
            await callback_query.answer()
            await callback_query.message.reply_text(
                build_help_text(config.batch_timeout), reply_markup=build_help_keyboard()
            )
            return

        sealed = await batch_manager.seal_batch(user_id)
        if sealed is not None:
            await callback_query.answer("Скачивание начинается")
        else:
            await callback_query.answer()

    return app


async def run_bot(config: Config, state: BotState) -> None:
    app = create_app(config, state)
    await app.start()
    try:
        await app.set_bot_commands(build_bot_commands())
        logger.info("Starting mediasaver bot")
        await idle()
    finally:
        await app.stop()


def main() -> None:
    config = load_config(os.environ)
    config.storage_dir.mkdir(parents=True, exist_ok=True)
    asyncio.run(run_bot(config, BotState()))


if __name__ == "__main__":
    main()
