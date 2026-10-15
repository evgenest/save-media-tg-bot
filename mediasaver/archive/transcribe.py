from __future__ import annotations

import json
import mimetypes
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional
from urllib import request
from urllib.error import HTTPError

# Speech-to-text via the ElevenLabs Scribe API (stdlib only, blocking - call
# it through asyncio.to_thread). Language is auto-detected: chats mix
# Ukrainian, Russian and English. Diarization and word timestamps are on:
# the words become timed per-speaker segments (segments.py).

API_URL = "https://api.elevenlabs.io/v1/speech-to-text"
# Default Scribe model; STT_MODEL overrides it (hot-reloaded, see live.py).
# /v1/models lists only TTS/voice-conversion models, so there is no API to
# discover the newest STT model - bump it here or via STT_MODEL.
MODEL_ID = "scribe_v2"
TIMEOUT_SECONDS = 600
NO_SPEECH = "речь не распознана — тишина, шум или слишком тихо"


class TranscriptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Transcript:
    text: str
    # Scribe words: {"text", "type": word|spacing|audio_event, "start", "end", "speaker_id"}
    words: "list[dict]" = field(default_factory=list)


def build_multipart(fields: "dict[str, str]", file_path: Path) -> "tuple[bytes, str]":
    boundary = uuid.uuid4().hex
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks = []
    for name, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    chunks.append(
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
            f'filename="{file_path.name}"\r\nContent-Type: {mime}\r\n\r\n'
        ).encode()
    )
    chunks.append(file_path.read_bytes())
    chunks.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def transcribe_file(
    file_path: Path,
    api_key: str,
    *,
    model: str = MODEL_ID,
    opener: Optional[Callable] = None,
) -> Transcript:
    body, content_type = build_multipart(
        {
            "model_id": model,
            "tag_audio_events": "false",
            "timestamps_granularity": "word",
            "diarize": "true",
        },
        file_path,
    )
    req = request.Request(
        API_URL,
        data=body,
        method="POST",
        headers={"xi-api-key": api_key, "Content-Type": content_type},
    )
    try:
        with (opener or request.urlopen)(req, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise TranscriptionError(f"HTTP {exc.code}: {detail}") from exc
    except OSError as exc:
        raise TranscriptionError(str(exc)) from exc
    text = (payload.get("text") or "").strip()
    if not text:
        # Silence, noise or speech too quiet to make out - not an API failure.
        raise TranscriptionError(NO_SPEECH)
    return Transcript(text=text, words=list(payload.get("words") or []))
