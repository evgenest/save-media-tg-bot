from __future__ import annotations

from typing import List

# Scribe words -> timed segments: a new segment starts when the speaker changes
# or after a pause that ends a sentence, so long monologues still get a
# timecode now and then without being cut mid-phrase.

PAUSE_SECONDS = 2.0
_SENTENCE_END = (".", "?", "!", "…")


def build_segments(words: List[dict], *, pause: float = PAUSE_SECONDS) -> List[dict]:
    """[{"start", "end", "speaker", "text"}], times in seconds."""
    segments: List[dict] = []
    for word in words:
        kind = word.get("type") or "word"
        text = word.get("text") or ""
        if kind == "audio_event":
            continue
        if kind == "spacing":
            if segments:
                segments[-1]["text"] += text
            continue
        start = float(word.get("start") or 0)
        end = float(word.get("end") or start)
        speaker = word.get("speaker_id") or "speaker_0"
        last = segments[-1] if segments else None
        if (
            last is not None
            and last["speaker"] == speaker
            and (start - last["end"] < pause or not last["text"].rstrip().endswith(_SENTENCE_END))
        ):
            last["text"] += text
            last["end"] = end
        else:
            segments.append({"start": start, "end": end, "speaker": speaker, "text": text})
    for segment in segments:
        segment["text"] = " ".join(segment["text"].split())
    return [segment for segment in segments if segment["text"]]


def speaker_ids(segments: List[dict]) -> List[str]:
    """Speaker ids in order of first appearance."""
    seen: List[str] = []
    for segment in segments:
        if segment["speaker"] not in seen:
            seen.append(segment["speaker"])
    return seen
