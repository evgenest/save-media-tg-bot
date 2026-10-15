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


def test_forum_topic_root_is_not_a_reply():
    record = build_record(message(text="в теме", message_thread_id=543, reply_to_message_id=543))

    assert record["reply_to"] is None


def test_real_reply_inside_forum_topic():
    record = build_record(message(text="ответ", message_thread_id=543, reply_to_message_id=600))

    assert record["reply_to"] == {"id": 600}


def test_topic_of():
    from mediasaver.archive.record import topic_of

    forum = SimpleNamespace(id=-100, title="Разработка", is_forum=True)
    assert topic_of(message(message_thread_id=543)) == 543
    assert topic_of(message(543, forum_topic_created=SimpleNamespace(title="лендинг"), chat=forum)) == 543
    assert topic_of(message(chat=forum)) == 1
    assert topic_of(message()) is None


def test_topic_created_service_text():
    record = build_record(
        message(543, service=SimpleNamespace(name="FORUM_TOPIC_CREATED"),
                forum_topic_created=SimpleNamespace(title="лендинг"))
    )

    assert record["extra"] == "создана тема «лендинг»"
