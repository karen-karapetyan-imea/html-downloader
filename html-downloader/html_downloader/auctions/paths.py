"""Auction job folder contract: data/auctions/{house}/{YYYY-MM}/."""

from __future__ import annotations

import re
from datetime import date, datetime, timezone
from pathlib import Path

from html_downloader.paths import DEFAULT_DATA_ROOT, DEFAULT_STATE_ROOT, PROJECT_ROOT

AUCTION_HOUSES: tuple[str, ...] = ("invaluable", "liveauctioneers", "artcurial")

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def job_month(value: date | None = None) -> str:
    """Return YYYY-MM for the given UTC date (default: today)."""
    day = value if value is not None else utc_today()
    return f"{day.year:04d}-{day.month:02d}"


def parse_job_month(value: str | None) -> str:
    """Parse YYYY-MM or default to current UTC month."""
    if value is None:
        return job_month()
    text = value.strip()
    if not _MONTH_RE.match(text):
        raise ValueError(f"invalid job month {value!r}; expected YYYY-MM")
    return text


def auction_data_root(data_root: Path | None = None) -> Path:
    root = data_root if data_root is not None else DEFAULT_DATA_ROOT
    return root / "auctions"


def auction_state_root(state_root: Path | None = None) -> Path:
    root = state_root if state_root is not None else DEFAULT_STATE_ROOT
    return root / "auctions"


def auction_job_dir(data_root: Path, auction_house: str, month: str) -> Path:
    return auction_data_root(data_root) / auction_house / month


def auction_html_dir(job: Path) -> Path:
    return job / "html"


def auction_urls_file(job: Path) -> Path:
    return job / "urls.txt"


def auction_sitemap_all_file(job: Path) -> Path:
    return job / "sitemap_all.txt"


def auction_results_file(job: Path) -> Path:
    return job / "results.jsonl"


def auction_diff_file(job: Path) -> Path:
    return job / "diff.json"


def auction_manifest_file(job: Path) -> Path:
    return job / "manifest.json"


def auction_metadata_file(job: Path) -> Path:
    return job / "metadata.json"


def auction_lastmod_state_file(state_root: Path, auction_house: str) -> Path:
    """state/auctions/{house}.json — isolated from marketplace lastmod files."""
    return auction_state_root(state_root) / f"{auction_house}.json"


def auction_artist_sold_progress_file(state_root: Path, auction_house: str) -> Path:
    """Resume checkpoint for artist sold-at-auction expansion crawls."""
    return auction_state_root(state_root) / f"{auction_house}_artist_sold_progress.json"


def auction_house_expand_progress_file(state_root: Path, auction_house: str) -> Path:
    """Resume checkpoint for auction-house HTML expansion crawls."""
    return auction_state_root(state_root) / f"{auction_house}_house_expand_progress.json"


def auction_algolia_browse_state_file(state_root: Path, auction_house: str) -> Path:
    """Year-partition cursor state for Algolia archive browse."""
    return auction_state_root(state_root) / f"{auction_house}_algolia_browse_state.json"


def auction_sitemap_progress_file(state_root: Path, auction_house: str) -> Path:
    """Child-sitemap resume checkpoint for houses with large gzipped indexes."""
    return auction_state_root(state_root) / f"{auction_house}_sitemap_progress.json"


def auction_sales_progress_file(state_root: Path, auction_house: str) -> Path:
    """Sale-ref resume checkpoint for API-driven auction houses (Artcurial)."""
    return auction_state_root(state_root) / f"{auction_house}_sales_progress.json"


def known_auction_result_paths(data_root: Path, auction_house: str) -> list[Path]:
    """Prior auction crawl logs for incremental discovery."""
    house_dir = auction_data_root(data_root) / auction_house
    if not house_dir.is_dir():
        return []
    return sorted(path for path in house_dir.glob("*/results.jsonl") if path.is_file())


def ensure_auction_job_dirs(job: Path) -> Path:
    auction_html_dir(job).mkdir(parents=True, exist_ok=True)
    return job


__all__ = [
    "AUCTION_HOUSES",
    "PROJECT_ROOT",
    "auction_algolia_browse_state_file",
    "auction_artist_sold_progress_file",
    "auction_data_root",
    "auction_diff_file",
    "auction_house_expand_progress_file",
    "auction_html_dir",
    "auction_job_dir",
    "auction_lastmod_state_file",
    "auction_manifest_file",
    "auction_metadata_file",
    "auction_results_file",
    "auction_sales_progress_file",
    "auction_sitemap_all_file",
    "auction_sitemap_progress_file",
    "auction_state_root",
    "auction_urls_file",
    "ensure_auction_job_dirs",
    "job_month",
    "known_auction_result_paths",
    "parse_job_month",
    "utc_today",
]
