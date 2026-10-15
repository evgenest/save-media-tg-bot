import json

from mediasaver.archive.refine import build_chunks, build_prompt, refine, validate_replicas
from mediasaver.archive.render import render_segments
from mediasaver.archive.speech import load_raw, refine_record, transcribe_record
from mediasaver.archive.store import ArchiveStore
from mediasaver.archive.transcribe import Transcript


def w(text, start, end, speaker="speaker_0"):
    return {"text": text, "type": "word", "start": start, "end": end, "speaker_id": speaker, "logprob": -0.1}


SPACE = {"text": " ", "type": "spacing"}

# Evgen asks in Russian, Mariana answers in Ukrainian; diarization put her
# "Так," under Evgen's voice.
WORDS = [
    w("Куда", 0.0, 0.3), SPACE, w("картинки?", 0.4, 0.9), SPACE,
    w("Так,", 1.5, 1.8), SPACE,
    w("під", 1.9, 2.1, "speaker_1"), SPACE, w("відгуки.", 2.2, 2.8, "speaker_1"), SPACE,
    {"text": "(сміх)", "type": "audio_event", "start": 2.9, "end": 3.2, "speaker_id": "speaker_1"},
    w("Окей.", 4.0, 4.4),
]
PEOPLE = ["Evgen", "Mariana"]


def test_chunks_cut_on_speaker_sentence_and_pause_without_audio_events():
    assert [(c["speaker"], c["text"]) for c in build_chunks(WORDS)] == [
        ("speaker_0", "Куда картинки?"),
        ("speaker_0", "Так,"),
        ("speaker_1", "під відгуки."),
        ("speaker_0", "Окей."),
    ]


def test_chunks_cap_word_count():
    words = [item for i in range(30) for item in (w(f"w{i}", i * 0.1, i * 0.1 + 0.05), SPACE)]
    assert [len(c["text"].split()) for c in build_chunks(words, max_words=25)] == [25, 5]


def test_prompt_numbers_chunks_with_voice_and_time():
    prompt = build_prompt(build_chunks(WORDS), "Evgen", PEOPLE, ["Mariana: привіт"])
    assert "2 [0:01] speaker_1: під відгуки." in prompt
    assert "Участники чата: Evgen, Mariana" in prompt and "Mariana: привіт" in prompt
    assert "(сміх)" not in prompt


def test_validate_replicas_requires_tiling_in_order():
    allowed = set(PEOPLE)
    ok = {"replicas": [{"first": 0, "last": 0, "speaker": "Evgen"}, {"first": 1, "last": 3, "speaker": "X"}]}
    assert validate_replicas(ok, 4, allowed) == [(0, 0, "Evgen"), (1, 3, None)]
    gap = {"replicas": [{"first": 0, "last": 0, "speaker": ""}, {"first": 2, "last": 3, "speaker": ""}]}
    overlap = {"replicas": [{"first": 0, "last": 2, "speaker": ""}, {"first": 2, "last": 3, "speaker": ""}]}
    short = {"replicas": [{"first": 0, "last": 2, "speaker": ""}]}
    beyond = {"replicas": [{"first": 0, "last": 4, "speaker": ""}]}
    for bad in (gap, overlap, short, beyond, {"replicas": []}, {}, {"replicas": [{"first": "0", "last": 3}]}):
        assert validate_replicas(bad, 4, allowed) is None


def test_refine_groups_by_meaning_and_reassigns_speaker_keeping_text():
    prompts = []

    def grouper(prompt):
        prompts.append(prompt)
        return {
            "replicas": [
                {"first": 0, "last": 0, "speaker": "Evgen"},
                {"first": 1, "last": 2, "speaker": "Mariana"},  # "Так," moves to Mariana
                {"first": 3, "last": 3, "speaker": ""},  # unsure -> voice majority name
            ]
        }

    segments, names, refined = refine(WORDS, "Evgen", PEOPLE, [], grouper=grouper, namer=lambda p: 1 / 0)
    assert refined and len(prompts) == 1
    assert [(s["speaker"], s["text"], s["start"]) for s in segments] == [
        ("Evgen", "Куда картинки?", 0.0),
        ("Mariana", "Так, під відгуки.", 1.5),
        ("Evgen", "Окей.", 4.0),
    ]
    assert names == {"speaker_0": "Evgen", "speaker_1": "Mariana"}
    assert "**[0:01] Mariana:** Так, під відгуки." in render_segments(
        {"transcript_segments": segments, "transcript_speakers": names}
    )


def test_invalid_grouping_falls_back_to_heuristic_and_namer():
    def grouper(prompt):
        return {"replicas": [{"first": 0, "last": 1, "speaker": "Evgen"}]}  # misses chunks

    segments, names, refined = refine(
        WORDS, "Evgen", PEOPLE, [], grouper=grouper, namer=lambda p: {"speaker_0": "Evgen"}
    )
    assert not refined
    assert segments[0]["speaker"] == "speaker_0" and names == {"speaker_0": "Evgen"}


def test_grouper_failure_falls_back():
    def grouper(prompt):
        raise RuntimeError("claude exit 1")

    segments, names, refined = refine(WORDS, "Evgen", PEOPLE, [], grouper=grouper, namer=None)
    assert not refined and names == {} and segments


def test_short_single_voice_skips_model():
    words = [w("Добрий", 0.0, 0.4), SPACE, w("день.", 0.5, 0.9)]
    segments, names, refined = refine(words, "Mariana", PEOPLE, [], grouper=lambda p: 1 / 0, namer=lambda p: 1 / 0)
    assert not refined and names == {"speaker_0": "Mariana"}
    assert segments == [{"start": 0.0, "end": 0.9, "speaker": "speaker_0", "text": "Добрий день."}]


def test_transcribe_record_writes_raw_sidecar_and_refine_only_reuses_it(tmp_path):
    store = ArchiveStore(tmp_path)
    store.append({"id": 1, "kind": "text", "text": "Привіт", "from": {"name": "Mariana"}})
    record = {"id": 2, "kind": "audio", "from": {"name": "Evgen"}, "media": [{"path": "media/2.ogg"}]}
    store.append(record)
    raw = {"text": "Куда картинки? ...", "language_code": "rus", "transcription_id": "t1", "words": WORDS}
    replicas = {
        "replicas": [
            {"first": 0, "last": 0, "speaker": "Evgen"},
            {"first": 1, "last": 2, "speaker": "Mariana"},
            {"first": 3, "last": 3, "speaker": "Evgen"},
        ]
    }
    update = transcribe_record(
        store,
        record,
        "k",
        transcriber=lambda p, k, model: Transcript(raw["text"], WORDS, raw),
        grouper=lambda prompt: replicas,
    )
    assert update["transcript_raw"] == "media/2_transcript.json"
    assert json.loads((tmp_path / "media/2_transcript.json").read_text(encoding="utf-8")) == raw
    assert not list((tmp_path / "media").glob("*.tmp"))
    assert update["transcript_refined"] and len(update["transcript_segments"]) == 3

    stored = store.load()[1]
    assert load_raw(store, stored).words == WORDS
    again = refine_record(store, stored, grouper=lambda prompt: {"replicas": [{"first": 0, "last": 3, "speaker": "Evgen"}]})
    assert [s["text"] for s in again["transcript_segments"]] == ["Куда картинки? Так, під відгуки. Окей."]
    assert store.load()[1]["transcript"] == raw["text"]  # text untouched by a refine-only redo


def test_old_records_with_speaker_ids_still_render():
    record = {
        "transcript_segments": [
            {"start": 0, "end": 1, "speaker": "speaker_0", "text": "Так."},
            {"start": 2, "end": 3, "speaker": "speaker_1", "text": "Да."},
        ],
        "transcript_speakers": {"speaker_1": "Mariana"},
    }
    text = render_segments(record)
    assert "**[0:00] Спикер 1:** Так." in text and "**[0:02] Mariana:** Да." in text
