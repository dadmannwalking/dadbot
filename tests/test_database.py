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
