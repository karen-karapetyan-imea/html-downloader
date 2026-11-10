"""`sync`: snapshot every finished crawl folder that has no up-to-date Mongo crawl, then compact.

Crawl folders come from the downloader: <data_root>/<platform>/<YYYY-MM-DD>/ with a manifest.json
whose status is running | completed | failed. Crawl state lives in etl_crawls.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

from etl_core.compact import compact
from etl_core.crawl import (
    CRAWL_MANIFEST_NAME,
    parse_folder_date,
    parse_timestamp,
    read_crawl_manifest,
)
from etl_core.dataset import DatasetSpec
from etl_core.documents import crawl_date_str, crawl_ledger_id
from etl_core.html_retention import datasets_for_platform, prune_html_folders
from etl_core.pipeline import ResumeMismatchError, RunConfig, run_snapshot
from etl_core.private_sales import detect_pending_private_sales
from etl_core.store import STATUS_COMPLETED, STATUS_RUNNING, EntityStore, MongoConfig

log = logging.getLogger(__name__)

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
    mongo: MongoConfig
    platforms: tuple[str, ...] = ()  # empty: every platform of the dataset
    workers: int = 1
    chunk_size: int = 2000
    include_unmanaged: bool = False
    dry_run: bool = False


@dataclass(slots=True)
class SyncResult:
    pending: list[PendingCrawl]
    processed: list[PendingCrawl] = field(default_factory=list)
    failed: list[PendingCrawl] = field(default_factory=list)
    compacted: list[str] = field(default_factory=list)
    compact_failed: list[str] = field(default_factory=list)
    private_sales_failed: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed and not self.compact_failed and not self.private_sales_failed


def sync(dataset: DatasetSpec[Any], config: SyncConfig) -> SyncResult:
    pending = find_pending(
        dataset, config.data_root, config.mongo, config.platforms, config.include_unmanaged
    )
    result = SyncResult(pending=pending)
    log.info("%s: %d crawl folders to process under %s", dataset.name, len(pending), config.data_root)
    for item in pending:
        log.info("pending %s %s: %s", item.platform, item.crawl_date, item.reason)
    if config.dry_run:
        return result

    for item in pending:
        try:
            _run(dataset, item, config)
        except Exception:
            log.exception("%s %s %s: run failed", dataset.name, item.platform, item.crawl_date)
            result.failed.append(item)
        else:
            result.processed.append(item)

    # Finalize already published mains; compact is a repair pass for successfully processed sources.
    for platform in sorted({item.platform for item in result.processed}):
        try:
            compact(dataset, platform, config.mongo)
        except Exception:
            log.exception("%s %s: compact failed", dataset.name, platform)
            result.compact_failed.append(platform)
        else:
            result.compacted.append(platform)

    if dataset.private_sales_dataset:
        _detect_private_sales(dataset, result, config.mongo)

    # Drop HTML folders older than the two newest fully uploaded crawls for each
    # platform this sync finished. Incomplete uploads are left alone.
    for platform in sorted({item.platform for item in result.processed}):
        if platform in result.compact_failed:
            continue
        try:
            retention = prune_html_folders(
                config.data_root,
                platform,
                config.mongo,
                datasets=datasets_for_platform(platform, dataset.name),
            )
        except Exception:
            log.exception("%s %s: HTML retention failed", dataset.name, platform)
            continue
        if retention.deleted:
            log.info(
                "%s %s: deleted %d old HTML folder(s): %s",
                dataset.name,
                platform,
                len(retention.deleted),
                ", ".join(p.name for p in retention.deleted),
            )

    log.info(
        "%s sync done: %d processed, %d failed, compacted %s, compact failed %s%s",
        dataset.name,
        len(result.processed),
        len(result.failed),
        result.compacted or "none",
        result.compact_failed or "none",
        f", private sales failed {result.private_sales_failed}" if result.private_sales_failed else "",
    )
    return result


def _detect_private_sales(dataset: DatasetSpec[Any], result: SyncResult, mongo: MongoConfig) -> None:
    """Diff the full crawls of every processed platform that has not been diffed yet."""
    store = EntityStore(mongo)
    try:
        for platform in sorted({item.platform for item in result.processed}):
            try:
                detect_pending_private_sales(store, dataset.private_sales_dataset, platform)
            except Exception:
                log.exception("%s %s: private sales detection failed", dataset.name, platform)
                result.private_sales_failed.append(platform)
    finally:
        store.close()


def find_pending(
    dataset: DatasetSpec[Any],
    data_root: Path,
    mongo: MongoConfig,
    platforms: tuple[str, ...] = (),
    include_unmanaged: bool = False,
) -> list[PendingCrawl]:
    selected = platforms or dataset.platforms
    for platform in selected:
        dataset.mapper(platform)
    store = EntityStore(mongo)
    try:
        pending = []
        for platform in selected:
            for data_dir, crawl_date in _crawl_dirs(data_root / platform):
                try:
                    item = _classify(
                        store, dataset.name, platform, data_dir, crawl_date, include_unmanaged
                    )
                except ValueError as exc:
                    log.warning("skipping %s: %s", data_dir, exc)
                    continue
                if item is not None:
                    pending.append(item)
        return pending
    finally:
        store.close()


def _crawl_dirs(platform_dir: Path) -> Iterator[tuple[Path, date]]:
    if not platform_dir.is_dir():
        return
    for path in sorted(platform_dir.iterdir()):
        crawl_date = parse_folder_date(path.name)
        if crawl_date is not None and path.is_dir():
            yield path, crawl_date


def _classify(
    store: EntityStore,
    dataset: str,
    platform: str,
    data_dir: Path,
    crawl_date: date,
    include_unmanaged: bool,
) -> PendingCrawl | None:
    crawl = read_crawl_manifest(data_dir)
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

    crawl_id = crawl_ledger_id(dataset, platform, crawl_date_str(crawl_date))
    etl = store.get_crawl(crawl_id)
    if etl is not None and etl.get("status") == STATUS_COMPLETED:
        etl_started = parse_timestamp(etl.get("started_at"))
        if crawl_finished is None or (etl_started is not None and etl_started >= crawl_finished):
            return None
        return pending(False, "crawl finished after the last ETL run")
    if etl is not None and etl.get("status") == STATUS_RUNNING:
        return pending(True, "interrupted ETL run")
    if etl is not None and etl.get("completed_chunks"):
        return pending(True, "interrupted ETL run")
    return pending(False, "new crawl")


def _run(dataset: DatasetSpec[Any], item: PendingCrawl, config: SyncConfig) -> None:
    run_config = RunConfig(
        dataset=dataset,
        platform=item.platform,
        data_dir=item.data_dir,
        crawl_date=item.crawl_date,
        mongo=config.mongo,
        workers=config.workers,
        chunk_size=config.chunk_size,
        resume=item.resume,
    )
    try:
        run_snapshot(run_config)
    except ResumeMismatchError as exc:
        log.warning("%s; starting the crawl from scratch", exc)
        run_snapshot(replace(run_config, resume=False))
