from dataclasses import dataclass

from mediasaver.core import media_session
from mediasaver.core.media_session import reset_media_session


class FakeSession:
    def __init__(self):
        self.stopped = False

    async def stop(self):
        self.stopped = True


@dataclass
class FakeDoc:
    file_id: str = "x"
    file_name: str = "a.zip"


@dataclass
class FakeMessage:
    id: int = 1
    document: object = None


class FakeClient:
    def __init__(self, dcs):
        self.media_sessions = {dc: FakeSession() for dc in dcs}


async def test_resets_only_the_files_dc(monkeypatch):
    monkeypatch.setattr(media_session, "media_dc_id", lambda message: 4)
    client = FakeClient([1, 4])
    dc4 = client.media_sessions[4]

    await reset_media_session(client, FakeMessage(document=FakeDoc()))

    assert dc4.stopped
    assert list(client.media_sessions) == [1]


async def test_resets_all_when_dc_unknown(monkeypatch):
    monkeypatch.setattr(media_session, "media_dc_id", lambda message: None)
    client = FakeClient([1, 4])
    sessions = list(client.media_sessions.values())

    await reset_media_session(client, FakeMessage(document=FakeDoc()))

    assert client.media_sessions == {}
    assert all(s.stopped for s in sessions)


async def test_stop_failure_does_not_raise(monkeypatch):
    class Broken(FakeSession):
        async def stop(self):
            raise RuntimeError("dead")

    monkeypatch.setattr(media_session, "media_dc_id", lambda message: 2)
    client = FakeClient([])
    client.media_sessions[2] = Broken()

    await reset_media_session(client, FakeMessage(document=FakeDoc()))

    assert client.media_sessions == {}


def test_media_dc_id_none_for_garbage_file_id():
    assert media_session.media_dc_id(FakeMessage(document=FakeDoc())) is None
