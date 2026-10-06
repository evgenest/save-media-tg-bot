from dataclasses import dataclass
from typing import Optional

from pyrogram import types

from rich_markdown import render_rich_message, render_rich_text
from text_export import is_text_message, render_message_markdown


def ordered_item(label: str, text) -> types.RichBlockListItem:
    return types.RichBlockListItem(label=label, blocks=[types.RichBlockParagraph(text=text)])


def make_rich_message(*blocks) -> types.RichMessage:
    return types.RichMessage(blocks=list(blocks))


@dataclass
class FakeMessage:
    id: int
    text: Optional[object] = None
    rich_message: Optional[object] = None


def test_render_rich_text_formatting():
    text = [
        types.RichTextBold(text="bold"),
        " ",
        types.RichTextItalic(text="it"),
        " ",
        types.RichTextUrl(text="site", url="https://example.com"),
        " ",
        types.RichTextCode(text="x"),
        " ",
        types.RichTextStrikethrough(text="old"),
        " ",
        types.RichTextUnderline(text="under"),
    ]

    assert render_rich_text(text) == "**bold** *it* [site](https://example.com) `x` ~~old~~ under"


def test_render_rich_text_autolink_keeps_bare_url():
    assert render_rich_text(types.RichTextUrl(text="https://a.b", url="https://a.b")) == "https://a.b"


def test_render_rich_message_paragraphs_and_ordered_list():
    # Shape of the real message that used to be silently ignored.
    rich = make_rich_message(
        types.RichBlockList(items=[ordered_item("1", "Перший екран — одразу в проблему")]),
        types.RichBlockParagraph(text="Велике сильне фото."),
        types.RichBlockParagraph(text="Кнопка: ХОЧУ РОЗУМІТИ КОЛІР"),
    )

    assert render_rich_message(rich) == (
        "1. Перший екран — одразу в проблему\n\n"
        "Велике сильне фото.\n\n"
        "Кнопка: ХОЧУ РОЗУМІТИ КОЛІР"
    )


def test_render_rich_message_bullets_checkboxes_and_multiline_items():
    rich = make_rich_message(
        types.RichBlockList(
            items=[
                types.RichBlockListItem(
                    label="•",
                    blocks=[
                        types.RichBlockParagraph(text="first"),
                        types.RichBlockParagraph(text="continued"),
                    ],
                ),
                types.RichBlockListItem(
                    label="•",
                    blocks=[types.RichBlockParagraph(text="done")],
                    has_checkbox=True,
                    is_checked=True,
                ),
            ]
        )
    )

    assert render_rich_message(rich) == "- first\n  continued\n- [x] done"


def test_render_rich_message_heading_quote_divider_code():
    rich = make_rich_message(
        types.RichBlockSectionHeading(text="Title", size=2),
        types.RichBlockBlockQuotation(
            blocks=[types.RichBlockParagraph(text="quoted")], credit="author"
        ),
        types.RichBlockDivider(),
        types.RichBlockPreformatted(text="print(1)", language="python"),
    )

    assert render_rich_message(rich) == (
        "## Title\n\n> quoted\n>\n> — author\n\n---\n\n```python\nprint(1)\n```"
    )


def test_render_rich_message_table():
    rich = make_rich_message(
        types.RichBlockTable(
            cells=[
                [types.RichBlockTableCell(text="a"), types.RichBlockTableCell(text="b")],
                [types.RichBlockTableCell(text="1"), types.RichBlockTableCell(text="x|y")],
            ]
        )
    )

    assert render_rich_message(rich) == "| a | b |\n|---|---|\n| 1 | x\\|y |"


def test_render_rich_message_marks_media_and_unknown_blocks():
    rich = make_rich_message(
        types.RichBlockPhoto(
            photo=None, caption=types.RichBlockCaption(text="подпись")
        ),
        types.RichBlockUnsupported(),
    )

    assert render_rich_message(rich) == (
        "*[фото — не сохранено]*\n\nподпись\n\n*[неподдерживаемый блок]*"
    )


def test_text_export_handles_rich_message_without_text():
    message = FakeMessage(
        id=1, rich_message=make_rich_message(types.RichBlockParagraph(text="hello"))
    )

    assert is_text_message(message)
    assert render_message_markdown(message) == "hello"


def test_is_text_message_false_without_text_and_rich():
    assert not is_text_message(FakeMessage(id=1))
