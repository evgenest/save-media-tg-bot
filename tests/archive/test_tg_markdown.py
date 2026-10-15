from mediasaver.archive.tg_markdown import entities_to_markdown, export_pieces_to_markdown


def entity(kind, offset, length, **extra):
    return {"type": kind, "offset": offset, "length": length, **extra}


def test_plain_text_without_entities():
    assert entities_to_markdown("просто текст", []) == "просто текст"


def test_inline_styles_are_commonmark():
    text = "жирный курсив зачёркнуто код"
    entities = [
        entity("bold", 0, 6),
        entity("italic", 7, 6),
        entity("strikethrough", 14, 10),
        entity("code", 25, 3),
    ]
    assert entities_to_markdown(text, entities) == "**жирный** *курсив* ~~зачёркнуто~~ `код`"


def test_offsets_count_utf16_units_for_emoji():
    text = "👋 привет"  # the emoji is 2 UTF-16 units
    assert entities_to_markdown(text, [entity("bold", 3, 6)]) == "👋 **привет**"


def test_nested_entities():
    text = "один два"
    entities = [entity("bold", 0, 8), entity("italic", 5, 3)]
    assert entities_to_markdown(text, entities) == "**один *два***"


def test_spaces_stay_outside_emphasis():
    assert entities_to_markdown("a bold b", [entity("bold", 1, 6)]) == "a **bold** b"


def test_text_link_and_hidden_styles():
    text = "сайт и спойлер"
    entities = [
        entity("text_link", 0, 4, url="https://example.com"),
        entity("spoiler", 7, 7),
        entity("underline", 7, 7),
    ]
    assert entities_to_markdown(text, entities) == "[сайт](https://example.com) и спойлер"


def test_pre_and_blockquote_are_blocks():
    text = "код:\nprint(1)\nцитата\nдальше"
    entities = [entity("pre", 5, 8, language="python"), entity("blockquote", 14, 6)]
    assert entities_to_markdown(text, entities) == (
        "код:\n\n```python\nprint(1)\n```\n\n> цитата\n\nдальше"
    )


def test_overlapping_entity_is_dropped_not_crashing():
    text = "abcdef"
    entities = [entity("bold", 0, 4), entity("italic", 2, 4)]
    assert entities_to_markdown(text, entities) == "**abcd**ef"


def test_export_pieces():
    pieces = [
        {"type": "plain", "text": "Привет, "},
        {"type": "bold", "text": "Мариана"},
        {"type": "plain", "text": "! "},
        {"type": "text_link", "text": "макет", "href": "https://figma.com/x"},
    ]
    assert export_pieces_to_markdown(pieces) == "Привет, **Мариана**! [макет](https://figma.com/x)"


def test_export_plain_string_text():
    assert export_pieces_to_markdown("просто") == "просто"
