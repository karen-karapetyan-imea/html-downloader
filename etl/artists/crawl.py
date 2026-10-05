"""Crawl folder access: HTML file discovery, the optional results.jsonl log, crawl date."""

import json
import logging
import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

log = logging.getLogger(__name__)

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
CRAWL_LOG_NAME = "results.jsonl"


@dataclass(frozen=True, slots=True)
class CrawlFile:
    path: str
    filename: str
    url: str | None = None
    crawled_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class LogEntry:
    url: str | None
    crawled_at: datetime | None


def html_root(data_dir: Path) -> Path:
    return data_dir / "html" if (data_dir / "html").is_dir() else data_dir


def iter_html_files(root: Path) -> Iterator[Path]:
    """Depth-first, sorted per directory: deterministic order without listing the whole tree up front."""
    with os.scandir(root) as it:
        entries = sorted(it, key=lambda e: e.name)
    for entry in entries:
        if entry.name.startswith(".") or entry.name == "__MACOSX":
            continue
        if entry.is_dir(follow_symlinks=False):
            yield from iter_html_files(Path(entry.path))
        elif entry.name.endswith(".html") and entry.is_file():
            yield Path(entry.path)


def parse_timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
    else:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def load_crawl_log(data_dir: Path) -> dict[str, LogEntry]:
    """filename -> url/timestamp from results.jsonl (last entry per file wins); empty if absent."""
    path = data_dir / CRAWL_LOG_NAME
    if not path.is_file():
        return {}
    entries: dict[str, LogEntry] = {}
    bad = 0
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if isinstance(record, dict) and record.get("filename"):
                entries[record["filename"]] = LogEntry(
                    url=record.get("url") or None, crawled_at=parse_timestamp(record.get("timestamp"))
                )
    if bad:
        log.warning("%s: skipped %d unreadable lines", path, bad)
    return entries


def iter_crawl_files(data_dir: Path, crawl_log: Mapping[str, LogEntry]) -> Iterator[CrawlFile]:
    for path in iter_html_files(html_root(data_dir)):
        entry = crawl_log.get(path.name)
        yield CrawlFile(
            path=str(path),
            filename=path.name,
            url=entry.url if entry else None,
            crawled_at=entry.crawled_at if entry else None,
        )


def detect_crawl_date(data_dir: Path, explicit: str | None = None) -> date:
    """--crawl-date wins; otherwise the nearest folder named YYYY-MM-DD (the crawl folder or a parent)."""
    if explicit:
        return date.fromisoformat(explicit)
    for part in (data_dir.resolve(), *data_dir.resolve().parents):
        if _DATE_RE.fullmatch(part.name):
            return date.fromisoformat(part.name)
    raise ValueError(f"cannot infer the crawl date from {data_dir}; pass --crawl-date YYYY-MM-DD")


def file_mtime(path: str) -> datetime:
    return datetime.fromtimestamp(os.stat(path).st_mtime, UTC)
