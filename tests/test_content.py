import json
import random

import pytest

from dadbot.content import ContentError, ContentService
from dadbot.db import Database


@pytest.fixture
async def database(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    await db.connect()
    yield db
    await db.close()


def write_pools(path, size=3):
    path.mkdir()
    for feature, filename in ContentService.FILES.items():
        values = [
            {
                "id": f"{feature}-{number}",
                "text": f"{feature} text {number}",
                **({"options": ["one", "two"]} if feature == "poll" else {}),
            }
            for number in range(size)
        ]
        (path / filename).write_text(json.dumps(values), encoding="utf-8")


async def test_rotation_exhausts_pool_before_repeating(database, tmp_path):
    content_path = tmp_path / "content"
    write_pools(content_path)
    service = ContentService(database, content_path, random.Random(7))

    first_cycle = [await service.next("dad_joke") for _ in range(3)]
    assert len({item.id for item in first_cycle}) == 3
    fourth = await service.next("dad_joke")
    assert fourth.id in {item.id for item in first_cycle}

    rows = await database.fetchall(
        "SELECT content_id FROM content_history WHERE feature='dad_joke'"
    )
    assert len(rows) == 4


async def test_rotation_history_survives_service_restart(database, tmp_path):
    content_path = tmp_path / "content"
    write_pools(content_path, size=2)
    first = ContentService(database, content_path, random.Random(1))
    used = await first.next("question")

    restarted = ContentService(database, content_path, random.Random(1))
    following = await restarted.next("question")
    assert following.id != used.id


async def test_peek_does_not_record(database, tmp_path):
    content_path = tmp_path / "content"
    write_pools(content_path)
    service = ContentService(database, content_path)
    await service.next("showcase", record=False)
    row = await database.fetchone("SELECT COUNT(*) FROM content_history")
    assert row[0] == 0


async def test_invalid_poll_is_rejected(database, tmp_path):
    content_path = tmp_path / "content"
    write_pools(content_path)
    (content_path / "polls.json").write_text(
        json.dumps([{"id": "bad", "text": "No choices", "options": []}]), encoding="utf-8"
    )
    with pytest.raises(ContentError, match="at least two"):
        ContentService(database, content_path).load()
