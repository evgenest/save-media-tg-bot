from __future__ import annotations

import logging
from typing import Optional, Tuple

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters

logger = logging.getLogger("mediasaver")

# Guard for forwarded messages that no content handler claimed: instead of
# silently dropping them, the bot tells the sender and offers to forward the
# message to the bot's owner (the developer) so support for that type can be
# added. The owner's report has a "Fixed" button that notifies the sender.

REPORT_YES_DATA = "report_unsupported"
REPORT_NO_DATA = "dismiss_unsupported"
# "fixed:<chat_id>:<message_id>" - where to send the "fixed" notice.
FIXED_DATA_PREFIX = "fixed:"

_MEDIA_TYPE_NAMES = {
    "sticker": "стикер",
    "live_photo": "живое фото",
    "contact": "контакт",
    "location": "геолокация",
    "venue": "место на карте",
    "poll": "опрос",
    "web_page": "ссылка с превью",
    "dice": "кубик / анимированный эмодзи",
    "game": "игра",
    "giveaway": "розыгрыш",
    "giveaway_winners": "итоги розыгрыша",
    "story": "история",
    "invoice": "счёт на оплату",
    "paid_media": "платный контент",
    "checklist": "чек-лист",
    "unsupported": "тип, неизвестный библиотеке бота",
}


def describe_unsupported(message) -> str:
    media = getattr(message, "media", None)
    if media is not None:
        key = str(getattr(media, "value", media)).lower()
        return _MEDIA_TYPE_NAMES.get(key, key)
    return "неизвестный формат: нет ни текста, ни медиа"


def build_unsupported_text(description: str) -> str:
    return (
        "⚠️ Это сообщение пропущено: бот пока не умеет его сохранять "
        f"(тип: {description}).\n\n"
        "Чтобы это починить, могу переслать сообщение разработчику — "
        "он разберётся и добавит поддержку. Переслать?"
    )


NOT_FORWARDED_TEXT = (
    "Я сохраняю только пересланные сообщения. Перешлите сюда сообщение "
    "(или несколько) из другого чата — и я его сохраню. Справка: /help"
)

FIXED_NOTICE_TEXT = (
    "✅ Ошибку с этим сообщением исправили. Перешлите его боту ещё раз — "
    "теперь оно должно сохраниться."
)


async def explain_not_forwarded(message) -> None:
    """Typed/sent-directly messages are skipped by design, not a bug - so no
    report to the developer, just a hint instead of silence."""
    await message.reply_text(
        NOT_FORWARDED_TEXT, reply_parameters=ReplyParameters(message_id=message.id)
    )


def build_report_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Да, переслать", callback_data=REPORT_YES_DATA),
                InlineKeyboardButton("Нет", callback_data=REPORT_NO_DATA),
            ]
        ]
    )


def build_fixed_data(chat_id: int, message_id: int) -> str:
    return f"{FIXED_DATA_PREFIX}{chat_id}:{message_id}"


def parse_fixed_data(data: str) -> Optional[Tuple[int, int]]:
    if not data.startswith(FIXED_DATA_PREFIX):
        return None
    try:
        chat_id, message_id = data[len(FIXED_DATA_PREFIX) :].split(":")
        return int(chat_id), int(message_id)
    except ValueError:
        return None


def build_fixed_keyboard(chat_id: int, message_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Исправлено", callback_data=build_fixed_data(chat_id, message_id)
                )
            ]
        ]
    )


def format_user(user) -> str:
    if user is None:
        return "неизвестный пользователь"
    name = " ".join(part for part in (user.first_name, user.last_name) if part) or "без имени"
    username = f" @{user.username}" if user.username else ""
    return f"{name}{username} (id {user.id})"


OWNER_REPORT_HINT = (
    "\n\nКогда поддержка будет добавлена, нажмите «Исправлено» — "
    "пользователь получит уведомление."
)


def build_owner_report_text(user, description: str, message_id: int) -> str:
    return (
        "Бот получил сообщение, которое не смог обработать.\n"
        f"От: {format_user(user)}\n"
        f"Тип: {description}\n"
        f"message_id: {message_id}"
        f"{OWNER_REPORT_HINT}"
    )


async def offer_report(message) -> None:
    description = describe_unsupported(message)
    logger.warning(
        "Unsupported message %s from user %s: %s",
        message.id,
        message.from_user.id if message.from_user else None,
        description,
    )
    await message.reply_text(
        build_unsupported_text(description),
        reply_markup=build_report_keyboard(),
        reply_parameters=ReplyParameters(message_id=message.id),
    )


async def handle_report_choice(client, callback_query, owner_user_id: int) -> None:
    """Callback for the Yes/No buttons under the "message skipped" notice.

    The notice is a reply to the skipped message, so the original is found via
    `reply_to_message_id` - no state is kept, buttons survive bot restarts.
    """
    notice = callback_query.message
    original_id = getattr(notice, "reply_to_message_id", None)
    # First paragraph is the "message skipped" line; drop the Yes/No question.
    skipped_line = (notice.text or "").split("\n\n")[0]

    if callback_query.data == REPORT_NO_DATA:
        await callback_query.answer()
        await notice.edit_text(f"{skipped_line}\n\nОк, оставляю как есть.", reply_markup=None)
        return

    if original_id is None:
        await callback_query.answer("Переслать не получится", show_alert=True)
        await notice.edit_reply_markup(reply_markup=None)
        return

    chat_id = notice.chat.id
    try:
        original = await client.get_messages(chat_id, original_id)
        report_text = build_owner_report_text(
            callback_query.from_user, describe_unsupported(original), original_id
        )
        keyboard = build_fixed_keyboard(chat_id, original_id)
        try:
            forwarded = await client.forward_messages(
                owner_user_id, from_chat_id=chat_id, message_ids=original_id
            )
        except Exception as exc:  # e.g. protected content: still deliver the report itself
            logger.warning("Could not forward unsupported message %s: %s", original_id, exc)
            await client.send_message(
                owner_user_id,
                report_text.replace(
                    OWNER_REPORT_HINT, f"\nПереслать оригинал не удалось: {exc}{OWNER_REPORT_HINT}"
                ),
                reply_markup=keyboard,
            )
        else:
            await client.send_message(
                owner_user_id,
                report_text,
                reply_markup=keyboard,
                reply_parameters=ReplyParameters(message_id=forwarded.id),
            )
    except Exception:
        logger.exception("Failed to report unsupported message %s to owner", original_id)
        await callback_query.answer("Не удалось переслать, попробуйте позже", show_alert=True)
        return

    await callback_query.answer("Передано разработчику")
    await notice.edit_text(
        f"{skipped_line}\n\n✅ Передано разработчику. Спасибо!", reply_markup=None
    )


async def handle_fixed(client, callback_query, owner_user_id: int) -> None:
    """Owner pressed "Fixed" under a report: notify the sender with a reply to
    their original message (or a plain message if it's gone)."""
    if callback_query.from_user.id != owner_user_id:
        await callback_query.answer()
        return

    target = parse_fixed_data(callback_query.data or "")
    if target is None:
        await callback_query.answer("Некорректная кнопка", show_alert=True)
        return
    chat_id, message_id = target

    try:
        try:
            await client.send_message(
                chat_id,
                FIXED_NOTICE_TEXT,
                reply_parameters=ReplyParameters(message_id=message_id),
            )
        except Exception as exc:  # original deleted by the user, etc.
            logger.warning("Fixed notice as reply to %s failed: %s", message_id, exc)
            await client.send_message(chat_id, FIXED_NOTICE_TEXT)
    except Exception:
        logger.exception("Failed to send fixed notice to chat %s", chat_id)
        await callback_query.answer("Не удалось уведомить пользователя", show_alert=True)
        return

    await callback_query.answer("Пользователь уведомлён")
    report_text = (callback_query.message.text or "").replace(OWNER_REPORT_HINT, "")
    await callback_query.message.edit_text(
        f"{report_text}\n\n✅ Исправлено, пользователь уведомлён.", reply_markup=None
    )
