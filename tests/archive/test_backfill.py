from types import SimpleNamespace
from zoneinfo import ZoneInfo

from pyrogram.errors import FloodWait

from mediasaver.archive.backfill import backfill_chat
from mediasaver.archive.config import ArchiveConfig
from mediasaver.archive.group import ChatArchive, archive_for
from mediasaver.archive.store import ArchiveStore
from tests.archive.archive_fakes import FakeClient, message

CHAT = -100
FORUM = SimpleNamespace(id=CHAT, title="Разработка", is_forum=True)


def make_archives(tmp_path):
    config = ArchiveConfig(
        chats={(CHAT, 543): tmp_path / "cf"},
        timezone=ZoneInfo("Europe/Berlin"),
        session_dir=tmp_path / "s",
        elevenlabs_api_key=None,
        transcribe_max_seconds=1800,
        stt_model="scribe_v2",
    )
    return {key: ChatArchive(ArchiveStore(root), config) for key, root in config.chats.items()}


class HistoryClient(FakeClient):
    def __init__(self, messages, flood_once=False):
        super().__init__()
        self.messages = {m.id: m for m in messages}
        self.calls = []
        self.flood_once = flood_once

    async def get_messages(self, chat_id, ids, replies=1):
        self.calls.append((ids[0], ids[-1], replies))
        if self.flood_once:
            self.flood_once = False
            raise FloodWait(value=30)
        return [self.messages.get(i) or SimpleNamespace(id=i, empty=True) for i in ids]


def topic_msg(message_id, text, topic=543, **extra):
    fields = {"message_thread_id": topic, "reply_to_message_id": topic, **extra}
    return message(message_id, text=text, chat=FORUM, empty=False, **fields)


async def no_sleep(_):
    return None


async def test_backfill_reads_only_its_topic_and_remembers_position(tmp_path):
    history = [
        message(543, chat=FORUM, empty=False, service=SimpleNamespace(name="FORUM_TOPIC_CREATED"),
                forum_topic_created=SimpleNamespace(title="лендинг")),
        topic_msg(544, "по колористике"),
        topic_msg(545, "другой проект", topic=428),
        topic_msg(546, "ответ", reply_to_message_id=544),
        topic_msg(390, "старое, General", topic=None),
    ]
    archives = make_archives(tmp_path)
    client = HistoryClient(history, flood_once=True)

    added = await backfill_chat(client, CHAT, archives, sleep=no_sleep, pause=0)

    archive = archives[(CHAT, 543)]
    records = {r["id"]: r for r in archive.store.load()}
    assert added == 3
    assert sorted(records) == [543, 544, 546]
    assert records[544]["reply_to"] is None
    assert records[546]["reply_to"] == {"id": 544}
    assert archive.store.read_meta()["scanned_until"] == 546
    assert all(call[2] == 0 for call in client.calls)  # no reply fetching
    day = (tmp_path / "cf" / "days").iterdir().__next__().read_text(encoding="utf-8")
    assert "по колористике" in day and "другой проект" not in day
    assert day.startswith("# лендинг — ")  # the topic's title, not the group's

    # Second start: resumes after the last seen id, nothing re-added.
    client.calls.clear()
    assert await backfill_chat(client, CHAT, archives, sleep=no_sleep, pause=0) == 0
    assert client.calls[0][0] == 547


async def test_live_message_already_backfilled_is_skipped(tmp_path):
    archives = make_archives(tmp_path)
    live = topic_msg(700, "новое")
    archive = archive_for(archives, live)
    await archive.on_message(FakeClient(), live)
    await archive.on_message(FakeClient(), live)

    assert len(archive.store.log_path.read_text(encoding="utf-8").splitlines()) == 1
