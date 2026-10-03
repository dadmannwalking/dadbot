from dadbot.cogs.operations import is_operator


def test_operator_permission_sources() -> None:
    owners = frozenset({10})
    assert is_operator(owners, 10, manage_guild=False, app_owner=False)
    assert is_operator(owners, 11, manage_guild=True, app_owner=False)
    assert is_operator(owners, 12, manage_guild=False, app_owner=True)
    assert not is_operator(owners, 13, manage_guild=False, app_owner=False)
