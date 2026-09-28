"""Christie's art-only discovery via the public ``lotsearch`` JSON API.

Per past sale (from the auction sitemaps):
  GET /api/discoverywebsite/auctionpages/lotsearch
      ?language=en&salenumber=N&saleroomcode=ROOM&page=P&pagesize=84
      &filterids=CoaCategories{Paintings}|CoaCategories{Photographs}|...

``|`` ORs facet values server-side. Facet values must keep the site's own
encoding (``Drawings+%26+Watercolors``); httpx then URL-encodes the whole
parameter once. Legacy online sales (page ``sale_id=-1``) return 0 hits —
full sitemap mode still covers their lots.

Past sales are immutable: a sale marked ``done`` is never re-queried unless
``force`` is set. The typed JSONL cache is the source of truth for URLs.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Self
from urllib.parse import quote_plus

import httpx

from html_downloader.auctions.christies import ChristiesSale
from html_downloader.auctions.sitemap_cache import (
    append_typed_cache,
    load_typed_cache_keys,
    typed_cache_path,
)
from html_downloader.auctions.urls import build_christies_lot_url
from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

LOTSEARCH_URL = "https://www.christies.com/api/discoverywebsite/auctionpages/lotsearch"
PAGE_SIZE = 84  # server-side cap; larger values are silently clamped
MAX_PAGES_PER_SALE = 100
DEFAULT_SEARCH_DELAY = 0.4
DEFAULT_SEARCH_WORKERS = 2
MAX_RETURN_NEW = 50_000
CATEGORY_FACET = "CoaCategories"

# Item Category facet values in site encoding, harvested from sale-page facets.
# Other observed values: Furniture+%26+Lighting, Books+%26+Manuscripts, Watches,
# Ancient+Art+%26+Antiquities, Memorabilia, All+other+categories+of+objects.
ART_CATEGORY_FACETS: tuple[str, ...] = (
    "Paintings",
    "Drawings+%26+Watercolors",
    "Prints+%26+Multiples",
    "Photographs",
    "Sculptures%2c+Statues+%26+Figures",
)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

SearchFn = Callable[[ChristiesSale, str, int], dict[str, Any] | None]


def facet_filter_id(category: str) -> str:
    """
    Build one ``CoaCategories{...}`` filter id.

    Accepts a human label (``"Prints & Multiples"``), the site encoding
    (``"Prints+%26+Multiples"``) or a full ``CoaCategories{...}`` id.
    """
    text = category.strip()
    if not text:
        raise ValueError("category must be non-empty")
    if text.startswith(f"{CATEGORY_FACET}{{") and text.endswith("}"):
        return text
    value = quote_plus(text) if (" " in text or "&" in text) else text
    return f"{CATEGORY_FACET}{{{value}}}"


def build_filterids(categories: Sequence[str]) -> str:
    """OR facet ids with ``|`` (deduplicated, order preserved)."""
    ids = list(dict.fromkeys(facet_filter_id(c) for c in categories if c and c.strip()))
    if not ids:
        raise ValueError("categories must contain at least one non-empty name")
    return "|".join(ids)


def lot_to_entry(lot: dict[str, Any], sale: ChristiesSale) -> SitemapEntry | None:
    """Map a lotsearch hit to a canonical lot entry (online ``/sso`` URLs included)."""
    object_id = str(lot.get("object_id") or "").strip()
    if not object_id.isdigit():
        return None
    date = str(lot.get("end_date") or lot.get("start_date") or "")[:10]
    lastmod = date if len(date) == 10 else sale.lastmod
    return SitemapEntry(
        url=build_christies_lot_url(object_id),
        lastmod=lastmod,
        entity_type="lot",
        entity_id=object_id,
    )


class SaleSearchState:
    """Per-sale checkpoint (``done`` / ``failed``) with batched atomic saves."""

    _SAVE_EVERY = 100

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._dirty = 0
        self._sales: dict[str, dict[str, Any]] = {}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("sales"), dict):
                    self._sales = loaded["sales"]
            except (OSError, json.JSONDecodeError) as exc:
                LOGGER.warning("could not load christies search state: %s", exc)

    def status(self, key: str) -> str | None:
        entry = self._sales.get(key)
        return entry.get("status") if entry else None

    def mark(self, key: str, status: str, *, hits: int = 0, written: int = 0) -> None:
        with self._lock:
            self._sales[key] = {"status": status, "hits": hits, "written": written}
            self._dirty += 1
            if self._dirty >= self._SAVE_EVERY:
                self._save_unlocked()

    def reset(self) -> None:
        with self._lock:
            self._sales = {}
            self._save_unlocked()

    def flush(self) -> None:
        with self._lock:
            self._save_unlocked()

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for entry in self._sales.values():
            status = str(entry.get("status"))
            out[status] = out.get(status, 0) + 1
        return out

    def _save_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({"sales": self._sales}, indent=1) + "\n", encoding="utf-8")
        tmp.replace(self.path)
        self._dirty = 0


class LotSearchClient:
    """Synchronous lotsearch client (thread-safe; no proxies required)."""

    def __init__(
        self,
        *,
        delay: float = DEFAULT_SEARCH_DELAY,
        client: httpx.Client | None = None,
        retries: int = 5,
    ) -> None:
        self.delay = delay
        self.retries = retries
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def search(self, sale: ChristiesSale, filterids: str, page: int) -> dict[str, Any] | None:
        """One lotsearch page; None on unrecoverable failure."""
        params = {
            "language": "en",
            "salenumber": sale.sale_number,
            "saleroomcode": sale.sale_room_code.upper(),
            "page": str(page),
            "pagesize": str(PAGE_SIZE),
            "filterids": filterids,
        }
        if self.delay > 0:
            time.sleep(self.delay * random.uniform(0.8, 1.2))
        for attempt in range(self.retries):
            try:
                resp = self._client.get(LOTSEARCH_URL, params=params)
            except httpx.HTTPError as exc:
                LOGGER.warning("christies lotsearch transport error sale=%s: %s", sale.key, exc)
                time.sleep(2 ** (attempt + 1))
                continue
            if resp.status_code in (403, 429, 503):
                wait = random.uniform(5, 15) * (attempt + 1)
                LOGGER.warning(
                    "christies lotsearch HTTP %s sale=%s — sleeping %.0fs",
                    resp.status_code,
                    sale.key,
                    wait,
                )
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                time.sleep(2 ** (attempt + 1))
                continue
            if resp.status_code >= 400:
                LOGGER.error("christies lotsearch HTTP %s sale=%s", resp.status_code, sale.key)
                return None
            try:
                data = resp.json()
            except ValueError:
                LOGGER.warning("christies lotsearch non-JSON body sale=%s", sale.key)
                time.sleep(2 ** (attempt + 1))
                continue
            return data if isinstance(data, dict) else None
        LOGGER.error("christies lotsearch failed after %s attempts sale=%s", self.retries, sale.key)
        return None


def search_sale(
    sale: ChristiesSale,
    *,
    filterids: str,
    search: SearchFn,
) -> tuple[list[SitemapEntry], int] | None:
    """Paginate one sale. Returns (entries, total_hits) or None on failure."""
    entries: list[SitemapEntry] = []
    total = 0
    for page in range(1, MAX_PAGES_PER_SALE + 1):
        data = search(sale, filterids, page)
        if data is None:
            return None
        lots = data.get("lots") or []
        if not isinstance(lots, list):
            lots = []
        try:
            total = int(data.get("total_hits_filtered") or 0)
        except (TypeError, ValueError):
            total = 0
        entries.extend(e for e in (lot_to_entry(lot, sale) for lot in lots) if e is not None)
        if not lots or page * PAGE_SIZE >= total:
            break
    return entries, total


def lot_cache_path(state_path: Path) -> Path:
    return typed_cache_path(state_path)


def expand_lots_from_lotsearch(
    *,
    state_path: Path,
    sales: Sequence[ChristiesSale],
    categories: Sequence[str] = ART_CATEGORY_FACETS,
    workers: int = DEFAULT_SEARCH_WORKERS,
    delay: float = DEFAULT_SEARCH_DELAY,
    force: bool = False,
    max_sales: int | None = None,
    search: SearchFn | None = None,
    client: httpx.Client | None = None,
) -> list[SitemapEntry]:
    """
    Query art lots for every pending sale; append new lots to the JSONL cache.

    Returns a capped list of lots first seen this run. A sale with zero art
    hits is ``done`` (not failed).
    """
    filterids = build_filterids(categories)
    cache_path = lot_cache_path(state_path)
    state = SaleSearchState(state_path)
    if force:
        state.reset()
        if cache_path.exists():
            cache_path.unlink()
        LOGGER.info("christies lotsearch force: state and cache cleared")

    todo = [sale for sale in sales if state.status(sale.key) != "done"]
    if max_sales is not None and max_sales >= 0:
        todo = todo[:max_sales]
    seen = load_typed_cache_keys(cache_path, label="christies art")
    LOGGER.info(
        "christies lotsearch sales=%s todo=%s cached_lots=%s workers=%s filterids=%s state=%s",
        len(sales),
        len(todo),
        len(seen),
        workers,
        filterids,
        state.counts(),
    )
    if not todo:
        state.flush()
        return []

    own_client: LotSearchClient | None = None
    search_fn = search
    if search_fn is None:
        own_client = LotSearchClient(delay=delay, client=client)
        search_fn = own_client.search

    lock = threading.Lock()
    new_returned: list[SitemapEntry] = []
    counters = {"new": 0, "done": 0, "failed": 0}

    def _ingest(sale: ChristiesSale, result: tuple[list[SitemapEntry], int] | None) -> None:
        with lock:
            if result is None:
                counters["failed"] += 1
                state.mark(sale.key, "failed")
                return
            entries, total = result
            fresh = [entry for entry in entries if seen.add(entry.entity_key)]
            append_typed_cache(cache_path, fresh)
            counters["new"] += len(fresh)
            counters["done"] += 1
            room = MAX_RETURN_NEW - len(new_returned)
            if room > 0:
                new_returned.extend(fresh[:room])
            state.mark(sale.key, "done", hits=total, written=len(fresh))
            processed = counters["done"] + counters["failed"]
            if processed % 250 == 0:
                LOGGER.info(
                    "christies lotsearch progress processed=%s/%s new=%s failed=%s",
                    processed,
                    len(todo),
                    counters["new"],
                    counters["failed"],
                )

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(search_sale, sale, filterids=filterids, search=search_fn): sale
                for sale in todo
            }
            for future in as_completed(futures):
                sale = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    LOGGER.error("christies lotsearch sale=%s crashed: %s", sale.key, exc)
                    result = None
                _ingest(sale, result)
    finally:
        state.flush()
        if own_client is not None:
            own_client.close()

    LOGGER.info(
        "christies lotsearch complete sales_done=%s failed=%s new=%s returned=%s cached_lots=%s",
        counters["done"],
        counters["failed"],
        counters["new"],
        len(new_returned),
        len(seen),
    )
    return new_returned


__all__ = [
    "ART_CATEGORY_FACETS",
    "DEFAULT_SEARCH_DELAY",
    "DEFAULT_SEARCH_WORKERS",
    "LOTSEARCH_URL",
    "PAGE_SIZE",
    "LotSearchClient",
    "SaleSearchState",
    "build_filterids",
    "expand_lots_from_lotsearch",
    "facet_filter_id",
    "lot_cache_path",
    "lot_to_entry",
    "search_sale",
]
