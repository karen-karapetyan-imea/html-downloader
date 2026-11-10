"""Keep only the N newest fully uploaded crawl HTML folders for a platform.

Mongo version documents and the crawl ledger stay. Only dated folders under the
data root are removed, and only when every dataset that reads that folder has a
completed etl_crawls row.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from etl_core.crawl import parse_folder_date
from etl_core.documents import crawl_date_str, crawl_ledger_id
from etl_core.store import STATUS_COMPLETED, EntityStore, MongoConfig

log = logging.getLogger(__name__)

DEFAULT_KEEP = 2

# Datasets that must be completed before a crawl folder counts as uploaded.
MARKETPLACE_DATASETS: tuple[str, ...] = ("artworks", "artists")
AUCTION_DATASETS: tuple[str, ...] = ("lots",)


@dataclass(frozen=True, slots=True)
class RetentionResult:
    kept: list[Path]
    deleted: list[Path]
    skipped_incomplete: list[Path]


def datasets_for_platform(platform: str, dataset_name: str) -> tuple[str, ...]:
    """Which ledger datasets must be completed for this platform's HTML folder."""
    if dataset_name == "lots" or platform in _auction_platforms():
        return AUCTION_DATASETS
    return MARKETPLACE_DATASETS


def prune_html_folders(
    data_root: Path,
    platform: str,
    mongo: MongoConfig,
    *,
    datasets: Sequence[str],
    keep: int = DEFAULT_KEEP,
) -> RetentionResult:
    """Delete fully uploaded folders older than the ``keep`` newest for ``platform``.

    Folders without a completed ledger row for every required dataset are left alone.
    Paths outside ``data_root`` are never deleted.
    """
    if keep < 1:
        raise ValueError(f"keep must be >= 1, got {keep}")

    root = data_root.resolve()
    platform_dir = root / platform
    folders = _dated_folders(platform_dir)
    if not folders:
        return RetentionResult(kept=[], deleted=[], skipped_incomplete=[])

    store = EntityStore(mongo)
    try:
        uploaded: list[tuple[date, Path]] = []
        incomplete: list[Path] = []
        for crawl_date, path in folders:
            if _fully_uploaded(store, datasets, platform, crawl_date):
                uploaded.append((crawl_date, path))
            else:
                incomplete.append(path)

        # Newest first by crawl date, then folder name for stable ties.
        uploaded.sort(key=lambda item: (item[0], item[1].name), reverse=True)
        keep_set = {path for _, path in uploaded[:keep]}
        to_delete = [path for _, path in uploaded[keep:]]

        deleted: list[Path] = []
        for path in to_delete:
            if not _is_under(root, path):
                log.warning("refusing to delete %s: outside data root %s", path, root)
                continue
            log.info("deleting HTML crawl folder %s (keeping %d newest uploaded)", path, keep)
            shutil.rmtree(path)
            deleted.append(path)

        return RetentionResult(
            kept=sorted(keep_set, key=lambda p: p.name, reverse=True),
            deleted=deleted,
            skipped_incomplete=incomplete,
        )
    finally:
        store.close()


def _dated_folders(platform_dir: Path) -> list[tuple[date, Path]]:
    if not platform_dir.is_dir():
        return []
    out: list[tuple[date, Path]] = []
    for path in platform_dir.iterdir():
        if not path.is_dir():
            continue
        crawl_date = parse_folder_date(path.name)
        if crawl_date is not None:
            out.append((crawl_date, path.resolve()))
    return out


def _fully_uploaded(
    store: EntityStore, datasets: Sequence[str], platform: str, crawl_date: date
) -> bool:
    date_s = crawl_date_str(crawl_date)
    for dataset in datasets:
        crawl_id = crawl_ledger_id(dataset, platform, date_s)
        row = store.get_crawl(crawl_id)
        if row is None or row.get("status") != STATUS_COMPLETED:
            return False
    return True


def _is_under(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return False
    return True


def _auction_platforms() -> frozenset[str]:
    from etl_core.sources import AUCTION_PLATFORMS

    return frozenset(AUCTION_PLATFORMS)
