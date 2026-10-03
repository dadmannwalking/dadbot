import sqlite3
from pathlib import Path

import pytest

from dadbot.db import Database


@pytest.mark.asyncio
async def test_schema_is_idempotent_and_persistent(tmp_path: Path) -> None:
    path = tmp_path / "test.sqlite3"
    first = Database(path)
    await first.connect()
    await first.execute(
        "INSERT INTO app_state(key, value, updated_at) VALUES (?, ?, ?)",
        ("survives", "yes", "2026-01-01T00:00:00+00:00"),
    )
    assert await first.healthy()
    await first.close()

    second = Database(path)
    await second.connect()
    row = await second.fetchone("SELECT value FROM app_state WHERE key = ?", ("survives",))
    assert row is not None and row["value"] == "yes"
    assert await second.healthy()
    await second.close()


@pytest.mark.asyncio
async def test_transaction_rolls_back(tmp_path: Path) -> None:
    database = Database(tmp_path / "test.sqlite3")
    await database.connect()
    with pytest.raises(RuntimeError):
        async with database.transaction() as connection:
            await connection.execute(
                "INSERT INTO app_state(key, value, updated_at) VALUES ('x', 'y', 'now')"
            )
            raise RuntimeError("rollback")
    assert await database.fetchone("SELECT key FROM app_state WHERE key='x'") is None
    await database.close()


@pytest.mark.asyncio
async def test_existing_event_schema_is_migrated_without_data_loss(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO schema_version VALUES (1)")
        connection.execute(
            "CREATE TABLE events (id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, "
            "channel_id INTEGER NOT NULL, name TEXT NOT NULL, event_at TEXT NOT NULL, "
            "created_by INTEGER NOT NULL, created_at TEXT NOT NULL, cancelled_at TEXT)"
        )
        connection.execute(
            "INSERT INTO events VALUES (1,2,3,'Existing event','2026-12-01',4,'now',NULL)"
        )

    database = Database(path)
    await database.connect()
    row = await database.fetchone("SELECT name,description,message_id FROM events WHERE id=1")
    version = await database.fetchone("SELECT version FROM schema_version")
    assert row is not None and row["name"] == "Existing event"
    assert row["description"] is None and row["message_id"] is None
    assert version is not None and version["version"] == 2
    await database.close()
