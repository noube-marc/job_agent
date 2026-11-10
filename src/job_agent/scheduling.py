"""Time window check and single-run lock.

The Windows task scheduler starts the agent regularly; the agent itself
checks that it is inside the configured window (days and hours, in the
configured time zone) and that no other run is in progress.
"""

import os
from contextlib import contextmanager
from datetime import datetime, timedelta
from collections.abc import Iterator
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from job_agent.config.models import ScheduleConfig

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
# A lock older than this is considered abandoned (crash, power cut).
STALE_LOCK_AFTER = timedelta(hours=3)


class AlreadyRunning(Exception):
    """Another run holds the lock."""


# Address Windows itself uses to detect a working internet connection.
CONNECTIVITY_URL = "http://www.msftconnecttest.com/connecttest.txt"


def internet_available(url: str = CONNECTIVITY_URL, timeout: float = 10) -> bool:
    """True if the internet can be reached (one small request)."""
    try:
        response = httpx.get(url, timeout=timeout, follow_redirects=False)
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def in_schedule(schedule: ScheduleConfig, now: datetime) -> bool:
    local = now.astimezone(ZoneInfo(schedule.timezone))
    if WEEKDAYS[local.weekday()] not in schedule.days:
        return False
    # The end hour is included: with 8 to 20, a run starting at 20:05 runs.
    return schedule.start_hour <= local.hour <= schedule.end_hour


@contextmanager
def run_lock(path: str | Path, now: datetime | None = None) -> Iterator[None]:
    """Exclusive lock file, removed at the end of the run."""
    lock = Path(path)
    lock.parent.mkdir(parents=True, exist_ok=True)
    current = now or datetime.now()
    if lock.exists():
        age = current - datetime.fromtimestamp(lock.stat().st_mtime)
        if age < STALE_LOCK_AFTER:
            raise AlreadyRunning(str(lock))
        lock.unlink(missing_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        raise AlreadyRunning(str(lock)) from None
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(f"pid={os.getpid()} started={current.isoformat()}\n")
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)
