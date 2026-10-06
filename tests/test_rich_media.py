from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

from pyrogram import types

from rich_media import attachment_extension, download_rich_media
from text_export import save_text_message


class FakeClient:
    def __init__(self, fail_names=()):
        self.fail_names = set(fail_names)
        self.downloads = []

    async def download_media(self, media, file_name, progress=None):
        if media.file_id in self.fail_names:
            raise RuntimeError("FILE_REFERENCE_EXPIRED")
        self.downloads.append((media.file_id, file_name))
        if progress:
            await progress(1, 1)
        with open(file_name, "wb") as f:
            f.write(b"x" * 10)
        return file_name


def photo(file_id: str):
    return SimpleNamespace(file_id=file_id)


def video(file_id: str, file_name: Optional[str] = None, mime_type: str = "video/mp4"):
    return SimpleNamespace(file_id=file_id, file_name=file_name, mime_type=mime_type)


@dataclass
class FakeMessage:
    id: int
    rich_message: object
    text: Optional[str] = None


def rich_with_media():
    return types.RichMessage(
        blocks=[
            types.RichBlockParagraph(text="intro"),
            types.RichBlockPhoto(
                photo=photo("p1"), caption=types.RichBlockCaption(text="first")
            ),
            types.RichBlockBlockQuotation(
                blocks=[types.RichBlockVideo(video=video("v1"), caption=None)]
            ),
        ]
    )


def test_attachment_extension():
    assert attachment_extension("фото", photo("p")) == ".jpg"
    assert attachment_extension("видео", video("v", file_name="clip.mov")) == ".mov"
    assert attachment_extension("видео", video("v", mime_type="video/mp4")) == ".mp4"


async def test_download_rich_media_saves_nested_media_in_order(tmp_path):
    client = FakeClient()
    progress_names = []

    def progress_for(name):
        async def progress(current, total):
            progress_names.append(name)

        return progress

    result = await download_rich_media(
        client, rich_with_media(), tmp_path, stem="text_x_5", progress_for=progress_for
    )

    assert result.stored_names == ["text_x_5_1.jpg", "text_x_5_2.mp4"]
    assert [d[0] for d in client.downloads] == ["p1", "v1"]
    assert result.total_bytes == 20
    assert result.errors == []
    assert progress_names == ["text_x_5_1.jpg", "text_x_5_2.mp4"]


async def test_save_text_message_links_photo_inline_and_video(tmp_path):
    message = FakeMessage(id=5, rich_message=rich_with_media())

    result = await save_text_message(
        message, tmp_path, date_str="20261006-120000", client=FakeClient()
    )

    content = (tmp_path / "text_20261006-120000_5.md").read_text(encoding="utf-8")
    assert content == (
        "intro\n\n"
        "![фото](text_20261006-120000_5_1.jpg)\n\nfirst\n\n"
        "> [видео: text_20261006-120000_5_2.mp4](text_20261006-120000_5_2.mp4)"
    )
    assert (tmp_path / "text_20261006-120000_5_1.jpg").exists()
    assert result.success
    assert result.attachments == ("text_20261006-120000_5_1.jpg", "text_20261006-120000_5_2.mp4")
    assert result.size_bytes == len(content.encode()) + 20


async def test_save_text_message_marks_failed_media_and_reports_error(tmp_path):
    message = FakeMessage(id=5, rich_message=rich_with_media())

    result = await save_text_message(
        message, tmp_path, date_str="d", client=FakeClient(fail_names={"p1"})
    )

    content = (tmp_path / "text_d_5.md").read_text(encoding="utf-8")
    assert "*[фото — не удалось скачать]*" in content
    assert "[видео: text_d_5_2.mp4]" in content
    assert not result.success
    assert "FILE_REFERENCE_EXPIRED" in result.error
    assert result.attachments == ("text_d_5_2.mp4",)


async def test_missing_media_object_is_reported(tmp_path):
    rich = types.RichMessage(blocks=[types.RichBlockPhoto(photo=None, caption=None)])

    result = await download_rich_media(FakeClient(), rich, tmp_path, stem="s")

    assert result.stored_names == []
    assert result.errors == ["фото #1: файл недоступен"]
