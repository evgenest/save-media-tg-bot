from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Optional

from pyrogram import enums

from unsupported import (
    NOT_FORWARDED_TEXT,
    REPORT_NO_DATA,
    REPORT_YES_DATA,
    build_developer_report_text,
    build_report_keyboard,
    build_unsupported_text,
    describe_unsupported,
    explain_not_forwarded,
    format_user,
    handle_report_choice,
    offer_report,
)


def make_user(**overrides):
    defaults = dict(id=111, first_name="Ann", last_name=None, username="ann")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@dataclass
class FakeNotice:
    text: str
    reply_to_message_id: Optional[int]
    chat: SimpleNamespace = field(default_factory=lambda: SimpleNamespace(id=111))
    edits: list = field(default_factory=list)

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))

    async def edit_reply_markup(self, reply_markup=None):
        self.edits.append((None, reply_markup))


@dataclass
class FakeCallbackQuery:
    data: str
    message: FakeNotice
    from_user: SimpleNamespace = field(default_factory=make_user)
    answers: list = field(default_factory=list)

    async def answer(self, text=None, show_alert=None):
        self.answers.append(text)


class FakeClient:
    def __init__(self, forward_fails: bool = False):
        self.forward_fails = forward_fails
        self.forwards: list = []
        self.sent: list = []

    async def get_messages(self, chat_id, message_id):
        return SimpleNamespace(id=message_id, media=enums.MessageMediaType.STICKER)

    async def forward_messages(self, chat_id, from_chat_id, message_ids):
        if self.forward_fails:
            raise RuntimeError("CHAT_FORWARDS_RESTRICTED")
        self.forwards.append((chat_id, from_chat_id, message_ids))
        return SimpleNamespace(id=900)

    async def send_message(self, chat_id, text, reply_parameters=None):
        self.sent.append((chat_id, text, reply_parameters))


NOTICE_TEXT = build_unsupported_text("стикер", can_report=True)


def test_describe_unsupported_known_media():
    message = SimpleNamespace(media=enums.MessageMediaType.POLL)
    assert describe_unsupported(message) == "опрос"


def test_describe_unsupported_no_media_no_text():
    assert "нет ни текста, ни медиа" in describe_unsupported(SimpleNamespace(media=None))


def test_build_unsupported_text_asks_only_when_reportable():
    assert "Переслать?" in build_unsupported_text("стикер", can_report=True)
    assert "Переслать?" not in build_unsupported_text("стикер", can_report=False)


def test_build_report_keyboard_has_yes_and_no():
    row = build_report_keyboard().inline_keyboard[0]
    assert [b.callback_data for b in row] == [REPORT_YES_DATA, REPORT_NO_DATA]


def test_format_user_and_developer_report():
    user = make_user(last_name="Lee")
    assert format_user(user) == "Ann Lee @ann (id 111)"
    text = build_developer_report_text(user, "стикер", 42)
    assert "Ann Lee @ann (id 111)" in text and "стикер" in text and "42" in text


async def test_offer_report_replies_to_message_with_keyboard():
    replies = []

    async def reply_text(text, reply_markup=None, reply_parameters=None):
        replies.append((text, reply_markup, reply_parameters))

    message = SimpleNamespace(
        id=307, media=None, from_user=make_user(), reply_text=reply_text
    )

    await offer_report(message, developer_user_id=111)

    text, markup, reply_parameters = replies[0]
    assert "пропущено" in text
    assert markup is not None
    assert reply_parameters.message_id == 307


async def test_offer_report_without_developer_has_no_keyboard():
    replies = []

    async def reply_text(text, reply_markup=None, reply_parameters=None):
        replies.append((text, reply_markup))

    message = SimpleNamespace(id=1, media=None, from_user=make_user(), reply_text=reply_text)

    await offer_report(message, developer_user_id=None)

    assert replies[0][1] is None


async def test_report_no_keeps_notice_and_drops_question():
    notice = FakeNotice(text=NOTICE_TEXT, reply_to_message_id=307)
    query = FakeCallbackQuery(data=REPORT_NO_DATA, message=notice)
    client = FakeClient()

    await handle_report_choice(client, query, developer_user_id=999)

    assert client.forwards == [] and client.sent == []
    text, markup = notice.edits[0]
    assert "Переслать?" not in text and "оставляю как есть" in text
    assert markup is None


async def test_report_yes_forwards_original_and_sends_report_as_reply():
    notice = FakeNotice(text=NOTICE_TEXT, reply_to_message_id=307)
    query = FakeCallbackQuery(data=REPORT_YES_DATA, message=notice)
    client = FakeClient()

    await handle_report_choice(client, query, developer_user_id=999)

    assert client.forwards == [(999, 111, 307)]
    chat_id, text, reply_parameters = client.sent[0]
    assert chat_id == 999
    assert "стикер" in text and "id 111" in text
    assert reply_parameters.message_id == 900
    assert "Передано разработчику" in notice.edits[0][0]


async def test_report_yes_still_reports_when_forward_is_blocked():
    notice = FakeNotice(text=NOTICE_TEXT, reply_to_message_id=307)
    query = FakeCallbackQuery(data=REPORT_YES_DATA, message=notice)
    client = FakeClient(forward_fails=True)

    await handle_report_choice(client, query, developer_user_id=999)

    chat_id, text, _ = client.sent[0]
    assert chat_id == 999 and "Переслать оригинал не удалось" in text
    assert "Передано разработчику" in notice.edits[0][0]


async def test_explain_not_forwarded_replies_with_hint():
    replies = []

    async def reply_text(text, reply_markup=None, reply_parameters=None):
        replies.append((text, reply_markup, reply_parameters))

    message = SimpleNamespace(id=337, reply_text=reply_text)

    await explain_not_forwarded(message)

    text, markup, reply_parameters = replies[0]
    assert text == NOT_FORWARDED_TEXT
    assert markup is None
    assert reply_parameters.message_id == 337
