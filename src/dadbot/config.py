from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv


def _text(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _required(name: str) -> str:
    value = _text(name)
    if not value:
        raise ValueError(f"Missing required environment value: {name}")
    return value


def _integer(name: str, default: int | None = None) -> int | None:
    raw = _text(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


def _integer_or(name: str, default: int) -> int:
    value = _integer(name)
    return default if value is None else value


def _boolean(name: str, default: bool = False) -> bool:
    raw = _text(name)
    if not raw:
        return default
    if raw.lower() in {"1", "true", "yes", "on"}:
        return True
    if raw.lower() in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _ids(name: str) -> frozenset[int]:
    raw = _text(name)
    if not raw:
        return frozenset()
    try:
        return frozenset(int(item.strip()) for item in raw.split(",") if item.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must contain comma-separated numeric Discord IDs") from exc


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    guild_id: int
    default_channel_id: int | None
    youtube_upload_channel_id: int | None
    live_notification_channel_id: int | None
    suggestion_channel_id: int | None
    highlight_channel_id: int | None
    owner_ids: frozenset[int]
    timezone: ZoneInfo
    database_path: Path
    content_path: Path
    log_path: Path
    log_level: str
    development_mode: bool
    sync_commands: bool
    youtube_channel_id: str
    youtube_api_key: str
    youtube_poll_minutes: int
    livestream_poll_minutes: int
    dad_joke_min_days: int
    dad_joke_max_days: int
    showcase_min_days: int
    showcase_max_days: int
    question_weekday: int
    poll_weekday: int
    schedule_hour_start: int
    schedule_hour_end: int
    backup_retention: int
    send_test_message: bool = False
    test_message: str = "Dadbot bootstrap connection test."
    twitch_client_id: str = ""
    twitch_client_secret: str = ""
    twitch_user_login: str = ""
    twitch_poll_minutes: int = 2

    @classmethod
    def from_environment(cls) -> Settings:
        load_dotenv()
        guild_id = _integer("DISCORD_GUILD_ID") or _integer("DISCORD_TEST_GUILD_ID")
        if guild_id is None:
            raise ValueError("Missing required environment value: DISCORD_GUILD_ID")
        default_channel = _integer("DISCORD_DEFAULT_CHANNEL_ID") or _integer(
            "DISCORD_TEST_CHANNEL_ID"
        )
        timezone_name = _text("BOT_TIMEZONE", "America/Indiana/Indianapolis")
        try:
            timezone = ZoneInfo(timezone_name)
        except Exception as exc:
            raise ValueError(f"BOT_TIMEZONE is invalid: {timezone_name}") from exc
        return cls(
            bot_token=_required("DISCORD_BOT_TOKEN"),
            guild_id=guild_id,
            default_channel_id=default_channel,
            youtube_upload_channel_id=_integer(
                "DISCORD_YOUTUBE_UPLOAD_CHANNEL_ID", default_channel
            ),
            live_notification_channel_id=_integer(
                "DISCORD_LIVE_NOTIFICATION_CHANNEL_ID", default_channel
            ),
            suggestion_channel_id=_integer("DISCORD_SUGGESTION_CHANNEL_ID", default_channel),
            highlight_channel_id=_integer("DISCORD_HIGHLIGHT_CHANNEL_ID", default_channel),
            owner_ids=_ids("DISCORD_OWNER_IDS"),
            timezone=timezone,
            database_path=Path(_text("DATABASE_PATH", "data/dadbot.sqlite3")),
            content_path=Path(_text("CONTENT_PATH", "content")),
            log_path=Path(_text("LOG_PATH", "logs/dadbot.log")),
            log_level=_text("LOG_LEVEL", "INFO").upper(),
            development_mode=_boolean("DEVELOPMENT_MODE", True),
            sync_commands=_boolean("DISCORD_SYNC_COMMANDS", True),
            youtube_channel_id=_text("YOUTUBE_CHANNEL_ID"),
            youtube_api_key=_text("YOUTUBE_API_KEY"),
            youtube_poll_minutes=_integer_or("YOUTUBE_POLL_MINUTES", 15),
            livestream_poll_minutes=_integer_or("LIVESTREAM_POLL_MINUTES", 5),
            dad_joke_min_days=_integer_or("DAD_JOKE_MIN_DAYS", 2),
            dad_joke_max_days=_integer_or("DAD_JOKE_MAX_DAYS", 4),
            showcase_min_days=_integer_or("SHOWCASE_MIN_DAYS", 7),
            showcase_max_days=_integer_or("SHOWCASE_MAX_DAYS", 12),
            question_weekday=_integer_or("QUESTION_WEEKDAY", 1),
            poll_weekday=_integer_or("POLL_WEEKDAY", 4),
            schedule_hour_start=_integer_or("SCHEDULE_HOUR_START", 10),
            schedule_hour_end=_integer_or("SCHEDULE_HOUR_END", 20),
            backup_retention=_integer_or("BACKUP_RETENTION", 14),
            send_test_message=_boolean("DISCORD_SEND_TEST_MESSAGE"),
            test_message=_text("DISCORD_TEST_MESSAGE", "Dadbot bootstrap connection test."),
            twitch_client_id=_text("TWITCH_CLIENT_ID"),
            twitch_client_secret=_text("TWITCH_CLIENT_SECRET"),
            twitch_user_login=_text("TWITCH_USER_LOGIN"),
            twitch_poll_minutes=_integer_or("TWITCH_POLL_MINUTES", 2),
        )

    @property
    def test_guild_id(self) -> int:
        return self.guild_id

    @property
    def test_channel_id(self) -> int | None:
        return self.default_channel_id
