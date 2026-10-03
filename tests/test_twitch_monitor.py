from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dadbot.db import Database
from dadbot.services.twitch import TwitchLivestreamMonitor, parse_streams
from dadbot.types import StreamState


def settings(**overrides):
    values = {
        "twitch_client_id": "client",
        "twitch_client_secret": "secret",
        "twitch_user_login": "dad",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.sqlite3")
    await database.connect()
    yield database
    await database.close()


def test_parse_streams_ignores_invalid_records():
    items = parse_streams(
        {"data": [{"id": "123", "title": "Building", "user_login": "Dad"}, {"title": "no id"}]},
        "fallback",
    )
    assert [(item.external_id, item.state) for item in items] == [("123", "live")]
    assert items[0].url == "https://www.twitch.tv/Dad"


class FakeResponse:
    def __init__(self, status, payload):
        self.status, self.payload = status, payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.get_responses = [
            FakeResponse(401, {}),
            FakeResponse(200, {"data": [{"id": "1", "title": "Live"}]}),
        ]
        self.posts = 0

    def post(self, *args, **kwargs):
        self.posts += 1
        return FakeResponse(200, {"access_token": f"token-{self.posts}"})

    def get(self, *args, **kwargs):
        return self.get_responses.pop(0)


@pytest.mark.asyncio
async def test_401_refreshes_app_token_and_retries(db):
    async def callback(item):
        pass

    session = FakeSession()
    monitor = TwitchLivestreamMonitor(settings(), db, session, callback)
    monitor._access_token = "expired"
    items = await monitor._fetch()
    assert [item.external_id for item in items] == ["1"]
    assert session.posts == 1
    assert monitor._access_token == "token-1"


@pytest.mark.asyncio
async def test_transitions_duplicate_suppression_and_ended_no_announcement(db):
    announced = []

    async def callback(item):
        announced.append(item.external_id)

    monitor = TwitchLivestreamMonitor(settings(), db, None, callback)
    assert await monitor.simulate("stream", state=StreamState.UNKNOWN) is False
    assert await monitor.simulate("stream", state=StreamState.LIVE) is True
    restarted = TwitchLivestreamMonitor(settings(), db, None, callback)
    assert await restarted.simulate("stream", state=StreamState.LIVE) is False
    assert await restarted.simulate("stream", state=StreamState.ENDED) is False
    assert announced == ["stream"]


@pytest.mark.asyncio
async def test_check_marks_missing_live_stream_ended(db, monkeypatch):
    async def callback(item):
        pass

    monitor = TwitchLivestreamMonitor(settings(), db, None, callback)
    await monitor.simulate("stream")

    async def empty():
        return []

    monkeypatch.setattr(monitor, "_fetch", empty)
    await monitor.check()
    row = await db.fetchone(
        "SELECT state FROM external_items WHERE source=? AND external_id=?",
        ("twitch_livestream", "stream"),
    )
    assert row["state"] == "ended"


@pytest.mark.asyncio
async def test_unconfigured_monitor_is_safe_no_op(db):
    async def callback(item):
        raise AssertionError("must not announce")

    monitor = TwitchLivestreamMonitor(settings(twitch_client_secret=""), db, None, callback)
    assert await monitor.check() == []
    assert monitor.status == "not_configured"
