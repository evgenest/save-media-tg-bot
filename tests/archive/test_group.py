from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from mediasaver.archive import speech as archive_speech
from mediasaver.archive.config import ArchiveConfig
from tests.archive.archive_fakes import FakeClient, message
from mediasaver.archive.store import ArchiveStore
from mediasaver.archive.group import ChatArchive, media_file_name


def make_archive(tmp_path, api_key=None):
    config = ArchiveConfig(
        chats={-100: tmp_path},
        timezone=ZoneInfo("Europe/Berlin"),
        session_dir=tmp_path / "sessions",
        elevenlabs_api_key=api_key,
        transcribe_max_seconds=1800,
    )
    return ChatArchive(ArchiveStore(tmp_path), config)


def test_media_file_name():
    assert media_file_name(5, {"kind": "document", "file_name": "ТЗ: v2.pdf"}) == "5_ТЗ_ v2.pdf"
    assert media_file_name(5, {"kind": "photo"}) == "5_photo.jpg"
    assert media_file_name(5, {"kind": "voice", "mime_type": "audio/ogg"}) == "5_voice.ogg"


async def test_text_message_is_archived_and_rendered(tmp_path):
    archive = make_archive(tmp_path)

    await archive.on_message(FakeClient(), message(text="привет"))

    assert archive.store.load()[0]["text"] == "привет"
    assert archive.store.read_meta()["title"] == "Color Fundamentals"
    day = (tmp_path / "days" / "2026-10-15.md").read_text(encoding="utf-8")
    assert day.startswith("# Color Fundamentals — 2026-10-15")
    assert "привет" in day


async def test_photo_is_downloaded(tmp_path):
    archive = make_archive(tmp_path)

    await archive.on_message(FakeClient(), message(photo=SimpleNamespace(file_size=10)))

    record = archive.store.load()[0]
    assert record["media"] == [{"kind": "photo", "file_size": 10, "path": "media/10_photo.jpg"}]
    assert (tmp_path / "media" / "10_photo.jpg").exists()


async def test_failed_download_is_recorded(tmp_path):
    archive = make_archive(tmp_path)

    await archive.on_message(FakeClient(fail=True), message(document=SimpleNamespace(file_name="a.pdf")))

    item = archive.store.load()[0]["media"][0]
    assert "path" not in item
    assert item["error"] == "FILE_REFERENCE_EXPIRED"


async def test_voice_is_transcribed(tmp_path, monkeypatch):
    calls = []

    def fake_transcribe(path, api_key):
        calls.append((Path(path).name, api_key))
        return "Добрий день"

    monkeypatch.setattr(archive_speech, "transcribe_file", fake_transcribe)
    archive = make_archive(tmp_path, api_key="key")

    await archive.on_message(FakeClient(), message(voice=SimpleNamespace(duration=3, mime_type="audio/ogg")))

    assert calls == [("10_voice.ogg", "key")]
    assert archive.store.load()[0]["transcript"] == "Добрий день"
    assert "Расшифровка:** Добрий день" in (tmp_path / "days" / "2026-10-15.md").read_text(encoding="utf-8")


async def test_no_transcription_without_key(tmp_path, monkeypatch):
    monkeypatch.setattr(archive_speech, "transcribe_file", lambda *a: 1 / 0)
    archive = make_archive(tmp_path)

    await archive.on_message(FakeClient(), message(voice=SimpleNamespace(duration=3)))

    assert "transcript" not in archive.store.load()[0]


async def test_edit_updates_text(tmp_path):
    archive = make_archive(tmp_path)
    await archive.on_message(FakeClient(), message(text="было"))

    edited = message(text="стало", edit_date=datetime(2026, 10, 15, 13, 0, tzinfo=timezone.utc))
    await archive.on_edit(edited)
    await archive.on_edit(message(text="стало"))  # no edit_date: reaction etc

    record = archive.store.load()[0]
    assert record["text"] == "стало"
    assert record["edit_date"] == "2026-10-15T13:00:00+00:00"
    assert len(archive.store.log_path.read_text(encoding="utf-8").splitlines()) == 2
