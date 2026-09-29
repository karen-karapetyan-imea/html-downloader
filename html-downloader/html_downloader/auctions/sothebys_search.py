"""Sotheby's discovery via the public site-search Algolia index (``bsp_dotcom_prod_en``).

``/en/search`` embeds a public key for a second Algolia app. Its index holds
~1.92M ``type:Lot`` records — current-platform lots, legacy lots not reachable
through ``/en/results`` and 1999–2004 sales that predate that listing — plus
``type:Auction`` landing pages. Hits carry the canonical page ``url``.

Enumeration (``algolia_partition``):

- Calendar-month ``endDate`` windows (epoch milliseconds) are the checkpoint
  unit, plus one window for records without ``endDate``.
- Filtered ``nbHits`` on this index is usually an estimate, so a partition is
  complete only on a short page (or, when Algolia flags the count exact, when
  every hit was retrieved).
- Full partitions split on ``endDate`` (down to one millisecond), then
  ``type`` → ``departments`` → ``lowEstimate`` → ``locations`` →
  ``auctionType`` → ``available`` → ``category``. A partition no field can
  shrink is recorded ``unsplittable`` and its window stays ``incomplete``.

Months that ended more than ``REOPEN_DAYS`` ago become ``done``; recent and
upcoming months stay ``open`` and are re-queried every run. An interrupted
month resumes at its unfinished partitions (``*_partitions.jsonl``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from html_downloader.auctions.algolia_partition import (
    CHUNK_BUDGET,
    MAX_HITS,
    AlgoliaIndexSearcher,
    FacetValues,
    NumericRange,
    Partition,
    PartitionCollector,
    PartitionLog,
    PartitionQueryError,
    QueryFn,
    SplitDimension,
    collect_hits,
    partition_log_path,
    quote_facet,
    range_clause,
)
from html_downloader.auctions.search_state import SaleSearchState
from html_downloader.auctions.sitemap_cache import (
    load_typed_cache_key_union,
    typed_cache_path,
)
from html_downloader.auctions.sothebys_algolia import (
    MAX_RETURN_NEW,
    SothebysAlgoliaClient,
)
from html_downloader.auctions.sothebys_legacy import GetFn, LegacyFetchError
from html_downloader.auctions.urls import (
    normalize_auction_url,
    sothebys_entity_from_url_path,
)
from html_downloader.auctions.window_expand import (
    CacheIngestor,
    WindowJob,
    expand_windows,
)
from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

SEARCH_PAGE_URL = "https://www.sothebys.com/en/search"
FIRST_YEAR = 1970
MONTHS_AHEAD = 24
REOPEN_DAYS = 90
NO_END_DATE_KEY = "no-end-date"
END_DATE_ATTR = "endDate"
ESTIMATE_ATTR = "lowEstimate"
MAX_END_MS = 1 << 45
ESTIMATE_RANGE = (0, 1 << 40)
HIT_ATTRIBUTES = ("url", "endDate")
FULL_TYPE_FILTER = "(type:Lot OR type:Auction)"
DEFAULT_SITE_SEARCH_WORKERS = 4

_CONFIG_PATTERNS = {
    field: re.compile(rf"{name}\s*[:=]\s*['\"]([^'\"]+)['\"]")
    for field, name in (
        ("app_id", "ALGOLIA_SEARCH_APP_ID"),
        ("api_key", "ALGOLIA_SEARCH_API_KEY"),
        ("index", "ALGOLIA_SEARCH_INDEX"),
    )
}


@dataclass(frozen=True, slots=True)
class SearchIndexConfig:
    app_id: str
    api_key: str
    index: str

    @property
    def host(self) -> str:
        return f"https://{self.app_id.lower()}-dsn.algolia.net"


DEFAULT_SEARCH_CONFIG = SearchIndexConfig(
    app_id="O28SY4Q7WU",
    api_key="e732e65c70ebf8b51d4e2f922b536496",
    index="bsp_dotcom_prod_en",
)


def parse_search_config(body: str) -> SearchIndexConfig | None:
    """Algolia app id / key / index embedded in the ``/en/search`` HTML."""
    found: dict[str, str] = {}
    for field, pattern in _CONFIG_PATTERNS.items():
        match = pattern.search(body)
        if match is None:
            return None
        found[field] = match.group(1).strip()
    return SearchIndexConfig(**found)


def fetch_search_config(get: GetFn) -> SearchIndexConfig:
    """Live config from the search page; falls back to the known public config."""
    try:
        body = get(SEARCH_PAGE_URL)
    except (LegacyFetchError, OSError) as exc:
        LOGGER.warning("sothebys site search page fetch failed: %s", exc)
        body = None
    config = parse_search_config(body) if body else None
    if config is None:
        LOGGER.warning("sothebys site search config not found; using built-in defaults")
        return DEFAULT_SEARCH_CONFIG
    return config


def make_site_search_client(
    get: GetFn, *, delay: float
) -> tuple[SothebysAlgoliaClient, str]:
    """Algolia client for the site-search app (key re-read from the page on 401/403) + index."""
    config = fetch_search_config(get)
    LOGGER.info("sothebys site search app=%s index=%s", config.app_id, config.index)
    client = SothebysAlgoliaClient(
        delay=delay,
        api_key=config.api_key,
        fetch_key=lambda: fetch_search_config(get).api_key,
        app_id=config.app_id,
        host=config.host,
    )
    return client, config.index


@dataclass(frozen=True, slots=True)
class SearchWindow:
    """Inclusive ``endDate`` range in epoch ms; ``start_ms is None`` = records without it."""

    key: str
    start_ms: int | None
    end_ms: int | None

    def is_settled(self, now: datetime) -> bool:
        if self.end_ms is None:
            return False
        cutoff = now - timedelta(days=REOPEN_DAYS)
        return self.end_ms < int(cutoff.timestamp() * 1000)


def _month_start_ms(year: int, month: int) -> int:
    return int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp() * 1000)


def month_windows(now: datetime, *, first_year: int = FIRST_YEAR) -> list[SearchWindow]:
    """One window per calendar month from ``first_year`` to ``MONTHS_AHEAD`` past ``now``."""
    last_index = now.year * 12 + now.month - 1 + MONTHS_AHEAD
    windows: list[SearchWindow] = []
    for index in range(first_year * 12, last_index + 1):
        year, month = divmod(index, 12)
        next_year, next_month = divmod(index + 1, 12)
        windows.append(
            SearchWindow(
                key=f"{year:04d}-{month + 1:02d}",
                start_ms=_month_start_ms(year, month + 1),
                end_ms=_month_start_ms(next_year, next_month + 1) - 1,
            )
        )
    return windows


def all_windows(now: datetime, *, first_year: int = FIRST_YEAR) -> list[SearchWindow]:
    return [
        *month_windows(now, first_year=first_year),
        SearchWindow(NO_END_DATE_KEY, None, None),
    ]


def build_search_filter(departments: Sequence[str] | None = None) -> str:
    """Lots + auctions (full archive), or art lots tagged with any of ``departments``."""
    names = list(dict.fromkeys(n.strip() for n in departments or () if n and n.strip()))
    if not names:
        return FULL_TYPE_FILTER
    clauses = " OR ".join(f"departments:{quote_facet(name)}" for name in names)
    return f"type:Lot AND ({clauses})"


class SiteSearcher(AlgoliaIndexSearcher):
    """The site-search index: ``url`` + ``endDate`` hits, no ``distinct`` grouping."""

    def __init__(self, query: QueryFn, index: str) -> None:
        super().__init__(
            query,
            index,
            attributes=HIT_ATTRIBUTES,
            extra_params={"distinct": "false", "analytics": "false"},
            max_hits=MAX_HITS,
        )


_TYPE_DIM = 1


def site_search_dimensions() -> tuple[SplitDimension, ...]:
    """Split order (every attribute is a facet of ``bsp_dotcom_prod_en``)."""
    return (
        NumericRange(END_DATE_ATTR, 0, MAX_END_MS, budget=CHUNK_BUDGET),
        FacetValues("type"),
        FacetValues("departments"),
        NumericRange(ESTIMATE_ATTR, *ESTIMATE_RANGE, budget=CHUNK_BUDGET),
        FacetValues("locations"),
        FacetValues("auctionType"),
        FacetValues("available"),
        FacetValues("category"),
    )


def window_root(window: SearchWindow) -> Partition:
    """Root partition: the month's ``endDate`` range, or records without one."""
    if window.start_ms is None or window.end_ms is None:
        # Also catches negative / out-of-range endDate values.
        clause = f"NOT {END_DATE_ATTR}:0 TO {MAX_END_MS}"
        return Partition(parts=((END_DATE_ATTR, clause),), dim=_TYPE_DIM)
    lo, hi = window.start_ms, window.end_ms + 1
    return Partition(
        parts=((END_DATE_ATTR, range_clause(END_DATE_ATTR, lo, hi)),),
        dim=0,
        ranges=((END_DATE_ATTR, lo, hi),),
    )


def collect_window(
    searcher: SiteSearcher, base: str, window: SearchWindow
) -> list[dict[str, Any]] | None:
    """Every hit of one window in memory (no checkpoint); None on failure."""
    collector = PartitionCollector(searcher, site_search_dimensions(), base=base)
    try:
        hits, summary = collect_hits(collector, root=window_root(window))
    except PartitionQueryError as exc:
        LOGGER.warning("sothebys site search window=%s failed: %s", window.key, exc)
        return None
    if not summary.complete:
        LOGGER.warning(
            "sothebys site search window=%s incomplete: %s", window.key, summary.incomplete
        )
    return hits


def _ms_to_date(value: Any) -> str | None:
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    if millis <= 0:
        return None
    return datetime.fromtimestamp(millis / 1000, tz=timezone.utc).date().isoformat()


def hit_to_entry(hit: dict[str, Any]) -> SitemapEntry | None:
    """Map a site-search hit to a canonical lot / sale entry via its ``url``."""
    raw = hit.get("url")
    if not isinstance(raw, str) or not raw:
        return None
    url = normalize_auction_url(raw)
    if not url:
        return None
    key = sothebys_entity_from_url_path(url)
    if key is None or key[0] not in ("lot", "sale"):
        return None
    return SitemapEntry(
        url=url,
        lastmod=_ms_to_date(hit.get(END_DATE_ATTR)),
        entity_type=key[0],
        entity_id=key[1],
    )


def lot_cache_path(state_path: Path) -> Path:
    return typed_cache_path(state_path)


def window_job(window: SearchWindow, base: str, now: datetime) -> WindowJob:
    return WindowJob(
        key=window.key,
        base=base,
        mapper=hit_to_entry,
        settled=window.is_settled(now),
        root=window_root(window),
    )


def expand_site_search(
    *,
    state_path: Path,
    query: QueryFn,
    index: str = DEFAULT_SEARCH_CONFIG.index,
    departments: Sequence[str] | None = None,
    workers: int = DEFAULT_SITE_SEARCH_WORKERS,
    force: bool = False,
    max_windows: int | None = None,
    known_cache_paths: Sequence[Path] = (),
    now: datetime | None = None,
) -> list[SitemapEntry]:
    """
    Enumerate pending windows of the site-search index; append new entities to JSONL.

    Keys already in this cache or ``known_cache_paths`` (other sources) are
    skipped, so every Sotheby's cache stays disjoint. Returns a capped list of
    entities first seen this run.
    """
    label = "sothebys site search art" if departments else "sothebys site search"
    cache_path = lot_cache_path(state_path)
    state = SaleSearchState(state_path)
    log = PartitionLog(partition_log_path(state_path))
    if force:
        state.reset()
        log.reset()
        if cache_path.exists():
            cache_path.unlink()
        LOGGER.info("%s force: state, partition log and cache cleared", label)

    moment = now or datetime.now(timezone.utc)
    todo = [w for w in all_windows(moment) if state.status(w.key) != "done"]
    if max_windows is not None and max_windows >= 0:
        todo = todo[:max_windows]
    if not todo:
        LOGGER.info("%s nothing to expand state=%s", label, state.counts())
        state.flush()
        return []
    seen = load_typed_cache_key_union([cache_path, *known_cache_paths], label=label)
    base = build_search_filter(departments)
    LOGGER.info(
        "%s windows todo=%s known=%s workers=%s state=%s",
        label,
        len(todo),
        len(seen),
        workers,
        state.counts(),
    )

    ingestor = CacheIngestor(cache_path, seen, max_return=MAX_RETURN_NEW)
    totals = expand_windows(
        label=label,
        jobs=[window_job(window, base, moment) for window in todo],
        searcher=SiteSearcher(query, index),
        dimensions=site_search_dimensions(),
        state=state,
        log=log,
        ingestor=ingestor,
        workers=workers,
        progress_every=12,
    )
    LOGGER.info(
        "%s complete done=%s open=%s incomplete=%s failed=%s expected=%s retrieved=%s "
        "partitions=%s incomplete_partitions=%s new=%s returned=%s known=%s",
        label,
        totals.done,
        totals.open,
        totals.incomplete,
        totals.failed,
        totals.expected,
        totals.retrieved,
        totals.partitions,
        totals.incomplete_partitions,
        ingestor.new,
        len(ingestor.new_returned),
        len(seen),
    )
    return ingestor.new_returned


__all__ = [
    "DEFAULT_SEARCH_CONFIG",
    "DEFAULT_SITE_SEARCH_WORKERS",
    "SearchIndexConfig",
    "SearchWindow",
    "SiteSearcher",
    "all_windows",
    "build_search_filter",
    "collect_window",
    "expand_site_search",
    "fetch_search_config",
    "hit_to_entry",
    "lot_cache_path",
    "make_site_search_client",
    "month_windows",
    "parse_search_config",
    "site_search_dimensions",
    "window_job",
    "window_root",
]
