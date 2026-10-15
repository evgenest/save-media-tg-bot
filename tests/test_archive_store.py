from archive_store import ArchiveStore


def test_updates_fold_by_id(tmp_path):
    store = ArchiveStore(tmp_path)
    store.append({"id": 2, "text": "b"})
    store.append({"id": 1, "text": "a"})
    store.append({"id": 1, "text": "a2", "edit_date": "x"})
    store.append({"id": 2, "transcript": "t"})

    assert store.load() == [
        {"id": 1, "text": "a2", "edit_date": "x"},
        {"id": 2, "text": "b", "transcript": "t"},
    ]


def test_torn_line_is_skipped(tmp_path):
    store = ArchiveStore(tmp_path)
    store.append({"id": 1})
    with store.log_path.open("a", encoding="utf-8") as log:
        log.write('{"id": 2, "te')

    assert store.load() == [{"id": 1}]


def test_meta_roundtrip(tmp_path):
    store = ArchiveStore(tmp_path)
    assert store.read_meta() == {}
    store.write_meta({"title": "Чат"})
    assert store.read_meta() == {"title": "Чат"}
