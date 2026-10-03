from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from dadbot.config import Settings
    from dadbot.db import Database

log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def is_moderator(user: discord.abc.User, owner_ids: frozenset[int]) -> bool:
    """Server-side authorization shared by commands and component callbacks."""
    if user.id in owner_ids:
        return True
    return isinstance(user, discord.Member) and (
        user.guild_permissions.manage_messages or user.guild_permissions.administrator
    )


def parse_offsets(raw: str) -> tuple[int, ...]:
    try:
        values = tuple(sorted({int(item.strip()) for item in raw.split(",")}, reverse=True))
    except ValueError as exc:
        raise ValueError("Offsets must be comma-separated whole minutes") from exc
    if not values or any(value <= 0 for value in values):
        raise ValueError("Reminder offsets must be positive minutes")
    if len(values) > 8:
        raise ValueError("An event may have at most 8 reminder offsets")
    return values


def parse_event_time(raw: str, settings: Settings) -> datetime:
    """Parse ISO-8601, treating a time without an offset as guild-local time."""
    try:
        value = datetime.fromisoformat(raw.strip())
    except ValueError as exc:
        raise ValueError("Use an ISO time such as 2026-10-15 19:30") from exc
    if value.tzinfo is None:
        value = value.replace(tzinfo=settings.timezone)
    return value.astimezone(UTC)


class SuggestionView(discord.ui.View):
    def __init__(self, cog: CommunityCog, suggestion_id: int) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.suggestion_id = suggestion_id
        self.vote.custom_id = f"dadbot:suggestion:{suggestion_id}:vote"
        self.accept.custom_id = f"dadbot:suggestion:{suggestion_id}:accept"
        self.decline.custom_id = f"dadbot:suggestion:{suggestion_id}:decline"

    @discord.ui.button(
        label="Support",
        emoji="👍",
        style=discord.ButtonStyle.primary,
        custom_id="dadbot:suggestion:vote",
    )
    async def vote(self, interaction: discord.Interaction, button: discord.ui.Button[Any]) -> None:
        row = await self.cog.db.fetchone(
            "SELECT status FROM suggestions WHERE id=?", (self.suggestion_id,)
        )
        if row is None:
            await interaction.response.send_message(
                "That suggestion no longer exists.", ephemeral=True
            )
            return
        if row["status"] != "open":
            await interaction.response.send_message(
                "Voting is closed for that suggestion.", ephemeral=True
            )
            return
        now = iso(utcnow())
        existing = await self.cog.db.fetchone(
            "SELECT 1 FROM suggestion_votes WHERE suggestion_id=? AND user_id=?",
            (self.suggestion_id, interaction.user.id),
        )
        if existing:
            await self.cog.db.execute(
                "DELETE FROM suggestion_votes WHERE suggestion_id=? AND user_id=?",
                (self.suggestion_id, interaction.user.id),
            )
            wording = "Support removed."
        else:
            await self.cog.db.execute(
                "INSERT INTO suggestion_votes(suggestion_id,user_id,created_at) VALUES(?,?,?)",
                (self.suggestion_id, interaction.user.id, now),
            )
            wording = "Support recorded."
        await interaction.response.edit_message(
            embed=await self.cog.suggestion_embed(self.suggestion_id), view=self
        )
        await interaction.followup.send(wording, ephemeral=True)

    async def _set_status(self, interaction: discord.Interaction, status: str) -> None:
        if not is_moderator(interaction.user, self.cog.settings.owner_ids):
            await interaction.response.send_message(
                "That button requires Manage Messages.", ephemeral=True
            )
            return
        changed = await self.cog.set_suggestion_status(self.suggestion_id, status)
        if not changed:
            await interaction.response.send_message(
                "That suggestion is unavailable.", ephemeral=True
            )
            return
        self._disable_all()
        await interaction.response.edit_message(
            embed=await self.cog.suggestion_embed(self.suggestion_id), view=self
        )

    def _disable_all(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True

    @discord.ui.button(
        label="Accept",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="dadbot:suggestion:accept",
    )
    async def accept(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        await self._set_status(interaction, "accepted")

    @discord.ui.button(
        label="Decline",
        emoji="✖️",
        style=discord.ButtonStyle.danger,
        custom_id="dadbot:suggestion:decline",
    )
    async def decline(
        self, interaction: discord.Interaction, button: discord.ui.Button[Any]
    ) -> None:
        await self._set_status(interaction, "declined")


class CommunityCog(commands.Cog):
    event = app_commands.Group(name="event", description="Manage community event reminders")
    highlight = app_commands.Group(name="highlight", description="Manage weekly highlights")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.settings: Settings = bot.settings  # type: ignore[attr-defined]
        self.db: Database = bot.db  # type: ignore[attr-defined]
        self.nomination_menu = app_commands.ContextMenu(
            name="Nominate for highlight", callback=self.nominate_message
        )

    async def cog_load(self) -> None:
        try:
            self.bot.tree.add_command(self.nomination_menu)
        except app_commands.CommandAlreadyRegistered:
            pass
        restored = await self.restore_persistent_views()
        log.info("Restored %d persistent suggestion view(s)", restored)

    async def restore_persistent_views(self) -> int:
        """Register component handlers for suggestions posted before this process."""
        rows = await self.db.fetchall(
            "SELECT id,message_id FROM suggestions WHERE status='open' AND message_id IS NOT NULL"
        )
        for row in rows:
            self.bot.add_view(SuggestionView(self, row["id"]), message_id=row["message_id"])
        return len(rows)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.nomination_menu.name, type=self.nomination_menu.type)

    @app_commands.command(
        name="suggest", description="Send an idea to the community suggestion box"
    )
    @app_commands.guild_only()
    @app_commands.describe(text="Your suggestion")
    async def suggest(
        self, interaction: discord.Interaction, text: app_commands.Range[str, 3, 1500]
    ) -> None:
        channel_id = self.settings.suggestion_channel_id
        if channel_id is None:
            await interaction.response.send_message(
                "The suggestion channel is not configured.", ephemeral=True
            )
            return
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "I cannot access the suggestion channel.", ephemeral=True
            )
            return
        now = iso(utcnow())
        suggestion_id = await self.db.execute(
            "INSERT INTO suggestions(guild_id,channel_id,author_id,body,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
            (
                interaction.guild_id,
                channel.id,
                interaction.user.id,
                str(text).strip(),
                "open",
                now,
                now,
            ),
        )
        view = SuggestionView(self, suggestion_id)
        try:
            message = await channel.send(
                embed=await self.suggestion_embed(suggestion_id), view=view
            )
        except Exception:
            await self.db.execute("DELETE FROM suggestions WHERE id=?", (suggestion_id,))
            log.exception("Failed to publish suggestion %s", suggestion_id)
            await interaction.response.send_message(
                "I couldn't publish that suggestion.", ephemeral=True
            )
            return
        await self.db.execute(
            "UPDATE suggestions SET message_id=? WHERE id=?", (message.id, suggestion_id)
        )
        self.bot.add_view(view, message_id=message.id)
        log.info("Suggestion %s created by %s", suggestion_id, interaction.user.id)
        await interaction.response.send_message(
            f"Suggestion #{suggestion_id} posted in {channel.mention}.", ephemeral=True
        )

    async def suggestion_embed(self, suggestion_id: int) -> discord.Embed:
        row = await self.db.fetchone(
            "SELECT s.*,COUNT(v.user_id) votes FROM suggestions s LEFT JOIN suggestion_votes v ON v.suggestion_id=s.id WHERE s.id=? GROUP BY s.id",
            (suggestion_id,),
        )
        if row is None:
            return discord.Embed(title="Suggestion unavailable", colour=discord.Colour.dark_grey())
        colours = {
            "open": discord.Colour.blurple(),
            "accepted": discord.Colour.green(),
            "declined": discord.Colour.red(),
        }
        embed = discord.Embed(
            title=f"Suggestion #{row['id']}",
            description=row["body"],
            colour=colours.get(row["status"], discord.Colour.dark_grey()),
        )
        embed.add_field(name="Status", value=str(row["status"]).title())
        embed.add_field(name="Community support", value=str(row["votes"]))
        embed.set_footer(text=f"Suggested by user {row['author_id']}")
        return embed

    async def set_suggestion_status(self, suggestion_id: int, status: str) -> bool:
        if status not in {"accepted", "declined", "open"}:
            raise ValueError("Invalid suggestion status")
        row = await self.db.fetchone("SELECT 1 FROM suggestions WHERE id=?", (suggestion_id,))
        if row is None:
            return False
        await self.db.execute(
            "UPDATE suggestions SET status=?,updated_at=? WHERE id=?",
            (status, iso(utcnow()), suggestion_id),
        )
        log.info("Suggestion %s changed to %s", suggestion_id, status)
        return True

    async def nominate_message(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        if not is_moderator(interaction.user, self.settings.owner_ids):
            await interaction.response.send_message(
                "Nominations require Manage Messages.", ephemeral=True
            )
            return
        if interaction.guild_id is None:
            await interaction.response.send_message("Highlights are server-only.", ephemeral=True)
            return
        try:
            nomination_id = await self.db.execute(
                "INSERT INTO quote_nominations(guild_id,channel_id,message_id,author_id,nominator_id,content,jump_url,nominated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    interaction.guild_id,
                    message.channel.id,
                    message.id,
                    message.author.id,
                    interaction.user.id,
                    message.content[:4000],
                    message.jump_url,
                    iso(utcnow()),
                ),
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                await interaction.response.send_message(
                    "That message is already nominated.", ephemeral=True
                )
                return
            raise
        log.info("Highlight nomination %s created for message %s", nomination_id, message.id)
        await interaction.response.send_message(
            f"Nominated as highlight #{nomination_id}.", ephemeral=True
        )

    @highlight.command(name="post", description="Post the oldest unfeatured nominated highlight")
    @app_commands.guild_only()
    async def highlight_post(self, interaction: discord.Interaction) -> None:
        if not is_moderator(interaction.user, self.settings.owner_ids):
            await interaction.response.send_message(
                "This requires Manage Messages.", ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        nomination_id = await self.post_next_highlight()
        if nomination_id is None:
            await interaction.followup.send("There are no unfeatured nominations.", ephemeral=True)
        else:
            await interaction.followup.send(f"Posted highlight #{nomination_id}.", ephemeral=True)

    async def post_next_highlight(self) -> int | None:
        row = await self.db.fetchone(
            "SELECT * FROM quote_nominations WHERE guild_id=? AND featured_at IS NULL ORDER BY nominated_at LIMIT 1",
            (self.settings.guild_id,),
        )
        if row is None:
            return None
        channel_id = self.settings.highlight_channel_id or row["channel_id"]
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.HTTPException:
                channel = None
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(  # noqa: TRY004 - unavailable runtime resource, not caller type
                "Highlight channel is unavailable"
            )
        content = (
            row["content"] or "*(The original message had no text or is no longer available.)*"
        )
        embed = discord.Embed(
            title="Highlight of the week", description=content[:4000], colour=discord.Colour.gold()
        )
        embed.add_field(name="Originally posted by", value=f"<@{row['author_id']}>")
        if row["jump_url"]:
            embed.add_field(name="Original", value=f"[Jump to message]({row['jump_url']})")
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await self.db.execute(
            "UPDATE quote_nominations SET featured_at=? WHERE id=? AND featured_at IS NULL",
            (iso(utcnow()), row["id"]),
        )
        log.info("Posted highlight nomination %s", row["id"])
        return int(row["id"])

    @event.command(name="create", description="Create an event and its reminders")
    @app_commands.guild_only()
    @app_commands.describe(
        when="ISO local date/time, e.g. 2026-10-15 19:30", offsets="Minutes before, comma separated"
    )
    async def event_create(
        self,
        interaction: discord.Interaction,
        name: app_commands.Range[str, 1, 100],
        when: str,
        offsets: str = "1440,60",
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not is_moderator(interaction.user, self.settings.owner_ids):
            await interaction.response.send_message(
                "Creating events requires Manage Messages.", ephemeral=True
            )
            return
        try:
            event_at = parse_event_time(when, self.settings)
            parsed_offsets = parse_offsets(offsets)
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return
        if event_at <= utcnow():
            await interaction.response.send_message(
                "The event must be in the future.", ephemeral=True
            )
            return
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("Choose a server text channel.", ephemeral=True)
            return
        now = iso(utcnow())
        async with self.db.transaction() as connection:
            cursor = await connection.execute(
                "INSERT INTO events(guild_id,channel_id,name,event_at,created_by,created_at) VALUES(?,?,?,?,?,?)",
                (
                    interaction.guild_id,
                    target.id,
                    str(name),
                    iso(event_at),
                    interaction.user.id,
                    now,
                ),
            )
            event_id = cursor.lastrowid
            for offset in parsed_offsets:
                due_at = event_at.timestamp() - offset * 60
                await connection.execute(
                    "INSERT INTO event_reminders(event_id,offset_minutes,due_at) VALUES(?,?,?)",
                    (event_id, offset, iso(datetime.fromtimestamp(due_at, UTC))),
                )
        await interaction.response.send_message(
            f"Event #{event_id} created for <t:{int(event_at.timestamp())}:F> in {target.mention}; reminders: "
            + ", ".join(f"{value}m" for value in parsed_offsets),
            ephemeral=True,
        )

    @event.command(name="list", description="List upcoming configured events")
    @app_commands.guild_only()
    async def event_list(self, interaction: discord.Interaction) -> None:
        rows = await self.db.fetchall(
            "SELECT id,name,event_at,channel_id FROM events WHERE guild_id=? AND cancelled_at IS NULL AND event_at>? ORDER BY event_at LIMIT 20",
            (interaction.guild_id, iso(utcnow())),
        )
        if not rows:
            text = "No upcoming events. The calendar is enjoying a tiny vacation."
        else:
            text = "\n".join(
                f"`#{row['id']}` **{row['name']}** — <t:{int(datetime.fromisoformat(row['event_at']).timestamp())}:F> in <#{row['channel_id']}>"
                for row in rows
            )
        await interaction.response.send_message(text, ephemeral=True)

    @event.command(name="cancel", description="Cancel an event and its unsent reminders")
    @app_commands.guild_only()
    async def event_cancel(self, interaction: discord.Interaction, event_id: int) -> None:
        if not is_moderator(interaction.user, self.settings.owner_ids):
            await interaction.response.send_message(
                "Cancelling events requires Manage Messages.", ephemeral=True
            )
            return
        row = await self.db.fetchone(
            "SELECT id FROM events WHERE id=? AND guild_id=? AND cancelled_at IS NULL",
            (event_id, interaction.guild_id),
        )
        if row is None:
            await interaction.response.send_message(
                "That active event was not found.", ephemeral=True
            )
            return
        await self.db.execute(
            "UPDATE events SET cancelled_at=? WHERE id=?", (iso(utcnow()), event_id)
        )
        await interaction.response.send_message(f"Event #{event_id} cancelled.", ephemeral=True)

    async def send_due_event_reminders(self, now: datetime | None = None) -> int:
        """Send useful due reminders. Safe to invoke repeatedly and after downtime."""
        current = (now or utcnow()).astimezone(UTC)
        rows = await self.db.fetchall(
            """SELECT r.event_id,r.offset_minutes,e.channel_id,e.name,e.event_at
               FROM event_reminders r JOIN events e ON e.id=r.event_id
               WHERE r.sent_at IS NULL AND e.cancelled_at IS NULL AND r.due_at<=? AND e.event_at>?
               ORDER BY r.due_at""",
            (iso(current), iso(current)),
        )
        sent = 0
        for row in rows:
            channel = self.bot.get_channel(row["channel_id"])
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(row["channel_id"])
                except discord.HTTPException:
                    log.exception("Cannot resolve reminder channel %s", row["channel_id"])
                    continue
            if not isinstance(channel, discord.TextChannel):
                continue
            event_at = datetime.fromisoformat(row["event_at"])
            try:
                await channel.send(
                    f"⏰ **{row['name']}** starts <t:{int(event_at.timestamp())}:R> "
                    f"(<t:{int(event_at.timestamp())}:F>)."
                )
            except discord.HTTPException:
                log.exception("Failed event reminder %s/%s", row["event_id"], row["offset_minutes"])
                continue
            await self.db.execute(
                "UPDATE event_reminders SET sent_at=? WHERE event_id=? AND offset_minutes=? AND sent_at IS NULL",
                (iso(current), row["event_id"], row["offset_minutes"]),
            )
            sent += 1
            log.info("Sent event reminder %s/%s", row["event_id"], row["offset_minutes"])
        return sent


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CommunityCog(bot))
