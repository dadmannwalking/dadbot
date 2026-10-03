from dadbot.config import Settings


def test_notification_channels_and_twitch_settings(monkeypatch) -> None:
    values = {
        "DISCORD_BOT_TOKEN": "test-token",
        "DISCORD_GUILD_ID": "1",
        "DISCORD_DEFAULT_CHANNEL_ID": "10",
        "DISCORD_YOUTUBE_UPLOAD_CHANNEL_ID": "11",
        "DISCORD_LIVE_NOTIFICATION_CHANNEL_ID": "12",
        "DISCORD_YOUTUBE_UPLOAD_ROLE_ID": "21",
        "DISCORD_LIVE_NOTIFICATION_ROLE_ID": "22",
        "DISCORD_EVENT_CHANNEL_ID": "13",
        "TWITCH_CLIENT_ID": "client",
        "TWITCH_CLIENT_SECRET": "secret",
        "TWITCH_USER_LOGIN": "dadmannwalking",
        "TWITCH_POLL_MINUTES": "3",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    settings = Settings.from_environment()

    assert settings.youtube_upload_channel_id == 11
    assert settings.live_notification_channel_id == 12
    assert settings.youtube_upload_role_id == 21
    assert settings.live_notification_role_id == 22
    assert settings.event_channel_id == 13
    assert settings.twitch_user_login == "dadmannwalking"
    assert settings.twitch_poll_minutes == 3


def test_notification_channels_fall_back_to_default(monkeypatch) -> None:
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DISCORD_GUILD_ID", "1")
    monkeypatch.setenv("DISCORD_DEFAULT_CHANNEL_ID", "10")
    monkeypatch.setenv("DISCORD_YOUTUBE_UPLOAD_CHANNEL_ID", "")
    monkeypatch.setenv("DISCORD_LIVE_NOTIFICATION_CHANNEL_ID", "")
    monkeypatch.setenv("DISCORD_EVENT_CHANNEL_ID", "")

    settings = Settings.from_environment()

    assert settings.youtube_upload_channel_id == 10
    assert settings.live_notification_channel_id == 10
    assert settings.event_channel_id == 10
