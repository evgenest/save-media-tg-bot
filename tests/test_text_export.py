from dataclasses import dataclass
from typing import Optional

from downloader import DownloadResult
from text_export import build_default_filename, render_message_markdown, save_text_message


class FakeStr(str):
    """Mimics Pyrogram's Str: a str subclass exposing a `.markdown` property
    that reflects the message's entities (bold/italic/links/etc)."""

    def __new__(cls, value: str, markdown: str):
        obj = super().__new__(cls, value)
        obj.markdown = markdown
        return obj


@dataclass
class FakeMessage:
    id: int
    text: Optional[object] = None


def test_build_default_filename():
    assert build_default_filename(42, "20260628-143005") == "text_20260628-143005_42.md"


def test_render_message_markdown_uses_markdown_property_when_present():
    message = FakeMessage(id=1, text=FakeStr("hello world", markdown="**hello** world"))

    assert render_message_markdown(message) == "**hello** world"


def test_render_message_markdown_falls_back_to_plain_string():
    message = FakeMessage(id=1, text="just plain text")

    assert render_message_markdown(message) == "just plain text"


def test_render_message_markdown_returns_empty_string_for_no_text():
    assert render_message_markdown(FakeMessage(id=1, text=None)) == ""


async def test_save_text_message_writes_markdown_file(tmp_path):
    message = FakeMessage(id=5, text=FakeStr("hi *there*", markdown="hi \\*there\\*"))

    result = await save_text_message(message, tmp_path, date_str="20260628-143005")

    assert result == DownloadResult(
        success=True,
        message_id=5,
        media_type="text",
        original_name=None,
        stored_name="text_20260628-143005_5.md",
        size_bytes=len("hi \\*there\\*".encode("utf-8")),
        file_path=tmp_path / "text_20260628-143005_5.md",
        message_date="20260628-143005",
        caption=None,
    )
    assert (tmp_path / "text_20260628-143005_5.md").read_text(encoding="utf-8") == "hi \\*there\\*"


async def test_save_text_message_resolves_filename_collision(tmp_path):
    (tmp_path / "text_20260628-143005_5.md").write_text("existing", encoding="utf-8")
    message = FakeMessage(id=5, text="new content")

    result = await save_text_message(message, tmp_path, date_str="20260628-143005")

    assert result.stored_name == "text_20260628-143005_5 (2).md"
    assert (tmp_path / "text_20260628-143005_5 (2).md").read_text(encoding="utf-8") == "new content"
