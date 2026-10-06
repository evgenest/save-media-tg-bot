from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional

from pyrogram import enums

from unsupported import (
    FIXED_NOTICE_TEXT,
    NOT_FORWARDED_TEXT,
    OWNER_REPORT_HINT,
    REPORT_NO_DATA,
    REPORT_YES_DATA,
    build_fixed_data,
    build_owner_report_text,
    build_report_keyboard,
    build_unsupported_text,
    describe_unsupported,
    explain_not_forwarded,
    format_user,
    handle_fixed,
    handle_report_choice,
    offer_report,
    parse_fixed_data,
)

OWNER_ID = 999


def make_user(**overrides):
    defaults = dict(id=111, first_name="Ann", last_name=None, username="ann")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@dataclass
class FakeBotMessage:
    text: str
    reply_to_message_id: Optional[int] = None
    chat: SimpleNamespace = field(default_factory=lambda: SimpleNamespace(id=111))
    edits: list = field(default_factory=list)

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))

    async def edit_reply_markup(self, reply_markup=None):
        self.edits.append((None, reply_markup))


@dataclass
class FakeCallbackQuery:
    data: str
    message: FakeBotMessage
    from_user: SimpleNamespace = field(default_factory=make_user)
    answers: list = field(default_factory=list)

    async def answer(self, text=None, show_alert=None):
        self.answers.append(text)


class FakeClient:
    def __init__(self, forward_fails: bool = False, reply_fails: bool = False):
        self.forward_fails = forward_fails
        self.reply_fails = reply_fails
        self.forwards: list = []
        self.sent: list = []

    async def get_messages(self, chat_id, message_id):
        return SimpleNamespace(id=message_id, media=enums.MessageMediaType.STICKER)

    async def forward_messages(self, chat_id, from_chat_id, message_ids):
        if self.forward_fails:
            raise RuntimeError("CHAT_FORWARDS_RESTRICTED")
        self.forwards.append((chat_id, from_chat_id, message_ids))
        return SimpleNamespace(id=900)

    async def send_message(self, chat_id, text, reply_markup=None, reply_parameters=None):
        if self.reply_fails and reply_parameters is not None:
            raise RuntimeError("MESSAGE_ID_INVALID")
        self.sent.append(
            SimpleNamespace(
                chat_id=chat_id, text=text, markup=reply_markup, reply=reply_parameters
            )
        )


def make_reply_recorder():
    replies = []

    async def reply_text(text, reply_markup=None, reply_parameters=None):
        replies.append((text, reply_markup, reply_parameters))

    return replies, reply_text


NOTICE_TEXT = build_unsupported_text("стикер")


def test_describe_unsupported_known_media():
    message = SimpleNamespace(media=enums.MessageMediaType.POLL)
    assert describe_unsupported(message) == "опрос"


def test_describe_unsupported_no_media_no_text():
    assert "нет ни текста, ни медиа" in describe_unsupported(SimpleNamespace(media=None))


def test_build_report_keyboard_has_yes_and_no():
    row = build_report_keyboard().inline_keyboard[0]
    assert [b.callback_data for b in row] == [REPORT_YES_DATA, REPORT_NO_DATA]


def test_fixed_data_roundtrip_and_fits_telegram_limit():
    data = build_fixed_data(8594873330, 2147483647)
    assert len(data.encode()) <= 64
    assert parse_fixed_data(data) == (8594873330, 2147483647)
    assert parse_fixed_data("fixed:oops") is None
    assert parse_fixed_data(REPORT_YES_DATA) is None


def test_format_user_and_owner_report():
    user = make_user(last_name="Lee")
    assert format_user(user) == "Ann Lee @ann (id 111)"
    text = build_owner_report_text(user, "стикер", 42)
    assert "Ann Lee @ann (id 111)" in text and "стикер" in text and "42" in text


async def test_offer_report_replies_to_message_with_keyboard():
    replies, reply_text = make_reply_recorder()
    message = SimpleNamespace(id=307, media=None, from_user=make_user(), reply_text=reply_text)

    await offer_report(message)

    text, markup, reply_parameters = replies[0]
    assert "пропущено" in text and "Переслать?" in text
    assert markup is not None
    assert reply_parameters.message_id == 307


async def test_explain_not_forwarded_replies_with_hint():
    replies, reply_text = make_reply_recorder()

    await explain_not_forwarded(SimpleNamespace(id=337, reply_text=reply_text))

    text, markup, reply_parameters = replies[0]
    assert text == NOT_FORWARDED_TEXT
    assert markup is None
    assert reply_parameters.message_id == 337


async def test_report_no_keeps_notice_and_drops_question():
    notice = FakeBotMessage(text=NOTICE_TEXT, reply_to_message_id=307)
    query = FakeCallbackQuery(data=REPORT_NO_DATA, message=notice)
    client = FakeClient()

    await handle_report_choice(client, query, owner_user_id=OWNER_ID)

    assert client.forwards == [] and client.sent == []
    text, markup = notice.edits[0]
    assert "Переслать?" not in text and "оставляю как есть" in text
    assert markup is None


async def test_report_yes_forwards_original_and_sends_report_with_fixed_button():
    notice = FakeBotMessage(text=NOTICE_TEXT, reply_to_message_id=307)
    query = FakeCallbackQuery(data=REPORT_YES_DATA, message=notice)
    client = FakeClient()

    await handle_report_choice(client, query, owner_user_id=OWNER_ID)

    assert client.forwards == [(OWNER_ID, 111, 307)]
    report = client.sent[0]
    assert report.chat_id == OWNER_ID
    assert "стикер" in report.text and "id 111" in report.text
    assert report.reply.message_id == 900
    button = report.markup.inline_keyboard[0][0]
    assert parse_fixed_data(button.callback_data) == (111, 307)
    assert "Передано разработчику" in notice.edits[0][0]


async def test_report_yes_still_reports_when_forward_is_blocked():
    notice = FakeBotMessage(text=NOTICE_TEXT, reply_to_message_id=307)
    query = FakeCallbackQuery(data=REPORT_YES_DATA, message=notice)
    client = FakeClient(forward_fails=True)

    await handle_report_choice(client, query, owner_user_id=OWNER_ID)

    report = client.sent[0]
    assert report.chat_id == OWNER_ID and "Переслать оригинал не удалось" in report.text
    assert report.markup is not None
    assert "Передано разработчику" in notice.edits[0][0]


def make_fixed_query(from_id: int = OWNER_ID):
    report = FakeBotMessage(
        text=build_owner_report_text(make_user(), "стикер", 307),
        chat=SimpleNamespace(id=OWNER_ID),
    )
    return FakeCallbackQuery(
        data=build_fixed_data(111, 307), message=report, from_user=make_user(id=from_id)
    )


async def test_fixed_notifies_sender_as_reply_and_marks_report():
    query = make_fixed_query()
    client = FakeClient()

    await handle_fixed(client, query, owner_user_id=OWNER_ID)

    notice = client.sent[0]
    assert (notice.chat_id, notice.text) == (111, FIXED_NOTICE_TEXT)
    assert notice.reply.message_id == 307
    text, markup = query.message.edits[0]
    assert "Исправлено, пользователь уведомлён" in text
    assert OWNER_REPORT_HINT not in text
    assert markup is None


async def test_fixed_falls_back_to_plain_message_when_original_is_gone():
    query = make_fixed_query()
    client = FakeClient(reply_fails=True)

    await handle_fixed(client, query, owner_user_id=OWNER_ID)

    assert client.sent[0].chat_id == 111 and client.sent[0].reply is None


async def test_fixed_ignored_for_non_owner():
    query = make_fixed_query(from_id=111)
    client = FakeClient()

    await handle_fixed(client, query, owner_user_id=OWNER_ID)

    assert client.sent == [] and query.message.edits == []
