"""Private sales: entities present in one full crawl and gone from the next full crawl.

Only full crawls can be compared (the downloader's manifest.json says `"crawl_mode": "full"`):
an incremental crawl only holds new or changed pages, so everything else would look gone.

A record is written to `private_sales` per vanished entity, with the data of its last version:

    _id               "<source>:<platform_id>:<missing_in_crawl>"
    entity_id, source, platform_id, entity_type
    last_seen_crawl   crawl date of the previous full crawl, where it was last seen
    missing_in_crawl  crawl date of the full crawl it is missing from
    last_seen_at      when it was last crawled
    data              its row in last_seen_crawl (title, artist, price, url, ...)

If the second crawl is much smaller than the first, it is more likely broken than full of sales,
so nothing is written (status "guard_tripped").
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from itertools import pairwise

from pymongo import ASCENDING, ReplaceOne

from etl_core import __version__
from etl_core.documents import crawl_date_str, crawl_ledger_id, entity_id, entity_type_for, seen_at
from etl_core.store import BULK_SIZE, STATUS_COMPLETED, EntityStore

log = logging.getLogger(__name__)

MIN_RATIO = 0.8
CRAWL_MODE_FULL = "full"
STATUS_OK = "ok"
STATUS_GUARD_TRIPPED = "guard_tripped"


@dataclass(frozen=True, slots=True)
class PrivateSalesResult:
    platform: str
    previous: str
    current: str
    previous_count: int
    current_count: int
    missing: int
    written: int
    status: str
    dry_run: bool = False


def detect_private_sales(
    store: EntityStore,
    dataset: str,
    platform: str,
    previous: date | str,
    current: date | str,
    *,
    min_ratio: float = MIN_RATIO,
    dry_run: bool = False,
) -> PrivateSalesResult:
    """Record entities of the `previous` crawl that are missing from the `current` crawl."""
    store.ensure_indexes()
    prev_s, curr_s = crawl_date_str(previous), crawl_date_str(current)
    if prev_s >= curr_s:
        raise ValueError(f"previous crawl {prev_s} must be before current crawl {curr_s}")
    for date_s in (prev_s, curr_s):
        ledger = store.get_crawl(crawl_ledger_id(dataset, platform, date_s))
        if ledger is None or ledger.get("status") != STATUS_COMPLETED:
            raise ValueError(f"{dataset} {platform} {date_s}: no completed ETL run for this crawl")

    previous_ids = _platform_ids(store, dataset, platform, prev_s)
    current_ids = _platform_ids(store, dataset, platform, curr_s)
    missing = sorted(previous_ids - current_ids)
    result = PrivateSalesResult(
        platform=platform,
        previous=prev_s,
        current=curr_s,
        previous_count=len(previous_ids),
        current_count=len(current_ids),
        missing=len(missing),
        written=0,
        status=STATUS_OK,
        dry_run=dry_run,
    )

    if len(current_ids) < min_ratio * len(previous_ids):
        log.warning(
            "%s %s: %s has %d %s, under %.0f%% of the %d in %s; not recording private sales",
            dataset,
            platform,
            curr_s,
            len(current_ids),
            dataset,
            min_ratio * 100,
            len(previous_ids),
            prev_s,
        )
        result = replace(result, status=STATUS_GUARD_TRIPPED)
    elif not dry_run:
        result = replace(result, written=_write(store, dataset, platform, prev_s, curr_s, missing))

    log.info(
        "%s %s: %d of %d %s from %s are gone in %s (%d there), %s%s",
        dataset,
        platform,
        result.missing,
        result.previous_count,
        dataset,
        prev_s,
        curr_s,
        result.current_count,
        result.status,
        " (dry run)" if dry_run else "",
    )
    if not dry_run:
        store.crawls().update_one(
            {"_id": crawl_ledger_id(dataset, platform, curr_s)},
            {
                "$set": {
                    "private_sales": {
                        "previous_crawl": prev_s,
                        "status": result.status,
                        "previous_count": result.previous_count,
                        "current_count": result.current_count,
                        "missing": result.missing,
                        "written": result.written,
                        "min_ratio": min_ratio,
                        "checked_at": datetime.now(UTC),
                    }
                }
            },
        )
    return result


def detect_pending_private_sales(
    store: EntityStore, dataset: str, platform: str, *, min_ratio: float = MIN_RATIO
) -> list[PrivateSalesResult]:
    """Diff each full crawl against the full crawl before it, unless that was already done."""
    full = list(
        store.crawls()
        .find(
            {
                "dataset": dataset,
                "source": platform,
                "status": STATUS_COMPLETED,
                "crawl_mode": CRAWL_MODE_FULL,
                "crawl_status": "completed",
            },
            projection={"crawl_date": 1, "private_sales": 1},
        )
        .sort("crawl_date", ASCENDING)
    )
    results = []
    for prev, curr in pairwise(full):
        done = curr.get("private_sales") or {}
        if done.get("previous_crawl") == prev["crawl_date"]:
            continue
        results.append(
            detect_private_sales(
                store, dataset, platform, prev["crawl_date"], curr["crawl_date"], min_ratio=min_ratio
            )
        )
    return results


def _platform_ids(store: EntityStore, dataset: str, platform: str, crawl_date: str) -> set[str]:
    # A cursor rather than distinct(): distinct's result must fit in one 16MB document.
    cursor = store.versions(dataset).find(
        {"source": platform, "crawl_date": crawl_date}, projection={"_id": 0, "platform_id": 1}
    )
    return {doc["platform_id"] for doc in cursor}


def _write(
    store: EntityStore,
    dataset: str,
    platform: str,
    previous: str,
    current: str,
    missing: list[str],
) -> int:
    write_token = uuid.uuid4().hex
    now = datetime.now(UTC)
    entity_type = entity_type_for(dataset)
    collection = store.private_sales()
    written = 0
    for batch in _chunks(missing, BULK_SIZE):
        versions = store.versions(dataset).find(
            {"entity_id": {"$in": [entity_id(platform, pid) for pid in batch]}, "crawl_date": previous}
        )
        ops = []
        for version in versions:
            data = version["data"]
            crawled = data.get("crawled_at")
            doc_id = f"{version['entity_id']}:{current}"
            ops.append(
                ReplaceOne(
                    {"_id": doc_id},
                    {
                        "_id": doc_id,
                        "entity_id": version["entity_id"],
                        "source": platform,
                        "platform_id": version["platform_id"],
                        "entity_type": entity_type,
                        "last_seen_crawl": previous,
                        "missing_in_crawl": current,
                        "last_seen_at": seen_at(crawled if isinstance(crawled, datetime) else None, previous),
                        "data": data,
                        "detected_at": now,
                        "etl_version": __version__,
                        "write_token": write_token,
                    },
                    upsert=True,
                )
            )
        if ops:
            collection.bulk_write(ops, ordered=False)
            written += len(ops)
    # Re-runs replace the earlier result for this crawl, e.g. after the crawl was reprocessed.
    collection.delete_many(
        {
            "source": platform,
            "entity_type": entity_type,
            "missing_in_crawl": current,
            "write_token": {"$ne": write_token},
        }
    )
    return written


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
