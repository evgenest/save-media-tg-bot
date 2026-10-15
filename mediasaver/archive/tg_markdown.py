from __future__ import annotations

from typing import Iterable, List, Optional

# Telegram text + entities -> CommonMark. Pyrogram's own `.markdown` uses its
# message-sending dialect (`__italic__`, `--underline--`, `||spoiler||`), which
# reads wrong as plain markdown, so the archive renders entities itself.
# Entities are dicts {type, offset, length, url?, language?, name?} with
# offsets in UTF-16 code units, as Telegram counts them.

_WRAP = {
    "bold": ("**", "**"),
    "italic": ("*", "*"),
    "strikethrough": ("~~", "~~"),
    "code": ("`", "`"),
}


def _slice(units: bytes, start: int, end: int) -> str:
    return units[start * 2 : end * 2].decode("utf-16-le")


def _wrap_inline(inner: str, opening: str, closing: str) -> str:
    # `** text**` isn't emphasis in markdown: keep the spaces outside.
    stripped = inner.strip()
    if not stripped:
        return inner
    lead = inner[: len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()) :]
    return f"{lead}{opening}{stripped}{closing}{trail}"


def _render_entity(entity: dict, inner: str) -> str:
    kind = entity["type"]
    if kind in _WRAP:
        return _wrap_inline(inner, *_WRAP[kind])
    if kind == "pre":
        return f"\n```{entity.get('language') or ''}\n{inner.strip(chr(10))}\n```\n"
    if kind in ("blockquote", "expandable_blockquote"):
        quoted = "\n".join(f"> {line}" if line else ">" for line in inner.split("\n"))
        return f"\n\n{quoted}\n\n"
    if kind == "text_link" and entity.get("url"):
        return _wrap_inline(inner, "[", f"]({entity['url']})") if inner.strip() else entity["url"]
    # underline, spoiler, mentions, hashtags, custom emoji...: visible text only
    return inner


def _render(units: bytes, start: int, end: int, entities: List[dict]) -> str:
    out: List[str] = []
    position = start
    index = 0
    while index < len(entities):
        entity = entities[index]
        entity_end = entity["offset"] + entity["length"]
        if entity["offset"] < position or entity_end > end:
            index += 1  # overlaps a previous entity - can't nest, drop it
            continue
        out.append(_slice(units, position, entity["offset"]))
        children = []
        index += 1
        while index < len(entities) and entities[index]["offset"] < entity_end:
            children.append(entities[index])
            index += 1
        inner = _render(units, entity["offset"], entity_end, children)
        out.append(_render_entity(entity, inner))
        position = entity_end
    out.append(_slice(units, position, end))
    return "".join(out)


def entities_to_markdown(text: str, entities: Optional[Iterable[dict]]) -> str:
    if not text:
        return ""
    units = text.encode("utf-16-le")
    ordered = sorted(
        (e for e in entities or [] if e.get("length", 0) > 0),
        key=lambda e: (e["offset"], -e["length"]),
    )
    rendered = _render(units, 0, len(units) // 2, ordered)
    # Block entities add their own blank lines; don't stack them.
    while "\n\n\n" in rendered:
        rendered = rendered.replace("\n\n\n", "\n\n")
    return rendered.strip("\n")


def pyrogram_entities(entities) -> List[dict]:
    result = []
    for entity in entities or []:
        kind = getattr(entity.type, "name", str(entity.type)).lower()
        result.append(
            {
                "type": kind,
                "offset": entity.offset,
                "length": entity.length,
                "url": getattr(entity, "url", None),
                "language": getattr(entity, "language", None),
            }
        )
    return result


def export_pieces_to_markdown(pieces) -> str:
    """Telegram Desktop export: `text_entities` is a flat list of pieces
    {type, text, href?, language?}; rebuild text + entities from it."""
    if isinstance(pieces, str):
        return pieces
    text_parts: List[str] = []
    entities: List[dict] = []
    offset = 0
    for piece in pieces or []:
        if isinstance(piece, str):
            piece = {"type": "plain", "text": piece}
        chunk = piece.get("text") or ""
        length = len(chunk.encode("utf-16-le")) // 2
        kind = piece.get("type", "plain")
        if kind != "plain" and length:
            entities.append(
                {
                    "type": kind,
                    "offset": offset,
                    "length": length,
                    "url": piece.get("href"),
                    "language": piece.get("language"),
                }
            )
        text_parts.append(chunk)
        offset += length
    return entities_to_markdown("".join(text_parts), entities)
