from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from dadbot.db import Database
from dadbot.services.livestream import YouTubeLivestreamMonitor
from dadbot.services.youtube import YouTubeUploadMonitor, parse_atom_feed
from dadbot.types import ExternalItem, StreamState

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
 <entry><yt:videoId>new</yt:videoId><title>Newest &amp; best</title>
  <link rel="alternate" href="https://youtu.be/new"/><published>2026-02-02T10:00:00Z</published></entry>
 <entry><yt:videoId>old</yt:videoId><title>Older</title>
  <published>2026-01-01T10:00:00Z</published></entry>
 <entry><title>Missing id</title></entry>
</feed>"""


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.sqlite3")
    await database.connect()
    yield database
    await database.close()


def settings(*, api_key: str = "key"):
    return SimpleNamespace(youtube_channel_id="channel", youtube_api_key=api_key)


def test_parse_atom_feed_orders_and_validates_entries():
    items = parse_atom_feed(ATOM)
    assert [item.external_id for item in items] == ["new", "old"]
    assert items[0].title == "Newest & best"
    assert items[1].url.endswith("watch?v=old")


@pytest.mark.asyncio
async def test_upload_simulation_suppresses_duplicates_across_restart(db):
    announced = []

    async def callback(item):
        announced.append(item.external_id)

    first = YouTubeUploadMonitor(settings(), db, None, callback)
    assert await first.simulate("vid-1", "A") is True
    second = YouTubeUploadMonitor(settings(), db, None, callback)
    assert await second.simulate("vid-1", "Changed metadata") is False
    assert announced == ["vid-1"]


@pytest.mark.asyncio
async def test_livestream_transitions_and_restart_persistence(db):
    announced = []

    async def callback(item):
        announced.append((item.external_id, item.state))

    monitor = YouTubeLivestreamMonitor(settings(), db, None, callback)
    assert await monitor.simulate("stream-1", state=StreamState.SCHEDULED) is False
    assert await monitor.simulate("stream-1", state=StreamState.LIVE) is True
    restarted = YouTubeLivestreamMonitor(settings(), db, None, callback)
    assert await restarted.simulate("stream-1", state=StreamState.LIVE) is False
    assert await restarted.simulate("stream-1", state=StreamState.ENDED) is False
    assert announced == [("stream-1", "live")]
    row = await db.fetchone(
        "SELECT state,announced_at FROM external_items WHERE source=? AND external_id=?",
        ("youtube_livestream", "stream-1"),
    )
    assert row["state"] == "ended"
    assert row["announced_at"] is not None


@pytest.mark.asyncio
async def test_check_marks_disappeared_live_stream_ended(db, monkeypatch):
    async def callback(item):
        pass

    monitor = YouTubeLivestreamMonitor(settings(), db, None, callback)
    await monitor.simulate("stream-2", state="live")

    async def no_results(event_type, state):
        return []

    monkeypatch.setattr(monitor, "_search", no_results)
    assert await monitor.check() == []
    row = await db.fetchone(
        "SELECT state FROM external_items WHERE source=? AND external_id=?",
        ("youtube_livestream", "stream-2"),
    )
    assert row["state"] == "ended"
    assert monitor.status == "ok"


@pytest.mark.asyncio
async def test_scheduled_to_live_check_announces_only_once(db, monkeypatch):
    announced = []

    async def callback(item):
        announced.append(item.external_id)

    monitor = YouTubeLivestreamMonitor(settings(), db, None, callback)

    async def scheduled(event_type, state):
        if state is StreamState.SCHEDULED:
            return [ExternalItem("youtube_livestream", "s", "Title", "url", "scheduled")]
        return []

    monkeypatch.setattr(monitor, "_search", scheduled)
    await monitor.check()

    async def live(event_type, state):
        if state is StreamState.LIVE:
            return [ExternalItem("youtube_livestream", "s", "Title", "url", "live")]
        return []

    monkeypatch.setattr(monitor, "_search", live)
    await monitor.check()
    await monitor.check()
    assert announced == ["s"]


@pytest.mark.asyncio
async def test_unconfigured_monitors_no_op_and_record_status(db):
    async def callback(item):
        raise AssertionError("must not announce")

    empty = SimpleNamespace(youtube_channel_id="", youtube_api_key="")
    upload = YouTubeUploadMonitor(empty, db, None, callback)
    stream = YouTubeLivestreamMonitor(empty, db, None, callback)
    assert await upload.check() == []
    assert await stream.check() == []
    assert upload.status == stream.status == "not_configured"
