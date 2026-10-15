import io
import json
from urllib.error import HTTPError

import pytest

from archive_speech import needs_transcript, transcribe_pending
from archive_store import ArchiveStore
from transcribe import API_URL, MODEL_ID, TranscriptionError, transcribe_file


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
        return FakeResponse(json.dumps({"text": " Привіт ", "language_code": "ukr"}).encode())

    assert transcribe_file(audio, "secret", opener=opener) == "Привіт"
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
    assert not needs_transcript({"kind": "voice", "media": [{"error": "x"}]}, 1800)
    assert not needs_transcript({"kind": "photo", "media": [{"path": "p"}]}, 1800)
    assert not needs_transcript(voice, 5)


def test_transcribe_pending_records_errors(tmp_path):
    store = ArchiveStore(tmp_path)
    store.append({"id": 1, "kind": "voice", "media": [{"path": "media/1.ogg"}]})
    store.append({"id": 2, "kind": "audio", "media": [{"path": "media/2.mp3"}]})

    def transcriber(path, key):
        if path.name == "2.mp3":
            raise TranscriptionError("HTTP 500")
        return "текст"

    assert transcribe_pending(store, "k", 1800, transcriber=transcriber, log=lambda _: None) == 2
    records = store.load()
    assert records[0]["transcript"] == "текст"
    assert records[1]["transcript_error"] == "HTTP 500"
