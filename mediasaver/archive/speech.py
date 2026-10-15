from __future__ import annotations

from typing import Callable, Optional

from mediasaver.archive.record import SPEECH_KINDS
from mediasaver.archive.segments import build_segments
from mediasaver.archive.speakers import CONTEXT_MESSAGES, Namer, name_speakers
from mediasaver.archive.store import ArchiveStore
from mediasaver.archive.transcribe import MODEL_ID, TranscriptionError, transcribe_file

# Transcription of archived voice/audio/video. Blocking (network) - the bot
# runs it via asyncio.to_thread.


def needs_transcript(record: dict, max_seconds: int, *, retry_errors: bool = False) -> bool:
    """retry_errors: also redo failed ones - after the key changed, a 401
    from the old key says nothing about the new one."""
    if record.get("kind") not in SPEECH_KINDS:
        return False
    if record.get("transcript"):
        return False
    if record.get("transcript_error") and not retry_errors:
        return False
    media = [item for item in record.get("media") or [] if item.get("path")]
    if not media:
        return False
    return int(media[0].get("duration") or 0) <= max_seconds


def _sender(record: dict) -> str:
    return (record.get("from") or {}).get("name") or "?"


def chat_context(store: ArchiveStore, record: dict) -> "tuple[list[str], list[str]]":
    """(participants, recent text messages before the record) for speaker naming."""
    records = store.load()
    participants = sorted({_sender(r) for r in records} - {"?"})
    earlier = [r for r in records if r.get("text") and r["id"] < record["id"]]
    context = [
        f"{_sender(r)}: {' '.join(r['text'].split())[:300]}" for r in earlier[-CONTEXT_MESSAGES:]
    ]
    return participants, context


def transcribe_record(
    store: ArchiveStore,
    record: dict,
    api_key: str,
    *,
    model: str = MODEL_ID,
    transcriber: Optional[Callable] = None,
    namer: Optional[Namer] = None,
) -> dict:
    """Transcribe the record's first media file and append the result (or the
    error) to the log. Returns the appended update."""
    path = store.root / record["media"][0]["path"]
    try:
        transcript = (transcriber or transcribe_file)(path, api_key, model=model)
        segments = build_segments(transcript.words)
        participants, context = chat_context(store, record)
        update = {
            "id": record["id"],
            "transcript": transcript.text,
            "transcript_error": None,
            "transcript_model": model,
            "transcript_segments": segments,
            "transcript_speakers": name_speakers(
                segments, _sender(record), participants, context, namer
            ),
        }
    except (TranscriptionError, OSError) as exc:
        update = {"id": record["id"], "transcript_error": str(exc)[:300]}
    store.append(update)
    return update


def transcribe_pending(
    store: ArchiveStore,
    api_key: str,
    max_seconds: int,
    *,
    model: str = MODEL_ID,
    transcriber: Optional[Callable] = None,
    namer: Optional[Namer] = None,
    log: Callable[[str], None] = print,
) -> int:
    count = 0
    for record in store.load():
        if needs_transcript(record, max_seconds):
            update = transcribe_record(
                store, record, api_key, model=model, transcriber=transcriber, namer=namer
            )
            status = "ok" if "transcript" in update else update["transcript_error"]
            log(f"#{record['id']} {record['kind']}: {status}")
            count += 1
    return count
