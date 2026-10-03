from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from dadbot.config import Settings
from dadbot.db import Database
from dadbot.types import ExternalItem, StreamState

from ._monitor import AnnouncementCallback, MonitorBase, mark_announced, upsert_external

log = logging.getLogger(__name__)
SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


def parse_search(payload: dict[str, Any], state: StreamState) -> list[ExternalItem]:
    result: list[ExternalItem] = []
    for raw in payload.get("items", []):
        video_id = raw.get("id", {}).get("videoId")
        snippet = raw.get("snippet", {})
        title = snippet.get("title")
        if isinstance(video_id, str) and isinstance(title, str):
            result.append(
                ExternalItem(
                    "youtube_livestream",
                    video_id,
                    title,
                    f"https://www.youtube.com/watch?v={video_id}",
                    state.value,
                )
            )
    return result


class YouTubeLivestreamMonitor(MonitorBase):
    def __init__(
        self,
        settings: Settings,
        db: Database,
        session: aiohttp.ClientSession,
        announce_callback: AnnouncementCallback,
    ) -> None:
        super().__init__(db, "youtube_livestreams")
        self.settings, self.session, self.announce_callback = settings, session, announce_callback
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.settings.youtube_channel_id and self.settings.youtube_api_key)

    async def _search(self, event_type: str, state: StreamState) -> list[ExternalItem]:
        params = {
            "part": "snippet",
            "channelId": self.settings.youtube_channel_id,
            "eventType": event_type,
            "type": "video",
            "maxResults": "10",
            "order": "date",
            "key": self.settings.youtube_api_key,
        }
        async with self.session.get(
            SEARCH_URL, params=params, timeout=aiohttp.ClientTimeout(total=20)
        ) as response:
            response.raise_for_status()
            return parse_search(await response.json(), state)

    async def check(self) -> list[ExternalItem]:
        if not self.configured:
            await self._record_status(
                "not_configured", "YouTube API livestream settings are incomplete"
            )
            return []
        async with self._lock:
            try:
                live, scheduled = await asyncio.gather(
                    self._search("live", StreamState.LIVE),
                    self._search("upcoming", StreamState.SCHEDULED),
                )
                visible = {item.external_id for item in live + scheduled}
                prior_active = await self.db.fetchall(
                    "SELECT external_id,title,url,state FROM external_items WHERE source=? AND state IN (?,?)",
                    ("youtube_livestream", StreamState.LIVE.value, StreamState.SCHEDULED.value),
                )
                for row in prior_active:
                    if row["external_id"] not in visible:
                        ended = ExternalItem(
                            "youtube_livestream",
                            row["external_id"],
                            row["title"] or "Livestream",
                            row["url"] or "",
                            StreamState.ENDED.value,
                        )
                        await upsert_external(self.db, ended)
                announced: list[ExternalItem] = []
                for item in scheduled + live:
                    previous, was_announced = await upsert_external(self.db, item)
                    if item.state == StreamState.LIVE.value and not was_announced:
                        await self.announce_callback(item)
                        await mark_announced(self.db, item)
                        announced.append(item)
                        log.info(
                            "Livestream went live: %s (previous=%s)", item.external_id, previous
                        )
                await self._record_status(
                    "ok", f"live={len(live)} upcoming={len(scheduled)}", success=True
                )
                return announced
            except Exception as exc:  # noqa: BLE001 - isolate scheduler from service/callback failure
                log.error("YouTube livestream check failed (%s)", type(exc).__name__)
                # aiohttp exception strings can contain request URLs (and their API key).
                await self._record_status("error", f"{type(exc).__name__}: check failed")
                return []

    async def simulate(
        self,
        video_id: str = "simulated-stream",
        title: str = "Simulated stream",
        state: StreamState | str = StreamState.LIVE,
        url: str | None = None,
    ) -> bool:
        state = StreamState(state)
        item = ExternalItem(
            "youtube_livestream",
            video_id,
            title,
            url or f"https://www.youtube.com/watch?v={video_id}",
            state.value,
        )
        _, announced = await upsert_external(self.db, item, {"simulated": True})
        if state is StreamState.LIVE and not announced:
            await self.announce_callback(item)
            await mark_announced(self.db, item)
            return True
        return False
