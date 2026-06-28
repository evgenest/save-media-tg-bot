from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

from downloader import (
    DownloadResult,
    MediaInfo,
    build_default_filename,
    download_media_message,
    extract_media_info,
    resolve_filename_collision,
    sanitize_filename,
)


@dataclass
class FakeMedia:
    file_name: Optional[str] = None
    file_size: int = 0
    mime_type: Optional[str] = None


@dataclass
class FakeMessage:
    id: int
    caption: Optional[str] = None
    photo: Optional[FakeMedia] = None
    video: Optional[FakeMedia] = None
    audio: Optional[FakeMedia] = None
    document: Optional[FakeMedia] = None
    voice: Optional[FakeMedia] = None
    video_note: Optional[FakeMedia] = None
    animation: Optional[FakeMedia] = None


class StubClient:
    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.calls: "list[str]" = []

    async def download_media(self, message, file_name: str) -> str:
        self.calls.append(file_name)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("network error")
        Path(file_name).write_bytes(b"x" * 10)
        return file_name


def test_sanitize_filename_replaces_unsafe_chars():
    assert sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "a_b_c_d_e_f_g_h_i_j"


def test_sanitize_filename_empty_after_sanitization_returns_fallback():
    assert sanitize_filename("...") == "file"


def test_resolve_filename_collision_no_collision_returns_original(tmp_path):
    assert resolve_filename_collision(tmp_path, "video.mp4") == "video.mp4"


def test_resolve_filename_collision_appends_numbered_suffix(tmp_path):
    (tmp_path / "video.mp4").write_bytes(b"")

    assert resolve_filename_collision(tmp_path, "video.mp4") == "video (2).mp4"


def test_resolve_filename_collision_increments_until_free(tmp_path):
    (tmp_path / "video.mp4").write_bytes(b"")
    (tmp_path / "video (2).mp4").write_bytes(b"")

    assert resolve_filename_collision(tmp_path, "video.mp4") == "video (3).mp4"


def test_build_default_filename_with_known_mime_type():
    name = build_default_filename("photo", 42, "20260628-143005", "image/jpeg")
    assert name == "photo_20260628-143005_42.jpg"


def test_build_default_filename_without_mime_type():
    name = build_default_filename("document", 42, "20260628-143005", None)
    assert name == "document_20260628-143005_42"


def test_extract_media_info_returns_none_for_text_message():
    assert extract_media_info(FakeMessage(id=1)) is None


def test_extract_media_info_returns_photo_info():
    message = FakeMessage(id=1, photo=FakeMedia(file_size=2048))

    info = extract_media_info(message)

    assert info == MediaInfo(media_type="photo", file_name=None, file_size=2048, mime_type=None)


async def test_download_media_message_success_first_attempt(tmp_path):
    message = FakeMessage(
        id=7, caption="hi", document=FakeMedia(file_name="report.pdf", mime_type="application/pdf")
    )
    client = StubClient()

    result = await download_media_message(client, message, tmp_path, date_str="20260628-143005")

    assert result == DownloadResult(
        success=True,
        message_id=7,
        media_type="document",
        original_name="report.pdf",
        stored_name="report.pdf",
        size_bytes=10,
        file_path=tmp_path / "report.pdf",
        message_date="20260628-143005",
        caption="hi",
    )
    assert client.calls == [str(tmp_path / "report.pdf")]


async def test_download_media_message_retries_then_succeeds(tmp_path):
    message = FakeMessage(id=8, document=FakeMedia(file_name="report.pdf"))
    client = StubClient(fail_times=1)

    result = await download_media_message(client, message, tmp_path, date_str="d", max_retries=2)

    assert result.success is True
    assert len(client.calls) == 2


async def test_download_media_message_fails_after_max_retries(tmp_path):
    message = FakeMessage(id=9, document=FakeMedia(file_name="report.pdf"))
    client = StubClient(fail_times=5)

    result = await download_media_message(client, message, tmp_path, date_str="d", max_retries=2)

    assert result.success is False
    assert result.error == "network error"
    assert len(client.calls) == 3


async def test_download_media_message_raises_for_non_media_message(tmp_path):
    message = FakeMessage(id=10)
    client = StubClient()

    with pytest.raises(ValueError, match="supported media"):
        await download_media_message(client, message, tmp_path, date_str="d")


async def test_download_media_message_uses_default_filename_when_missing(tmp_path):
    message = FakeMessage(id=11, photo=FakeMedia(mime_type="image/jpeg"))
    client = StubClient()

    result = await download_media_message(client, message, tmp_path, date_str="20260628-143005")

    assert result.stored_name == "photo_20260628-143005_11.jpg"
