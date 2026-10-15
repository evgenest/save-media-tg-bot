from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, List

# One archive per chat directory:
#   messages.jsonl  - append-only log; each line is a partial record
#                     {"id": ..., <fields>}, later lines override earlier
#                     fields of the same id (edits, transcripts, imports)
#   media/          - downloaded files
#   days/*.md, index.md - rendered from the log (archive_render.py)
#
# The log is the source of truth: rendering can always be redone from it.

LOG_NAME = "messages.jsonl"
MEDIA_DIR = "media"
META_NAME = "chat.json"


class ArchiveStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.media_dir = root / MEDIA_DIR
        self.log_path = root / LOG_NAME
        self._lock = threading.Lock()

    def ensure_dirs(self) -> None:
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def append(self, update: dict) -> None:
        if "id" not in update:
            raise ValueError("archive update needs an id")
        line = json.dumps(update, ensure_ascii=False, sort_keys=True)
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(line + "\n")

    def load(self) -> List[dict]:
        """Records folded by id, ordered by id (= chronological in a chat)."""
        records: Dict[int, dict] = {}
        if not self.log_path.exists():
            return []
        with self._lock:
            lines = self.log_path.read_text(encoding="utf-8").splitlines()
        for line in lines:
            if not line.strip():
                continue
            try:
                update = json.loads(line)
            except json.JSONDecodeError:
                continue  # a torn last line after a crash - skip it
            records.setdefault(update["id"], {}).update(update)
        return [records[key] for key in sorted(records)]

    def known_ids(self) -> "set[int]":
        return {record["id"] for record in self.load()}

    def read_meta(self) -> dict:
        path = self.root / META_NAME
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def update_meta(self, **fields) -> dict:
        meta = self.read_meta()
        meta.update(fields)
        self.write_meta(meta)
        return meta

    def write_meta(self, meta: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / META_NAME
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
