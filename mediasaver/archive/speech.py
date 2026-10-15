from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from mediasaver.archive.record import SPEECH_KINDS
from mediasaver.archive.refine import Grouper, claude_cli_grouper, refine
from mediasaver.archive.speakers import CONTEXT_MESSAGES, Namer, claude_cli_namer
from mediasaver.archive.store import MEDIA_DIR, ArchiveStore
from mediasaver.archive.transcribe import MODEL_ID, Transcript, TranscriptionError, transcribe_file

# Transcription of archived voice/audio/video. Blocking (network) - the bot
# runs it via asyncio.to_thread. The full Scribe response goes to a sidecar
# media/<id>_transcript.json (record field transcript_raw), the log keeps the
# text and the replicas (transcript_segments, transcript_speakers).


def claude_passes(model: Optional[str], claude_bin: Optional[str] = None) -> dict:
    """namer/grouper kwargs for transcribe_record; model None (SPEAKER_MODEL=off) -> none."""
    if not model:
        return {}
    return {"namer": claude_cli_namer(model, claude_bin), "grouper": claude_cli_grouper(model, claude_bin)}


def raw_path(record: dict) -> str:
    return f"{MEDIA_DIR}/{record['id']}_transcript.json"


def _write_raw(store: ArchiveStore, record: dict, transcript: Transcript) -> str:
    relative = raw_path(record)
    raw = transcript.raw or {"text": transcript.text, "words": transcript.words}
    path = store.root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return relative


def load_raw(store: ArchiveStore, record: dict) -> Transcript:
    """The stored Scribe response of a record (for a refine-only redo)."""
    relative = record.get("transcript_raw")
    if not relative:
        raise FileNotFoundError(f"#{record['id']}: нет transcript_raw")
    raw = json.loads(Path(store.root / relative).read_text(encoding="utf-8"))
    return Transcript(text=(raw.get("text") or "").strip(), words=list(raw.get("words") or []), raw=raw)


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
    grouper: Optional[Grouper] = None,
) -> dict:
    """Transcribe the record's first media file and append the result (or the
    error) to the log. Returns the appended update."""
    path = store.root / record["media"][0]["path"]
    try:
        transcript = (transcriber or transcribe_file)(path, api_key, model=model)
        update = {
            "id": record["id"],
            "transcript": transcript.text,
            "transcript_error": None,
            "transcript_model": model,
            "transcript_raw": _write_raw(store, record, transcript),
            **_replicas(store, record, transcript, namer, grouper),
        }
    except (TranscriptionError, OSError) as exc:
        update = {"id": record["id"], "transcript_error": str(exc)[:300]}
    store.append(update)
    return update


def _replicas(store, record, transcript, namer, grouper) -> dict:
    participants, context = chat_context(store, record)
    segments, speakers, refined = refine(
        transcript.words, _sender(record), participants, context, grouper=grouper, namer=namer
    )
    return {
        "transcript_segments": segments,
        "transcript_speakers": speakers,
        "transcript_refined": refined,
    }


def refine_record(
    store: ArchiveStore,
    record: dict,
    *,
    namer: Optional[Namer] = None,
    grouper: Optional[Grouper] = None,
) -> dict:
    """Redo only the replicas from the stored Scribe response (no new STT call)."""
    update = {"id": record["id"], **_replicas(store, record, load_raw(store, record), namer, grouper)}
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
    grouper: Optional[Grouper] = None,
    log: Callable[[str], None] = print,
) -> int:
    count = 0
    for record in store.load():
        if needs_transcript(record, max_seconds):
            update = transcribe_record(
                store, record, api_key, model=model, transcriber=transcriber, namer=namer, grouper=grouper
            )
            status = "ok" if "transcript" in update else update["transcript_error"]
            log(f"#{record['id']} {record['kind']}: {status}")
            count += 1
    return count
