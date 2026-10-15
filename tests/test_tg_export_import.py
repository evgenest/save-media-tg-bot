import json

from archive_store import ArchiveStore
from tg_export_import import main


def write_export(export_dir, messages):
    export_dir.mkdir()
    (export_dir / "voice_messages").mkdir()
    (export_dir / "voice_messages" / "audio_1.ogg").write_bytes(b"ogg")
    (export_dir / "photos").mkdir()
    (export_dir / "photos" / "photo_1.jpg").write_bytes(b"jpg")
    data = {"name": "Color Fundamentals", "type": "private_supergroup", "id": 1, "messages": messages}
    (export_dir / "result.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


MESSAGES = [
    {"id": 1, "type": "service", "date_unixtime": "1760000000", "actor": "Евгений", "actor_id": "user7",
     "action": "create_group", "title": "Color Fundamentals"},
    {"id": 2, "type": "message", "date_unixtime": "1760000100", "from": "Мариана", "from_id": "user42",
     "text": [{"type": "bold", "text": "Привет"}, " всем"],
     "text_entities": [{"type": "bold", "text": "Привет"}, {"type": "plain", "text": " всем"}]},
    {"id": 3, "type": "message", "date_unixtime": "1760000200", "from": "Мариана", "from_id": "user42",
     "reply_to_message_id": 2, "file": "voice_messages/audio_1.ogg", "media_type": "voice_message",
     "mime_type": "audio/ogg", "duration_seconds": 5, "text": "", "text_entities": []},
    {"id": 4, "type": "message", "date_unixtime": "1760000300", "edited_unixtime": "1760000400",
     "from": "Евгений", "from_id": "user7", "photo": "photos/photo_1.jpg", "text": "макет",
     "text_entities": [{"type": "plain", "text": "макет"}]},
    {"id": 5, "type": "message", "date_unixtime": "1760000500", "from": "Мариана", "from_id": "user42",
     "file": "(File not included. Change data exporting settings to download.)", "file_name": "big.mov",
     "media_type": "video_file", "text": "", "text_entities": []},
]


def test_import_export(tmp_path, capsys):
    export_dir = tmp_path / "export"
    archive_dir = tmp_path / "archive"
    write_export(export_dir, MESSAGES)
    store = ArchiveStore(archive_dir)
    store.append({"id": 2, "date": "2025-10-09T08:56:40+00:00", "text": "живая копия", "source": "bot",
                  "from": {"id": 42, "name": "Мариана", "username": None}})

    assert main([str(export_dir), str(archive_dir), "--tz", "UTC"]) == 0

    records = {record["id"]: record for record in store.load()}
    assert records[1]["kind"] == "service"
    assert records[1]["from"] == {"id": 7, "name": "Евгений", "username": None}
    assert records[2]["text"] == "живая копия"  # the bot's copy wins
    assert records[3]["kind"] == "voice"
    assert records[3]["reply_to"] == {"id": 2}
    assert records[3]["media"] == [
        {"kind": "voice", "mime_type": "audio/ogg", "duration": 5, "path": "media/3_audio_1.ogg"}
    ]
    assert records[4]["kind"] == "photo"
    assert records[4]["edit_date"] is not None
    assert (archive_dir / "media" / "4_photo_1.jpg").read_bytes() == b"jpg"
    assert records[5]["media"][0]["error"] == "файла нет в экспорте"
    assert "Импортировано: 4, уже было от бота: 1" in capsys.readouterr().out

    day = (archive_dir / "days" / "2025-10-09.md").read_text(encoding="utf-8")
    assert day.startswith("# Color Fundamentals — 2025-10-09")
    assert "> ↳ ответ на [#2](#m2) (Мариана): «живая копия»" in day
