import io
import json
from urllib.error import HTTPError

import pytest

from mediasaver.archive.speech import needs_transcript, transcribe_pending
from mediasaver.archive.store import ArchiveStore
from mediasaver.archive.transcribe import API_URL, MODEL_ID, Transcript, TranscriptionError, transcribe_file


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_transcribe_file_sends_multipart(tmp_path):
    audio = tmp_path / "1_voice.ogg"
    audio.write_bytes(b"OggS-data")
    seen = {}

    def opener(req, timeout):
        seen["url"] = req.full_url
        seen["key"] = req.get_header("Xi-api-key")
        seen["body"] = req.data
        words = [{"text": "Привіт", "type": "word", "start": 0.1, "end": 0.5, "speaker_id": "speaker_0"}]
        return FakeResponse(json.dumps({"text": " Привіт ", "language_code": "ukr", "words": words}).encode())

    result = transcribe_file(audio, "secret", opener=opener)
    assert result.text == "Привіт"
    assert result.words[0]["speaker_id"] == "speaker_0"
    assert b'name="diarize"\r\n\r\ntrue' in seen["body"]
    assert b'name="timestamps_granularity"\r\n\r\ncharacter' in seen["body"]
    assert b'name="tag_audio_events"\r\n\r\ntrue' in seen["body"]
    assert result.raw["language_code"] == "ukr"
    assert f'name="model_id"\r\n\r\n{MODEL_ID}'.encode() in seen["body"]
    transcribe_file(audio, "secret", model="scribe_v3", opener=opener)
    assert b'name="model_id"\r\n\r\nscribe_v3' in seen["body"]
    transcribe_file(audio, "secret", opener=opener)  # restore seen for the checks below
    assert seen["url"] == API_URL
    assert seen["key"] == "secret"
    assert f'name="model_id"\r\n\r\n{MODEL_ID}'.encode() in seen["body"]
    assert b'filename="1_voice.ogg"\r\nContent-Type: audio/ogg' in seen["body"]
    assert b"OggS-data" in seen["body"]


def test_transcribe_file_http_error(tmp_path):
    audio = tmp_path / "a.ogg"
    audio.write_bytes(b"x")

    def opener(req, timeout):
        raise HTTPError(API_URL, 401, "Unauthorized", {}, io.BytesIO(b'{"detail":"bad key"}'))

    with pytest.raises(TranscriptionError, match="HTTP 401"):
        transcribe_file(audio, "bad", opener=opener)


def test_needs_transcript():
    voice = {"kind": "voice", "media": [{"path": "media/1.ogg", "duration": 10}]}
    assert needs_transcript(voice, 1800)
    assert not needs_transcript({**voice, "transcript": "x"}, 1800)
    assert not needs_transcript({**voice, "transcript_error": "x"}, 1800)
    assert needs_transcript({**voice, "transcript_error": "x"}, 1800, retry_errors=True)
    assert not needs_transcript({**voice, "transcript": "t"}, 1800, retry_errors=True)
    assert not needs_transcript({"kind": "voice", "media": [{"error": "x"}]}, 1800)
    assert not needs_transcript({"kind": "photo", "media": [{"path": "p"}]}, 1800)
    assert not needs_transcript(voice, 5)


def test_transcribe_pending_records_errors(tmp_path):
    store = ArchiveStore(tmp_path)
    store.append({"id": 1, "kind": "voice", "media": [{"path": "media/1.ogg"}]})
    store.append({"id": 2, "kind": "audio", "media": [{"path": "media/2.mp3"}]})

    def transcriber(path, key, model):
        if path.name == "2.mp3":
            raise TranscriptionError("HTTP 500")
        return Transcript("текст")

    assert transcribe_pending(store, "k", 1800, transcriber=transcriber, log=lambda _: None) == 2
    records = store.load()
    assert records[0]["transcript"] == "текст"
    assert records[1]["transcript_error"] == "HTTP 500"


def test_transcribe_file_no_speech(tmp_path):
    from mediasaver.archive.transcribe import NO_SPEECH

    audio = tmp_path / "a.ogg"
    audio.write_bytes(b"x")

    def opener(req, timeout):
        return FakeResponse(json.dumps({"text": "", "language_probability": 0.12}).encode())

    with pytest.raises(TranscriptionError, match=NO_SPEECH):
        transcribe_file(audio, "k", opener=opener)
