import json
from datetime import datetime

from storage import Manifest, ManifestEntry, create_batch_dir


def test_create_batch_dir_creates_directory_with_timestamp_name(tmp_path):
    started_at = datetime(2026, 6, 28, 14, 30, 5)

    batch_dir = create_batch_dir(tmp_path, started_at)

    assert batch_dir == tmp_path / "2026-06-28_14-30-05"
    assert batch_dir.is_dir()


def test_create_batch_dir_resolves_collision_with_numeric_suffix(tmp_path):
    started_at = datetime(2026, 6, 28, 14, 30, 5)
    (tmp_path / "2026-06-28_14-30-05").mkdir()

    batch_dir = create_batch_dir(tmp_path, started_at)

    assert batch_dir == tmp_path / "2026-06-28_14-30-05_2"
    assert batch_dir.is_dir()


def test_manifest_add_entry_writes_json_file(tmp_path):
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    manifest = Manifest(batch_dir, datetime(2026, 6, 28, 14, 30, 5))
    entry = ManifestEntry(
        message_id=1,
        original_name="video.mp4",
        stored_name="video.mp4",
        media_type="video",
        size_bytes=1024,
        message_date="20260628-143005",
        caption="hello",
    )

    manifest.add_entry(entry)

    data = json.loads(manifest.path.read_text(encoding="utf-8"))
    assert data["files"] == [
        {
            "message_id": 1,
            "original_name": "video.mp4",
            "stored_name": "video.mp4",
            "media_type": "video",
            "size_bytes": 1024,
            "message_date": "20260628-143005",
            "caption": "hello",
            "error": None,
        }
    ]


def test_manifest_add_entry_appends_multiple_entries(tmp_path):
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    manifest = Manifest(batch_dir, datetime(2026, 6, 28, 14, 30, 5))

    manifest.add_entry(ManifestEntry(1, "a.jpg", "a.jpg", "photo", 10, "d1", None))
    manifest.add_entry(ManifestEntry(2, "b.jpg", "b.jpg", "photo", 20, "d2", None))

    data = json.loads(manifest.path.read_text(encoding="utf-8"))
    assert len(data["files"]) == 2
    assert manifest.entries[1].message_id == 2


def test_manifest_write_leaves_no_temp_file(tmp_path):
    batch_dir = tmp_path / "batch"
    batch_dir.mkdir()
    manifest = Manifest(batch_dir, datetime(2026, 6, 28, 14, 30, 5))

    manifest.add_entry(ManifestEntry(1, "a.jpg", "a.jpg", "photo", 10, "d1", None))

    assert not manifest.path.with_suffix(".json.tmp").exists()
