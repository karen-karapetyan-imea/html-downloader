"""One-shot import of legacy Parquet snapshots into MongoDB."""

from __future__ import annotations

import logging
from dataclasses import fields
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq

from etl_core.dataset import DatasetSpec
from etl_core.documents import crawl_date_str, dedup_rows
from etl_core.layout import MANIFEST_NAME, platform_snapshots_dir
from etl_core.schema import CRAWL_DATE_COLUMN
from etl_core.store import EntityStore, MongoConfig

log = logging.getLogger(__name__)


def migrate_parquet(
    dataset: DatasetSpec[Any],
    from_dir: Path,
    mongo: MongoConfig,
    platforms: tuple[str, ...] = (),
) -> dict[str, int]:
    """Read snapshots/platform_name=*/crawl_date=*/*.parquet and upsert versions + mains."""
    store = EntityStore(mongo)
    try:
        store.ensure_indexes()
        selected = platforms or _discover_platforms(from_dir)
        totals: dict[str, int] = {}
        for platform in selected:
            dataset.mapper(platform)
            rows = _migrate_platform(store, dataset, from_dir, platform)
            totals[platform] = rows
            log.info("%s: migrated %d %s rows from %s", platform, rows, dataset.name, from_dir)
        return totals
    finally:
        store.close()


def _discover_platforms(from_dir: Path) -> tuple[str, ...]:
    root = from_dir / "snapshots"
    if not root.is_dir():
        raise FileNotFoundError(f"no snapshots/ under {from_dir}")
    names = []
    for path in sorted(root.iterdir()):
        if path.is_dir() and path.name.startswith("platform_name="):
            names.append(path.name.split("=", 1)[1])
    if not names:
        raise FileNotFoundError(f"no platform_name=* partitions under {root}")
    return tuple(names)


def _migrate_platform(
    store: EntityStore, dataset: DatasetSpec[Any], from_dir: Path, platform: str
) -> int:
    platform_dir = platform_snapshots_dir(from_dir, platform)
    if not platform_dir.is_dir():
        log.warning("no snapshots for %s under %s", platform, from_dir)
        return 0
    total = 0
    for snap in sorted(platform_dir.glob(f"{CRAWL_DATE_COLUMN}=*")):
        if not snap.is_dir() or not (snap / MANIFEST_NAME).is_file():
            continue
        crawl_date = date.fromisoformat(snap.name.split("=", 1)[1])
        parts = sorted(snap.glob("part-*.parquet"))
        if not parts:
            continue
        rows = []
        for part in parts:
            rows.extend(_rows_from_parquet(dataset, part))
        winners = dedup_rows(rows, dataset.key_column, dataset.within_crawl_order)
        total += store.publish_rows(
            dataset.name,
            platform,
            crawl_date,
            winners,
            dataset.key_column,
            dataset.within_crawl_order,
        )
        log.info(
            "migrated %s %s: %d rows -> %d winners",
            platform,
            crawl_date_str(crawl_date),
            len(rows),
            len(winners),
        )
    return total


def _rows_from_parquet(dataset: DatasetSpec[Any], path: Path) -> list[Any]:
    table = pq.read_table(path)
    columns = {name: table.column(name).to_pylist() for name in table.column_names}
    n = table.num_rows
    row_type = dataset.row_type
    field_names = [f.name for f in fields(row_type)]
    rows = []
    for i in range(n):
        values: dict[str, Any] = {}
        for name in field_names:
            raw = columns[name][i] if name in columns else None
            values[name] = _coerce(raw, name, row_type)
        rows.append(row_type(**values))
    return rows


def _coerce(raw: object, name: str, row_type: type) -> object:
    if raw is None:
        return None
    # Parquet decimals often arrive as Decimal already; dates as date/datetime.
    if isinstance(raw, datetime) and name.endswith("_date") and not name.endswith("crawled_at"):
        return raw.date() if hasattr(raw, "date") else raw
    if isinstance(raw, float) and name in {"price", "low_estimate", "high_estimate", "price_realized"}:
        return Decimal(str(raw))
    return raw
