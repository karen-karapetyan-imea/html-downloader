"""Calendar-month scheduling for the auction crawl loop."""

from __future__ import annotations

import calendar
from datetime import datetime, timezone


def _clamp_day(year: int, month: int, day: int) -> int:
    """Clamp day to the last valid day of the target month."""
    last = calendar.monthrange(year, month)[1]
    return min(day, last)


def add_calendar_months(anchor: datetime, months: int = 1) -> datetime:
    """
    Advance `anchor` by `months` calendar months, preserving time-of-day.

    Day-of-month is clamped when the target month is shorter (e.g. Jan 31 → Feb 28/29).
    """
    if months < 0:
        raise ValueError("months must be >= 0")
    total = anchor.month - 1 + months
    year = anchor.year + total // 12
    month = total % 12 + 1
    day = _clamp_day(year, month, anchor.day)
    return anchor.replace(year=year, month=month, day=day)


def next_monthly_run(
    anchor: datetime,
    *,
    run_day: int | None = None,
) -> datetime:
    """
    Compute the next monthly cycle start from `anchor`.

    Default: same day-of-month next calendar month (cycle-start anchored).
    If `run_day` is set (1–31), the next run is the next occurrence of that
    day-of-month at the same time-of-day as `anchor` (clamped for short months).
    When `run_day` equals today's day, still advances to next month.
    """
    if run_day is not None:
        if not 1 <= run_day <= 31:
            raise ValueError(f"run_day must be 1–31, got {run_day}")
        # Next calendar month on the configured day
        provisional = add_calendar_months(anchor, 1)
        day = _clamp_day(provisional.year, provisional.month, run_day)
        return provisional.replace(day=day)

    return add_calendar_months(anchor, 1)


def seconds_until(target: datetime, *, now: datetime | None = None) -> float:
    """Seconds from `now` until `target` (0 if already past)."""
    current = now if now is not None else datetime.now(tz=target.tzinfo or timezone.utc)
    if current.tzinfo is None and target.tzinfo is not None:
        current = current.replace(tzinfo=target.tzinfo)
    elif target.tzinfo is None and current.tzinfo is not None:
        target = target.replace(tzinfo=current.tzinfo)
    delta = (target - current).total_seconds()
    return max(0.0, delta)


def parse_run_day(value: str | None) -> int | None:
    """Parse AUCTION_RUN_DAY env value; empty/None → None (use cycle-start day)."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    day = int(text)
    if not 1 <= day <= 31:
        raise ValueError(f"AUCTION_RUN_DAY must be 1–31, got {day}")
    return day
