"""Redo transcripts of chosen messages (with timecodes and speakers) and re-render the archive.

    uv run --env-file .env.local python -m mediasaver.archive.retranscribe <archive dir> <id> [<id> …]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from mediasaver.archive.config import _speaker_model
from mediasaver.archive.render import render_archive
from mediasaver.archive.speakers import claude_cli_namer
from mediasaver.archive.speech import transcribe_record
from mediasaver.archive.store import ArchiveStore
from mediasaver.archive.transcribe import MODEL_ID


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive_dir", type=Path)
    parser.add_argument("ids", type=int, nargs="+")
    parser.add_argument("--tz", default=os.environ.get("ARCHIVE_TZ") or "Europe/Berlin")
    args = parser.parse_args(argv)

    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        print("ELEVENLABS_API_KEY не задан", file=sys.stderr)
        return 1
    speaker_model = _speaker_model(os.environ.get("SPEAKER_MODEL"))
    namer = claude_cli_namer(speaker_model, os.environ.get("CLAUDE_BIN") or None) if speaker_model else None

    store = ArchiveStore(args.archive_dir)
    records = {record["id"]: record for record in store.load()}
    for message_id in args.ids:
        record = records.get(message_id)
        if record is None or not [m for m in record.get("media") or [] if m.get("path")]:
            print(f"#{message_id}: нет записи или файла", file=sys.stderr)
            continue
        update = transcribe_record(
            store, record, api_key, model=os.environ.get("STT_MODEL") or MODEL_ID, namer=namer
        )
        status = update.get("transcript_error") or f"ok, голоса: {update['transcript_speakers']}"
        print(f"#{message_id}: {status}")

    title = store.read_meta().get("topic_title") or store.read_meta().get("title") or "Telegram"
    render_archive(store.root, store.load(), ZoneInfo(args.tz), title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
