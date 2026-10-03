from types import SimpleNamespace

from dadbot.bot import notification_content


def test_notification_role_is_explicitly_allow_listed() -> None:
    role = SimpleNamespace(id=21, mention="<@&21>")
    guild = SimpleNamespace(id=1, get_role=lambda role_id: role if role_id == 21 else None)

    content, allowed = notification_content("New upload", guild, 21)

    assert content == "<@&21> New upload"
    assert allowed.everyone is False
    assert allowed.users is False
    assert allowed.roles == [role]


def test_missing_role_does_not_emit_raw_mention() -> None:
    guild = SimpleNamespace(id=1, get_role=lambda _: None)

    content, allowed = notification_content("New upload", guild, 999)

    assert content == "New upload"
    assert allowed.everyone is False
    assert allowed.users is False
    assert allowed.roles is False
    assert allowed.replied_user is False
