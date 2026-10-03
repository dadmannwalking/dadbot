from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from dadbot.config import Settings
from dadbot.content import ContentService
from dadbot.db import Database
from dadbot.logging_setup import configure_logging
from dadbot.scheduler import EngagementScheduler
from dadbot.services.livestream import YouTubeLivestreamMonitor
from dadbot.services.twitch import TwitchLivestreamMonitor
from dadbot.services.youtube import YouTubeUploadMonitor
from dadbot.types import ExternalItem

log = logging.getLogger(__name__)


def notification_content(
    label: str, guild: discord.Guild, role_id: int | None
) -> tuple[str, discord.AllowedMentions]:
    """Build a notification with one explicitly allow-listed role mention."""
    if role_id is None:
        return label, discord.AllowedMentions.none()
    role = guild.get_role(role_id)
    if role is None:
        log.warning("Configured notification role %s is not visible in guild %s", role_id, guild.id)
        return label, discord.AllowedMentions.none()
    return (
        f"{role.mention} {label}",
        discord.AllowedMentions(everyone=False, users=False, roles=[role], replied_user=False),
    )


class DadBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.messages = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.db = Database(settings.database_path)
        self.content = ContentService(self.db, settings.content_path)
        self.http_session: aiohttp.ClientSession
        self.youtube_uploads: YouTubeUploadMonitor
        self.livestreams: YouTubeLivestreamMonitor
        self.twitch_livestreams: TwitchLivestreamMonitor
        self.scheduler: EngagementScheduler
        self.started_at = datetime.now(UTC)
        self._monitor_tasks: list[asyncio.Task[None]] = []

    async def setup_hook(self) -> None:
        await self.db.connect()
        self.content.load()
        self.http_session = aiohttp.ClientSession()
        self.youtube_uploads = YouTubeUploadMonitor(
            self.settings, self.db, self.http_session, self.announce_upload
        )
        self.livestreams = YouTubeLivestreamMonitor(
            self.settings, self.db, self.http_session, self.announce_livestream
        )
        self.twitch_livestreams = TwitchLivestreamMonitor(
            self.settings, self.db, self.http_session, self.announce_livestream
        )
        await self.load_extension("dadbot.cogs.community")
        await self.load_extension("dadbot.cogs.operations")
        callbacks = {
            "dad_joke": lambda: self.post_feature("dad_joke"),
            "question": lambda: self.post_feature("question"),
            "poll": lambda: self.post_feature("poll"),
            "showcase": lambda: self.post_feature("showcase"),
            "event_reminders": self.send_due_event_reminders,
        }
        self.scheduler = EngagementScheduler(self.settings, self.db, callbacks)
        await self.scheduler.start()
        guild = discord.Object(id=self.settings.guild_id)
        if self.settings.sync_commands:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %d application commands to guild %s", len(synced), guild.id)
        self._monitor_tasks = [
            asyncio.create_task(
                self._monitor_loop(
                    "youtube uploads",
                    self.youtube_uploads.check,
                    self.settings.youtube_poll_minutes,
                )
            ),
            asyncio.create_task(
                self._monitor_loop(
                    "livestreams", self.livestreams.check, self.settings.livestream_poll_minutes
                )
            ),
            asyncio.create_task(
                self._monitor_loop(
                    "Twitch livestreams",
                    self.twitch_livestreams.check,
                    self.settings.twitch_poll_minutes,
                )
            ),
        ]

    async def close(self) -> None:
        log.info("dadbot shutdown requested")
        for task in self._monitor_tasks:
            task.cancel()
        for task in self._monitor_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if hasattr(self, "scheduler"):
            await self.scheduler.shutdown(wait=False)
        if hasattr(self, "http_session") and not self.http_session.closed:
            await self.http_session.close()
        await self.db.close()
        await super().close()
        log.info("dadbot shutdown complete")

    async def on_ready(self) -> None:
        if self.user:
            log.info(
                "Connected as %s (%s); guild=%s latency_ms=%.0f",
                self.user,
                self.user.id,
                self.settings.guild_id,
                self.latency * 1000,
            )

    async def on_resumed(self) -> None:
        log.info("Discord session resumed")

    async def on_error(self, event_method: str, *args: Any, **kwargs: Any) -> None:
        log.exception("Unhandled Discord event error in %s", event_method)

    async def _monitor_loop(self, name: str, callback: Any, minutes: int) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await callback()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Uncaught %s monitor failure", name)
            await asyncio.sleep(max(1, minutes) * 60)

    async def target_channel(self, channel_id: int | None = None) -> discord.TextChannel:
        channel_id = channel_id or self.settings.default_channel_id
        if channel_id is None:
            raise RuntimeError("DISCORD_DEFAULT_CHANNEL_ID is not configured")
        channel = self.get_channel(channel_id)
        if channel is None:
            channel = await self.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(  # noqa: TRY004 - unavailable runtime resource, not caller type
                f"Configured channel {channel_id} is not a server text channel"
            )
        return channel

    async def post_feature(self, feature: str) -> discord.Message:
        if feature not in {"dad_joke", "question", "poll", "challenge", "showcase"}:
            raise ValueError(f"Unknown engagement feature: {feature}")
        item = await self.content.next(feature)
        channel = await self.target_channel()
        if feature == "poll":
            poll = discord.Poll(question=item.text, duration=timedelta(hours=24))
            for option in item.options:
                poll.add_answer(text=option)
            message = await channel.send(
                content="📊 Weekly very-scientific community poll:", poll=poll
            )
        else:
            headings = {
                "dad_joke": "👨 Dad joke delivery",
                "question": "💬 Question of the week",
                "challenge": "🏁 Community challenge",
                "showcase": "🛠️ Show-and-tell time",
            }
            embed = discord.Embed(
                description=item.text,
                color=discord.Color.orange(),
            )
            embed.set_author(name=headings[feature])
            message = await channel.send(embed=embed)
        log.info("Posted %s content item %s as message %s", feature, item.id, message.id)
        return message

    async def announce_upload(self, item: ExternalItem) -> None:
        channel = await self.target_channel(self.settings.youtube_upload_channel_id)
        embed = discord.Embed(
            title=item.title[:256],
            url=item.url,
            description="Fresh from YouTube. The pixels are still warm.",
            color=discord.Color.red(),
        )
        content, allowed_mentions = notification_content(
            "📺 **New video uploaded!**", channel.guild, self.settings.youtube_upload_role_id
        )
        await channel.send(content, embed=embed, allowed_mentions=allowed_mentions)
        log.info("Announced YouTube upload %s", item.external_id)

    async def announce_livestream(self, item: ExternalItem) -> None:
        channel = await self.target_channel(self.settings.live_notification_channel_id)
        is_twitch = item.source == "twitch_livestream"
        embed = discord.Embed(
            title=item.title[:256],
            url=item.url,
            description="We’re live—come hang out!",
            color=discord.Color.from_rgb(145, 70, 255) if is_twitch else discord.Color.red(),
        )
        platform = "Twitch" if is_twitch else "YouTube"
        content, allowed_mentions = notification_content(
            f"🔴 **We’re live on {platform} now!**",
            channel.guild,
            self.settings.live_notification_role_id,
        )
        await channel.send(content, embed=embed, allowed_mentions=allowed_mentions)
        log.info("Announced %s livestream %s", platform, item.external_id)

    async def send_due_event_reminders(self) -> None:
        cog = self.get_cog("CommunityCog")
        if cog is None:
            raise RuntimeError("Community cog is unavailable")
        await cog.send_due_event_reminders()  # type: ignore[attr-defined]


async def tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
    log.error(
        "Application command failed: %s",
        interaction.command,
        exc_info=(type(error), error, error.__traceback__),
    )
    message = "That command hit an unexpected snag. It has been logged."
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


async def run() -> None:
    settings = Settings.from_environment()
    configure_logging(settings.log_level, settings.log_path)
    bot = DadBot(settings)
    bot.tree.on_error = tree_error
    log.info("Starting dadbot; development_mode=%s", settings.development_mode)
    async with bot:
        await bot.start(settings.bot_token)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except ValueError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc
