from __future__ import annotations

import platform
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import discord
from discord import app_commands
from discord.ext import commands

from dadbot.types import StreamState

if TYPE_CHECKING:
    from dadbot.bot import DadBot


def is_operator(
    configured_owners: frozenset[int], user_id: int, *, manage_guild: bool, app_owner: bool
) -> bool:
    return user_id in configured_owners or manage_guild or app_owner


async def require_operator(interaction: discord.Interaction, bot: DadBot) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    allowed = is_operator(
        bot.settings.owner_ids,
        interaction.user.id,
        manage_guild=bool(permissions and permissions.manage_guild),
        app_owner=await bot.is_owner(interaction.user),
    )
    if not allowed:
        await interaction.response.send_message(
            "That control panel is for bot owners and server managers.", ephemeral=True
        )
    return allowed


class OperationsCog(commands.Cog):
    status_commands = app_commands.Group(name="bot", description="dadbot operational commands")
    dev_group = app_commands.Group(name="dev", description="Protected development commands")

    def __init__(self, bot: DadBot) -> None:
        self.bot = bot

    @status_commands.command(name="status", description="Show dadbot operational health")
    async def status(self, interaction: discord.Interaction) -> None:
        now = datetime.now(UTC)
        uptime = now - self.bot.started_at
        latest_job = await self.bot.db.fetchone(
            "SELECT job_id, last_success_at FROM job_runs WHERE last_success_at IS NOT NULL "
            "ORDER BY last_success_at DESC LIMIT 1"
        )
        upcoming = self.bot.scheduler.upcoming(3)
        next_text = (
            "\n".join(f"• `{job['id']}`: {job['next_run_at']}" for job in upcoming)
            or "No jobs scheduled"
        )
        upload_status = self.bot.youtube_uploads.status
        stream_status = self.bot.livestreams.status
        twitch_status = self.bot.twitch_livestreams.status
        embed = discord.Embed(title="dadbot status", color=discord.Color.green(), timestamp=now)
        embed.add_field(name="Uptime", value=str(uptime).split(".")[0], inline=True)
        embed.add_field(name="Latency", value=f"{self.bot.latency * 1000:.0f} ms", inline=True)
        embed.add_field(
            name="Database",
            value="healthy" if await self.bot.db.healthy() else "error",
            inline=True,
        )
        embed.add_field(name="YouTube uploads", value=upload_status, inline=True)
        embed.add_field(name="YouTube live", value=stream_status, inline=True)
        embed.add_field(name="Twitch live", value=twitch_status, inline=True)
        embed.add_field(
            name="Last scheduled success",
            value=(
                f"{latest_job['job_id']} — {latest_job['last_success_at']}"
                if latest_job
                else "None yet"
            ),
            inline=False,
        )
        embed.add_field(name="Upcoming", value=next_text[:1024], inline=False)
        embed.set_footer(text=f"dadbot 0.1.0 • Python {platform.python_version()}")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        command_name = interaction.command.qualified_name if interaction.command else ""
        if command_name.startswith("dev "):
            if not self.bot.settings.development_mode:
                await interaction.response.send_message(
                    "Development mode is disabled.", ephemeral=True
                )
                return False
            return await require_operator(interaction, self.bot)
        return True

    @dev_group.command(name="trigger", description="Run an engagement feature immediately")
    async def trigger(
        self,
        interaction: discord.Interaction,
        feature: Literal["dad_joke", "question", "poll", "challenge", "showcase"],
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.bot.post_feature(feature)
        await interaction.followup.send(f"Triggered `{feature}`.", ephemeral=True)

    @dev_group.command(name="youtube", description="Simulate one YouTube upload")
    async def youtube(self, interaction: discord.Interaction, video_id: str = "dev-upload") -> None:
        await interaction.response.defer(ephemeral=True)
        await self.bot.youtube_uploads.simulate(
            video_id, "A simulated upload", f"https://youtu.be/{video_id}"
        )
        await interaction.followup.send("Upload transition simulated.", ephemeral=True)

    @dev_group.command(name="livestream", description="Simulate a livestream transition")
    async def livestream(
        self,
        interaction: discord.Interaction,
        state: Literal["scheduled", "live", "ended"],
        video_id: str = "dev-stream",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.bot.livestreams.simulate(
            video_id, "A simulated livestream", StreamState(state), f"https://youtu.be/{video_id}"
        )
        await interaction.followup.send(f"Livestream is now `{state}`.", ephemeral=True)

    @dev_group.command(name="twitch", description="Simulate a Twitch livestream transition")
    async def twitch(
        self,
        interaction: discord.Interaction,
        state: Literal["live", "ended"],
        stream_id: str = "dev-twitch-stream",
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        await self.bot.twitch_livestreams.simulate(
            stream_id, "A simulated Twitch livestream", StreamState(state)
        )
        await interaction.followup.send(f"Twitch livestream is now `{state}`.", ephemeral=True)

    @dev_group.command(name="scheduler", description="Inspect scheduled jobs")
    async def scheduler_status(self, interaction: discord.Interaction) -> None:
        jobs = self.bot.scheduler.upcoming(10)
        text = "\n".join(f"{job['id']}: {job['next_run_at']}" for job in jobs)
        await interaction.response.send_message(text or "No jobs scheduled.", ephemeral=True)


async def setup(bot: DadBot) -> None:
    await bot.add_cog(OperationsCog(bot))
