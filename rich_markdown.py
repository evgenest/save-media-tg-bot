from __future__ import annotations

from typing import Iterable, List

from pyrogram import types

# Rich messages are Telegram's structured text format (Instant View-like
# blocks: paragraphs, lists, headings, tables...). Such a message has an empty
# `message.text` and keeps everything in `message.rich_message.blocks`, so
# Pyrogram's `.markdown` doesn't cover it - this module renders the blocks.

_MEDIA_PLACEHOLDERS = (
    (types.RichBlockPhoto, "фото"),
    (types.RichBlockVideo, "видео"),
    (types.RichBlockAnimation, "анимация"),
    (types.RichBlockAudio, "аудио"),
    (types.RichBlockVoiceNote, "голосовое"),
)


def render_rich_text(text) -> str:
    if text is None:
        return ""
    if isinstance(text, str):
        return text
    if isinstance(text, list):
        return "".join(render_rich_text(part) for part in text)

    inner = render_rich_text(getattr(text, "text", None))
    if isinstance(text, types.RichTextBold):
        return f"**{inner}**" if inner else ""
    if isinstance(text, types.RichTextItalic):
        return f"*{inner}*" if inner else ""
    if isinstance(text, types.RichTextStrikethrough):
        return f"~~{inner}~~" if inner else ""
    if isinstance(text, types.RichTextCode):
        return f"`{inner}`" if inner else ""
    if isinstance(text, types.RichTextMarked):
        return f"=={inner}==" if inner else ""
    if isinstance(text, types.RichTextUrl):
        url = render_rich_text(text.url)
        return inner if not url or inner == url else f"[{inner}]({url})"
    if isinstance(text, types.RichTextCustomEmoji):
        return text.alternative_text or ""
    if isinstance(text, types.RichTextMathematicalExpression):
        return f"${text.expression}$"
    # Underline, spoiler, mentions, hashtags, dates, etc: no markdown
    # equivalent worth emitting - keep the visible text.
    return inner


def _render_caption(caption) -> str:
    if caption is None:
        return ""
    text = render_rich_text(caption.text)
    credit = render_rich_text(caption.credit)
    if text and credit:
        return f"{text} — {credit}"
    return text or credit


def _quote(markdown: str) -> str:
    return "\n".join(f"> {line}" if line else ">" for line in markdown.split("\n"))


def _indent(markdown: str, prefix: str) -> str:
    return "\n".join(f"{prefix}{line}" if line else "" for line in markdown.split("\n"))


def _render_list_item(item) -> str:
    label = item.label if item.label and item.label != "•" else "-"
    if label != "-" and not label.endswith((".", ")")):
        label += "."  # raw ordered items may carry a bare "1"
    if item.has_checkbox:
        label = f"{label} [{'x' if item.is_checked else ' '}]"
    body = _render_blocks(item.blocks, separator="\n")
    if not body:
        return label
    first, _, rest = body.partition("\n")
    indent = " " * (len(label) + 1)
    return f"{label} {first}" + (f"\n{_indent(rest, indent)}" if rest else "")


def _render_table(block) -> str:
    rows = [
        [render_rich_text(cell.text).replace("|", "\\|").replace("\n", " ") for cell in row]
        for row in (block.cells or [])
    ]
    rows = [row for row in rows if row]
    if not rows:
        return _render_caption(block.caption)
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    lines = [f"| {' | '.join(rows[0])} |", f"|{'---|' * width}"]
    lines += [f"| {' | '.join(row)} |" for row in rows[1:]]
    caption = _render_caption(block.caption)
    return "\n".join(lines) + (f"\n\n{caption}" if caption else "")


def render_rich_block(block) -> str:
    if isinstance(block, types.RichBlockParagraph):
        return render_rich_text(block.text)
    if isinstance(block, types.RichBlockSectionHeading):
        level = min(max(block.size or 1, 1), 6)
        return f"{'#' * level} {render_rich_text(block.text)}"
    if isinstance(block, types.RichBlockPreformatted):
        return f"```{block.language or ''}\n{render_rich_text(block.text)}\n```"
    if isinstance(block, types.RichBlockFooter):
        return render_rich_text(block.text)
    if isinstance(block, types.RichBlockDivider):
        return "---"
    if isinstance(block, types.RichBlockMathematicalExpression):
        return f"$$\n{block.expression}\n$$"
    if isinstance(block, types.RichBlockAnchor):
        return ""
    if isinstance(block, types.RichBlockList):
        return "\n".join(_render_list_item(item) for item in block.items)
    if isinstance(block, types.RichBlockBlockQuotation):
        body = _render_blocks(block.blocks)
        credit = render_rich_text(block.credit)
        return _quote(body + (f"\n\n— {credit}" if credit else ""))
    if isinstance(block, types.RichBlockPullQuotation):
        body = render_rich_text(block.text)
        credit = render_rich_text(block.credit)
        return _quote(body + (f"\n\n— {credit}" if credit else ""))
    if isinstance(block, types.RichBlockThinking):
        return _quote(render_rich_text(block.text))
    if isinstance(block, (types.RichBlockCollage, types.RichBlockSlideshow)):
        return _join([_render_blocks(block.blocks), _render_caption(block.caption)])
    if isinstance(block, types.RichBlockTable):
        return _render_table(block)
    if isinstance(block, types.RichBlockDetails):
        summary = render_rich_text(block.summary)
        return _join([f"**{summary}**" if summary else "", _render_blocks(block.blocks)])
    if isinstance(block, types.RichBlockMap):
        location = block.location
        coords = f"{location.latitude}, {location.longitude}" if location else "?"
        return _join([f"*[карта: {coords}]*", _render_caption(block.caption)])
    for media_type, label in _MEDIA_PLACEHOLDERS:
        if isinstance(block, media_type):
            return _join([f"*[{label} — не сохранено]*", _render_caption(block.caption)])
    if isinstance(block, types.RichBlockCaption):
        return _render_caption(block)
    return "*[неподдерживаемый блок]*"


def _join(parts: Iterable[str], separator: str = "\n\n") -> str:
    return separator.join(part for part in parts if part)


def _render_blocks(blocks, separator: str = "\n\n") -> str:
    rendered: List[str] = [render_rich_block(block) for block in (blocks or [])]
    return _join(rendered, separator)


def render_rich_message(rich_message) -> str:
    return _render_blocks(rich_message.blocks)
