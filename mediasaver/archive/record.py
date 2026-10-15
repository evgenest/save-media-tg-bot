from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from mediasaver.media.rich_markdown import render_rich_message
from mediasaver.archive.tg_markdown import entities_to_markdown, pyrogram_entities

# Pyrogram Message -> archive record (plain dict, stored as JSON). Media files
# are downloaded separately (archive_bot.py) and added under "media".

MEDIA_KINDS = ("photo", "video", "voice", "audio", "video_note", "animation", "document", "sticker")
# Kinds whose speech gets transcribed (if a transcription key is configured).
SPEECH_KINDS = ("voice", "audio", "video_note", "video")
SNIPPET_LENGTH = 120


def iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:  # Pyrogram returns naive local datetimes
        value = value.astimezone()
    return value.astimezone(timezone.utc).isoformat()


def person_name(user) -> str:
    if user is None:
        return "?"
    parts = [getattr(user, "first_name", None), getattr(user, "last_name", None)]
    name = " ".join(part for part in parts if part)
    return name or getattr(user, "title", None) or getattr(user, "username", None) or "?"


def sender_of(message) -> dict:
    user = getattr(message, "from_user", None)
    if user is not None:
        return {"id": user.id, "name": person_name(user), "username": getattr(user, "username", None)}
    chat = getattr(message, "sender_chat", None)
    if chat is not None:
        return {"id": chat.id, "name": getattr(chat, "title", None) or "?", "username": None}
    return {"id": None, "name": "?", "username": None}


def message_kind(message) -> str:
    for kind in MEDIA_KINDS:
        if getattr(message, kind, None) is not None:
            return kind
    if getattr(message, "rich_message", None) is not None:
        return "rich"
    for kind in ("poll", "location", "venue", "contact", "dice", "story"):
        if getattr(message, kind, None) is not None:
            return kind
    if getattr(message, "service", None) is not None:
        return "service"
    if getattr(message, "text", None) is not None:
        return "text"
    return "other"


def plain_text(message) -> str:
    text = getattr(message, "text", None) or getattr(message, "caption", None)
    return str(text) if text else ""


def snippet(message) -> str:
    text = " ".join(plain_text(message).split())
    if not text:
        kind = message_kind(message)
        return f"[{kind}]" if kind != "text" else ""
    return text if len(text) <= SNIPPET_LENGTH else text[: SNIPPET_LENGTH - 1] + "…"


def markdown_body(message, attachments=None) -> str:
    """Formatted text or caption as CommonMark; rich messages (structured
    blocks, empty `text`) are rendered from their blocks."""
    text = getattr(message, "text", None)
    if text is not None:
        return entities_to_markdown(str(text), pyrogram_entities(getattr(message, "entities", None)))
    rich_message = getattr(message, "rich_message", None)
    if rich_message is not None:
        return render_rich_message(rich_message, attachments or {})
    caption = getattr(message, "caption", None)
    if caption is None:
        return ""
    return entities_to_markdown(
        str(caption), pyrogram_entities(getattr(message, "caption_entities", None))
    )


def forward_label(message) -> Optional[str]:
    origin = getattr(message, "forward_origin", None)
    if origin is None:
        return None
    for attr in ("sender_user", "sender_chat", "chat"):
        source = getattr(origin, attr, None)
        if source is not None:
            return person_name(source)
    return getattr(origin, "sender_user_name", None) or "скрытый отправитель"


def topic_of(message) -> Optional[int]:
    """Forum topic of a message: its thread id, the message's own id for the
    "topic created" service message, 1 for the General topic, None outside
    forums."""
    thread = getattr(message, "message_thread_id", None)
    if thread:
        return thread
    if getattr(message, "forum_topic_created", None) is not None:
        return message.id
    if getattr(getattr(message, "chat", None), "is_forum", False):
        return 1
    return None


def reply_info(message) -> Optional[dict]:
    replied = getattr(message, "reply_to_message", None)
    replied_id = getattr(message, "reply_to_message_id", None)
    if replied is None and replied_id is None:
        return None
    # In a forum every topic message "replies" to the topic's root message;
    # only a reply to some other message is a real one.
    thread = getattr(message, "message_thread_id", None)
    if thread and (getattr(replied, "id", None) or replied_id) == thread:
        return None
    info: dict = {"id": getattr(replied, "id", None) or replied_id}
    if replied is not None:
        info["from"] = sender_of(replied)["name"]
        info["snippet"] = snippet(replied)
    external = getattr(message, "external_reply", None)
    if external is not None and replied is None:
        info["external"] = True  # reply to a message in another chat
    return info


def extra_info(message, kind: str) -> Optional[str]:
    """One-line description of content that has no text or file."""
    if kind == "poll":
        poll = message.poll
        options = "; ".join(getattr(option, "text", str(option)) for option in poll.options or [])
        return f"Опрос: {poll.question} — {options}"
    if kind in ("location", "venue"):
        location = message.location or getattr(message.venue, "location", None)
        title = getattr(message.venue, "title", None) if kind == "venue" else None
        coords = f"{location.latitude}, {location.longitude}" if location else "?"
        return f"Место: {title + ' — ' if title else ''}{coords}"
    if kind == "contact":
        contact = message.contact
        name = " ".join(part for part in (contact.first_name, contact.last_name) if part)
        return f"Контакт: {name} {contact.phone_number or ''}".strip()
    if kind == "dice":
        return f"Кубик: {message.dice.emoji} {message.dice.value}"
    if kind == "sticker":
        return f"Стикер {getattr(message.sticker, 'emoji', '') or ''}".strip()
    if kind == "story":
        return "История (story)"
    if kind == "service":
        return service_description(message)
    return None


def service_description(message) -> str:
    if getattr(message, "new_chat_members", None):
        return "добавлены в чат: " + ", ".join(person_name(user) for user in message.new_chat_members)
    if getattr(message, "left_chat_member", None):
        return f"покинул(а) чат: {person_name(message.left_chat_member)}"
    if getattr(message, "new_chat_title", None):
        return f"новое название чата: {message.new_chat_title}"
    topic = getattr(message, "forum_topic_created", None)
    if topic is not None:
        return f"создана тема «{getattr(topic, 'title', '')}»"
    pinned = getattr(message, "pinned_message", None)
    if pinned is not None:
        return f"закреплено сообщение #{pinned.id}"
    if getattr(message, "migrate_to_chat_id", None):
        return f"группа стала супергруппой, новый id {message.migrate_to_chat_id}"
    service = getattr(message, "service", None)
    return f"служебное: {getattr(service, 'name', service)}".lower()


def media_meta(message, kind: str) -> dict:
    media = getattr(message, kind, None)
    meta = {"kind": kind}
    for attr in ("file_name", "mime_type", "file_size", "duration", "width", "height"):
        value = getattr(media, attr, None)
        if value is not None:
            meta[attr] = value
    return meta


def build_record(message, *, attachments=None) -> dict:
    kind = message_kind(message)
    record = {
        "id": message.id,
        "date": iso(message.date),
        "edit_date": iso(getattr(message, "edit_date", None)),
        "from": sender_of(message),
        "kind": kind,
        "text": markdown_body(message, attachments),
        "reply_to": reply_info(message),
        "quote": None,
        "forward_from": forward_label(message),
        "media_group_id": getattr(message, "media_group_id", None),
        "extra": extra_info(message, kind),
        "source": "bot",
    }
    quote = getattr(message, "quote", None)
    if quote is not None and getattr(quote, "text", None):
        record["quote"] = str(quote.text)
    if record["media_group_id"] is not None:
        record["media_group_id"] = str(record["media_group_id"])
    return record
