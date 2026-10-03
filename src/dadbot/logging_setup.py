from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(level: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    handlers.append(RotatingFileHandler(path, maxBytes=5_000_000, backupCount=5))
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=level, handlers=handlers, force=True)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
