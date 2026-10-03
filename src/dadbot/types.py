from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class StreamState(StrEnum):
    UNKNOWN = "unknown"
    SCHEDULED = "scheduled"
    LIVE = "live"
    ENDED = "ended"


@dataclass(frozen=True, slots=True)
class ExternalItem:
    source: str
    external_id: str
    title: str
    url: str
    state: str
    published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ContentItem:
    id: str
    text: str
    options: tuple[str, ...] = ()
