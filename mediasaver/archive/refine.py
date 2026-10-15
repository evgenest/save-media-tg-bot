from __future__ import annotations

import logging
import subprocess
from collections import Counter
from typing import Callable, Dict, List, Optional, Tuple

from mediasaver.archive.claude_cli import run_structured
from mediasaver.archive.segments import build_segments
from mediasaver.archive.speakers import Namer, name_speakers

# Semantic pass over a diarized transcript: Scribe words -> small numbered
# chunks (word runs cut at a speaker change, sentence end or pause), a model
# (local Claude CLI) groups consecutive chunks into replicas by meaning and
# says who speaks each one - it may overrule the diarization. The model
# returns only chunk ranges and names: the text stays verbatim from Scribe.
# Invalid answer or failure -> heuristic segments (segments.py) + voice
# naming (speakers.py). A short note with one voice skips the model.

log = logging.getLogger(__name__)

SHORT_SECONDS = 120.0  # one voice and no longer -> the sender, no model
CHUNK_PAUSE = 0.7
CHUNK_MAX_WORDS = 25
_SENTENCE_END = (".", "?", "!", "…")

Grouper = Callable[[str], dict]

_SCHEMA = {
    "type": "object",
    "properties": {
        "replicas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "first": {"type": "integer"},
                    "last": {"type": "integer"},
                    "speaker": {"type": "string"},
                },
                "required": ["first", "last", "speaker"],
            },
        }
    },
    "required": ["replicas"],
}


def claude_cli_grouper(model: str, claude_bin: Optional[str] = None) -> Grouper:
    def grouper(prompt: str) -> dict:
        return run_structured(prompt, _SCHEMA, model=model, claude_bin=claude_bin)

    return grouper


def build_chunks(words: List[dict], *, pause: float = CHUNK_PAUSE, max_words: int = CHUNK_MAX_WORDS) -> List[dict]:
    """[{"start", "end", "speaker", "text"}] - fine word runs; audio events left out."""
    chunks: List[dict] = []
    count = 0
    for word in words:
        kind = word.get("type") or "word"
        text = word.get("text") or ""
        if kind == "audio_event":
            continue
        if kind == "spacing":
            if chunks:
                chunks[-1]["text"] += text
            continue
        start = float(word.get("start") or 0)
        end = float(word.get("end") or start)
        speaker = word.get("speaker_id") or "speaker_0"
        last = chunks[-1] if chunks else None
        if (
            last is not None
            and last["speaker"] == speaker
            and start - last["end"] < pause
            and not last["text"].rstrip().endswith(_SENTENCE_END)
            and count < max_words
        ):
            last["text"] += text
            last["end"] = end
            count += 1
        else:
            chunks.append({"start": start, "end": end, "speaker": speaker, "text": text})
            count = 1
    for chunk in chunks:
        chunk["text"] = " ".join(chunk["text"].split())
    return [chunk for chunk in chunks if chunk["text"]]


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def build_prompt(chunks: List[dict], sender: str, participants: List[str], context: List[str]) -> str:
    numbered = "\n".join(
        f"{i} [{_clock(c['start'])}] {c['speaker']}: {c['text']}" for i, c in enumerate(chunks)
    )
    return "\n\n".join(
        [
            "Это расшифровка аудио из рабочего Telegram-чата, порезанная на мелкие фрагменты "
            "с номерами, таймкодами и метками голоса от автоматической разметки (speaker_0, "
            "speaker_1, …). Разметка голосов бывает ошибочной: путает людей, режет одну фразу "
            "между голосами, склеивает двух людей в один голос.",
            "Задача: собери подряд идущие фрагменты в реплики по смыслу и укажи, кто из участников "
            "чата говорит каждую реплику. Правила:\n"
            "- реплики покрывают все фрагменты от 0 до последнего по порядку, без пропусков и "
            "наложений: first следующей = last предыдущей + 1;\n"
            "- реплика — то, что один человек говорит подряд; не режь посреди фразы;\n"
            "- метка голоса обычно верна: где она меняется, начинай новую реплику; не склеивай "
            "вопрос и ответ на него, реплику и короткое «да»/«угу» собеседника в одну реплику;\n"
            "- переопределяй метку, только когда фрагмент явно сказан другим человеком (продолжение "
            "оборванной фразы, язык, род глагола — «я зробила»/«я сделал», обращение по имени, "
            "кто спрашивает и кто отвечает); тогда вынеси его в реплику этого человека;\n"
            "- длинный монолог дели на несколько реплик по сменам мысли (примерно каждые 30–60 с), "
            "чтобы таймкоды помогали искать место;\n"
            "- учитывай, что обсуждалось в недавних сообщениях чата; человек обычно держится "
            "своего языка;\n"
            "- speaker — имя участника ровно как в списке; если речь не участника (видео, "
            "фон) или уверенности нет — пустая строка;\n"
            "- текст фрагментов не меняй и не пересказывай — ответ содержит только номера и имена.",
            f"Аудио отправил(а): {sender}",
            "Участники чата: " + ", ".join(participants),
            "Недавние сообщения чата:\n" + ("\n".join(context) or "—"),
            f"Фрагменты (0–{len(chunks) - 1}):\n" + numbered,
            'Ответ: {"replicas": [{"first": 0, "last": 4, "speaker": "<имя>"}, …]}',
        ]
    )


def validate_replicas(output: dict, count: int, allowed: "set[str]") -> Optional[List[Tuple[int, int, Optional[str]]]]:
    """[(first, last, name or None)] when the ranges tile 0..count-1 in order, else None."""
    replicas = output.get("replicas") if isinstance(output, dict) else None
    if not isinstance(replicas, list) or not replicas:
        return None
    result = []
    expected = 0
    for item in replicas:
        if not isinstance(item, dict):
            return None
        first, last = item.get("first"), item.get("last")
        if not isinstance(first, int) or not isinstance(last, int) or isinstance(first, bool):
            return None
        if first != expected or last < first or last >= count:
            return None
        name = item.get("speaker")
        result.append((first, last, name if isinstance(name, str) and name in allowed else None))
        expected = last + 1
    return result if expected == count else None


def _voice_names(chunks: List[dict], replicas) -> Dict[str, str]:
    """speaker id -> the name the model gave most of its chunks."""
    votes: Dict[str, Counter] = {}
    for first, last, name in replicas:
        if name is None:
            continue
        for chunk in chunks[first : last + 1]:
            votes.setdefault(chunk["speaker"], Counter())[name] += len(chunk["text"])
    return {voice: counter.most_common(1)[0][0] for voice, counter in votes.items()}


def merge_replicas(chunks: List[dict], replicas) -> "tuple[list[dict], dict[str, str]]":
    names = _voice_names(chunks, replicas)
    segments = []
    for first, last, name in replicas:
        part = chunks[first : last + 1]
        if name is None:
            voices = Counter()
            for chunk in part:
                voices[chunk["speaker"]] += len(chunk["text"])
            voice = voices.most_common(1)[0][0]
            name = names.get(voice) or voice
        segments.append(
            {
                "start": part[0]["start"],
                "end": part[-1]["end"],
                "speaker": name,
                "text": " ".join(chunk["text"] for chunk in part),
            }
        )
    return segments, names


def refine(
    words: List[dict],
    sender: str,
    participants: List[str],
    context: List[str],
    *,
    grouper: Optional[Grouper] = None,
    namer: Optional[Namer] = None,
) -> "tuple[list[dict], dict[str, str], bool]":
    """(segments, speaker id -> name, refined by the model?). Segment speaker is
    a name after the model pass, a speaker id after the fallback."""
    chunks = build_chunks(words)
    voices = {chunk["speaker"] for chunk in chunks}
    duration = chunks[-1]["end"] if chunks else 0
    if grouper is not None and chunks and not (len(voices) == 1 and duration <= SHORT_SECONDS):
        allowed = set(participants) | {sender}
        try:
            replicas = validate_replicas(
                grouper(build_prompt(chunks, sender, participants, context)), len(chunks), allowed
            )
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as exc:
            log.warning("Transcript refine failed: %s", exc)
            replicas = None
        if replicas is not None:
            segments, names = merge_replicas(chunks, replicas)
            return segments, names, True
        log.warning("Transcript refine: invalid grouping, falling back to heuristic segments")
    segments = build_segments(words)
    return segments, name_speakers(segments, sender, participants, context, namer), False
