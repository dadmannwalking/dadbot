from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from dadbot.bot import DadBot
from dadbot.cogs.community import EventView, SuggestionView, iso
from dadbot.config import Settings
from dadbot.logging_setup import configure_logging
from dadbot.types import StreamState

log = logging.getLogger(__name__)


class LiveSmokeBot(DadBot):
    """One-shot, test-server-only verification using real Discord operations."""

    def __init__(self, settings: Settings) -> None:
        if not settings.development_mode:
            raise ValueError("Live smoke testing requires DEVELOPMENT_MODE=true")
        super().__init__(settings)
        self._smoke_started = False

    async def on_ready(self) -> None:
        await super().on_ready()
        if self._smoke_started:
            return
        self._smoke_started = True
        try:
            await self.run_smoke_test()
        finally:
            await self.close()

    async def run_smoke_test(self) -> None:
        channel = await self.target_channel()
        results: list[str] = []
        posted = {}
        for feature in ("dad_joke", "question", "poll", "challenge", "showcase"):
            posted[feature] = await self.post_feature(feature)
            results.append(f"{feature}: sent")

        first_upload = await self.youtube_uploads.simulate(
            "dadbot-smoke-upload", "dadbot upload smoke test"
        )
        duplicate_upload = await self.youtube_uploads.simulate(
            "dadbot-smoke-upload", "dadbot upload smoke test"
        )
        if duplicate_upload:
            raise AssertionError("Upload duplicate suppression failed")
        results.append(
            f"youtube: {'announced' if first_upload else 'persisted'}; duplicate blocked"
        )

        await self.livestreams.simulate(
            "dadbot-smoke-stream", "dadbot livestream smoke test", StreamState.SCHEDULED
        )
        first_live = await self.livestreams.simulate(
            "dadbot-smoke-stream", "dadbot livestream smoke test", StreamState.LIVE
        )
        duplicate_live = await self.livestreams.simulate(
            "dadbot-smoke-stream", "dadbot livestream smoke test", StreamState.LIVE
        )
        await self.livestreams.simulate(
            "dadbot-smoke-stream", "dadbot livestream smoke test", StreamState.ENDED
        )
        if duplicate_live:
            raise AssertionError("Livestream duplicate suppression failed")
        results.append(
            f"livestream: {'announced' if first_live else 'persisted'}; duplicate blocked"
        )

        first_twitch = await self.twitch_livestreams.simulate(
            "dadbot-smoke-twitch", "dadbot Twitch smoke test", StreamState.LIVE
        )
        duplicate_twitch = await self.twitch_livestreams.simulate(
            "dadbot-smoke-twitch", "dadbot Twitch smoke test", StreamState.LIVE
        )
        await self.twitch_livestreams.simulate(
            "dadbot-smoke-twitch", "dadbot Twitch smoke test", StreamState.ENDED
        )
        if duplicate_twitch:
            raise AssertionError("Twitch duplicate suppression failed")
        results.append(f"twitch: {'announced' if first_twitch else 'persisted'}; duplicate blocked")

        cog = self.get_cog("CommunityCog")
        if cog is None:
            raise AssertionError("Community cog was not loaded")
        now = iso(datetime.now(UTC))
        suggestion_id = await self.db.execute(
            "INSERT INTO suggestions(guild_id,channel_id,author_id,body,status,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                self.settings.guild_id,
                channel.id,
                self.user.id,
                "Live smoke test suggestion: add more groan-worthy jokes.",
                "open",
                now,
                now,
            ),
        )
        view = SuggestionView(cog, suggestion_id)  # type: ignore[arg-type]
        suggestion_message = await channel.send(
            embed=await cog.suggestion_embed(suggestion_id),
            view=view,  # type: ignore[attr-defined]
        )
        await self.db.execute(
            "UPDATE suggestions SET message_id=? WHERE id=?", (suggestion_message.id, suggestion_id)
        )
        self.add_view(view, message_id=suggestion_message.id)
        results.append("suggestion: persisted with buttons")

        nomination_id = await self.db.execute(
            "INSERT INTO quote_nominations(guild_id,channel_id,message_id,author_id,nominator_id,"
            "content,jump_url,nominated_at) VALUES(?,?,?,?,?,?,?,?)",
            (
                self.settings.guild_id,
                channel.id,
                posted["question"].id,
                self.user.id,
                self.user.id,
                "A live-test highlight, selected with all the ceremony it deserves.",
                posted["question"].jump_url,
                now,
            ),
        )
        featured = await cog.post_next_highlight()  # type: ignore[attr-defined]
        if featured is None:
            raise AssertionError("Highlight posting failed")
        results.append(f"highlight: nomination {nomination_id} persisted; {featured} featured")

        event_at = datetime.now(UTC) + timedelta(minutes=30)
        async with self.db.transaction() as connection:
            cursor = await connection.execute(
                "INSERT INTO events(guild_id,channel_id,name,description,event_at,created_by,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    self.settings.guild_id,
                    channel.id,
                    "dadbot live-test event",
                    "A harmless test gathering for checking the snacks and the RSVP buttons.",
                    iso(event_at),
                    self.user.id,
                    now,
                ),
            )
            await connection.execute(
                "INSERT INTO event_reminders(event_id,offset_minutes,due_at) VALUES(?,?,?)",
                (cursor.lastrowid, 60, iso(datetime.now(UTC) - timedelta(minutes=1))),
            )
            event_id = cursor.lastrowid
        event_view = EventView(cog, event_id)  # type: ignore[arg-type]
        event_message = await channel.send(
            embed=await cog.event_embed(event_id),
            view=event_view,  # type: ignore[attr-defined]
        )
        await self.db.execute(
            "UPDATE events SET message_id=? WHERE id=?", (event_message.id, event_id)
        )
        self.add_view(event_view, message_id=event_message.id)
        sent = await cog.send_due_event_reminders()  # type: ignore[attr-defined]
        if sent < 1:
            raise AssertionError("Event reminder was not sent")
        results.append("event: announcement buttons and reminder sent")

        guild_commands = await self.tree.fetch_commands(
            guild=self.get_guild(self.settings.guild_id)
        )
        names = {command.name for command in guild_commands}
        required = {"bot", "dev", "suggest", "event", "highlight"}
        if not required.issubset(names):
            raise AssertionError(f"Missing application commands: {required - names}")
        results.append(f"commands: {', '.join(sorted(names))}")
        await channel.send(
            "✅ **dadbot live integration smoke test passed**\n"
            + "\n".join(f"• {r}" for r in results)
        )
        log.info("Live Discord smoke test passed: %s", "; ".join(results))


async def run() -> None:
    settings = Settings.from_environment()
    configure_logging(settings.log_level, settings.log_path)
    bot = LiveSmokeBot(settings)
    async with bot:
        await bot.start(settings.bot_token)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
