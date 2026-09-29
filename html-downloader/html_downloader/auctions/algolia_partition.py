"""Exhaustive enumeration of an Algolia index under its 1,000-hit query cap.

Algolia returns at most ``paginationLimitedTo`` (1,000) hits per query, so a
filter matching more records must be split into partitions that each fit.
``PartitionCollector`` does this recursively:

- A query returning a full page always splits (``nbHits`` may be an estimate).
- When ``nbHits`` is exact (``exhaustive.nbHits`` / ``exhaustiveNbHits``), a
  leaf is complete only if every hit was retrieved; otherwise it is retried
  once, then reported ``count_mismatch``.
- Splits follow an ordered list of ``SplitDimension``s. A numeric range adds
  a ``NOT attr:lo TO hi`` remainder on first use, so records without the
  attribute are never dropped.
- A partition no dimension can shrink keeps its first page and is reported
  ``unsplittable``; it is never counted as complete.

``PartitionLog`` persists split plans and finished leaves per window (an
auction, a month) so an interrupted window resumes at its unfinished
partitions instead of starting over.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

LOGGER = logging.getLogger(__name__)

MAX_HITS = 1000  # Algolia hitsPerPage and paginationLimitedTo cap
CHUNK_BUDGET = 800  # below MAX_HITS: facet counts are estimates
MAX_FACET_VALUES = 1000
MAX_FACET_SPLIT = 50  # longer NOT-remainders would bloat the filter string
MAX_INCOMPLETE_SAMPLES = 50

QueryFn = Callable[[str, dict[str, Any]], dict[str, Any] | None]


def quote_facet(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def facet_literal(value: str) -> str:
    """Facet filter operand; booleans stay bare (``withdrawn:false``)."""
    return value if value in ("true", "false") else quote_facet(value)


def range_clause(attr: str, lo: int, hi: int) -> str:
    """Half-open ``[lo, hi)`` so fractional values never fall between chunks."""
    return f"{attr} >= {lo} AND {attr} < {hi}"


@dataclass(frozen=True, slots=True)
class SearchPage:
    hits: list[dict[str, Any]]
    nb_hits: int
    exact: bool


def parse_page(data: dict[str, Any]) -> SearchPage:
    hits = [hit for hit in (data.get("hits") or []) if isinstance(hit, dict)]
    try:
        nb_hits = int(data.get("nbHits"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        nb_hits = len(hits)
    exhaustive = data.get("exhaustive")
    if isinstance(exhaustive, dict) and "nbHits" in exhaustive:
        exact = bool(exhaustive["nbHits"])
    else:
        exact = bool(data.get("exhaustiveNbHits", False))
    return SearchPage(hits=hits, nb_hits=nb_hits, exact=exact)


class IndexSearcher(Protocol):
    max_hits: int

    def page(self, filters: str) -> SearchPage | None: ...

    def facet_counts(self, filters: str, attr: str) -> dict[str, int] | None: ...


class AlgoliaIndexSearcher:
    """First page + facet counts of one index through a ``QueryFn``."""

    def __init__(
        self,
        query: QueryFn,
        index: str,
        *,
        attributes: Sequence[str],
        extra_params: dict[str, Any] | None = None,
        max_hits: int = MAX_HITS,
    ) -> None:
        self._query = query
        self.index = index
        self._attributes = ",".join(attributes)
        self._extra = dict(extra_params or {})
        self.max_hits = max_hits

    def _run(self, filters: str, params: dict[str, Any]) -> dict[str, Any] | None:
        return self._query(
            self.index,
            {"query": "", "filters": filters, "attributesToHighlight": "", **self._extra, **params},
        )

    def page(self, filters: str) -> SearchPage | None:
        data = self._run(
            filters,
            {"hitsPerPage": self.max_hits, "page": 0, "attributesToRetrieve": self._attributes},
        )
        return None if data is None else parse_page(data)

    def facet_counts(self, filters: str, attr: str) -> dict[str, int] | None:
        data = self._run(
            filters, {"hitsPerPage": 0, "facets": attr, "maxValuesPerFacet": MAX_FACET_VALUES}
        )
        raw = ((data or {}).get("facets") or {}).get(attr)
        if not isinstance(raw, dict):
            return None
        counts: dict[str, int] = {}
        for value, count in raw.items():
            try:
                counts[str(value)] = int(count)
            except (TypeError, ValueError):
                continue
        return counts


@dataclass(frozen=True, slots=True)
class Partition:
    """AND-ed clauses on top of a window's base filter, plus split bookkeeping.

    ``parts`` holds one clause per slot (attribute); ``dim`` is the first
    dimension allowed to split it further; ``ranges`` are the current numeric
    bounds per attribute.
    """

    parts: tuple[tuple[str, str], ...] = ()
    dim: int = 0
    ranges: tuple[tuple[str, int, int], ...] = ()

    @property
    def key(self) -> str:
        return " AND ".join(clause for _, clause in self.parts) or "*"

    def filters(self, base: str) -> str:
        clauses = [base] if base else []
        clauses.extend(clause for _, clause in self.parts)
        return " AND ".join(clauses)

    def range_of(self, attr: str) -> tuple[int, int] | None:
        for name, lo, hi in self.ranges:
            if name == attr:
                return lo, hi
        return None

    def child(
        self, slot: str, clause: str, dim: int, bounds: tuple[int, int] | None = None
    ) -> Partition:
        if any(name == slot for name, _ in self.parts):
            parts = tuple((name, clause if name == slot else old) for name, old in self.parts)
        else:
            parts = (*self.parts, (slot, clause))
        ranges = tuple(r for r in self.ranges if r[0] != slot)
        if bounds is not None:
            ranges = (*ranges, (slot, bounds[0], bounds[1]))
        return Partition(parts=parts, dim=dim, ranges=ranges)

    def to_json(self) -> dict[str, Any]:
        return {
            "parts": [list(part) for part in self.parts],
            "dim": self.dim,
            "ranges": [list(r) for r in self.ranges],
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> Partition:
        return cls(
            parts=tuple((str(s), str(c)) for s, c in raw.get("parts") or ()),
            dim=int(raw.get("dim") or 0),
            ranges=tuple((str(a), int(lo), int(hi)) for a, lo, hi in raw.get("ranges") or ()),
        )


class SplitDimension(Protocol):
    attr: str

    def split(
        self,
        partition: Partition,
        dim: int,
        base: str,
        searcher: IndexSearcher,
        expected: int,
    ) -> list[Partition] | None:
        """Children covering ``partition`` (None when this dimension cannot shrink it)."""
        ...


def numeric_counts(raw: dict[str, int]) -> dict[int, int]:
    counts: dict[int, int] = {}
    for value, count in raw.items():
        try:
            bucket = math.floor(float(value))
        except (TypeError, ValueError, OverflowError):
            continue
        counts[bucket] = counts.get(bucket, 0) + count
    return counts


def plan_chunks(
    counts: dict[int, int], lo: int, hi: int, budget: int = CHUNK_BUDGET
) -> list[tuple[int, int]]:
    """
    Split ``[lo, hi)`` into contiguous half-open chunks of about ``budget`` hits.

    Chunks always cover the whole range, so values missing from ``counts``
    (truncated facets) are still queried; a value at or above ``budget`` gets
    its own ``[v, v + 1)`` chunk so its overflow reaches the next dimension.
    """
    values = sorted(v for v in counts if lo <= v < hi)
    chunks: list[tuple[int, int]] = []
    start, total = lo, 0
    for value in values:
        count = counts[value]
        if total and total + count > budget:
            chunks.append((start, value))
            start, total = value, 0
        if count >= budget:
            if start < value:
                chunks.append((start, value))
            chunks.append((value, value + 1))
            start, total = value + 1, 0
            continue
        total += count
    if start < hi:
        chunks.append((start, hi))
    return chunks


@dataclass(frozen=True, slots=True)
class NumericRange:
    """Facet-planned (else bisected) ranges of a numeric attribute."""

    attr: str
    lo: int
    hi: int
    budget: int = CHUNK_BUDGET

    def split(
        self,
        partition: Partition,
        dim: int,
        base: str,
        searcher: IndexSearcher,
        expected: int,
    ) -> list[Partition] | None:
        del expected
        children: list[Partition] = []
        current = partition.range_of(self.attr)
        if current is None:
            lo, hi = self.lo, self.hi
            # Records without the attribute (or outside the range) are never dropped.
            children.append(partition.child(self.attr, f"NOT {self.attr}:{lo} TO {hi}", dim + 1))
        else:
            lo, hi = current
        if hi - lo <= 1:
            return None
        raw = searcher.facet_counts(partition.filters(base), self.attr) or {}
        chunks = plan_chunks(numeric_counts(raw), lo, hi, self.budget)
        if len(chunks) < 2:
            mid = lo + (hi - lo) // 2
            chunks = [(lo, mid), (mid, hi)]
        for start, stop in chunks:
            next_dim = dim if stop - start > 1 else dim + 1
            children.append(
                partition.child(
                    self.attr, range_clause(self.attr, start, stop), next_dim, (start, stop)
                )
            )
        return children


@dataclass(frozen=True, slots=True)
class FacetValues:
    """One child per facet value plus a remainder excluding all of them."""

    attr: str
    max_values: int = MAX_FACET_SPLIT

    def split(
        self,
        partition: Partition,
        dim: int,
        base: str,
        searcher: IndexSearcher,
        expected: int,
    ) -> list[Partition] | None:
        counts = searcher.facet_counts(partition.filters(base), self.attr)
        if not counts or len(counts) > self.max_values:
            return None
        values = sorted(counts)
        if len(values) == 1 and counts[values[0]] >= expected:
            return None
        children = [
            partition.child(self.attr, f"{self.attr}:{facet_literal(v)}", dim + 1) for v in values
        ]
        remainder = " AND ".join(f"NOT {self.attr}:{facet_literal(v)}" for v in values)
        children.append(partition.child(self.attr, remainder, dim + 1))
        return children


@dataclass(frozen=True, slots=True)
class LeafUrls:
    """URL accounting a leaf callback reports back: distinct keys and newly written."""

    unique: int = 0
    new: int = 0


@dataclass(frozen=True, slots=True)
class LeafRecord:
    partition: str
    filters: str
    expected_hits: int
    expected_exact: bool
    retrieved_hits: int
    unique_urls: int
    new_urls: int
    known_urls: int
    complete: bool
    reason: str | None = None


@dataclass(slots=True)
class CollectSummary:
    """Totals for one window; ``expected_*`` come from its root query."""

    expected_hits: int | None = None
    expected_exact: bool = False
    retrieved_hits: int = 0
    unique_urls: int = 0
    new_urls: int = 0
    partitions: int = 0
    resumed_partitions: int = 0
    incomplete_count: int = 0
    incomplete: list[dict[str, Any]] = field(default_factory=list)

    @property
    def shortfall(self) -> bool:
        return (
            self.expected_exact
            and self.expected_hits is not None
            and self.retrieved_hits < self.expected_hits
        )

    @property
    def complete(self) -> bool:
        return self.incomplete_count == 0 and not self.shortfall

    def set_expected(self, hits: Any, exact: Any) -> None:
        try:
            self.expected_hits = int(hits)
        except (TypeError, ValueError):
            self.expected_hits = None
        self.expected_exact = bool(exact) and self.expected_hits is not None

    def add_leaf(self, record: dict[str, Any], *, resumed: bool = False) -> None:
        self.partitions += 1
        self.resumed_partitions += int(resumed)
        self.retrieved_hits += int(record.get("retrieved_hits") or 0)
        self.unique_urls += int(record.get("unique_urls") or 0)
        self.new_urls += int(record.get("new_urls") or 0)
        if not record.get("complete"):
            self.incomplete_count += 1
            if len(self.incomplete) < MAX_INCOMPLETE_SAMPLES:
                self.incomplete.append(
                    {
                        "filters": record.get("filters"),
                        "expected_hits": record.get("expected_hits"),
                        "retrieved_hits": record.get("retrieved_hits"),
                        "reason": record.get("reason"),
                    }
                )

    def state_fields(self) -> dict[str, Any]:
        """Extra per-window fields for ``SaleSearchState``."""
        return {
            "expected": self.expected_hits,
            "expected_exact": self.expected_exact,
            "retrieved": self.retrieved_hits,
            "urls": self.unique_urls,
            "partitions": self.partitions,
            "incomplete": self.incomplete_count,
        }

    def to_json(self) -> dict[str, Any]:
        return {
            **self.state_fields(),
            "new_urls": self.new_urls,
            "resumed_partitions": self.resumed_partitions,
            "shortfall": self.shortfall,
            "complete": self.complete,
            "incomplete_samples": self.incomplete,
        }


class PartitionQueryError(RuntimeError):
    """An Algolia query failed after the client's retries."""


def partition_log_path(state_path: Path) -> Path:
    """Sidecar partition log next to a window state file."""
    return state_path.with_name(state_path.stem + "_partitions.jsonl")


@dataclass(slots=True)
class _EpochRecords:
    epoch: str
    records: list[dict[str, Any]]
    ended: bool = False


class PartitionLog:
    """
    Append-only JSONL of ``begin`` / ``split`` / ``leaf`` / ``end`` records per window.

    Every record carries ``window`` and ``epoch``; only the latest epoch per
    window matters. A window whose latest epoch has no ``end`` record was
    interrupted and resumes in that epoch; otherwise a new epoch starts.
    ``path=None`` keeps everything in memory (read-only audits).
    """

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._latest: dict[str, _EpochRecords] = {}
        if path is not None and path.exists():
            self._load(path)

    def _load(self, path: Path) -> None:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            LOGGER.warning("could not read partition log %s: %s", path, exc)
            return
        for line in raw.decode("utf-8", errors="replace").splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue  # torn trailing line after a crash
            if not isinstance(record, dict):
                continue
            window, epoch = record.get("window"), record.get("epoch")
            if not isinstance(window, str) or not isinstance(epoch, str):
                continue
            self._track(window, epoch, record)
        if raw and not raw.endswith(b"\n"):
            with open(path, "a", encoding="utf-8") as handle:
                handle.write("\n")

    def _track(self, window: str, epoch: str, record: dict[str, Any]) -> None:
        current = self._latest.get(window)
        if current is None or current.epoch != epoch:
            current = _EpochRecords(epoch=epoch, records=[])
            self._latest[window] = current
        current.records.append(record)
        if record.get("kind") == "end":
            current.ended = True

    def start(self, window: str, *, resume: bool = True) -> WindowRun:
        """Resume the window's interrupted epoch, or begin a new one."""
        with self._lock:
            latest = self._latest.get(window)
            if resume and latest is not None and not latest.ended:
                return WindowRun(self, window, latest.epoch, list(latest.records))
            epoch = uuid.uuid4().hex[:12]
        run = WindowRun(self, window, epoch, [])
        self.append(window, epoch, {"kind": "begin"})
        return run

    def append(self, window: str, epoch: str, record: dict[str, Any]) -> None:
        full = {"window": window, "epoch": epoch, **record}
        line = json.dumps(full, ensure_ascii=False)
        with self._lock:
            self._track(window, epoch, full)
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")

    def latest(self) -> dict[str, list[dict[str, Any]]]:
        """Records of the latest epoch per window."""
        with self._lock:
            return {window: list(entry.records) for window, entry in self._latest.items()}

    def compact(self) -> None:
        """Rewrite the file with only the latest epoch per window (atomic)."""
        if self.path is None:
            return
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.writelines(
                    json.dumps(record, ensure_ascii=False) + "\n"
                    for entry in self._latest.values()
                    for record in entry.records
                )
            tmp.replace(self.path)

    def reset(self) -> None:
        with self._lock:
            self._latest = {}
            if self.path is not None and self.path.exists():
                self.path.unlink()


class WindowRun:
    """One epoch of one window: replays finished work, records new work."""

    def __init__(
        self, log: PartitionLog, window: str, epoch: str, prior: list[dict[str, Any]]
    ) -> None:
        self.window = window
        self.epoch = epoch
        self._log = log
        self._leaves = {
            str(r.get("partition")): r
            for r in prior
            if r.get("kind") == "leaf" and r.get("complete")
        }
        self._splits = {str(r.get("partition")): r for r in prior if r.get("kind") == "split"}

    @property
    def resumed(self) -> bool:
        return bool(self._leaves or self._splits)

    def completed_leaf(self, key: str) -> dict[str, Any] | None:
        return self._leaves.get(key)

    def split_plan(self, key: str) -> dict[str, Any] | None:
        return self._splits.get(key)

    def record_split(
        self, partition: Partition, children: Sequence[Partition], page: SearchPage
    ) -> None:
        self._log.append(
            self.window,
            self.epoch,
            {
                "kind": "split",
                "partition": partition.key,
                "expected_hits": page.nb_hits,
                "expected_exact": page.exact,
                "children": [child.to_json() for child in children],
            },
        )

    def record_leaf(self, record: LeafRecord) -> None:
        self._log.append(self.window, self.epoch, {"kind": "leaf", **asdict(record)})

    def finish(self, status: str, summary: CollectSummary, **extra: Any) -> None:
        self._log.append(
            self.window,
            self.epoch,
            {"kind": "end", "status": status, **summary.to_json(), **extra},
        )


LeafFn = Callable[[Partition, list[dict[str, Any]]], LeafUrls]


class PartitionCollector:
    """Recursive, resumable enumeration of ``base`` split along ``dimensions``."""

    def __init__(
        self,
        searcher: IndexSearcher,
        dimensions: Sequence[SplitDimension],
        *,
        base: str,
        retries: int = 1,
    ) -> None:
        self.searcher = searcher
        self.dimensions = tuple(dimensions)
        self.base = base
        self.retries = retries

    def collect(
        self,
        on_leaf: LeafFn,
        *,
        root: Partition | None = None,
        run: WindowRun | None = None,
    ) -> CollectSummary:
        """Visit every leaf under ``root``; raises ``PartitionQueryError`` on failure."""
        summary = CollectSummary()
        self._visit(root or Partition(), on_leaf, run, summary, is_root=True)
        return summary

    def _visit(
        self,
        partition: Partition,
        on_leaf: LeafFn,
        run: WindowRun | None,
        summary: CollectSummary,
        *,
        is_root: bool,
    ) -> None:
        if run is not None:
            done = run.completed_leaf(partition.key)
            if done is not None:
                if is_root:
                    summary.set_expected(done.get("expected_hits"), done.get("expected_exact"))
                summary.add_leaf(done, resumed=True)
                return
            plan = run.split_plan(partition.key)
            if plan is not None:
                if is_root:
                    summary.set_expected(plan.get("expected_hits"), plan.get("expected_exact"))
                for raw in plan.get("children") or ():
                    self._visit(Partition.from_json(raw), on_leaf, run, summary, is_root=False)
                return

        page = self._fetch(partition)
        attempts = 0
        while self._mismatch(page) and attempts < self.retries:
            attempts += 1
            page = self._fetch(partition)
        if is_root:
            summary.set_expected(page.nb_hits, page.exact)

        if self._overflows(page):
            children = self._split(partition, page)
            if children is not None:
                if run is not None:
                    run.record_split(partition, children, page)
                for child in children:
                    self._visit(child, on_leaf, run, summary, is_root=False)
                return
            self._leaf(partition, page, on_leaf, run, summary, reason="unsplittable")
            return
        self._leaf(
            partition,
            page,
            on_leaf,
            run,
            summary,
            reason="count_mismatch" if self._mismatch(page) else None,
        )

    def _fetch(self, partition: Partition) -> SearchPage:
        filters = partition.filters(self.base)
        page = self.searcher.page(filters)
        if page is None:
            raise PartitionQueryError(f"algolia query failed filters={filters}")
        return page

    def _overflows(self, page: SearchPage) -> bool:
        limit = self.searcher.max_hits
        return len(page.hits) >= limit or (page.exact and page.nb_hits > limit)

    def _mismatch(self, page: SearchPage) -> bool:
        return not self._overflows(page) and page.exact and len(page.hits) != page.nb_hits

    def _split(self, partition: Partition, page: SearchPage) -> list[Partition] | None:
        for dim in range(partition.dim, len(self.dimensions)):
            children = self.dimensions[dim].split(
                partition, dim, self.base, self.searcher, page.nb_hits
            )
            if children is not None and len(children) >= 2:
                return children
        return None

    def _leaf(
        self,
        partition: Partition,
        page: SearchPage,
        on_leaf: LeafFn,
        run: WindowRun | None,
        summary: CollectSummary,
        *,
        reason: str | None,
    ) -> None:
        urls = on_leaf(partition, page.hits)
        record = LeafRecord(
            partition=partition.key,
            filters=partition.filters(self.base),
            expected_hits=page.nb_hits,
            expected_exact=page.exact,
            retrieved_hits=len(page.hits),
            unique_urls=urls.unique,
            new_urls=urls.new,
            known_urls=max(0, urls.unique - urls.new),
            complete=reason is None,
            reason=reason,
        )
        if reason is not None:
            LOGGER.warning(
                "algolia partition incomplete reason=%s nbHits=%s retrieved=%s filters=%s",
                reason,
                page.nb_hits,
                len(page.hits),
                record.filters,
            )
        if run is not None:
            run.record_leaf(record)
        summary.add_leaf(asdict(record))


def collect_hits(
    collector: PartitionCollector, *, root: Partition | None = None
) -> tuple[list[dict[str, Any]], CollectSummary]:
    """Every hit under ``root`` in memory (no log, no resume)."""
    hits: list[dict[str, Any]] = []

    def on_leaf(_partition: Partition, page_hits: list[dict[str, Any]]) -> LeafUrls:
        hits.extend(page_hits)
        return LeafUrls(unique=len(page_hits))

    return hits, collector.collect(on_leaf, root=root)


__all__ = [
    "CHUNK_BUDGET",
    "MAX_HITS",
    "AlgoliaIndexSearcher",
    "CollectSummary",
    "FacetValues",
    "IndexSearcher",
    "LeafRecord",
    "LeafUrls",
    "NumericRange",
    "Partition",
    "PartitionCollector",
    "PartitionLog",
    "PartitionQueryError",
    "SearchPage",
    "SplitDimension",
    "WindowRun",
    "collect_hits",
    "facet_literal",
    "numeric_counts",
    "parse_page",
    "partition_log_path",
    "plan_chunks",
    "quote_facet",
    "range_clause",
]
