from __future__ import annotations

import argparse
import asyncio

from dadbot.backup import backup_database
from dadbot.config import Settings
from dadbot.db import Database


async def initialize() -> None:
    settings = Settings.from_environment()
    database = Database(settings.database_path)
    await database.connect()
    healthy = await database.healthy()
    await database.close()
    if not healthy:
        raise SystemExit("Database integrity check failed")
    print(f"Initialized {settings.database_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="dadbot local administration")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("init-db")
    subcommands.add_parser("check-db")
    backup = subcommands.add_parser("backup")
    backup.add_argument("--retention", type=int)
    args = parser.parse_args()
    if args.command in {"init-db", "check-db"}:
        asyncio.run(initialize())
    else:
        settings = Settings.from_environment()
        path = backup_database(
            settings.database_path,
            settings.database_path.parent / "backups",
            args.retention or settings.backup_retention,
        )
        print(path)


if __name__ == "__main__":
    main()
