"""Tests for the time window and the run lock."""

import os
from datetime import UTC, datetime, timedelta, timezone

import pytest

from job_agent.scheduling import AlreadyRunning, in_schedule, run_lock

PARIS_WINTER = timezone(timedelta(hours=1))


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 11, 10, 8, 0, tzinfo=PARIS_WINTER), True),  # Tuesday 08:00
        (datetime(2026, 11, 10, 20, 5, tzinfo=PARIS_WINTER), True),  # Tuesday 20:05
        (datetime(2026, 11, 10, 21, 0, tzinfo=PARIS_WINTER), False),  # Tuesday 21:00
        (datetime(2026, 11, 10, 7, 59, tzinfo=PARIS_WINTER), False),  # Tuesday 07:59
        (datetime(2026, 11, 14, 10, 0, tzinfo=PARIS_WINTER), True),  # Saturday
        (datetime(2026, 11, 15, 10, 0, tzinfo=PARIS_WINTER), False),  # Sunday
        (datetime(2026, 11, 10, 7, 30, tzinfo=UTC), True),  # 08:30 in Paris
    ],
)
def test_in_schedule(config, moment: datetime, expected: bool) -> None:
    assert in_schedule(config.schedule, moment) is expected


def test_lock_prevents_two_runs(tmp_path) -> None:
    lock = tmp_path / "agent.lock"
    with run_lock(lock), pytest.raises(AlreadyRunning), run_lock(lock):
        pass
    assert not lock.exists()


def test_stale_lock_is_replaced(tmp_path) -> None:
    lock = tmp_path / "agent.lock"
    lock.write_text("old run")
    old = (datetime.now() - timedelta(hours=5)).timestamp()
    os.utime(lock, (old, old))
    with run_lock(lock):
        assert "pid=" in lock.read_text()


def test_internet_available(monkeypatch) -> None:
    import httpx

    from job_agent import scheduling

    monkeypatch.setattr(scheduling.httpx, "get", lambda url, **kw: httpx.Response(200, text="Microsoft Connect Test"))
    assert scheduling.internet_available()

    def offline(url, **kw):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(scheduling.httpx, "get", offline)
    assert not scheduling.internet_available()

    # A Wi-Fi login page answers with a redirect: not a working internet.
    monkeypatch.setattr(scheduling.httpx, "get", lambda url, **kw: httpx.Response(302))
    assert not scheduling.internet_available()
