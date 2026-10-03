from __future__ import annotations

import json
import random
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from .db import Database
from .types import ContentItem


class ContentError(ValueError):
    """A content pool is missing or malformed."""


class ContentService:
    """Loads local content and rotates it using durable usage history."""

    FILES: ClassVar[dict[str, str]] = {
        "dad_joke": "dad_jokes.json",
        "question": "questions.json",
        "poll": "polls.json",
        "challenge": "challenges.json",
        "showcase": "showcase.json",
    }

    def __init__(self, database: Database, content_path: Path, rng: random.Random | None = None):
        self.database = database
        self.content_path = content_path
        self.rng = rng or random.Random()
        self._pools: dict[str, tuple[ContentItem, ...]] = {}

    def load(self) -> None:
        pools: dict[str, tuple[ContentItem, ...]] = {}
        for feature, filename in self.FILES.items():
            path = self.content_path / filename
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ContentError(f"Cannot load {feature} content from {path}: {exc}") from exc
            if not isinstance(raw, list) or not raw:
                raise ContentError(f"{path} must contain a non-empty JSON array")
            items: list[ContentItem] = []
            ids: set[str] = set()
            for index, value in enumerate(raw):
                if not isinstance(value, dict):
                    raise ContentError(f"{path}: item {index} must be an object")
                item_id, text = value.get("id"), value.get("text")
                options = value.get("options", [])
                if not isinstance(item_id, str) or not item_id.strip():
                    raise ContentError(f"{path}: item {index} has no valid id")
                if item_id in ids:
                    raise ContentError(f"{path}: duplicate id {item_id!r}")
                if not isinstance(text, str) or not text.strip():
                    raise ContentError(f"{path}: item {item_id!r} has no valid text")
                if not isinstance(options, list) or not all(
                    isinstance(x, str) and x for x in options
                ):
                    raise ContentError(f"{path}: item {item_id!r} has invalid options")
                if feature == "poll" and len(options) < 2:
                    raise ContentError(f"{path}: poll {item_id!r} needs at least two options")
                ids.add(item_id)
                items.append(ContentItem(item_id, text.strip(), tuple(options)))
            pools[feature] = tuple(items)
        self._pools = pools

    def pool(self, feature: str) -> Sequence[ContentItem]:
        if not self._pools:
            self.load()
        try:
            return self._pools[feature]
        except KeyError as exc:
            raise ContentError(f"Unknown content feature: {feature}") from exc

    async def next(self, feature: str, *, record: bool = True) -> ContentItem:
        """Choose among least-used items, preferring the least recently used."""
        items = self.pool(feature)
        rows = await self.database.fetchall(
            """SELECT content_id, COUNT(*) AS uses, MAX(used_at) AS last_used
               FROM content_history WHERE feature = ? GROUP BY content_id""",
            (feature,),
        )
        history = {str(row["content_id"]): (int(row["uses"]), row["last_used"]) for row in rows}
        minimum = min((history.get(item.id, (0, None))[0] for item in items), default=0)
        candidates = [item for item in items if history.get(item.id, (0, None))[0] == minimum]
        # Randomize a fresh cycle, while least-recently-used breaks ties in later cycles.
        never_used = [item for item in candidates if history.get(item.id, (0, None))[1] is None]
        if never_used:
            chosen = self.rng.choice(never_used)
        else:
            oldest = min(history[item.id][1] for item in candidates)
            chosen = self.rng.choice([item for item in candidates if history[item.id][1] == oldest])
        if record:
            await self.record(feature, chosen.id)
        return chosen

    async def record(self, feature: str, content_id: str, used_at: datetime | None = None) -> None:
        stamp = (used_at or datetime.now(UTC)).isoformat()
        await self.database.execute(
            "INSERT INTO content_history(feature, content_id, used_at) VALUES (?, ?, ?)",
            (feature, content_id, stamp),
        )
