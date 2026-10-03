from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from dadbot.db import Database
from dadbot.types import ExternalItem

AnnouncementCallback = Callable[[ExternalItem], Awaitable[None]]


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


class MonitorBase:
    def __init__(self, db: Database, monitor_name: str) -> None:
        self.db = db
        self.monitor_name = monitor_name
        self.last_checked_at: datetime | None = None
        self.last_success_at: datetime | None = None
        self.status = "not_configured"
        self.detail: str | None = None

    async def _record_status(
        self, status: str, detail: str | None = None, *, success: bool = False
    ) -> None:
        now = utcnow()
        self.last_checked_at = now
        if success:
            self.last_success_at = now
        self.status, self.detail = status, detail
        await self.db.execute(
            """INSERT INTO monitor_state(monitor,status,last_checked_at,last_success_at,detail)
               VALUES(?,?,?,?,?) ON CONFLICT(monitor) DO UPDATE SET status=excluded.status,
               last_checked_at=excluded.last_checked_at,
               last_success_at=COALESCE(excluded.last_success_at,monitor_state.last_success_at),
               detail=excluded.detail""",
            (
                self.monitor_name,
                status,
                now.isoformat(),
                now.isoformat() if success else None,
                detail,
            ),
        )


async def upsert_external(
    db: Database, item: ExternalItem, metadata: dict[str, Any] | None = None
) -> tuple[str | None, bool]:
    row = await db.fetchone(
        "SELECT state,announced_at FROM external_items WHERE source=? AND external_id=?",
        (item.source, item.external_id),
    )
    previous = str(row["state"]) if row else None
    now = iso()
    await db.execute(
        """INSERT INTO external_items(source,external_id,state,title,url,metadata_json,
           first_seen_at,updated_at) VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(source,external_id) DO UPDATE SET state=excluded.state,
           title=excluded.title,url=excluded.url,metadata_json=excluded.metadata_json,
           updated_at=excluded.updated_at""",
        (
            item.source,
            item.external_id,
            item.state,
            item.title,
            item.url,
            json.dumps(metadata or {}, sort_keys=True),
            now,
            now,
        ),
    )
    return previous, bool(row and row["announced_at"])


async def mark_announced(db: Database, item: ExternalItem) -> None:
    await db.execute(
        "UPDATE external_items SET announced_at=?,updated_at=? WHERE source=? AND external_id=?",
        (iso(), iso(), item.source, item.external_id),
    )
