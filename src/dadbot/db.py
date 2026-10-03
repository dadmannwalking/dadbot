from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS guild_config (guild_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(guild_id, key));
CREATE TABLE IF NOT EXISTS content_history (feature TEXT NOT NULL, content_id TEXT NOT NULL, used_at TEXT NOT NULL, PRIMARY KEY(feature, content_id, used_at));
CREATE INDEX IF NOT EXISTS idx_content_history_feature ON content_history(feature, used_at DESC);
CREATE TABLE IF NOT EXISTS external_items (source TEXT NOT NULL, external_id TEXT NOT NULL, state TEXT NOT NULL, title TEXT, url TEXT, metadata_json TEXT NOT NULL DEFAULT '{}', first_seen_at TEXT NOT NULL, updated_at TEXT NOT NULL, announced_at TEXT, PRIMARY KEY(source, external_id));
CREATE TABLE IF NOT EXISTS monitor_state (monitor TEXT PRIMARY KEY, status TEXT NOT NULL, last_checked_at TEXT, last_success_at TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS suggestions (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, author_id INTEGER NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', message_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS suggestion_votes (suggestion_id INTEGER NOT NULL REFERENCES suggestions(id) ON DELETE CASCADE, user_id INTEGER NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(suggestion_id, user_id));
CREATE TABLE IF NOT EXISTS quote_nominations (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL, author_id INTEGER NOT NULL, nominator_id INTEGER NOT NULL, content TEXT NOT NULL, jump_url TEXT, nominated_at TEXT NOT NULL, featured_at TEXT, UNIQUE(guild_id, message_id));
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, channel_id INTEGER NOT NULL, name TEXT NOT NULL, description TEXT, event_at TEXT NOT NULL, created_by INTEGER NOT NULL, created_at TEXT NOT NULL, message_id INTEGER, cancelled_at TEXT);
CREATE TABLE IF NOT EXISTS event_reminders (event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE, offset_minutes INTEGER NOT NULL, due_at TEXT NOT NULL, sent_at TEXT, PRIMARY KEY(event_id, offset_minutes));
CREATE INDEX IF NOT EXISTS idx_event_reminders_due ON event_reminders(due_at, sent_at);
CREATE TABLE IF NOT EXISTS event_responses (event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE, user_id INTEGER NOT NULL, response TEXT NOT NULL CHECK(response IN ('going','not_going','not_interested')), updated_at TEXT NOT NULL, PRIMARY KEY(event_id, user_id));
CREATE TABLE IF NOT EXISTS job_runs (job_id TEXT PRIMARY KEY, last_started_at TEXT, last_success_at TEXT, last_error TEXT, next_run_at TEXT);
CREATE TABLE IF NOT EXISTS app_state (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.path)
        self._connection.row_factory = aiosqlite.Row
        await self._connection.executescript(SCHEMA)
        await self._migrate()
        await self._connection.commit()

    async def _migrate(self) -> None:
        """Apply small idempotent schema upgrades for existing local databases."""
        connection = self._conn()
        cursor = await connection.execute("PRAGMA table_info(events)")
        event_columns = {str(row[1]) for row in await cursor.fetchall()}
        if "description" not in event_columns:
            await connection.execute("ALTER TABLE events ADD COLUMN description TEXT")
        if "message_id" not in event_columns:
            await connection.execute("ALTER TABLE events ADD COLUMN message_id INTEGER")
        await connection.execute("DELETE FROM schema_version")
        await connection.execute("INSERT INTO schema_version(version) VALUES (2)")

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    @property
    def connected(self) -> bool:
        return self._connection is not None

    def _conn(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Database is not connected")
        return self._connection

    async def execute(self, sql: str, parameters: Sequence[Any] = ()) -> int:
        async with self._lock:
            cursor = await self._conn().execute(sql, parameters)
            await self._conn().commit()
            return cursor.lastrowid or 0

    async def executemany(self, sql: str, parameters: Iterable[Sequence[Any]]) -> None:
        async with self._lock:
            await self._conn().executemany(sql, parameters)
            await self._conn().commit()

    async def fetchone(self, sql: str, parameters: Sequence[Any] = ()) -> aiosqlite.Row | None:
        cursor = await self._conn().execute(sql, parameters)
        return await cursor.fetchone()

    async def fetchall(self, sql: str, parameters: Sequence[Any] = ()) -> list[aiosqlite.Row]:
        cursor = await self._conn().execute(sql, parameters)
        return list(await cursor.fetchall())

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            connection = self._conn()
            try:
                await connection.execute("BEGIN IMMEDIATE")
                yield connection
            except Exception:
                await connection.rollback()
                raise
            else:
                await connection.commit()

    async def healthy(self) -> bool:
        row = await self.fetchone("PRAGMA integrity_check")
        return bool(row and row[0] == "ok")
