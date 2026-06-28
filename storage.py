from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ManifestEntry:
    message_id: int
    original_name: Optional[str]
    stored_name: str
    media_type: str
    size_bytes: int
    message_date: str
    caption: Optional[str]
    error: Optional[str] = None


def create_batch_dir(storage_dir: Path, started_at: datetime) -> Path:
    base_name = started_at.strftime("%Y-%m-%d_%H-%M-%S")
    candidate = storage_dir / base_name
    suffix = 2
    while candidate.exists():
        candidate = storage_dir / f"{base_name}_{suffix}"
        suffix += 1
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


class Manifest:
    def __init__(self, batch_dir: Path, started_at: datetime) -> None:
        self.path = batch_dir / "manifest.json"
        self._batch_dir = batch_dir
        self._started_at = started_at
        self._entries: "list[ManifestEntry]" = []

    @property
    def entries(self) -> "list[ManifestEntry]":
        return list(self._entries)

    def add_entry(self, entry: ManifestEntry) -> None:
        self._entries.append(entry)
        self._write()

    def _write(self) -> None:
        data = {
            "batch_dir": str(self._batch_dir),
            "started_at": self._started_at.isoformat(),
            "files": [asdict(entry) for entry in self._entries],
        }
        tmp_path = self.path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)
