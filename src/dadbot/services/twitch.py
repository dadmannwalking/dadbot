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
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
STREAMS_URL = "https://api.twitch.tv/helix/streams"
SOURCE = "twitch_livestream"


def parse_streams(payload: dict[str, Any], user_login: str) -> list[ExternalItem]:
    """Convert valid Helix stream records into Dadbot external items."""
    result: list[ExternalItem] = []
    for raw in payload.get("data", []):
        stream_id = raw.get("id")
        title = raw.get("title")
        if isinstance(stream_id, str) and isinstance(title, str):
            login = raw.get("user_login")
            channel = login if isinstance(login, str) and login else user_login
            result.append(
                ExternalItem(
                    SOURCE,
                    stream_id,
                    title,
                    f"https://www.twitch.tv/{channel}",
                    StreamState.LIVE.value,
                )
            )
    return result


class TwitchLivestreamMonitor(MonitorBase):
    def __init__(
        self,
        settings: Settings,
        db: Database,
        session: aiohttp.ClientSession,
        announce_callback: AnnouncementCallback,
    ) -> None:
        super().__init__(db, "twitch_livestreams")
        self.settings, self.session, self.announce_callback = settings, session, announce_callback
        self._access_token: str | None = None
        self._lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(
            self.settings.twitch_client_id
            and self.settings.twitch_client_secret
            and self.settings.twitch_user_login
        )

    async def _authenticate(self) -> str:
        async with self.session.post(
            TOKEN_URL,
            data={
                "client_id": self.settings.twitch_client_id,
                "client_secret": self.settings.twitch_client_secret,
                "grant_type": "client_credentials",
            },
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            response.raise_for_status()
            payload = await response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("Twitch token response did not include an access token")
        self._access_token = token
        return token

    async def _fetch(self, *, refresh: bool = False) -> list[ExternalItem]:
        token = (
            await self._authenticate() if refresh or not self._access_token else self._access_token
        )
        async with self.session.get(
            STREAMS_URL,
            params={"user_login": self.settings.twitch_user_login},
            headers={
                "Authorization": f"Bearer {token}",
                "Client-Id": self.settings.twitch_client_id,
            },
            timeout=aiohttp.ClientTimeout(total=20),
        ) as response:
            if response.status == 401 and not refresh:
                self._access_token = None
                return await self._fetch(refresh=True)
            response.raise_for_status()
            return parse_streams(await response.json(), self.settings.twitch_user_login)

    async def check(self) -> list[ExternalItem]:
        if not self.configured:
            await self._record_status("not_configured", "Twitch settings are incomplete")
            return []
        async with self._lock:
            try:
                live = await self._fetch()
                visible = {item.external_id for item in live}
                prior = await self.db.fetchall(
                    "SELECT external_id,title,url FROM external_items WHERE source=? AND state=?",
                    (SOURCE, StreamState.LIVE.value),
                )
                for row in prior:
                    if row["external_id"] not in visible:
                        await upsert_external(
                            self.db,
                            ExternalItem(
                                SOURCE,
                                row["external_id"],
                                row["title"] or "Twitch stream",
                                row["url"] or "",
                                StreamState.ENDED.value,
                            ),
                        )
                announced: list[ExternalItem] = []
                for item in live:
                    previous, was_announced = await upsert_external(self.db, item)
                    if not was_announced:
                        await self.announce_callback(item)
                        await mark_announced(self.db, item)
                        announced.append(item)
                        log.info(
                            "Twitch stream went live: %s (previous=%s)", item.external_id, previous
                        )
                await self._record_status("ok", f"live={len(live)}", success=True)
                return announced
            except Exception as exc:  # noqa: BLE001 - isolate scheduler and hide credentials
                log.error("Twitch livestream check failed (%s)", type(exc).__name__)
                await self._record_status("error", f"{type(exc).__name__}: check failed")
                return []

    async def simulate(
        self,
        stream_id: str = "simulated-twitch-stream",
        title: str = "Simulated Twitch stream",
        state: StreamState | str = StreamState.LIVE,
        url: str | None = None,
    ) -> bool:
        state = StreamState(state)
        item = ExternalItem(
            SOURCE,
            stream_id,
            title,
            url or f"https://www.twitch.tv/{self.settings.twitch_user_login}",
            state.value,
        )
        _, announced = await upsert_external(self.db, item, {"simulated": True})
        if state is StreamState.LIVE and not announced:
            await self.announce_callback(item)
            await mark_announced(self.db, item)
            return True
        return False
