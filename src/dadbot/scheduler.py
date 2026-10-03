from __future__ import annotations

import inspect
import logging
import random
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.date import DateTrigger

from .config import Settings
from .db import Database

JobCallback = Callable[[], Awaitable[None] | None]

logger = logging.getLogger(__name__)


class EngagementScheduler:
    """Failure-isolated scheduling for engagement posts and reminder polling.

    Callbacks own Discord-specific posting. This class owns timing, durable run
    metadata, and the rule that a failed feature cannot stop other features.
    """

    RANDOM_JOBS: ClassVar[frozenset[str]] = frozenset({"dad_joke", "showcase"})
    WEEKLY_JOBS: ClassVar[frozenset[str]] = frozenset({"question", "poll"})

    def __init__(
        self,
        settings: Settings,
        database: Database,
        callbacks: Mapping[str, JobCallback] | None = None,
        *,
        rng: random.Random | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.callbacks = dict(callbacks or {})
        self.rng = rng or random.Random()
        self._now = now or (lambda: datetime.now(self.settings.timezone))
        self._scheduler = AsyncIOScheduler(timezone=settings.timezone)
        self._started = False

    @property
    def running(self) -> bool:
        return self._started and self._scheduler.running

    def set_callback(self, job_id: str, callback: JobCallback) -> None:
        self.callbacks[job_id] = callback

    async def start(self) -> None:
        if self._started:
            return
        self._scheduler.start()
        self._started = True
        await self._schedule_random(
            "dad_joke", self.settings.dad_joke_min_days, self.settings.dad_joke_max_days
        )
        await self._schedule_random(
            "showcase", self.settings.showcase_min_days, self.settings.showcase_max_days
        )
        await self._schedule_weekly("question", self.settings.question_weekday)
        await self._schedule_weekly("poll", self.settings.poll_weekday)
        self._scheduler.add_job(
            self._dispatch,
            "interval",
            id="event_reminders",
            args=("event_reminders", False),
            minutes=1,
            next_run_time=self._now(),
            coalesce=True,
            max_instances=1,
            misfire_grace_time=300,
            replace_existing=True,
        )
        await self._store_next_run("event_reminders")
        logger.info("Engagement scheduler started with %d jobs", len(self._scheduler.get_jobs()))

    async def shutdown(self, wait: bool = True) -> None:
        if not self._started:
            return
        self._scheduler.shutdown(wait=wait)
        self._started = False
        logger.info("Engagement scheduler stopped")

    async def run_now(self, job_id: str) -> bool:
        """Run a registered callback immediately without changing its cadence."""
        if job_id not in self.callbacks:
            return False
        await self._dispatch(job_id, False)
        return True

    def upcoming(self, limit: int = 10) -> list[dict[str, str | None]]:
        jobs = sorted(self._scheduler.get_jobs(), key=lambda job: job.next_run_time)
        return [
            {
                "id": job.id,
                "next_run_at": job.next_run_time.isoformat() if job.next_run_time else None,
            }
            for job in jobs[:limit]
        ]

    async def _dispatch(self, job_id: str, reschedule: bool = False) -> None:
        started = datetime.now(UTC).isoformat()
        await self.database.execute(
            """INSERT INTO job_runs(job_id, last_started_at, last_error)
               VALUES (?, ?, NULL) ON CONFLICT(job_id) DO UPDATE SET
               last_started_at=excluded.last_started_at, last_error=NULL""",
            (job_id, started),
        )
        callback = self.callbacks.get(job_id)
        try:
            if callback is None:
                logger.debug("No callback registered for scheduled job %s", job_id)
            else:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            finished = datetime.now(UTC).isoformat()
            await self.database.execute(
                "UPDATE job_runs SET last_success_at=?, last_error=NULL WHERE job_id=?",
                (finished, job_id),
            )
            logger.info("Scheduled job completed: %s", job_id)
        except Exception as exc:  # callbacks are deliberate feature boundaries
            logger.exception("Scheduled job failed: %s", job_id)
            await self.database.execute(
                "UPDATE job_runs SET last_error=? WHERE job_id=?", (str(exc)[:1000], job_id)
            )
        finally:
            if reschedule and self._started:
                if job_id == "dad_joke":
                    await self._schedule_random(
                        job_id, self.settings.dad_joke_min_days, self.settings.dad_joke_max_days
                    )
                elif job_id == "showcase":
                    await self._schedule_random(
                        job_id, self.settings.showcase_min_days, self.settings.showcase_max_days
                    )
                elif job_id == "question":
                    await self._schedule_weekly(job_id, self.settings.question_weekday)
                elif job_id == "poll":
                    await self._schedule_weekly(job_id, self.settings.poll_weekday)

    async def _schedule_random(self, job_id: str, minimum_days: int, maximum_days: int) -> None:
        minimum_days, maximum_days = sorted((max(1, minimum_days), max(1, maximum_days)))
        delay = timedelta(seconds=self.rng.uniform(minimum_days * 86400, maximum_days * 86400))
        candidate = self._now() + delay
        candidate = self._put_in_window(candidate)
        self._add_date_job(job_id, candidate)
        await self._store_next_run(job_id)

    async def _schedule_weekly(self, job_id: str, weekday: int) -> None:
        now = self._now()
        weekday %= 7
        days = (weekday - now.weekday()) % 7
        target_date = (now + timedelta(days=days)).date()
        hour = self.rng.randint(self.settings.schedule_hour_start, self.settings.schedule_hour_end)
        minute = self.rng.randint(0, 59)
        candidate = datetime.combine(
            target_date, datetime.min.time(), self.settings.timezone
        ).replace(hour=hour, minute=minute)
        # Same-day recovery is useful until the configured window closes.
        if candidate <= now:
            if days == 0 and now.hour <= self.settings.schedule_hour_end:
                candidate = now + timedelta(minutes=self.rng.randint(1, 15))
            else:
                candidate += timedelta(days=7)
        self._add_date_job(job_id, candidate)
        await self._store_next_run(job_id)

    def _put_in_window(self, value: datetime) -> datetime:
        start = self.settings.schedule_hour_start
        end = self.settings.schedule_hour_end
        if value.hour < start:
            return value.replace(
                hour=start, minute=self.rng.randint(0, 59), second=0, microsecond=0
            )
        if value.hour > end or (value.hour == end and (value.minute or value.second)):
            following = value + timedelta(days=1)
            return following.replace(
                hour=start, minute=self.rng.randint(0, 59), second=0, microsecond=0
            )
        return value

    def _add_date_job(self, job_id: str, when: datetime) -> None:
        self._scheduler.add_job(
            self._dispatch,
            trigger=DateTrigger(run_date=when, timezone=self.settings.timezone),
            id=job_id,
            args=(job_id, True),
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600 if job_id in self.WEEKLY_JOBS else 300,
            replace_existing=True,
        )

    async def _store_next_run(self, job_id: str) -> None:
        job = self._scheduler.get_job(job_id)
        next_run = job.next_run_time.isoformat() if job and job.next_run_time else None
        await self.database.execute(
            """INSERT INTO job_runs(job_id, next_run_at) VALUES (?, ?)
               ON CONFLICT(job_id) DO UPDATE SET next_run_at=excluded.next_run_at""",
            (job_id, next_run),
        )
