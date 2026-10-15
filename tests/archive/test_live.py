import asyncio
import os
from pathlib import Path

from mediasaver.archive import speech as archive_speech
from mediasaver.archive.config import load_archive_config
from mediasaver.archive.live import LiveArchives, diff_configs, parse_env_file


def test_parse_env_file():
    text = """
# comment
API_ID=1
export ARCHIVE_TZ="Europe/Kyiv"
ELEVENLABS_API_KEY='sk_x'
BROKEN LINE
EMPTY=
"""
    assert parse_env_file(text) == {
        "API_ID": "1",
        "ARCHIVE_TZ": "Europe/Kyiv",
        "ELEVENLABS_API_KEY": "sk_x",
        "EMPTY": "",
    }


def cfg(**env):
    return load_archive_config(env)


def test_diff_configs():
    old = cfg(ARCHIVE_CHATS="-1/5=/a")
    new = cfg(ARCHIVE_CHATS="-1/5=/a;-2=/b", ELEVENLABS_API_KEY="k", ARCHIVE_TZ="UTC")

    changes = diff_configs(old, new)

    assert changes.added == [(-2, None)]
    assert changes.removed == []
    assert changes.transcription
    assert changes.timezone
    assert not diff_configs(new, new).transcription
    assert diff_configs(new, None).removed == [(-1, 5), (-2, None)]


def write_env(path: Path, text: str, bump: int) -> None:
    path.write_text(text, encoding="utf-8")
    stat = path.stat()
    os.utime(path, (stat.st_atime, stat.st_mtime + bump))  # mtime must move


def make_live(tmp_path, text):
    env_file = tmp_path / ".env.local"
    env_file.write_text(text, encoding="utf-8")
    environ = {"API_ID": "1", **parse_env_file(text)}  # as `uv run --env-file`
    return LiveArchives(load_archive_config(environ), env_file=env_file, environ=environ), env_file


def test_reload_adds_and_removes_archives(tmp_path):
    live, env_file = make_live(tmp_path, f"ARCHIVE_CHATS=-1/5={tmp_path / 'a'}\n")
    assert list(live.archives) == [(-1, 5)]
    assert live.reload() is None  # unchanged file

    write_env(env_file, f"ARCHIVE_CHATS=-1/5={tmp_path / 'a'};-2={tmp_path / 'b'}\n", 10)
    changes = live.reload()

    assert changes.added == [(-2, None)]
    assert set(live.archives) == {(-1, 5), (-2, None)}

    write_env(env_file, f"ARCHIVE_CHATS=-2={tmp_path / 'b'}\n", 20)
    live.reload()
    assert list(live.archives) == [(-2, None)]


def test_key_removed_from_file_is_unset(tmp_path):
    live, env_file = make_live(tmp_path, f"ARCHIVE_CHATS=-1={tmp_path}\nELEVENLABS_API_KEY=k\n")
    assert live.archives[(-1, None)].config.elevenlabs_api_key == "k"

    write_env(env_file, f"ARCHIVE_CHATS=-1={tmp_path}\n", 10)
    live.reload()

    assert live.archives[(-1, None)].config.elevenlabs_api_key is None


def test_bad_config_keeps_previous(tmp_path):
    live, env_file = make_live(tmp_path, f"ARCHIVE_CHATS=-1={tmp_path}\n")

    write_env(env_file, f"ARCHIVE_CHATS=-1={tmp_path}\nARCHIVE_TZ=Mars/Base\n", 10)

    assert live.reload() is None
    assert live.config.timezone.key == "Europe/Berlin"
    assert list(live.archives) == [(-1, None)]


async def test_new_key_transcribes_pending_voices(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(archive_speech, "transcribe_file", lambda path, key, model: calls.append(key) or "текст")
    root = tmp_path / "a"
    live, env_file = make_live(tmp_path, f"ARCHIVE_CHATS=-1={root}\n")
    store = live.archives[(-1, None)].store
    store.append({"id": 1, "date": "2026-10-15T10:00:00+00:00", "kind": "voice",
                  "media": [{"kind": "voice", "path": "media/1.ogg"}]})

    write_env(env_file, f"ARCHIVE_CHATS=-1={root}\nELEVENLABS_API_KEY=new\n", 10)
    await live.refresh(client=None)
    await asyncio.gather(*live._tasks)

    assert calls == ["new"]
    assert store.load()[0]["transcript"] == "текст"


def test_build_live_archives(tmp_path):
    from bot import build_live_archives

    assert build_live_archives({}) is None
    live = build_live_archives({"CONFIG_FILE": str(tmp_path / ".env.local")})
    assert live is not None and live.archives == {} and live.config is None
    assert live.session_dir == Path("sessions")


async def test_changed_key_retries_failed_transcripts(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(archive_speech, "transcribe_file", lambda path, key, model: calls.append(key) or "текст")
    root = tmp_path / "a"
    live, env_file = make_live(tmp_path, f"ARCHIVE_CHATS=-1={root}\nELEVENLABS_API_KEY=bad\n")
    store = live.archives[(-1, None)].store
    store.append({"id": 1, "date": "2026-10-15T10:00:00+00:00", "kind": "voice",
                  "media": [{"kind": "voice", "path": "media/1.ogg"}],
                  "transcript_error": "HTTP 401: Invalid API key"})

    # Same key, unrelated edit: a failed record is not retried.
    write_env(env_file, f"ARCHIVE_CHATS=-1={root}\nELEVENLABS_API_KEY=bad\nARCHIVE_TZ=UTC\n", 10)
    await live.refresh(client=None)
    await asyncio.gather(*live._tasks)
    assert calls == []

    write_env(env_file, f"ARCHIVE_CHATS=-1={root}\nELEVENLABS_API_KEY=good\n", 20)
    await live.refresh(client=None)
    await asyncio.gather(*live._tasks)

    record = store.load()[0]
    assert calls == ["good"]
    assert record["transcript"] == "текст"
    assert record["transcript_error"] is None


async def test_stt_model_is_hot_reloaded(tmp_path, monkeypatch):
    models = []
    monkeypatch.setattr(archive_speech, "transcribe_file", lambda path, key, model: models.append(model) or "т")
    root = tmp_path / "a"
    live, env_file = make_live(tmp_path, f"ARCHIVE_CHATS=-1={root}\nELEVENLABS_API_KEY=k\n")
    archive = live.archives[(-1, None)]

    write_env(env_file, f"ARCHIVE_CHATS=-1={root}\nELEVENLABS_API_KEY=k\nSTT_MODEL=scribe_v3\n", 10)
    changes = live.reload()
    archive.store.append({"id": 1, "date": "2026-10-15T10:00:00+00:00", "kind": "voice",
                          "media": [{"kind": "voice", "path": "media/1.ogg"}]})
    await archive.transcribe_pending()

    assert not changes.transcription  # a new model doesn't redo old transcripts
    assert models == ["scribe_v3"]
    assert archive.store.load()[0]["transcript_model"] == "scribe_v3"
