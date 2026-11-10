"""Private sales: what changed between two full crawls that looks like a sale.

Comparing a previous and a current full crawl of one platform, an entity is recorded when it
- is in the previous crawl and gone from the current one (reason "disappeared"), or
- is in both, and goes from is_sold false to is_sold true (reason "marked_sold"). An empty is_sold
  in the previous crawl (the page did not say) does not count: it may have been sold already.

Disappearances are skipped when the artist is gone as a whole, since that means the artist took
their work off the platform rather than sold it. An artist is gone when
- none of their artworks are left in the current crawl (counted by platform_artist_id), or
- their profile is in the previous crawl's artists and not in the current crawl's. This needs the
  artists ETL of both crawls; until it has run, automatic detection waits for it.

Only full crawls can be compared (the downloader's manifest.json says `"crawl_mode": "full"`):
an incremental crawl only holds new or changed pages, so everything else would look gone.

One document per entity in `private_sales`:

    _id                "<source>:<platform_id>:<detected_in_crawl>"
    entity_id, source, platform_id, entity_type
    reason             "disappeared" | "marked_sold"
    previous_crawl     crawl date where it was last seen listed (and not sold)
    detected_in_crawl  crawl date where it was gone, or shown as sold
    last_seen_at       when it was crawled in previous_crawl
    data               its row in previous_crawl (title, artist, asking price, url, ...)
    sold_data          marked_sold only: its row in detected_in_crawl

If the current crawl is much smaller than the previous one, it is more likely broken than full of
sales, so disappearances are not recorded (status "guard_tripped"). Sold flips still are: they are
read from pages that were downloaded, so a broken crawl does not invent them.
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
REASON_DISAPPEARED = "disappeared"
REASON_MARKED_SOLD = "marked_sold"
ARTISTS = "artists"


@dataclass(frozen=True, slots=True)
class PrivateSalesResult:
    platform: str
    previous: str
    current: str
    previous_count: int
    current_count: int
    missing: int
    artist_removed: int  # of `missing`: skipped because their artist is gone
    removed_artists: int
    profiles_checked: bool
    marked_sold: int
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
    """Record entities of `previous` that are gone from `current`, or newly marked sold in it."""
    store.ensure_indexes()
    prev_s, curr_s = crawl_date_str(previous), crawl_date_str(current)
    if prev_s >= curr_s:
        raise ValueError(f"previous crawl {prev_s} must be before current crawl {curr_s}")
    for date_s in (prev_s, curr_s):
        ledger = store.get_crawl(crawl_ledger_id(dataset, platform, date_s))
        if ledger is None or ledger.get("status") != STATUS_COMPLETED:
            raise ValueError(f"{dataset} {platform} {date_s}: no completed ETL run for this crawl")

    before = _entries(store, dataset, platform, prev_s)
    after = _entries(store, dataset, platform, curr_s)
    missing = [pid for pid in before if pid not in after]
    marked_sold = [
        pid
        for pid, entry in after.items()
        if entry.is_sold is True and pid in before and before[pid].is_sold is False
    ]

    removed_artists = {e.artist_id for e in before.values() if e.artist_id} - {
        e.artist_id for e in after.values() if e.artist_id
    }
    profiles_checked = dataset != ARTISTS and _artists_ready(store, platform, prev_s, curr_s)
    if profiles_checked:
        removed_artists |= _artist_ids(store, platform, prev_s) - _artist_ids(store, platform, curr_s)
    else:
        log.warning(
            "%s %s: no completed artists ETL for %s and %s; only artists with no artworks left count as gone",
            dataset,
            platform,
            prev_s,
            curr_s,
        )
    disappeared = [pid for pid in missing if before[pid].artist_id not in removed_artists]

    guard_tripped = len(after) < min_ratio * len(before)
    result = PrivateSalesResult(
        platform=platform,
        previous=prev_s,
        current=curr_s,
        previous_count=len(before),
        current_count=len(after),
        missing=len(missing),
        artist_removed=len(missing) - len(disappeared),
        removed_artists=len(removed_artists),
        profiles_checked=profiles_checked,
        marked_sold=len(marked_sold),
        written=0,
        status=STATUS_GUARD_TRIPPED if guard_tripped else STATUS_OK,
        dry_run=dry_run,
    )

    if guard_tripped:
        log.warning(
            "%s %s: %s has %d %s, under %.0f%% of the %d in %s; not recording disappearances",
            dataset,
            platform,
            curr_s,
            len(after),
            dataset,
            min_ratio * 100,
            len(before),
            prev_s,
        )
    if not dry_run:
        reasons = {pid: REASON_MARKED_SOLD for pid in marked_sold}
        if not guard_tripped:
            reasons.update((pid, REASON_DISAPPEARED) for pid in disappeared)
        result = replace(result, written=_write(store, dataset, platform, prev_s, curr_s, reasons))

    log.info(
        "%s %s: of %d %s in %s, %d are gone (%d of them with their artist, %d artists gone) "
        "and %d newly sold in %s (%d there), %s%s",
        dataset,
        platform,
        result.previous_count,
        dataset,
        prev_s,
        result.missing,
        result.artist_removed,
        result.removed_artists,
        result.marked_sold,
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
                        "artist_removed": result.artist_removed,
                        "removed_artists": result.removed_artists,
                        "profiles_checked": result.profiles_checked,
                        "marked_sold": result.marked_sold,
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
    """Diff each full crawl against the full crawl before it, unless that was already done.

    A pair waits while the platform has artists but their ETL has not finished for both crawls.
    """
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
        if (
            dataset != ARTISTS
            and _has_artists(store, platform)
            and not _artists_ready(store, platform, prev["crawl_date"], curr["crawl_date"])
        ):
            log.info(
                "%s %s: %s vs %s waits for the artists ETL of both crawls",
                dataset,
                platform,
                prev["crawl_date"],
                curr["crawl_date"],
            )
            continue
        results.append(
            detect_private_sales(
                store, dataset, platform, prev["crawl_date"], curr["crawl_date"], min_ratio=min_ratio
            )
        )
    return results


@dataclass(frozen=True, slots=True)
class _Entry:
    is_sold: bool | None
    artist_id: str | None


def _entries(store: EntityStore, dataset: str, platform: str, crawl_date: str) -> dict[str, _Entry]:
    """platform_id -> sale status and artist for every entity in the crawl."""
    # A cursor rather than distinct(): distinct's result must fit in one 16MB document.
    cursor = store.versions(dataset).find(
        {"source": platform, "crawl_date": crawl_date},
        projection={"_id": 0, "platform_id": 1, "data.is_sold": 1, "data.platform_artist_id": 1},
    )
    entries = {}
    for doc in cursor:
        data = doc.get("data") or {}
        entries[doc["platform_id"]] = _Entry(data.get("is_sold"), data.get("platform_artist_id"))
    return entries


def _artist_ids(store: EntityStore, platform: str, crawl_date: str) -> set[str]:
    cursor = store.versions(ARTISTS).find(
        {"source": platform, "crawl_date": crawl_date}, projection={"_id": 0, "platform_id": 1}
    )
    return {doc["platform_id"] for doc in cursor}


def _has_artists(store: EntityStore, platform: str) -> bool:
    return bool(store.crawls().count_documents({"dataset": ARTISTS, "source": platform}, limit=1))


def _artists_ready(store: EntityStore, platform: str, *crawl_dates: str) -> bool:
    for crawl_date in crawl_dates:
        ledger = store.get_crawl(crawl_ledger_id(ARTISTS, platform, crawl_date))
        if ledger is None or ledger.get("status") != STATUS_COMPLETED:
            return False
    return True


def _write(
    store: EntityStore,
    dataset: str,
    platform: str,
    previous: str,
    current: str,
    reasons: dict[str, str],
) -> int:
    write_token = uuid.uuid4().hex
    now = datetime.now(UTC)
    entity_type = entity_type_for(dataset)
    collection = store.private_sales()
    written = 0
    for batch in _chunks(sorted(reasons), BULK_SIZE):
        before = _versions(store, dataset, platform, batch, previous)
        sold = [pid for pid in batch if reasons[pid] == REASON_MARKED_SOLD]
        after = _versions(store, dataset, platform, sold, current) if sold else {}
        ops = []
        for pid in batch:
            version = before.get(pid)
            if version is None:
                continue
            data = version["data"]
            crawled = data.get("crawled_at")
            doc_id = f"{version['entity_id']}:{current}"
            doc = {
                "_id": doc_id,
                "entity_id": version["entity_id"],
                "source": platform,
                "platform_id": pid,
                "entity_type": entity_type,
                "reason": reasons[pid],
                "previous_crawl": previous,
                "detected_in_crawl": current,
                "last_seen_at": seen_at(crawled if isinstance(crawled, datetime) else None, previous),
                "data": data,
                "detected_at": now,
                "etl_version": __version__,
                "write_token": write_token,
            }
            if pid in after:
                doc["sold_data"] = after[pid]["data"]
            ops.append(ReplaceOne({"_id": doc_id}, doc, upsert=True))
        if ops:
            collection.bulk_write(ops, ordered=False)
            written += len(ops)
    # Re-runs replace the earlier result for this crawl, e.g. after the crawl was reprocessed.
    collection.delete_many(
        {
            "source": platform,
            "entity_type": entity_type,
            "detected_in_crawl": current,
            "write_token": {"$ne": write_token},
        }
    )
    return written


def _versions(
    store: EntityStore, dataset: str, platform: str, platform_ids: list[str], crawl_date: str
) -> dict[str, dict]:
    cursor = store.versions(dataset).find(
        {"entity_id": {"$in": [entity_id(platform, pid) for pid in platform_ids]}, "crawl_date": crawl_date}
    )
    return {doc["platform_id"]: doc for doc in cursor}


def _chunks(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
