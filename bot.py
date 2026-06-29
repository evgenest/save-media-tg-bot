from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Dict, Optional

from pyrogram import Client, filters
from pyrogram.types import (
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from batch_manager import Batch, BatchManager
from config import Config, load_config
from downloader import DownloadResult, download_media_message
from storage import Manifest, ManifestEntry, create_batch_dir

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
    def __init__(self) -> None:
        self.batch_dirs: Dict[int, Path] = {}
        self.manifests: Dict[int, Manifest] = {}
        self.status_messages: Dict[int, Message] = {}
        self.live_progress: Dict[int, LiveProgress] = {}
        self.locks: Dict[int, asyncio.Lock] = {}

    def get_lock(self, user_id: int) -> asyncio.Lock:
        lock = self.locks.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            self.locks[user_id] = lock
        return lock


def is_allowed(user_id: Optional[int], config: Config) -> bool:
    return user_id is not None and user_id in config.allowed_user_ids


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
        "👋 Привет! Этот бот сохраняет медиафайлы, которые вы ему пересылаете.\n\n"
        "Как это работает:\n"
        "1. Пришлите (перешлите) одно или несколько сообщений с фото, видео, "
        "аудио или документами.\n"
        "2. Бот скачивает их по одному и показывает статус — сколько файлов "
        "сохранено и что скачивается прямо сейчас.\n"
        "3. Нажмите «✅ Завершить пакет» под статусом, либо просто не "
        f"присылайте файлы {timeout_seconds} секунд — пакет закроется сам.\n"
        "4. В конце вы получите итог: количество файлов и общий размер.\n\n"
        "Эта справка доступна в любой момент — командой /help или кнопкой ниже."
    )


def build_finish_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Завершить пакет", callback_data=FINISH_BATCH_DATA)]]
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


async def refresh_status_message(batch: Batch, live: LiveProgress, status_message):
    try:
        return await status_message.edit_text(
            build_status_text(batch, live), reply_markup=build_finish_keyboard()
        )
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
        state.batch_dirs[batch.user_id] = batch_dir
        state.manifests[batch.user_id] = Manifest(batch_dir, batch.started_at)
        status_message = await app.send_message(
            batch.user_id, build_status_text(batch), reply_markup=build_finish_keyboard()
        )
        state.status_messages[batch.user_id] = status_message

    async def on_file_added(batch: Batch, outcome: DownloadResult) -> None:
        manifest = state.manifests[batch.user_id]
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
        status_message = state.status_messages.get(batch.user_id)
        if status_message is not None:
            state.status_messages[batch.user_id] = await status_message.edit_text(
                build_status_text(batch), reply_markup=build_finish_keyboard()
            )

    async def on_batch_closed(batch: Batch) -> None:
        batch_dir = state.batch_dirs.pop(batch.user_id, None)
        state.manifests.pop(batch.user_id, None)
        status_message = state.status_messages.pop(batch.user_id, None)
        if status_message is not None and batch_dir is not None:
            await status_message.edit_text(
                build_summary_text(batch, batch_dir), reply_markup=None
            )

    batch_manager = BatchManager(
        batch_timeout=config.batch_timeout,
        on_batch_created=on_batch_created,
        on_file_added=on_file_added,
        on_batch_closed=on_batch_closed,
    )

    @app.on_message(filters.command(["start", "help"]) & filters.private)
    async def handle_help_commands(client: Client, message: Message) -> None:
        if not is_allowed(message.from_user.id if message.from_user else None, config):
            return

        await message.reply_text(
            build_help_text(config.batch_timeout), reply_markup=build_help_keyboard()
        )

    @app.on_message(MEDIA_FILTER & filters.private)
    async def handle_media(client: Client, message: Message) -> None:
        if not is_allowed(message.from_user.id if message.from_user else None, config):
            return

        user_id = message.from_user.id
        await batch_manager.ensure_batch(user_id)
        batch_dir = state.batch_dirs[user_id]
        date_str = message.date.strftime("%Y%m%d-%H%M%S")
        outcome = await download_media_message(client, message, batch_dir, date_str=date_str)
        await batch_manager.record_file(user_id, outcome)

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

        await batch_manager.close_batch(user_id)
        await callback_query.answer("Пакет завершён")

    return app


def main() -> None:
    config = load_config(os.environ)
    config.storage_dir.mkdir(parents=True, exist_ok=True)
    app = create_app(config, BotState())
    logger.info("Starting mediasaver bot")
    app.run()


if __name__ == "__main__":
    main()
