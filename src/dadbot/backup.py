from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def backup_database(source: Path, destination_dir: Path, retention: int = 14) -> Path:
    if not source.exists():
        raise FileNotFoundError(f"Database does not exist: {source}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    destination = destination_dir / f"dadbot-{stamp}.sqlite3"
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
    backups = sorted(destination_dir.glob("dadbot-*.sqlite3"), reverse=True)
    for old_backup in backups[max(1, retention) :]:
        old_backup.unlink()
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a safe SQLite backup")
    parser.add_argument("--database", type=Path, default=Path("data/dadbot.sqlite3"))
    parser.add_argument("--destination", type=Path, default=Path("data/backups"))
    parser.add_argument("--retention", type=int, default=14)
    args = parser.parse_args()
    print(backup_database(args.database, args.destination, args.retention))


if __name__ == "__main__":
    main()
