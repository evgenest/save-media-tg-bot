from datetime import datetime, timezone
from types import SimpleNamespace

from mediasaver.archive.record import build_record, message_kind, snippet
from tests.archive.archive_fakes import entity, message, user


def test_text_message_record():
    record = build_record(message(text="привет мир", entities=[entity("bold", 0, 6)]))

    assert record["id"] == 10
    assert record["kind"] == "text"
    assert record["text"] == "**привет** мир"
    assert record["from"] == {"id": 1, "name": "Мариана Вайс", "username": None}
    assert record["date"] == "2026-10-15T12:30:00+00:00"
    assert record["reply_to"] is None
    assert record["source"] == "bot"


def test_reply_with_quote_and_forward():
    replied = message(5, text="первое сообщение очень длинное", from_user=user(2, "Евгений", None))
    record = build_record(
        message(
            text="ответ",
            reply_to_message=replied,
            reply_to_message_id=5,
            quote=SimpleNamespace(text="очень длинное"),
            forward_origin=SimpleNamespace(sender_user=user(3, "Кто-то", None)),
        )
    )

    assert record["reply_to"] == {"id": 5, "from": "Евгений", "snippet": "первое сообщение очень длинное"}
    assert record["quote"] == "очень длинное"
    assert record["forward_from"] == "Кто-то"


def test_photo_caption_is_text():
    record = build_record(
        message(photo=SimpleNamespace(file_size=100), caption="подпись", caption_entities=[entity("italic", 0, 7)])
    )

    assert record["kind"] == "photo"
    assert record["text"] == "*подпись*"


def test_voice_kind_and_snippet():
    voice = message(voice=SimpleNamespace(duration=42, mime_type="audio/ogg"))

    assert message_kind(voice) == "voice"
    assert snippet(voice) == "[voice]"


def test_service_message():
    record = build_record(
        message(service=SimpleNamespace(name="NEW_CHAT_MEMBERS"), new_chat_members=[user(5, "Бот", None)])
    )

    assert record["kind"] == "service"
    assert record["extra"] == "добавлены в чат: Бот"


def test_naive_dates_are_converted_to_utc():
    record = build_record(message(text="x", date=datetime(2026, 10, 15, 12, 30)))

    assert record["date"].endswith("+00:00")
