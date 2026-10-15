from zoneinfo import ZoneInfo

from archive_render import render_archive, render_media

TZ = ZoneInfo("Europe/Berlin")


def rec(message_id, date, text="", **extra):
    return {
        "id": message_id,
        "date": date,
        "from": {"id": 1, "name": "Мариана"},
        "kind": "text",
        "text": text,
        **extra,
    }


def test_days_in_local_time_and_reply_links(tmp_path):
    records = [
        # 23:30 UTC on the 14th is already the 15th in Berlin (UTC+2)
        rec(1, "2026-10-14T21:30:00+00:00", "вчера"),
        rec(2, "2026-10-14T23:30:00+00:00", "сегодня", reply_to={"id": 1}),
        rec(3, "2026-10-15T08:00:00+00:00", "ещё", reply_to={"id": 2, "from": "Мариана", "snippet": "сегодня"}),
    ]

    render_archive(tmp_path, records, TZ, "Чат")

    day14 = (tmp_path / "days" / "2026-10-14.md").read_text(encoding="utf-8")
    day15 = (tmp_path / "days" / "2026-10-15.md").read_text(encoding="utf-8")
    assert '<a id="m1"></a>\n### 23:30 — Мариана · #1' in day14
    assert "> ↳ ответ на [#1](2026-10-14.md#m1) (Мариана): «вчера»" in day15
    assert "> ↳ ответ на [#2](#m2) (Мариана): «сегодня»" in day15
    index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "| [2026-10-15](days/2026-10-15.md) | 2 | 0 | 0 |" in index


def test_media_transcript_and_edit(tmp_path):
    records = [
        rec(
            7,
            "2026-10-15T10:00:00+00:00",
            kind="voice",
            edit_date="2026-10-15T10:05:00+00:00",
            media=[{"kind": "voice", "path": "media/7_voice.ogg", "duration": 75}],
            transcript="Добрий день",
        )
    ]

    render_archive(tmp_path, records, TZ, "Чат")

    day = (tmp_path / "days" / "2026-10-15.md").read_text(encoding="utf-8")
    assert "· изменено 15.10 12:05" in day
    assert "[голосовое: 1:15](../media/7_voice.ogg)" in day
    assert "> **Расшифровка:** Добрий день" in day


def test_render_media_variants():
    assert render_media({"kind": "photo", "path": "media/1_photo.jpg"}) == "![фото](../media/1_photo.jpg)"
    assert render_media({"kind": "document", "path": "media/1 a.png", "file_name": "a.png"}) == (
        "![файл: a.png](<../media/1 a.png>)"
    )
    assert render_media({"kind": "video", "error": "timeout"}) == "*[видео — не сохранено: timeout]*"


def test_service_and_unknown_reply(tmp_path):
    records = [
        rec(1, "2026-10-15T10:00:00+00:00", kind="service", extra="добавлены в чат: Бот"),
        rec(2, "2026-10-15T10:01:00+00:00", "ок", reply_to={"id": 99}),
    ]

    render_archive(tmp_path, records, TZ, "Чат")

    day = (tmp_path / "days" / "2026-10-15.md").read_text(encoding="utf-8")
    assert "*12:00 — Мариана: добавлены в чат: Бот*" in day
    assert "> ↳ ответ на #99 (?)" in day
