from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import dadbot.cogs.community as community_module
from dadbot.cogs.community import CommunityCog, is_moderator, parse_event_time, parse_offsets
from dadbot.db import Database


def test_parse_offsets_deduplicates_and_sorts() -> None:
    assert parse_offsets("60, 1440,60") == (1440, 60)


@pytest.mark.parametrize("raw", ["", "0", "-4", "one-hour", "1,2,3,4,5,6,7,8,9"])
def test_parse_offsets_rejects_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_offsets(raw)


def test_naive_event_time_uses_configured_timezone() -> None:
    settings = SimpleNamespace(timezone=ZoneInfo("America/Indiana/Indianapolis"))
    result = parse_event_time("2026-10-15 19:30", settings)
    assert result.tzinfo is UTC
    assert result == datetime(2026, 10, 15, 23, 30, tzinfo=UTC)


def test_owner_is_moderator_without_member_object() -> None:
    assert is_moderator(SimpleNamespace(id=42), frozenset({42}))
    assert not is_moderator(SimpleNamespace(id=43), frozenset({42}))


@pytest.mark.asyncio
async def test_suggestion_status_persists_across_database_restart(tmp_path: Path) -> None:
    path = tmp_path / "community.sqlite3"
    db = Database(path)
    await db.connect()
    now = datetime.now(UTC).isoformat()
    suggestion_id = await db.execute(
        "INSERT INTO suggestions(guild_id,channel_id,author_id,body,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (1, 2, 3, "More tasteful puns", "open", now, now),
    )
    cog = CommunityCog.__new__(CommunityCog)
    cog.db = db
    assert await cog.set_suggestion_status(suggestion_id, "accepted")
    await db.close()

    reopened = Database(path)
    await reopened.connect()
    row = await reopened.fetchone("SELECT status FROM suggestions WHERE id=?", (suggestion_id,))
    assert row is not None and row["status"] == "accepted"
    await reopened.close()


@pytest.mark.asyncio
async def test_cancelled_and_past_events_are_not_due(tmp_path: Path) -> None:
    path = tmp_path / "community.sqlite3"
    db = Database(path)
    await db.connect()
    now = datetime(2026, 10, 3, 12, tzinfo=UTC)
    old = "2026-10-03T11:00:00+00:00"
    event_id = await db.execute(
        "INSERT INTO events(guild_id,channel_id,name,event_at,created_by,created_at) VALUES(?,?,?,?,?,?)",
        (1, 2, "Past event", old, 3, old),
    )
    await db.execute(
        "INSERT INTO event_reminders(event_id,offset_minutes,due_at) VALUES(?,?,?)",
        (event_id, 60, "2026-10-03T10:00:00+00:00"),
    )
    cog = CommunityCog.__new__(CommunityCog)
    cog.db = db
    cog.bot = SimpleNamespace(get_channel=lambda _: None)
    assert await cog.send_due_event_reminders(now) == 0
    await db.close()


@pytest.mark.asyncio
async def test_sent_reminder_is_suppressed_on_next_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = Database(tmp_path / "community.sqlite3")
    await db.connect()
    now = datetime(2026, 10, 3, 12, tzinfo=UTC)
    event_id = await db.execute(
        "INSERT INTO events(guild_id,channel_id,name,event_at,created_by,created_at) VALUES(?,?,?,?,?,?)",
        (1, 2, "Soon", "2026-10-03T13:00:00+00:00", 3, now.isoformat()),
    )
    await db.execute(
        "INSERT INTO event_reminders(event_id,offset_minutes,due_at) VALUES(?,?,?)",
        (event_id, 60, now.isoformat()),
    )

    class FakeTextChannel:
        def __init__(self) -> None:
            self.messages: list[str] = []

        async def send(self, message: str) -> None:
            self.messages.append(message)

    channel = FakeTextChannel()
    monkeypatch.setattr(community_module.discord, "TextChannel", FakeTextChannel)
    cog = CommunityCog.__new__(CommunityCog)
    cog.db = db
    cog.bot = SimpleNamespace(get_channel=lambda _: channel)
    assert await cog.send_due_event_reminders(now) == 1
    assert await cog.send_due_event_reminders(now) == 0
    assert len(channel.messages) == 1
    await db.close()
