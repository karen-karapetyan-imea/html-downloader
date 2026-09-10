from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from html_downloader.auctions.paths import (
    auction_job_dir,
    auction_lastmod_state_file,
    job_month,
    parse_job_month,
)
from html_downloader.auctions.schedule import (
    add_calendar_months,
    next_monthly_run,
    parse_run_day,
)
from html_downloader.paths import lastmod_state_file


def test_job_month_format() -> None:
    assert job_month(date(2026, 9, 8)) == "2026-09"
    assert job_month(date(2026, 10, 1)) == "2026-10"
    assert job_month(date(2026, 11, 30)) == "2026-11"


def test_parse_job_month() -> None:
    assert parse_job_month("2026-09") == "2026-09"


def test_parse_job_month_invalid() -> None:
    try:
        parse_job_month("2026-9")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_auction_job_dir_uses_yyyy_mm() -> None:
    root = Path("/tmp/data")
    assert auction_job_dir(root, "invaluable", "2026-09") == root / "auctions" / "invaluable" / "2026-09"


def test_state_isolation_from_marketplace() -> None:
    state_root = Path("/tmp/state")
    auction_path = auction_lastmod_state_file(state_root, "invaluable")
    market_path = lastmod_state_file(state_root, "invaluable")
    assert auction_path == state_root / "auctions" / "invaluable.json"
    assert market_path == state_root / "invaluable_lastmod.json"
    assert auction_path != market_path


def test_add_calendar_months_transitions() -> None:
    jan = datetime(2026, 1, 8, 12, 0, 0)
    assert add_calendar_months(jan, 1) == datetime(2026, 2, 8, 12, 0, 0)
    feb = datetime(2026, 2, 8, 12, 0, 0)
    assert add_calendar_months(feb, 1) == datetime(2026, 3, 8, 12, 0, 0)
    dec = datetime(2026, 12, 8, 12, 0, 0)
    assert add_calendar_months(dec, 1) == datetime(2027, 1, 8, 12, 0, 0)


def test_add_calendar_months_clamps_short_month() -> None:
    jan31 = datetime(2026, 1, 31, 10, 0, 0)
    assert add_calendar_months(jan31, 1) == datetime(2026, 2, 28, 10, 0, 0)


def test_next_monthly_run_default() -> None:
    anchor = datetime(2026, 9, 8, 15, 30, 0)
    assert next_monthly_run(anchor) == datetime(2026, 10, 8, 15, 30, 0)


def test_next_monthly_run_with_run_day() -> None:
    anchor = datetime(2026, 9, 8, 15, 30, 0)
    assert next_monthly_run(anchor, run_day=1) == datetime(2026, 10, 1, 15, 30, 0)


def test_parse_run_day() -> None:
    assert parse_run_day(None) is None
    assert parse_run_day("") is None
    assert parse_run_day("1") == 1
    assert parse_run_day("28") == 28
