"""Sotheby's discovery audit: index totals, per-source coverage and cross-source overlap.

The three source caches are disjoint by construction (each pass skips keys the
others hold), so they cannot show how much the sources overlap. This module:

- reads state files, partition logs and caches (no network) for per-source
  expected / retrieved / URL / incomplete-partition figures and checks that
  the caches really are disjoint;
- asks both Algolia indexes for their own totals (a few queries);
- ``recount``: re-enumerates the platform and site-search indexes read-only
  with the same partitioning, so exact ``A∩B``, ``A∩C``, ``B∩C``, ``A∩B∩C``
  and any live keys missing from every cache can be reported;
- ``test_lot_sample``: exact test / real lot counts for sampled auctions.

Keys are held as 64-bit blake2b hashes (collision odds ~1e-7 at 2.5M keys).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from typing import Any

from html_downloader.auctions.algolia_partition import (
    CollectSummary,
    Partition,
    PartitionCollector,
    PartitionLog,
    collect_hits,
    parse_page,
    partition_log_path,
    quote_facet,
)
from html_downloader.auctions.search_state import SaleSearchState
from html_downloader.auctions.sitemap_cache import (
    iter_typed_cache_rows,
    typed_cache_path,
)
from html_downloader.auctions.sothebys_algolia import (
    AUCTIONS_INDEX,
    LOTS_INDEX,
    REAL_AUCTION_FILTER,
    REAL_LOT_FILTER,
    TEST_LOT_FILTER,
    QueryFn,
    SothebysAuction,
    hit_to_entry,
    list_auctions,
    lot_dimensions,
    lot_filters,
    lots_searcher,
)
from html_downloader.auctions.sothebys_legacy import LISTING_KEY
from html_downloader.auctions.sothebys_search import (
    SiteSearcher,
    all_windows,
    build_search_filter,
    site_search_dimensions,
    window_root,
)
from html_downloader.auctions.sothebys_search import hit_to_entry as site_hit_to_entry
from html_downloader.auctions.window_expand import HitMapper, unique_entries
from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

SOURCE_NAMES = ("platform", "legacy", "site_search")
MAX_SAMPLES = 200
MISSING_SAMPLE = 1000


def key_hash(entity_type: str, entity_id: str) -> int:
    digest = hashlib.blake2b(f"{entity_type}\t{entity_id}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def entry_hash(entry: SitemapEntry) -> int:
    return key_hash(entry.entity_type, entry.entity_id or "")


@dataclass(frozen=True, slots=True)
class AuditSources:
    """State files of one mode (full archive or art-only); None = source disabled."""

    platform_state: Path
    legacy_state: Path | None = None
    site_search_state: Path | None = None
    art_filter: str | None = None
    site_departments: Sequence[str] | None = None

    def states(self) -> dict[str, Path]:
        paths = {
            "platform": self.platform_state,
            "legacy": self.legacy_state,
            "site_search": self.site_search_state,
        }
        return {name: path for name, path in paths.items() if path is not None}


@dataclass(slots=True)
class CacheScan:
    """One pass over every cache: key hashes, per-type counts, legacy lots per sale."""

    hashes: dict[str, set[int]] = field(default_factory=dict)
    rows: dict[str, int] = field(default_factory=dict)
    types: dict[str, dict[str, int]] = field(default_factory=dict)
    legacy_lots_per_sale: Counter[str] = field(default_factory=Counter)

    @property
    def union(self) -> set[int]:
        out: set[int] = set()
        for keys in self.hashes.values():
            out |= keys
        return out


def _legacy_sale_of(entity_id: str) -> str | None:
    """``legacy/{year}/{slug}/{lot}`` → ``{year}/{slug}``."""
    if not entity_id.startswith("legacy/"):
        return None
    sale, _, lot = entity_id[len("legacy/") :].rpartition("/")
    return sale if sale and lot else None


def scan_caches(states: Mapping[str, Path]) -> CacheScan:
    scan = CacheScan()
    for name, state_path in states.items():
        keys: set[int] = set()
        types: Counter[str] = Counter()
        rows = 0
        for entity_type, entity_id, _url, _lastmod in iter_typed_cache_rows(
            typed_cache_path(state_path), label=f"sothebys audit {name}"
        ):
            rows += 1
            keys.add(key_hash(entity_type, entity_id))
            types[entity_type] += 1
            if entity_type == "lot":
                sale = _legacy_sale_of(entity_id)
                if sale is not None:
                    scan.legacy_lots_per_sale[sale] += 1
        scan.hashes[name] = keys
        scan.rows[name] = rows
        scan.types[name] = dict(types)
    return scan


def overlap_stats(sets: Mapping[str, set[int]]) -> dict[str, Any]:
    """Sizes, every pairwise and the full intersection, and the union."""
    names = list(sets)
    out: dict[str, Any] = {"sizes": {name: len(sets[name]) for name in names}}
    out["pairs"] = {
        f"{a} & {b}": len(sets[a] & sets[b]) for a, b in combinations(names, 2)
    }
    if len(names) >= 3:
        common = set.intersection(*(sets[name] for name in names))
        out["all"] = {" & ".join(names): len(common)}
    union: set[int] = set().union(*sets.values()) if sets else set()
    out["union"] = len(union)
    out["sum"] = sum(len(keys) for keys in sets.values())
    return out


def cache_stats(scan: CacheScan) -> dict[str, Any]:
    """Per-cache sizes and the disjointness invariant (sum == union)."""
    overlap = overlap_stats(scan.hashes)
    duplicate_rows = {
        name: scan.rows[name] - len(keys) for name, keys in scan.hashes.items()
    }
    return {
        "rows": dict(scan.rows),
        "unique": overlap["sizes"],
        "types": dict(scan.types),
        "duplicate_rows_within_cache": duplicate_rows,
        "cross_cache_overlap": overlap["pairs"],
        "sum_unique": overlap["sum"],
        "total_unique": overlap["union"],
        "disjoint": overlap["sum"] == overlap["union"]
        and not any(duplicate_rows.values()),
    }


def _incomplete_leaves(state_path: Path) -> tuple[int, list[dict[str, Any]]]:
    count = 0
    samples: list[dict[str, Any]] = []
    for window, records in PartitionLog(partition_log_path(state_path)).latest().items():
        for record in records:
            if record.get("kind") != "leaf" or record.get("complete"):
                continue
            count += 1
            if len(samples) < MAX_SAMPLES:
                samples.append(
                    {
                        "window": window,
                        "filters": record.get("filters"),
                        "expected_hits": record.get("expected_hits"),
                        "retrieved_hits": record.get("retrieved_hits"),
                        "reason": record.get("reason"),
                    }
                )
    return count, samples


def window_source_stats(state_path: Path) -> dict[str, Any]:
    """Platform / site-search figures from the state file and the partition log."""
    entries = SaleSearchState(state_path).items()
    statuses = Counter(str(entry.get("status")) for _, entry in entries)
    audited = [(key, entry) for key, entry in entries if "expected" in entry]
    exact = [(key, entry) for key, entry in audited if entry.get("expected_exact")]
    shortfalls = [
        {
            "window": key,
            "url": entry.get("url"),
            "expected": entry.get("expected"),
            "retrieved": entry.get("retrieved"),
        }
        for key, entry in exact
        if int(entry.get("retrieved") or 0) < int(entry.get("expected") or 0)
    ]
    not_complete = [
        {
            "window": key,
            "status": entry.get("status"),
            "url": entry.get("url"),
            "expected": entry.get("expected"),
            "retrieved": entry.get("retrieved"),
            "incomplete": entry.get("incomplete"),
        }
        for key, entry in entries
        if entry.get("status") in ("incomplete", "failed", "in_progress")
    ]
    leaf_count, leaf_samples = _incomplete_leaves(state_path)
    return {
        "windows": len(entries),
        "statuses": dict(statuses),
        "audited_windows": len(audited),
        "pre_audit_windows": len(entries) - len(audited),
        "expected_hits": sum(int(e.get("expected") or 0) for _, e in audited),
        "expected_exact_windows": len(exact),
        "expected_exact_hits": sum(int(e.get("expected") or 0) for _, e in exact),
        "retrieved_hits": sum(int(e.get("hits") or 0) for _, e in entries),
        "urls": sum(int(e.get("urls") or 0) for _, e in audited),
        "written": sum(int(e.get("written") or 0) for _, e in entries),
        "partitions": sum(int(e.get("partitions") or 0) for _, e in audited),
        "incomplete_partitions": leaf_count,
        "incomplete_partition_samples": leaf_samples,
        "not_complete_windows": not_complete[:MAX_SAMPLES],
        "not_complete_window_count": len(not_complete),
        "shortfall_windows": shortfalls[:MAX_SAMPLES],
        "shortfall_window_count": len(shortfalls),
    }


def legacy_stats(state_path: Path, scan: CacheScan) -> dict[str, Any]:
    """Legacy sales vs the lot count each sale page showed (lots counted in every cache)."""
    entries = [(k, e) for k, e in SaleSearchState(state_path).items() if k != LISTING_KEY]
    statuses = Counter(str(entry.get("status")) for _, entry in entries)
    done = [(key, entry) for key, entry in entries if entry.get("status") == "done"]
    mismatches = []
    for key, entry in done:
        expected = int(entry.get("hits") or 0)
        found = scan.legacy_lots_per_sale.get(key, 0)
        if found < expected:
            mismatches.append({"sale": key, "expected_lots": expected, "cached_lots": found})
    mismatches.sort(key=lambda m: m["cached_lots"] - m["expected_lots"])
    return {
        "sales": len(entries),
        "statuses": dict(statuses),
        "expected_lots": sum(int(entry.get("hits") or 0) for _, entry in done),
        "generated_urls": scan.types.get("legacy", {}).get("lot", 0),
        "short_sales": len(mismatches),
        "short_lots": sum(m["expected_lots"] - m["cached_lots"] for m in mismatches),
        "short_sale_samples": mismatches[:MAX_SAMPLES],
    }


def _count(
    query: QueryFn,
    index: str,
    *,
    filters: str = "",
    facets: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "query": "",
        "filters": filters,
        "hitsPerPage": 0,
        "attributesToHighlight": "",
        **(extra or {}),
    }
    if facets:
        params.update(facets=facets, maxValuesPerFacet=100)
    data = query(index, params)
    if data is None:
        return {"filters": filters, "error": "query failed"}
    page = parse_page(data)
    exhaustive = data.get("exhaustive") if isinstance(data.get("exhaustive"), dict) else {}
    out: dict[str, Any] = {"filters": filters, "nb_hits": page.nb_hits, "exact": page.exact}
    if facets:
        out["facets"] = data.get("facets") or {}
        out["facets_exact"] = bool(exhaustive.get("facetsCount", data.get("exhaustiveFacetsCount")))
    return out


def index_totals(
    platform_query: QueryFn, site_query: QueryFn | None, site_index: str | None
) -> dict[str, Any]:
    """What each index says about its own size (``exact`` flags which counts are estimates)."""
    out: dict[str, Any] = {
        "platform": {
            "lots": _count(platform_query, LOTS_INDEX, facets="isTestLot"),
            "real_lots": _count(platform_query, LOTS_INDEX, filters=REAL_LOT_FILTER),
            "test_lots": _count(platform_query, LOTS_INDEX, filters=TEST_LOT_FILTER),
            "auctions": _count(platform_query, AUCTIONS_INDEX),
            "real_auctions": _count(platform_query, AUCTIONS_INDEX, filters=REAL_AUCTION_FILTER),
        }
    }
    if site_query is not None and site_index:
        out["site_search"] = {
            "all": _count(site_query, site_index, facets="type", extra={"distinct": "false"}),
        }
    return out


def pick_sample(
    auctions: Sequence[SothebysAuction], hits_by_auction: Mapping[str, int], size: int
) -> list[SothebysAuction]:
    """Largest auctions plus an even spread over time (deterministic)."""
    if size <= 0 or not auctions:
        return []
    by_size = sorted(auctions, key=lambda a: -hits_by_auction.get(a.key, 0))
    by_time = sorted(auctions, key=lambda a: (a.end_date or "", a.key))
    largest = by_size[: max(1, size // 4)]
    step = max(1, len(by_time) // max(1, size - len(largest)))
    chosen: dict[str, SothebysAuction] = {a.key: a for a in largest}
    for auction in by_time[::step]:
        if len(chosen) >= size:
            break
        chosen.setdefault(auction.key, auction)
    return list(chosen.values())


def sample_test_lots(
    query: QueryFn, auctions: Sequence[SothebysAuction]
) -> dict[str, Any]:
    """Exact per-auction total / test / real lot counts (``isTestLot`` stays the prod filter)."""
    rows = []
    for auction in auctions:
        base = f"auctionId:{quote_facet(auction.auction_id)}"
        counts = {
            name: _count(query, LOTS_INDEX, filters=filters)
            for name, filters in (
                ("total", base),
                ("test", f"{base} AND {TEST_LOT_FILTER}"),
                ("real", f"{base} AND {REAL_LOT_FILTER}"),
            )
        }
        rows.append(
            {
                "auction_id": auction.auction_id,
                "url": auction.url,
                **{name: c.get("nb_hits") for name, c in counts.items()},
                "exact": all(c.get("exact") for c in counts.values()),
            }
        )
    totals = {name: sum(int(r.get(name) or 0) for r in rows) for name in ("total", "test", "real")}
    return {"auctions": len(rows), **totals, "rows": rows}


@dataclass(slots=True)
class _Recount:
    """Thread-safe raw key set of one source during a read-only re-enumeration."""

    cache_union: set[int]
    keys: set[int] = field(default_factory=set)
    missing: dict[int, str] = field(default_factory=dict)
    missing_count: int = 0
    windows: int = 0
    retrieved_hits: int = 0
    expected_exact_hits: int = 0
    incomplete: list[dict[str, Any]] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, key: str, entries: Iterable[SitemapEntry], summary: CollectSummary) -> None:
        with self.lock:
            self.windows += 1
            self.retrieved_hits += summary.retrieved_hits
            if summary.expected_exact:
                self.expected_exact_hits += summary.expected_hits or 0
            if not summary.complete:
                self.incomplete.append({"window": key, **summary.to_json()})
            for entry in entries:
                digest = entry_hash(entry)
                if digest in self.keys:
                    continue
                self.keys.add(digest)
                if digest not in self.cache_union:
                    self.missing_count += 1
                    if len(self.missing) < MISSING_SAMPLE:
                        self.missing[digest] = entry.url

    def fail(self, key: str) -> None:
        with self.lock:
            self.failed.append(key)

    def report(self) -> dict[str, Any]:
        return {
            "windows": self.windows,
            "retrieved_hits": self.retrieved_hits,
            "expected_exact_hits": self.expected_exact_hits,
            "unique_keys": len(self.keys),
            "missing_from_caches": self.missing_count,
            "missing_sample": sorted(self.missing.values())[:MAX_SAMPLES],
            "incomplete_windows": self.incomplete[:MAX_SAMPLES],
            "incomplete_window_count": len(self.incomplete),
            "failed_windows": self.failed,
        }


@dataclass(frozen=True, slots=True)
class _RecountJob:
    key: str
    collector: PartitionCollector
    mapper: HitMapper
    root: Partition | None = None
    extra: tuple[SitemapEntry, ...] = ()


def _run_recount(jobs: Sequence[_RecountJob], target: _Recount, workers: int, label: str) -> None:
    def _one(job: _RecountJob) -> None:
        hits, summary = collect_hits(job.collector, root=job.root)
        target.add(job.key, [*unique_entries(hits, job.mapper), *job.extra], summary)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(_one, job): job for job in jobs}
        for done, future in enumerate(as_completed(futures), start=1):
            job = futures[future]
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 - PartitionQueryError or a crash
                LOGGER.error("%s recount window=%s failed: %s", label, job.key, exc)
                target.fail(job.key)
            if done % 250 == 0:
                LOGGER.info("%s recount progress %s/%s", label, done, len(jobs))


def recount(
    sources: AuditSources,
    scan: CacheScan,
    *,
    platform_query: QueryFn,
    site_query: QueryFn | None,
    site_index: str | None,
    workers: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Re-enumerate both Algolia sources read-only and compute exact overlaps."""
    cache_union = scan.union
    moment = now or datetime.now(timezone.utc)
    raw: dict[str, set[int]] = {}
    out: dict[str, Any] = {}

    platform = _Recount(cache_union)
    searcher = lots_searcher(platform_query)
    include_sales = sources.art_filter is None
    auctions = list_auctions(platform_query)
    jobs = [
        _RecountJob(
            key=auction.key,
            collector=PartitionCollector(
                searcher, lot_dimensions(), base=lot_filters(auction, sources.art_filter)
            ),
            mapper=_platform_mapper(auction),
            extra=(auction.sale_entry(),) if include_sales else (),
        )
        for auction in auctions
    ]
    _run_recount(jobs, platform, workers, "sothebys platform")
    raw["platform"] = platform.keys
    out["platform"] = {"auctions": len(auctions), **platform.report()}

    if "legacy" in scan.hashes:
        raw["legacy"] = scan.hashes["legacy"]

    if site_query is not None and site_index and sources.site_search_state is not None:
        site = _Recount(cache_union)
        site_searcher = SiteSearcher(site_query, site_index)
        base = build_search_filter(sources.site_departments)
        site_jobs = [
            _RecountJob(
                key=window.key,
                collector=PartitionCollector(site_searcher, site_search_dimensions(), base=base),
                mapper=site_hit_to_entry,
                root=window_root(window),
            )
            for window in all_windows(moment)
        ]
        _run_recount(site_jobs, site, workers, "sothebys site search")
        raw["site_search"] = site.keys
        out["site_search"] = site.report()

    overlap = overlap_stats(raw)
    raw_union = set().union(*raw.values()) if raw else set()
    overlap["cache_union"] = len(cache_union)
    overlap["raw_not_in_caches"] = len(raw_union - cache_union)
    overlap["caches_not_in_raw"] = len(cache_union - raw_union)
    out["overlap"] = overlap
    return out


def _platform_mapper(auction: SothebysAuction) -> Callable[[dict[str, Any]], SitemapEntry | None]:
    return lambda hit: hit_to_entry(hit, auction)


def count_lines(path: Path) -> int | None:
    if not path.is_file():
        return None
    with open(path, "rb") as handle:
        return sum(1 for line in handle if line.strip())


def build_report(
    sources: AuditSources,
    *,
    urls_path: Path | None = None,
    platform_query: QueryFn | None = None,
    site_query: QueryFn | None = None,
    site_index: str | None = None,
    do_recount: bool = False,
    sample_size: int = 0,
    workers: int = 2,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Full audit dict; network parts run only when a query function is given."""
    states = sources.states()
    scan = scan_caches(states)
    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "mode": "artworks" if sources.art_filter else "full",
        "sources": {},
        "caches": cache_stats(scan),
    }
    if "platform" in states:
        report["sources"]["platform"] = window_source_stats(states["platform"])
    if "legacy" in states:
        report["sources"]["legacy"] = legacy_stats(states["legacy"], scan)
    if "site_search" in states:
        report["sources"]["site_search"] = window_source_stats(states["site_search"])
    if urls_path is not None:
        lines = count_lines(urls_path)
        report["urls_txt"] = {
            "path": str(urls_path),
            "lines": lines,
            "matches_total_unique": lines == report["caches"]["total_unique"]
            if lines is not None
            else None,
        }
    if platform_query is not None:
        report["index_totals"] = index_totals(platform_query, site_query, site_index)
        if sample_size > 0:
            auctions = list_auctions(platform_query)
            hits = {
                key: int(entry.get("hits") or 0)
                for key, entry in SaleSearchState(sources.platform_state).items()
            }
            report["test_lots"] = sample_test_lots(
                platform_query, pick_sample(auctions, hits, sample_size)
            )
        if do_recount:
            report["recount"] = recount(
                sources,
                scan,
                platform_query=platform_query,
                site_query=site_query,
                site_index=site_index,
                workers=workers,
                now=now,
            )
    return report


def _fmt(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "NO"
    return f"{value:,}" if isinstance(value, int) else str(value)


def format_report(report: dict[str, Any]) -> str:
    """Short human summary of ``build_report`` output."""
    lines = ["Sotheby's discovery audit", "=" * 25, f"mode: {report.get('mode')}"]

    def row(label: str, value: Any) -> None:
        lines.append(f"{label + ':':<26}{_fmt(value)}")

    titles = {"platform": "Platform Algolia", "legacy": "Legacy", "site_search": "Site Search"}
    for name, stats in report.get("sources", {}).items():
        lines += ["", titles.get(name, name), "-" * len(titles.get(name, name))]
        if name == "legacy":
            row("Sales", stats["sales"])
            row("Expected lots", stats["expected_lots"])
            row("Generated URLs", stats["generated_urls"])
            row("Short sales / lots", f"{stats['short_sales']:,} / {stats['short_lots']:,}")
            continue
        row("Windows" if name == "site_search" else "Auctions", stats["windows"])
        row("Pre-audit windows", stats["pre_audit_windows"])
        row("Algolia hits (expected)", stats["expected_hits"])
        row("  of which exact", stats["expected_exact_hits"])
        row("Retrieved hits", stats["retrieved_hits"])
        row("Unique URLs (cache)", report["caches"]["unique"].get(name, 0))
        row("Partitions", stats["partitions"])
        row("Incomplete partitions", stats["incomplete_partitions"])
        row("Not complete windows", stats["not_complete_window_count"])
        row("Shortfall windows", stats["shortfall_window_count"])

    caches = report["caches"]
    lines += ["", "Caches", "------"]
    row("Sum of cache sizes", caches["sum_unique"])
    row("Total unique URLs", caches["total_unique"])
    row("Disjoint", caches["disjoint"])
    if "urls_txt" in report:
        row("urls.txt lines", report["urls_txt"]["lines"])

    totals = report.get("index_totals")
    if totals:
        lines += ["", "Index totals", "------------"]
        for source, counts in totals.items():
            for name, count in counts.items():
                exact = "exact" if count.get("exact") else "estimate"
                row(f"{source}.{name}", f"{_fmt(count.get('nb_hits'))} ({exact})")
                types = (count.get("facets") or {}).get("type")
                if isinstance(types, dict):
                    for type_name in ("Lot", "Auction"):
                        row(f"  type:{type_name}", types.get(type_name, 0))

    sample = report.get("test_lots")
    if sample:
        lines += ["", "Test-lot sample", "---------------"]
        row("Auctions", sample["auctions"])
        row("Total / test / real", f"{sample['total']:,} / {sample['test']:,} / {sample['real']:,}")

    recounted = report.get("recount")
    if recounted:
        overlap = recounted["overlap"]
        lines += ["", "Cross-source overlap (recount)", "------------------------------"]
        for name, size in overlap["sizes"].items():
            row(f"{name} raw", size)
        for pair, size in overlap["pairs"].items():
            row(pair, size)
        for triple, size in overlap.get("all", {}).items():
            row(triple, size)
        row("Raw union", overlap["union"])
        row("Raw not in caches", overlap["raw_not_in_caches"])
        row("Caches not in raw", overlap["caches_not_in_raw"])
    return "\n".join(lines)


__all__ = [
    "AuditSources",
    "CacheScan",
    "build_report",
    "cache_stats",
    "format_report",
    "index_totals",
    "key_hash",
    "legacy_stats",
    "overlap_stats",
    "pick_sample",
    "recount",
    "sample_test_lots",
    "scan_caches",
    "window_source_stats",
]
