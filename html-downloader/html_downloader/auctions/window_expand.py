"""Resumable, partitioned expansion of checkpoint windows into a typed JSONL cache.

Shared by Sotheby's Algolia sources: each window (auction, month) is enumerated
by a ``PartitionCollector``; every finished leaf is deduplicated against the
union of all source caches and appended before the leaf is logged, so a crash
only ever re-queries one leaf.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from html_downloader.auctions.algolia_partition import (
    CollectSummary,
    IndexSearcher,
    LeafUrls,
    Partition,
    PartitionCollector,
    PartitionLog,
    SplitDimension,
)
from html_downloader.auctions.search_state import SaleSearchState
from html_downloader.auctions.sitemap_cache import TypedKeySet, append_typed_cache
from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

HitMapper = Callable[[dict[str, Any]], SitemapEntry | None]


@dataclass(frozen=True, slots=True)
class WindowJob:
    """One checkpoint window: base filter, root partition, hit mapper, settle flag."""

    key: str
    base: str
    mapper: HitMapper
    settled: bool
    root: Partition = field(default_factory=Partition)
    extra_entries: tuple[SitemapEntry, ...] = ()
    fields: dict[str, Any] = field(default_factory=dict)


class CacheIngestor:
    """Thread-safe dedupe against every source's keys + append to this source's cache."""

    def __init__(self, cache_path: Path, seen: TypedKeySet, *, max_return: int) -> None:
        self.cache_path = cache_path
        self.seen = seen
        self.max_return = max_return
        self.new_returned: list[SitemapEntry] = []
        self.new = 0
        self._lock = threading.Lock()

    def write(self, entries: Sequence[SitemapEntry]) -> int:
        """Append entries no cache holds yet; returns how many were new."""
        if not entries:
            return 0
        with self._lock:
            fresh = [entry for entry in entries if self.seen.add(entry.entity_key)]
            append_typed_cache(self.cache_path, fresh)
            self.new += len(fresh)
            room = self.max_return - len(self.new_returned)
            if room > 0:
                self.new_returned.extend(fresh[:room])
            return len(fresh)


def unique_entries(hits: Sequence[dict[str, Any]], mapper: HitMapper) -> list[SitemapEntry]:
    by_key: dict[tuple[str, str], SitemapEntry] = {}
    for hit in hits:
        entry = mapper(hit)
        if entry is not None:
            by_key.setdefault(entry.entity_key, entry)
    return list(by_key.values())


def window_status(summary: CollectSummary, settled: bool) -> str:
    if not summary.complete:
        return "incomplete"
    return "done" if settled else "open"


@dataclass(slots=True)
class ExpandTotals:
    done: int = 0
    open: int = 0
    incomplete: int = 0
    failed: int = 0
    expected: int = 0
    retrieved: int = 0
    partitions: int = 0
    incomplete_partitions: int = 0

    @property
    def processed(self) -> int:
        return self.done + self.open + self.incomplete + self.failed


def expand_windows(
    *,
    label: str,
    jobs: Sequence[WindowJob],
    searcher: IndexSearcher,
    dimensions: Sequence[SplitDimension],
    state: SaleSearchState,
    log: PartitionLog,
    ingestor: CacheIngestor,
    workers: int,
    progress_every: int,
) -> ExpandTotals:
    """Enumerate every job; marks state and closes each window's log epoch."""
    totals = ExpandTotals()
    lock = threading.Lock()

    def _process(job: WindowJob) -> None:
        run = log.start(job.key)
        collector = PartitionCollector(searcher, dimensions, base=job.base)
        extra_new = 0

        def on_leaf(_partition: Partition, hits: list[dict[str, Any]]) -> LeafUrls:
            nonlocal extra_new
            # Extras (the sale page) precede the window's lots and only follow a
            # successful query, so a failing window leaves no trace in the cache.
            extra_new += ingestor.write(job.extra_entries)
            entries = unique_entries(hits, job.mapper)
            return LeafUrls(unique=len(entries), new=ingestor.write(entries))

        summary = collector.collect(on_leaf, root=job.root, run=run)
        extra_new += ingestor.write(job.extra_entries)
        status = window_status(summary, job.settled)
        run.finish(status, summary)
        state.mark(
            job.key,
            status,
            hits=summary.retrieved_hits,
            written=summary.new_urls + extra_new,
            epoch=run.epoch,
            **summary.state_fields(),
            **job.fields,
        )
        with lock:
            setattr(totals, status, getattr(totals, status) + 1)
            totals.expected += summary.expected_hits or 0
            totals.retrieved += summary.retrieved_hits
            totals.partitions += summary.partitions
            totals.incomplete_partitions += summary.incomplete_count

    def _failed(job: WindowJob, exc: BaseException) -> None:
        # No ``end`` record: the next run resumes this epoch's finished leaves.
        LOGGER.error("%s window=%s failed: %s", label, job.key, exc)
        state.mark(job.key, "failed", **job.fields)
        with lock:
            totals.failed += 1

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(_process, job): job for job in jobs}
            for future in as_completed(futures):
                job = futures[future]
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001 - PartitionQueryError or a crash
                    _failed(job, exc)
                if totals.processed % progress_every == 0:
                    LOGGER.info(
                        "%s progress windows=%s/%s expected=%s retrieved=%s new=%s "
                        "incomplete=%s failed=%s",
                        label,
                        totals.processed,
                        len(jobs),
                        totals.expected,
                        totals.retrieved,
                        ingestor.new,
                        totals.incomplete,
                        totals.failed,
                    )
    finally:
        state.flush()
        log.compact()
    return totals


__all__ = [
    "CacheIngestor",
    "ExpandTotals",
    "HitMapper",
    "WindowJob",
    "expand_windows",
    "unique_entries",
    "window_status",
]
