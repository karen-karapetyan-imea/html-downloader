"""`sync`: snapshot every finished crawl folder that has no up-to-date snapshot, then compact.

Crawl folders come from the downloader: <data_root>/<platform>/<YYYY-MM-DD>/ with a manifest.json
whose status is running | completed | failed. All sync state lives in the output tree (snapshot
_manifest.json / _run.json), so running it again, or after a crash, is safe.
"""

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

from etl_core.compact import compact
from etl_core.crawl import parse_folder_date, parse_timestamp
from etl_core.dataset import DatasetSpec
from etl_core.layout import MANIFEST_NAME, RUN_NAME, snapshot_dir
from etl_core.pipeline import ResumeMismatchError, RunConfig, run_snapshot

log = logging.getLogger(__name__)

CRAWL_MANIFEST_NAME = "manifest.json"
# A failed download still leaves valid pages, and the next cycle writes to a new date folder.
FINISHED_STATUSES = frozenset({"completed", "failed"})


@dataclass(frozen=True, slots=True)
class PendingCrawl:
    platform: str
    data_dir: Path
    crawl_date: date
    resume: bool
    reason: str


@dataclass(frozen=True, slots=True)
class SyncConfig:
    data_root: Path
    out: Path
    platforms: tuple[str, ...] = ()  # empty: every platform of the dataset
    workers: int = 1
    chunk_size: int = 2000
    include_unmanaged: bool = False  # also crawl folders without a downloader manifest.json
    dry_run: bool = False


@dataclass(slots=True)
class SyncResult:
    pending: list[PendingCrawl]
    processed: list[PendingCrawl] = field(default_factory=list)
    failed: list[PendingCrawl] = field(default_factory=list)
    compacted: list[str] = field(default_factory=list)
    compact_failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed and not self.compact_failed


def sync(dataset: DatasetSpec[Any], config: SyncConfig) -> SyncResult:
    pending = find_pending(dataset, config.data_root, config.out, config.platforms, config.include_unmanaged)
    result = SyncResult(pending=pending)
    log.info("%s: %d crawl folders to process under %s", dataset.name, len(pending), config.data_root)
    for item in pending:
        log.info("pending %s %s: %s", item.platform, item.crawl_date, item.reason)
    if config.dry_run:
        return result

    for item in pending:
        try:
            _run(dataset, item, config)
        except Exception:  # one bad crawl folder must not block the others
            log.exception("%s %s %s: run failed", dataset.name, item.platform, item.crawl_date)
            result.failed.append(item)
        else:
            result.processed.append(item)

    for platform in sorted({item.platform for item in result.processed}):
        try:
            compact(dataset, platform, config.out)
        except Exception:
            log.exception("%s %s: compact failed", dataset.name, platform)
            result.compact_failed.append(platform)
        else:
            result.compacted.append(platform)

    log.info(
        "%s sync done: %d processed, %d failed, compacted %s, compact failed %s",
        dataset.name,
        len(result.processed),
        len(result.failed),
        result.compacted or "none",
        result.compact_failed or "none",
    )
    return result


def find_pending(
    dataset: DatasetSpec[Any],
    data_root: Path,
    out: Path,
    platforms: tuple[str, ...] = (),
    include_unmanaged: bool = False,
) -> list[PendingCrawl]:
    selected = platforms or dataset.platforms
    for platform in selected:
        dataset.mapper(platform)
    pending = []
    for platform in selected:
        for data_dir, crawl_date in _crawl_dirs(data_root / platform):
            try:
                item = _classify(platform, data_dir, crawl_date, out, include_unmanaged)
            except ValueError as exc:
                log.warning("skipping %s: %s", data_dir, exc)
                continue
            if item is not None:
                pending.append(item)
    return pending


def _crawl_dirs(platform_dir: Path) -> Iterator[tuple[Path, date]]:
    if not platform_dir.is_dir():
        return
    for path in sorted(platform_dir.iterdir()):
        crawl_date = parse_folder_date(path.name)
        if crawl_date is not None and path.is_dir():
            yield path, crawl_date


def _classify(
    platform: str, data_dir: Path, crawl_date: date, out: Path, include_unmanaged: bool
) -> PendingCrawl | None:
    crawl = _read_json(data_dir / CRAWL_MANIFEST_NAME)
    if crawl is None:
        if not include_unmanaged:
            log.debug("skipping %s: no %s", data_dir, CRAWL_MANIFEST_NAME)
            return None
        crawl_finished = None
    elif crawl.get("status") not in FINISHED_STATUSES:
        log.info("skipping %s: crawl status is %r", data_dir, crawl.get("status"))
        return None
    else:
        crawl_finished = parse_timestamp(crawl.get("finished_at"))

    def pending(resume: bool, reason: str) -> PendingCrawl:
        return PendingCrawl(platform, data_dir, crawl_date, resume, reason)

    snapshot = snapshot_dir(out, platform, crawl_date)
    etl = _read_json(snapshot / MANIFEST_NAME)
    if etl is not None:
        etl_started = parse_timestamp(etl.get("started_at"))
        if crawl_finished is None or (etl_started is not None and etl_started >= crawl_finished):
            return None
        return pending(False, "crawl finished after the last ETL run")
    if (snapshot / RUN_NAME).is_file():
        return pending(True, "interrupted ETL run")
    return pending(False, "new crawl")


def _run(dataset: DatasetSpec[Any], item: PendingCrawl, config: SyncConfig) -> None:
    run_config = RunConfig(
        dataset=dataset,
        platform=item.platform,
        data_dir=item.data_dir,
        out=config.out,
        crawl_date=item.crawl_date,
        workers=config.workers,
        chunk_size=config.chunk_size,
        resume=item.resume,
    )
    try:
        run_snapshot(run_config)
    except ResumeMismatchError as exc:
        log.warning("%s; starting the snapshot from scratch", exc)
        run_snapshot(replace(run_config, resume=False))


def _read_json(path: Path) -> dict[str, Any] | None:
    """None if the file is missing; ValueError if it is not a JSON object (e.g. caught mid-write)."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return data
