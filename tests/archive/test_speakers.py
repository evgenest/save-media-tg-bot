from mediasaver.archive.render import render_segments
from mediasaver.archive.segments import build_segments, speaker_ids
from mediasaver.archive.speakers import build_prompt, name_speakers
from mediasaver.archive.speech import transcribe_record
from mediasaver.archive.store import ArchiveStore
from mediasaver.archive.transcribe import Transcript


def w(text, start, end, speaker="speaker_0"):
    return {"text": text, "type": "word", "start": start, "end": end, "speaker_id": speaker}


SPACE = {"text": " ", "type": "spacing"}

WORDS = [
    w("Так.", 0.0, 0.4), SPACE, w("Ну", 0.5, 0.7), SPACE,
    w("Да,", 1.0, 1.3, "speaker_1"), SPACE, w("так.", 1.4, 1.6, "speaker_1"), SPACE,
    {"text": "(сміх)", "type": "audio_event", "start": 1.7, "end": 2.0},
    w("Окей", 5.0, 5.3, "speaker_1"),
]


def test_build_segments_splits_on_speaker_and_pause():
    segments = build_segments(WORDS)
    assert [(s["speaker"], s["text"], s["start"]) for s in segments] == [
        ("speaker_0", "Так. Ну", 0.0),
        ("speaker_1", "Да, так.", 1.0),
        ("speaker_1", "Окей", 5.0),  # pause > 2 s -> new timecode
    ]
    assert speaker_ids(segments) == ["speaker_0", "speaker_1"]


def test_pause_mid_phrase_does_not_split():
    words = [w("Беремо", 0.0, 0.4), SPACE, w("картинки,", 0.5, 0.9), SPACE, w("починаючи", 4.0, 4.5)]
    assert [s["text"] for s in build_segments(words)] == ["Беремо картинки, починаючи"]


def test_single_voice_is_the_sender_without_model():
    segments = build_segments(WORDS[:3])
    assert name_speakers(segments, "Mariana", ["Mariana", "Evgen"], [], namer=lambda p: 1 / 0) == {
        "speaker_0": "Mariana"
    }


def test_several_voices_named_by_model_only_known_names():
    segments = build_segments(WORDS)
    prompts = []

    def namer(prompt):
        prompts.append(prompt)
        return {"speaker_0": "Evgen", "speaker_1": "Mariana", "speaker_9": "Evgen", "x": "Stranger"}

    names = name_speakers(segments, "Evgen", ["Evgen", "Mariana"], ["Mariana: привіт"], namer)
    assert names == {"speaker_0": "Evgen", "speaker_1": "Mariana"}
    assert "speaker_1: Да, так." in prompts[0] and "Mariana: привіт" in prompts[0]


def test_model_failure_leaves_voices_unnamed():
    def namer(prompt):
        raise RuntimeError("claude exit 1")

    assert name_speakers(build_segments(WORDS), "Evgen", ["Evgen"], [], namer) == {}


def test_render_segments_names_and_fallback():
    record = {
        "transcript_segments": build_segments(WORDS),
        "transcript_speakers": {"speaker_1": "Mariana"},
    }
    text = render_segments(record)
    assert "**[0:00] Спикер 1:** Так. Ну" in text
    assert "**[0:01] Mariana:** Да, так." in text
    assert "**[0:05] Mariana:** Окей" in text


def test_transcribe_record_stores_segments_and_speakers(tmp_path):
    store = ArchiveStore(tmp_path)
    store.append({"id": 1, "kind": "text", "text": "Привіт", "from": {"name": "Mariana"}})
    record = {"id": 2, "kind": "audio", "from": {"name": "Evgen"}, "media": [{"path": "media/2.ogg"}]}
    store.append(record)
    seen = []

    def namer(prompt):
        seen.append(prompt)
        return {"speaker_0": "Evgen", "speaker_1": "Mariana"}

    update = transcribe_record(
        store, record, "k", transcriber=lambda p, k, model: Transcript("t", WORDS), namer=namer
    )
    assert update["transcript_speakers"] == {"speaker_0": "Evgen", "speaker_1": "Mariana"}
    assert len(update["transcript_segments"]) == 3
    assert "Mariana: Привіт" in seen[0]
    assert store.load()[1]["transcript_segments"] == update["transcript_segments"]


def test_prompt_lists_participants():
    prompt = build_prompt(build_segments(WORDS), "Evgen", ["Evgen", "Mariana"], [])
    assert "Участники чата: Evgen, Mariana" in prompt
