from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from xml.etree import ElementTree

import aiohttp

from dadbot.config import Settings
from dadbot.db import Database
from dadbot.types import ExternalItem

from ._monitor import AnnouncementCallback, MonitorBase, mark_announced, upsert_external

log = logging.getLogger(__name__)
ATOM_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


def _date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(text).astimezone(UTC)
    except ValueError:
        return None


def parse_atom_feed(payload: str | bytes) -> list[ExternalItem]:
    """Parse a YouTube Atom feed, ignoring malformed entries."""
    root = ElementTree.fromstring(payload)
    items: list[ExternalItem] = []
    for entry in root.findall("atom:entry", NS):
        video_id = entry.findtext("yt:videoId", namespaces=NS)
        title = entry.findtext("atom:title", namespaces=NS)
        link = entry.find("atom:link[@rel='alternate']", NS)
        if not video_id or not title:
            continue
        items.append(
            ExternalItem(
                source="youtube_upload",
                external_id=video_id,
                title=title.strip(),
                url=(link.get("href") if link is not None else None)
                or f"https://www.youtube.com/watch?v={video_id}",
                state="published",
                published_at=_date(entry.findtext("atom:published", namespaces=NS)),
            )
        )
    return sorted(
        items, key=lambda item: item.published_at or datetime.min.replace(tzinfo=UTC), reverse=True
    )


class YouTubeUploadMonitor(MonitorBase):
    def __init__(
        self,
        settings: Settings,
        db: Database,
        session: aiohttp.ClientSession,
        announce_callback: AnnouncementCallback,
    ) -> None:
        super().__init__(db, "youtube_uploads")
        self.settings, self.session, self.announce_callback = settings, session, announce_callback
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.settings.youtube_channel_id)

    async def check(self) -> list[ExternalItem]:
        if not self.configured:
            await self._record_status("not_configured", "YOUTUBE_CHANNEL_ID is not configured")
            return []
        async with self._lock:
            try:
                url = ATOM_URL.format(channel_id=self.settings.youtube_channel_id)
                async with self.session.get(
                    url, timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    response.raise_for_status()
                    entries = parse_atom_feed(await response.read())
                announced: list[ExternalItem] = []
                # Announce only the newest unseen upload, preventing a backlog dump after downtime.
                for item in entries[:1]:
                    _, already_announced = await upsert_external(self.db, item)
                    stream = await self.db.fetchone(
                        "SELECT 1 FROM external_items WHERE source=? AND external_id=?",
                        ("youtube_livestream", item.external_id),
                    )
                    # Atom also contains past/live broadcasts; the livestream monitor owns those.
                    if stream:
                        await mark_announced(self.db, item)
                        continue
                    if not already_announced:
                        await self.announce_callback(item)
                        await mark_announced(self.db, item)
                        announced.append(item)
                await self._record_status("ok", f"feed entries={len(entries)}", success=True)
                return announced
            except Exception as exc:
                log.exception("YouTube upload check failed")
                await self._record_status("error", f"{type(exc).__name__}: check failed")
                return []

    async def simulate(
        self,
        video_id: str = "simulated-upload",
        title: str = "Simulated upload",
        url: str | None = None,
    ) -> bool:
        item = ExternalItem(
            "youtube_upload",
            video_id,
            title,
            url or f"https://www.youtube.com/watch?v={video_id}",
            "published",
        )
        _, announced = await upsert_external(self.db, item, {"simulated": True})
        if announced:
            return False
        await self.announce_callback(item)
        await mark_announced(self.db, item)
        return True
