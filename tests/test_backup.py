import sqlite3
from pathlib import Path

from dadbot.backup import backup_database


def test_backup_is_valid_and_enforces_retention(tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE sample(value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('kept')")
    destination = backup_database(source, tmp_path / "backups", retention=1)
    with sqlite3.connect(destination) as connection:
        assert connection.execute("SELECT value FROM sample").fetchone() == ("kept",)
