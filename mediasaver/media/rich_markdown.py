from __future__ import annotations

from typing import Iterable, Iterator, List, Mapping, Optional, Tuple

from pyrogram import types

# Rich messages are Telegram's structured text format (Instant View-like
# blocks: paragraphs, lists, headings, tables...). Such a message has an empty
# `message.text` and keeps everything in `message.rich_message.blocks`, so
# Pyrogram's `.markdown` doesn't cover it - this module renders the blocks.

# Media embedded in blocks: (block type, attribute holding the media, label).
# Files are downloaded next to the .md (see rich_media.py) and linked by a
# relative path, keyed by id(block) in `attachments`.
_MEDIA_BLOCKS = (
    (types.RichBlockPhoto, "photo", "фото"),
    (types.RichBlockVideo, "video", "видео"),
    (types.RichBlockAnimation, "animation", "анимация"),
    (types.RichBlockAudio, "audio", "аудио"),
    (types.RichBlockVoiceNote, "voice_note", "голосовое"),
)

# id(block) -> stored file name, or None if the download failed.
Attachments = Mapping[int, Optional[str]]


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


def _render_list_item(item, attachments: Attachments) -> str:
    label = item.label if item.label and item.label != "•" else "-"
    if label != "-" and not label.endswith((".", ")")):
        label += "."  # raw ordered items may carry a bare "1"
    if item.has_checkbox:
        label = f"{label} [{'x' if item.is_checked else ' '}]"
    body = _render_blocks(item.blocks, attachments, separator="\n")
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


def media_of(block) -> Optional[Tuple[str, object]]:
    """(label, media object) for a media block, None for anything else."""
    for media_type, attr, label in _MEDIA_BLOCKS:
        if isinstance(block, media_type):
            return label, getattr(block, attr, None)
    return None


def iter_media_blocks(blocks) -> Iterator:
    """Media blocks in document order, including nested ones."""
    for block in blocks or []:
        if media_of(block) is not None:
            yield block
        elif isinstance(block, types.RichBlockList):
            for item in block.items:
                yield from iter_media_blocks(item.blocks)
        else:
            yield from iter_media_blocks(getattr(block, "blocks", None))


def _link_target(file_name: str) -> str:
    # CommonMark: angle brackets allow spaces/parentheses in the destination.
    return f"<{file_name}>" if any(c in file_name for c in " ()<>") else file_name


def _render_media(block, label: str, attachments: Attachments) -> str:
    caption = _render_caption(block.caption)
    if id(block) not in attachments:
        return _join([f"*[{label} — не сохранено]*", caption])
    file_name = attachments[id(block)]
    if file_name is None:
        return _join([f"*[{label} — не удалось скачать]*", caption])
    target = _link_target(file_name)
    if label == "фото":
        return _join([f"![{label}]({target})", caption])
    return _join([f"[{label}: {file_name}]({target})", caption])


def render_rich_block(block, attachments: Attachments = {}) -> str:
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
        return "\n".join(_render_list_item(item, attachments) for item in block.items)
    if isinstance(block, types.RichBlockBlockQuotation):
        body = _render_blocks(block.blocks, attachments)
        credit = render_rich_text(block.credit)
        return _quote(body + (f"\n\n— {credit}" if credit else ""))
    if isinstance(block, types.RichBlockPullQuotation):
        body = render_rich_text(block.text)
        credit = render_rich_text(block.credit)
        return _quote(body + (f"\n\n— {credit}" if credit else ""))
    if isinstance(block, types.RichBlockThinking):
        return _quote(render_rich_text(block.text))
    if isinstance(block, (types.RichBlockCollage, types.RichBlockSlideshow)):
        return _join([_render_blocks(block.blocks, attachments), _render_caption(block.caption)])
    if isinstance(block, types.RichBlockTable):
        return _render_table(block)
    if isinstance(block, types.RichBlockDetails):
        summary = render_rich_text(block.summary)
        return _join(
            [f"**{summary}**" if summary else "", _render_blocks(block.blocks, attachments)]
        )
    if isinstance(block, types.RichBlockMap):
        location = block.location
        coords = f"{location.latitude}, {location.longitude}" if location else "?"
        return _join([f"*[карта: {coords}]*", _render_caption(block.caption)])
    media = media_of(block)
    if media is not None:
        return _render_media(block, media[0], attachments)
    if isinstance(block, types.RichBlockCaption):
        return _render_caption(block)
    return "*[неподдерживаемый блок]*"


def _join(parts: Iterable[str], separator: str = "\n\n") -> str:
    return separator.join(part for part in parts if part)


def _render_blocks(blocks, attachments: Attachments, separator: str = "\n\n") -> str:
    rendered: List[str] = [render_rich_block(block, attachments) for block in (blocks or [])]
    return _join(rendered, separator)


def render_rich_message(rich_message, attachments: Attachments = {}) -> str:
    return _render_blocks(rich_message.blocks, attachments)
