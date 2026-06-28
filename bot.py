from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, Optional

from pyrogram import Client, filters
from pyrogram.types import (
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

MEDIA_FILTER = (
    filters.photo
    | filters.video
    | filters.audio
    | filters.document
    | filters.voice
    | filters.video_note
    | filters.animation
)


class BotState:
    def __init__(self) -> None:
        self.batch_dirs: Dict[int, Path] = {}
        self.manifests: Dict[int, Manifest] = {}
        self.status_messages: Dict[int, Message] = {}


def is_allowed(user_id: Optional[int], config: Config) -> bool:
    return user_id is not None and user_id in config.allowed_user_ids


def build_status_text(batch: Batch) -> str:
    size_mb = batch.total_bytes / (1024 * 1024)
    text = f"Сохранено: {batch.file_count} файлов, {size_mb:.1f} МБ"
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


def build_finish_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Завершить пакет", callback_data=FINISH_BATCH_DATA)]]
    )


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
        if callback_query.data != FINISH_BATCH_DATA:
            return

        user_id = callback_query.from_user.id
        if not is_allowed(user_id, config):
            await callback_query.answer()
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
