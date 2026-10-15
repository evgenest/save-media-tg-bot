from __future__ import annotations

from typing import Callable, Optional

from mediasaver.archive.record import SPEECH_KINDS
from mediasaver.archive.store import ArchiveStore
from mediasaver.archive.transcribe import TranscriptionError, transcribe_file

# Transcription of archived voice/audio/video. Blocking (network) - the bot
# runs it via asyncio.to_thread.


def needs_transcript(record: dict, max_seconds: int) -> bool:
    if record.get("kind") not in SPEECH_KINDS:
        return False
    if record.get("transcript") or record.get("transcript_error"):
        return False
    media = [item for item in record.get("media") or [] if item.get("path")]
    if not media:
        return False
    return int(media[0].get("duration") or 0) <= max_seconds


def transcribe_record(
    store: ArchiveStore,
    record: dict,
    api_key: str,
    *,
    transcriber: Optional[Callable] = None,
) -> dict:
    """Transcribe the record's first media file and append the result (or the
    error) to the log. Returns the appended update."""
    path = store.root / record["media"][0]["path"]
    try:
        text = (transcriber or transcribe_file)(path, api_key)
        update = {"id": record["id"], "transcript": text}
    except (TranscriptionError, OSError) as exc:
        update = {"id": record["id"], "transcript_error": str(exc)[:300]}
    store.append(update)
    return update


def transcribe_pending(
    store: ArchiveStore,
    api_key: str,
    max_seconds: int,
    *,
    transcriber: Optional[Callable] = None,
    log: Callable[[str], None] = print,
) -> int:
    count = 0
    for record in store.load():
        if needs_transcript(record, max_seconds):
            update = transcribe_record(store, record, api_key, transcriber=transcriber)
            status = "ok" if "transcript" in update else update["transcript_error"]
            log(f"#{record['id']} {record['kind']}: {status}")
            count += 1
    return count
