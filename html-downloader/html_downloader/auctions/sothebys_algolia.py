"""Sotheby's discovery via the public Algolia index behind sothebys.com.

Sotheby's XML sitemaps list no lots or auctions, so discovery is API-only:

1. ``POST clientapi.prod.sothelabs.com/graphql`` → ``algoliaSearchKey`` with
   empty ``requestedFilters`` returns a global secured search key.
2. ``prod_auctions`` (``isTestRecord:false``) lists every auction on the
   current platform (~2019 onward), one query per ``slugYear``.
3. ``prod_lots`` is queried per auction (``auctionId:"…" AND isTestLot:false``,
   plus an optional art facet filter). About 45% of the index is test data, so
   the ``isTestLot:false`` filter is mandatory.

Algolia caps every query at 1,000 hits, so ``algolia_partition`` splits an
overflowing auction recursively (``sessionId`` → ``lotNr`` → ``subLotNr`` →
``lowEstimate`` → ``withdrawn`` → ``lotState``). Per-auction ``nbHits`` is
exact, so an auction is complete only when every hit was retrieved; the state
entry records expected / retrieved hits, URLs and partitions.

Closed auctions are immutable: they are marked ``done`` and never re-queried
unless ``force`` is set. Opened / Published auctions are marked ``open`` and
re-queried on every run so lots are picked up as they are published.
Auctions with an incomplete partition are marked ``incomplete`` and retried.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Self
from urllib.parse import urlencode

import httpx

from html_downloader.auctions.algolia_partition import (
    MAX_HITS,
    AlgoliaIndexSearcher,
    FacetValues,
    NumericRange,
    PartitionCollector,
    PartitionLog,
    PartitionQueryError,
    SplitDimension,
    collect_hits,
    partition_log_path,
    quote_facet,
)
from html_downloader.auctions.search_state import SaleSearchState
from html_downloader.auctions.sitemap_cache import (
    load_typed_cache_key_union,
    typed_cache_path,
)
from html_downloader.auctions.urls import (
    build_sothebys_sale_url,
    normalize_auction_url,
    sothebys_entity_from_url_path,
)
from html_downloader.auctions.window_expand import (
    CacheIngestor,
    WindowJob,
    expand_windows,
    unique_entries,
)
from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

ALGOLIA_APP_ID = "KAR1UEUPJD"
ALGOLIA_HOST = "https://kar1ueupjd-dsn.algolia.net"
LOTS_INDEX = "prod_lots"
AUCTIONS_INDEX = "prod_auctions"
GRAPHQL_URL = "https://clientapi.prod.sothelabs.com/graphql"
KEY_QUERY = (
    "query AlgoliaSearchKeyQuery($filters: [KeyValuePair!]) "
    "{ algoliaSearchKey(requestedFilters: $filters) { key } }"
)
SITE_ROOT = "https://www.sothebys.com"

REAL_LOT_FILTER = "isTestLot:false"
TEST_LOT_FILTER = "isTestLot:true"
REAL_AUCTION_FILTER = "isTestRecord:false"
FIRST_AUCTION_YEAR = 2015
LOT_NR_RANGE = (0, 1 << 20)
SMALL_INT_RANGE = (0, 1 << 16)
ESTIMATE_RANGE = (0, 1 << 40)
START_DATE_RANGE = (0, 1 << 33)
DEFAULT_SEARCH_DELAY = 0.4
DEFAULT_SEARCH_WORKERS = 2
MAX_RETURN_NEW = 50_000

LOT_ATTRIBUTES = ("slug", "lotNr")
AUCTION_ATTRIBUTES = ("slugYear", "slugName", "state", "auctionDates")

# Matched against both ``departments`` and ``objectTypes`` (OR). Department
# tagging covers ~183k art lots, objectTypes ~92k; the union is ~202k.
ART_FACETS: tuple[str, ...] = (
    "Contemporary Art",
    "Impressionist & Modern Art",
    "Old Master Paintings",
    "Old Master Drawings",
    "Prints",
    "Photographs",
    "19th Century European Paintings",
    "American Art",
    "Modern British & Irish Art",
    "Modern & Contemporary South Asian Art",
    "Contemporary Arab, Iranian & Turkish Art",
    "African Modern & Contemporary Art",
    "Victorian, Pre-Raphaelite & British Impressionist Art",
    "Modern Art | Asia",
    "Contemporary Asian Art",
    "Latin American Art",
    "Modern Latin American Art",
    "Contemporary Latin American Art",
    "British Watercolours & Drawings 1550-1850",
    "Chinese Paintings – Modern",
    "Chinese Paintings – Classical",
    "Modern & Contemporary Southeast Asian Art",
    "Russian Art",
    "General Fine Arts",
    "Painting",
    "Work on Paper",
    "Print",
    "Photograph",
    "Sculpture",
    "Mixed Media",
)
ART_FACET_ATTRIBUTES = ("departments", "objectTypes")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

QueryFn = Callable[[str, dict[str, Any]], dict[str, Any] | None]
KeyFetchFn = Callable[[], str]


@dataclass(frozen=True, slots=True)
class SothebysAuction:
    auction_id: str
    slug_year: str
    slug_name: str
    state: str
    end_date: str | None

    @property
    def key(self) -> str:
        return self.auction_id

    @property
    def is_closed(self) -> bool:
        return self.state.lower() == "closed"

    @property
    def url(self) -> str:
        return build_sothebys_sale_url(self.slug_year, self.slug_name)

    def sale_entry(self) -> SitemapEntry:
        return SitemapEntry(
            url=self.url,
            lastmod=self.end_date,
            entity_type="sale",
            entity_id=f"{self.slug_year}/{self.slug_name.lower()}",
        )


def build_art_filter(names: Sequence[str]) -> str:
    """OR every name across ``departments`` and ``objectTypes`` (deduplicated)."""
    unique = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
    if not unique:
        raise ValueError("art facet names must contain at least one non-empty value")
    clauses = [
        f"{attr}:{quote_facet(name)}" for name in unique for attr in ART_FACET_ATTRIBUTES
    ]
    return "(" + " OR ".join(clauses) + ")"


def lot_filters(auction: SothebysAuction, extra_filter: str | None = None) -> str:
    base = f"auctionId:{quote_facet(auction.auction_id)} AND {REAL_LOT_FILTER}"
    return f"{base} AND {extra_filter}" if extra_filter else base


def _epoch_to_date(value: Any) -> str | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()


def auction_from_hit(hit: dict[str, Any]) -> SothebysAuction | None:
    auction_id = str(hit.get("objectID") or "").strip()
    year = str(hit.get("slugYear") or "").strip()
    name = str(hit.get("slugName") or "").strip()
    if not auction_id or not year.isdigit() or len(year) != 4 or not name:
        return None
    dates = hit.get("auctionDates") or {}
    end_date = None
    if isinstance(dates, dict):
        end_date = next(
            (
                d
                for d in (_epoch_to_date(dates.get(k)) for k in ("endDate", "startDate", "openDate"))
                if d
            ),
            None,
        )
    return SothebysAuction(
        auction_id=auction_id,
        slug_year=year,
        slug_name=name,
        state=str(hit.get("state") or ""),
        end_date=end_date,
    )


def hit_to_entry(hit: dict[str, Any], auction: SothebysAuction) -> SitemapEntry | None:
    """Map a ``prod_lots`` hit to a canonical lot entry (``slug`` is the URL path)."""
    slug = hit.get("slug")
    if not isinstance(slug, str) or not slug.startswith("/"):
        return None
    url = normalize_auction_url(SITE_ROOT + slug)
    if not url:
        return None
    key = sothebys_entity_from_url_path(url)
    if key is None or key[0] != "lot":
        return None
    return SitemapEntry(url=url, lastmod=auction.end_date, entity_type="lot", entity_id=key[1])


class SothebysAlgoliaClient:
    """
    Thread-safe Algolia client; fetches the key lazily and refreshes it on 401/403.

    Defaults target the current-platform app (key from GraphQL); ``app_id`` /
    ``host`` / ``fetch_key`` point it at another Sotheby's Algolia app.
    """

    def __init__(
        self,
        *,
        delay: float = DEFAULT_SEARCH_DELAY,
        client: httpx.Client | None = None,
        retries: int = 5,
        api_key: str | None = None,
        fetch_key: KeyFetchFn | None = None,
        app_id: str = ALGOLIA_APP_ID,
        host: str = ALGOLIA_HOST,
    ) -> None:
        self.delay = delay
        self.retries = retries
        self.app_id = app_id
        self.host = host.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        self._fetch_key = fetch_key or self._fetch_key_from_graphql
        self._key = api_key
        self._key_lock = threading.Lock()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _fetch_key_from_graphql(self) -> str:
        resp = self._client.post(
            GRAPHQL_URL,
            json={"query": KEY_QUERY, "variables": {"filters": []}},
            headers={"Origin": SITE_ROOT, "Referer": SITE_ROOT + "/"},
        )
        resp.raise_for_status()
        key = ((resp.json().get("data") or {}).get("algoliaSearchKey") or {}).get("key")
        if not isinstance(key, str) or not key:
            raise RuntimeError("sothebys GraphQL returned no algoliaSearchKey")
        return key

    def api_key(self) -> str:
        with self._key_lock:
            if self._key is None:
                self._key = self._fetch_key()
                LOGGER.info("sothebys algolia search key fetched")
            return self._key

    def _refresh_key(self, stale: str) -> None:
        # Only the first thread to see a stale key refetches it.
        with self._key_lock:
            if self._key == stale:
                self._key = self._fetch_key()
                LOGGER.info("sothebys algolia search key refreshed")

    def query(self, index: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """One Algolia query; None on unrecoverable failure."""
        url = f"{self.host}/1/indexes/{index}/query"
        body = {"params": urlencode({k: v for k, v in params.items() if v is not None})}
        if self.delay > 0:
            time.sleep(self.delay * random.uniform(0.8, 1.2))
        refreshed = False
        for attempt in range(self.retries):
            try:
                key = self.api_key()
                resp = self._client.post(
                    url,
                    json=body,
                    headers={
                        "X-Algolia-Application-Id": self.app_id,
                        "X-Algolia-API-Key": key,
                        "Origin": SITE_ROOT,
                        "Referer": SITE_ROOT + "/",
                    },
                )
            except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                LOGGER.warning("sothebys algolia transport error index=%s: %s", index, exc)
                time.sleep(2 ** (attempt + 1))
                continue
            if resp.status_code in (401, 403) and not refreshed:
                refreshed = True
                try:
                    self._refresh_key(key)
                except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                    LOGGER.warning("sothebys algolia key refresh failed: %s", exc)
                continue
            if resp.status_code in (401, 403, 429) or resp.status_code >= 500:
                wait = random.uniform(5, 15) * (attempt + 1)
                LOGGER.warning(
                    "sothebys algolia HTTP %s index=%s — sleeping %.0fs",
                    resp.status_code,
                    index,
                    wait,
                )
                time.sleep(wait)
                continue
            if resp.status_code >= 400:
                LOGGER.error("sothebys algolia HTTP %s index=%s", resp.status_code, index)
                return None
            try:
                data = resp.json()
            except ValueError:
                LOGGER.warning("sothebys algolia non-JSON body index=%s", index)
                time.sleep(2 ** (attempt + 1))
                continue
            return data if isinstance(data, dict) else None
        LOGGER.error("sothebys algolia failed after %s attempts index=%s", self.retries, index)
        return None


def lot_dimensions() -> tuple[SplitDimension, ...]:
    """Split order inside one auction (all attributes exist on ``prod_lots``)."""
    return (
        NumericRange("sessionId", *SMALL_INT_RANGE),
        NumericRange("lotNr", *LOT_NR_RANGE),
        NumericRange("subLotNr", *SMALL_INT_RANGE),
        NumericRange("lowEstimate", *ESTIMATE_RANGE),
        FacetValues("withdrawn"),
        FacetValues("lotState"),
    )


def lots_searcher(query: QueryFn) -> AlgoliaIndexSearcher:
    return AlgoliaIndexSearcher(query, LOTS_INDEX, attributes=LOT_ATTRIBUTES, max_hits=MAX_HITS)


def list_auctions(
    query: QueryFn,
    *,
    first_year: int = FIRST_AUCTION_YEAR,
    last_year: int | None = None,
) -> list[SothebysAuction]:
    """Every real auction, newest first (one ``slugYear`` query per year)."""
    end = last_year if last_year is not None else datetime.now(timezone.utc).year + 1
    searcher = AlgoliaIndexSearcher(
        query, AUCTIONS_INDEX, attributes=AUCTION_ATTRIBUTES, max_hits=MAX_HITS
    )
    dimensions = (NumericRange("auctionDates.startDate", *START_DATE_RANGE),)
    auctions: dict[str, SothebysAuction] = {}
    for year in range(first_year, end + 1):
        collector = PartitionCollector(
            searcher,
            dimensions,
            base=f"{REAL_AUCTION_FILTER} AND slugYear:{quote_facet(str(year))}",
        )
        try:
            hits, summary = collect_hits(collector)
        except PartitionQueryError as exc:
            raise RuntimeError(f"sothebys auction listing failed for year={year}") from exc
        if not summary.complete:
            LOGGER.error(
                "sothebys auction listing year=%s incomplete: %s", year, summary.incomplete
            )
        for hit in hits:
            auction = auction_from_hit(hit)
            if auction is not None:
                auctions[auction.key] = auction
    ordered = sorted(
        auctions.values(),
        key=lambda a: (a.end_date or "", a.slug_year, a.slug_name),
        reverse=True,
    )
    LOGGER.info("sothebys auctions collected=%s years=%s-%s", len(ordered), first_year, end)
    return ordered


def search_auction_lots(
    auction: SothebysAuction,
    *,
    query: QueryFn,
    extra_filter: str | None = None,
) -> tuple[list[SitemapEntry], int] | None:
    """All real lots of one auction in memory (no checkpoint). None on failure."""
    collector = PartitionCollector(
        lots_searcher(query), lot_dimensions(), base=lot_filters(auction, extra_filter)
    )
    try:
        hits, _summary = collect_hits(collector)
    except PartitionQueryError as exc:
        LOGGER.warning("sothebys auction=%s lot query failed: %s", auction.key, exc)
        return None
    return unique_entries(hits, lambda hit: hit_to_entry(hit, auction)), len(hits)


def lot_cache_path(state_path: Path) -> Path:
    return typed_cache_path(state_path)


def expand_lots_from_algolia(
    *,
    state_path: Path,
    auctions: Sequence[SothebysAuction],
    query: QueryFn,
    art_filter: str | None = None,
    include_sales: bool = True,
    workers: int = DEFAULT_SEARCH_WORKERS,
    force: bool = False,
    max_sales: int | None = None,
    known_cache_paths: Sequence[Path] = (),
) -> list[SitemapEntry]:
    """
    Query lots for every pending auction; append new entities to the JSONL cache.

    Returns a capped list of entities first seen this run. Closed, complete
    auctions become ``done``; others become ``open`` / ``incomplete`` and are
    re-queried next run. Keys already in ``known_cache_paths`` (other sources'
    caches) are skipped. An interrupted auction resumes at its unfinished
    partitions (``*_partitions.jsonl``).
    """
    label = "sothebys art" if art_filter else "sothebys"
    cache_path = lot_cache_path(state_path)
    state = SaleSearchState(state_path)
    log = PartitionLog(partition_log_path(state_path))
    if force:
        state.reset()
        log.reset()
        if cache_path.exists():
            cache_path.unlink()
        LOGGER.info("%s force: state, partition log and cache cleared", label)

    todo = [auction for auction in auctions if state.status(auction.key) != "done"]
    # Closed auctions first (stable: keeps caller order within each group), so
    # ``max_sales`` is not spent on upcoming auctions that have no lots yet.
    todo.sort(key=lambda auction: not auction.is_closed)
    if max_sales is not None and max_sales >= 0:
        todo = todo[:max_sales]
    seen = load_typed_cache_key_union([cache_path, *known_cache_paths], label=label)
    LOGGER.info(
        "%s algolia auctions=%s todo=%s known=%s workers=%s art_filter=%s state=%s",
        label,
        len(auctions),
        len(todo),
        len(seen),
        workers,
        bool(art_filter),
        state.counts(),
    )
    if not todo:
        state.flush()
        return []

    ingestor = CacheIngestor(cache_path, seen, max_return=MAX_RETURN_NEW)
    totals = expand_windows(
        label=f"{label} algolia",
        jobs=[auction_job(auction, art_filter, include_sales=include_sales) for auction in todo],
        searcher=lots_searcher(query),
        dimensions=lot_dimensions(),
        state=state,
        log=log,
        ingestor=ingestor,
        workers=workers,
        progress_every=250,
    )
    LOGGER.info(
        "%s algolia complete done=%s open=%s incomplete=%s failed=%s expected=%s "
        "retrieved=%s partitions=%s incomplete_partitions=%s new=%s returned=%s known=%s",
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


def auction_job(
    auction: SothebysAuction, art_filter: str | None, *, include_sales: bool
) -> WindowJob:
    return WindowJob(
        key=auction.key,
        base=lot_filters(auction, art_filter),
        mapper=lambda hit: hit_to_entry(hit, auction),
        settled=auction.is_closed,
        extra_entries=(auction.sale_entry(),) if include_sales else (),
        fields={"url": auction.url},
    )


__all__ = [
    "ALGOLIA_APP_ID",
    "ART_FACETS",
    "AUCTIONS_INDEX",
    "DEFAULT_SEARCH_DELAY",
    "DEFAULT_SEARCH_WORKERS",
    "LOTS_INDEX",
    "MAX_HITS",
    "REAL_LOT_FILTER",
    "TEST_LOT_FILTER",
    "SothebysAlgoliaClient",
    "SothebysAuction",
    "auction_from_hit",
    "auction_job",
    "build_art_filter",
    "expand_lots_from_algolia",
    "hit_to_entry",
    "list_auctions",
    "lot_cache_path",
    "lot_dimensions",
    "lot_filters",
    "lots_searcher",
    "quote_facet",
    "search_auction_lots",
]
