from datetime import datetime, timezone
from types import SimpleNamespace

from pyrogram import enums


def user(user_id=1, first="Мариана", last="Вайс", username=None):
    return SimpleNamespace(id=user_id, first_name=first, last_name=last, username=username)


def entity(kind, offset, length, url=None):
    return SimpleNamespace(
        type=getattr(enums.MessageEntityType, kind.upper()), offset=offset, length=length, url=url, language=None
    )


def message(message_id=10, *, text=None, entities=None, from_user=None, date=None, chat_id=-100, **extra):
    fields = dict(
        id=message_id,
        date=date or datetime(2026, 10, 15, 12, 30, tzinfo=timezone.utc),
        edit_date=None,
        from_user=from_user or user(),
        sender_chat=None,
        chat=SimpleNamespace(id=chat_id, title="Color Fundamentals"),
        text=text,
        entities=entities,
        caption=None,
        caption_entities=None,
        reply_to_message=None,
        reply_to_message_id=None,
        external_reply=None,
        quote=None,
        forward_origin=None,
        media_group_id=None,
        rich_message=None,
        service=None,
    )
    fields.update(extra)
    return SimpleNamespace(**fields)


class FakeClient:
    def __init__(self, fail=False):
        self.fail = fail
        self.downloads = []

    async def download_media(self, media, file_name, progress=None):
        if self.fail:
            raise RuntimeError("FILE_REFERENCE_EXPIRED")
        self.downloads.append(file_name)
        with open(file_name, "wb") as f:
            f.write(b"x" * 10)
        return file_name
