from __future__ import annotations

import logging
import subprocess
from typing import Callable, Dict, List, Optional

from mediasaver.archive.claude_cli import run_structured
from mediasaver.archive.segments import speaker_ids

# Diarized speaker ids -> people. One voice: the sender of the message. Several
# voices (a recorded call): a model guesses who is who from the chat
# participants, recent messages and the speech itself, through the local
# Claude Code CLI (claude_cli.py). Used when the semantic pass (refine.py)
# fails. Unknown or failed -> no name, the renderer shows «Спикер N».

log = logging.getLogger(__name__)

# CLI alias: the newest Sonnet. Haiku (~10x cheaper) merged question and answer
# into one replica in about half of the test runs on a 6-min call.
DEFAULT_SPEAKER_MODEL = "sonnet"
MAX_SPEECH_CHARS = 20000
CONTEXT_MESSAGES = 15

Namer = Callable[[str], Dict[str, str]]

_SCHEMA = {
    "type": "object",
    "properties": {"speakers": {"type": "object", "additionalProperties": {"type": "string"}}},
    "required": ["speakers"],
}


def claude_cli_namer(model: str = DEFAULT_SPEAKER_MODEL, claude_bin: Optional[str] = None) -> Namer:
    def namer(prompt: str) -> Dict[str, str]:
        return run_structured(prompt, _SCHEMA, model=model, claude_bin=claude_bin).get("speakers") or {}

    return namer


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def build_prompt(segments: List[dict], sender: str, participants: List[str], context: List[str]) -> str:
    speech = "\n".join(
        f"[{_clock(s['start'])}] {s['speaker']}: {s['text']}" for s in segments
    )[:MAX_SPEECH_CHARS]
    return "\n\n".join(
        [
            "Это расшифровка аудио из рабочего Telegram-чата с разметкой по голосам "
            "(speaker_0, speaker_1, …). Определи, кто из участников чата говорит каждым голосом. "
            "Опирайся на обращения по имени, язык и манеру речи, кто что обещал или спрашивал "
            "в недавних сообщениях. Если для голоса нет уверенности — не включай его в ответ.",
            f"Аудио отправил(а): {sender}",
            "Участники чата: " + ", ".join(participants),
            "Недавние сообщения чата:\n" + ("\n".join(context) or "—"),
            "Расшифровка:\n" + speech,
            'Ответ: {"speakers": {"speaker_0": "<имя участника как в списке>", …}}',
        ]
    )


def name_speakers(
    segments: List[dict],
    sender: str,
    participants: List[str],
    context: List[str],
    namer: Optional[Namer],
) -> Dict[str, str]:
    """speaker id -> name; ids left out stay unnamed."""
    ids = speaker_ids(segments)
    if len(ids) == 1:
        return {ids[0]: sender}
    if not ids or namer is None:
        return {}
    try:
        guessed = namer(build_prompt(segments, sender, participants, context))
    except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
        log.warning("Speaker naming failed: %s", exc)
        return {}
    allowed = set(participants) | {sender}
    return {
        speaker: name
        for speaker, name in guessed.items()
        if speaker in ids and isinstance(name, str) and name in allowed
    }
