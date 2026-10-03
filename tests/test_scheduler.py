import random
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dadbot.config import Settings
from dadbot.db import Database
from dadbot.scheduler import EngagementScheduler


def settings(tmp_path) -> Settings:
    return Settings(
        bot_token="test",
        guild_id=1,
        default_channel_id=2,
        youtube_upload_channel_id=2,
        live_notification_channel_id=2,
        suggestion_channel_id=2,
        highlight_channel_id=2,
        owner_ids=frozenset({3}),
        timezone=ZoneInfo("UTC"),
        database_path=tmp_path / "db.sqlite3",
        content_path=Path("content"),
        log_path=tmp_path / "test.log",
        log_level="INFO",
        development_mode=True,
        sync_commands=False,
        youtube_channel_id="",
        youtube_api_key="",
        youtube_poll_minutes=15,
        livestream_poll_minutes=5,
        dad_joke_min_days=2,
        dad_joke_max_days=4,
        showcase_min_days=7,
        showcase_max_days=12,
        question_weekday=1,
        poll_weekday=4,
        schedule_hour_start=10,
        schedule_hour_end=20,
        backup_retention=14,
    )


@pytest.fixture
async def database(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    await db.connect()
    yield db
    await db.close()


async def test_start_registers_jobs_and_persists_next_runs(database, tmp_path):
    fixed = datetime(2026, 10, 3, 12, 0, tzinfo=ZoneInfo("UTC"))
    scheduler = EngagementScheduler(
        settings(tmp_path), database, now=lambda: fixed, rng=random.Random(4)
    )
    await scheduler.start()
    try:
        assert {item["id"] for item in scheduler.upcoming()} == {
            "dad_joke",
            "showcase",
            "question",
            "poll",
            "event_reminders",
        }
        rows = await database.fetchall("SELECT job_id, next_run_at FROM job_runs")
        assert len(rows) == 5
        assert all(row["next_run_at"] for row in rows)
    finally:
        await scheduler.shutdown()


async def test_manual_run_records_success(database, tmp_path):
    calls = []

    async def callback():
        calls.append("called")

    scheduler = EngagementScheduler(settings(tmp_path), database, {"dad_joke": callback})
    assert await scheduler.run_now("dad_joke") is True
    assert calls == ["called"]
    row = await database.fetchone("SELECT * FROM job_runs WHERE job_id='dad_joke'")
    assert row["last_started_at"]
    assert row["last_success_at"]
    assert row["last_error"] is None
    assert await scheduler.run_now("missing") is False


async def test_callback_failure_is_isolated_and_recorded(database, tmp_path):
    async def broken():
        raise RuntimeError("network fell over")

    scheduler = EngagementScheduler(settings(tmp_path), database, {"question": broken})
    assert await scheduler.run_now("question") is True
    row = await database.fetchone("SELECT * FROM job_runs WHERE job_id='question'")
    assert "network fell over" in row["last_error"]
    assert row["last_success_at"] is None


async def test_random_jobs_land_inside_posting_window(database, tmp_path):
    fixed = datetime(2026, 10, 3, 23, 0, tzinfo=ZoneInfo("UTC"))
    scheduler = EngagementScheduler(
        replace(settings(tmp_path), dad_joke_min_days=1, dad_joke_max_days=1),
        database,
        now=lambda: fixed,
        rng=random.Random(2),
    )
    await scheduler.start()
    try:
        run_at = scheduler._scheduler.get_job("dad_joke").next_run_time
        assert 10 <= run_at.hour <= 20
    finally:
        await scheduler.shutdown()
