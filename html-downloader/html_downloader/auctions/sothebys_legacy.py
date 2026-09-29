"""Sotheby's legacy (pre-2019) archive discovery via server-rendered HTML.

The Algolia index only covers the current platform (~mid-2019 onward). Older
sales still resolve on sothebys.com but are not in any index or sitemap:

1. ``/en/results?p=N`` lists every past sale, 15 per page, newest first.
   Legacy sales link to ``/en/auctions/{year}/{slug}.html`` (current-platform
   sales link to ``/en/buy/auction/...`` and are ignored here). ``f2=<id>``
   department filters combine with OR and apply to legacy sales.
2. A legacy sale page shows ``AuctionsModule-lotsCount`` (``"88 lots"``) and
   12 lot links per ``?p=N`` page; a page past the end repeats the last page.
3. Lot pages live at ``/en/auctions/ecatalogue/{year}/{slug}/lot.{n}.html``.

Most sales number lots contiguously, so the first and last lot pages are
enough to generate every lot URL; other sales are paged in full.

The legacy archive is immutable: once the listing walk completes and a sale
is expanded (or 404s), it is never fetched again unless ``force`` is set.
"""

from __future__ import annotations

import html
import logging
import math
import random
import re
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from curl_cffi import requests as crequests

from html_downloader.auctions.search_state import SaleSearchState
from html_downloader.auctions.sitemap_cache import (
    append_typed_cache,
    load_typed_cache_key_union,
    typed_cache_path,
)
from html_downloader.auctions.urls import (
    build_sothebys_legacy_lot_url,
    build_sothebys_legacy_sale_url,
)
from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

RESULTS_URL = "https://www.sothebys.com/en/results"
LOTS_PER_PAGE = 12
DEFAULT_LEGACY_DELAY = 0.5
DEFAULT_LEGACY_WORKERS = 2
MAX_RETURN_NEW = 50_000
LISTING_KEY = "__listing__"
MIN_HTML_BYTES = 2_000

# Results-page department filter labels treated as art in art-only mode.
LEGACY_ART_DEPARTMENTS: tuple[str, ...] = (
    "Contemporary Art",
    "Impressionist & Modern Art",
    "Old Master Paintings",
    "Old Master Drawings",
    "Prints",
    "Photographs",
    "19th Century European Paintings",
    "American Art",
    "Modern British & Irish Art",
    "African Modern & Contemporary Art",
    "Victorian, Pre-Raphaelite & British Impressionist Art",
    "Modern Art | Asia",
    "Latin American Art",
    "British Watercolours & Drawings 1550-1850",
    "British Paintings 1550-1850",
    "Chinese Paintings – Modern",
    "Chinese Paintings – Classical",
    "Modern & Contemporary Southeast Asian Art",
    "Indian & South Asian Modern & Contemporary Art",
    "Modern & Contemporary Middle East",
    "Russian Art",
    "Aboriginal Art",
    "Canadian Art",
    "Czech Art",
    "Swiss Art",
    "Israeli & International Art",
    "Dutch & Belgian Paintings",
    "German, Austrian & Central European Paintings",
    "Italian Paintings",
    "Spanish Paintings",
    "Orientalist Paintings",
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_RESULTS_SALE_RE = re.compile(
    r'"(?:https://www\.sothebys\.com)?/en/auctions/(\d{4})/([a-z0-9-]+)\.html"',
    re.IGNORECASE,
)
_PAGE_COUNT_RE = re.compile(r"data-page-count>\s*(\d+)\s*<")
_LOTS_COUNT_RE = re.compile(r'AuctionsModule-lotsCount"[^>]*>\s*([\d,]+)\s*lots?\b', re.IGNORECASE)
_F2_INPUT_RE = re.compile(r'<input\b[^>]*\bname="f2"[^>]*>', re.IGNORECASE)
_VALUE_ATTR_RE = re.compile(r'\bvalue="([^"]+)"')
_LABEL_RE = re.compile(r'<label for="([^"]+)">\s*([^<]+?)\s*</label>')

FetchFn = Callable[[str], tuple[int, str]]
GetFn = Callable[[str], str | None]


class LegacyFetchError(RuntimeError):
    """A legacy page could not be fetched after all retries."""


@dataclass(frozen=True, slots=True)
class LegacySale:
    year: str
    slug: str

    @classmethod
    def from_key(cls, key: str) -> LegacySale | None:
        year, _, slug = key.partition("/")
        if len(year) != 4 or not year.isdigit() or not slug:
            return None
        return cls(year=year, slug=slug)

    @property
    def key(self) -> str:
        return f"{self.year}/{self.slug}"

    @property
    def url(self) -> str:
        return build_sothebys_legacy_sale_url(self.year, self.slug)

    def page_url(self, page: int) -> str:
        return self.url if page <= 1 else f"{self.url}?p={page}"

    def sale_entry(self) -> SitemapEntry:
        return SitemapEntry(
            url=self.url, lastmod=None, entity_type="sale", entity_id=f"legacy/{self.key}"
        )

    def lot_entry(self, lot_nr: str) -> SitemapEntry:
        return SitemapEntry(
            url=build_sothebys_legacy_lot_url(self.year, self.slug, lot_nr),
            lastmod=None,
            entity_type="lot",
            entity_id=f"legacy/{self.key}/{lot_nr}",
        )


@dataclass(frozen=True, slots=True)
class LegacySaleResult:
    status: str  # "done" | "missing"
    lot_count: int
    lots: tuple[str, ...]


def parse_results_page(body: str) -> tuple[list[LegacySale], int | None]:
    """Legacy sales linked from one ``/en/results`` page, plus the total page count."""
    sales = list(
        dict.fromkeys(
            LegacySale(year=year, slug=slug.lower()) for year, slug in _RESULTS_SALE_RE.findall(body)
        )
    )
    match = _PAGE_COUNT_RE.search(body)
    return sales, int(match.group(1)) if match else None


def parse_department_filters(body: str) -> dict[str, str]:
    """Map results-page department labels to their ``f2`` filter ids."""
    labels = {key: html.unescape(text) for key, text in _LABEL_RE.findall(body)}
    out: dict[str, str] = {}
    for tag in _F2_INPUT_RE.findall(body):
        value = _VALUE_ATTR_RE.search(tag)
        if value and value.group(1) in labels:
            out[labels[value.group(1)]] = value.group(1)
    return out


def parse_sale_page(body: str, sale: LegacySale) -> tuple[int | None, list[str]]:
    """Lot count (if shown) and this sale's lot numbers in page order."""
    count_match = _LOTS_COUNT_RE.search(body)
    count = int(count_match.group(1).replace(",", "")) if count_match else None
    lot_re = re.compile(
        rf"/en/auctions/ecatalogue/{sale.year}/{re.escape(sale.slug)}/lot\.([0-9a-z]+)\.html",
        re.IGNORECASE,
    )
    lots = list(dict.fromkeys(token.lower() for token in lot_re.findall(body)))
    return count, lots


def contiguous_lot_numbers(
    count: int, first_page: Sequence[str], last_page: Sequence[str]
) -> list[str] | None:
    """
    Every lot number when the sale is numbered ``start..end`` with no gaps.

    Requires numeric, consecutive first and last pages and ``end - start + 1 ==
    count``; returns None otherwise so the caller pages through the sale.
    """
    if count <= 0 or not first_page or not last_page:
        return None
    if not all(token.isdigit() for token in (*first_page, *last_page)):
        return None
    first = [int(token) for token in first_page]
    last = [int(token) for token in last_page]
    start, end = first[0], last[-1]
    if first != list(range(start, start + len(first))):
        return None
    if last != list(range(end - len(last) + 1, end + 1)):
        return None
    if end - start + 1 != count:
        return None
    return [str(n) for n in range(start, end + 1)]


def results_page_url(page: int, filter_ids: Sequence[str] = ()) -> str:
    params: list[tuple[str, Any]] = [("f2", fid) for fid in filter_ids]
    if page > 1:
        params.append(("p", page))
    return f"{RESULTS_URL}?{urlencode(params)}" if params else RESULTS_URL


class LegacyHtmlClient:
    """Thread-safe HTML getter: jittered delay, retries, 404 → None."""

    def __init__(
        self,
        *,
        delay: float = DEFAULT_LEGACY_DELAY,
        retries: int = 4,
        timeout: float = 40.0,
        fetch: FetchFn | None = None,
    ) -> None:
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self._fetch = fetch or self._fetch_with_session
        self._local = threading.local()

    def _session(self) -> crequests.Session:
        session = getattr(self._local, "session", None)
        if session is None:
            session = crequests.Session(impersonate="chrome131")
            session.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                }
            )
            self._local.session = session
        return session

    def _fetch_with_session(self, url: str) -> tuple[int, str]:
        response = self._session().get(url, timeout=self.timeout, allow_redirects=True)
        return int(response.status_code), response.text or ""

    def get(self, url: str) -> str | None:
        """Page HTML; None on 404 (permanent). Raises LegacyFetchError otherwise."""
        if self.delay > 0:
            time.sleep(self.delay * random.uniform(0.8, 1.2))
        last_error = ""
        for attempt in range(self.retries):
            try:
                status, body = self._fetch(url)
            except OSError as exc:
                last_error = f"transport: {exc}"
            else:
                if status == 404:
                    return None
                if status == 200 and len(body) >= MIN_HTML_BYTES:
                    return body
                last_error = f"status={status} bytes={len(body)}"
            wait = random.uniform(3, 8) * (attempt + 1)
            LOGGER.warning(
                "sothebys legacy fetch failed url=%s %s — retry in %.0fs", url, last_error, wait
            )
            time.sleep(wait)
        raise LegacyFetchError(f"sothebys legacy fetch failed url={url} {last_error}")


def list_legacy_sales(
    get: GetFn,
    *,
    departments: Sequence[str] | None = None,
    workers: int = DEFAULT_LEGACY_WORKERS,
) -> tuple[list[LegacySale], bool]:
    """
    Walk ``/en/results`` and return (legacy sales newest first, complete).

    Pages are fetched in ascending order: new sales pushing items onto later
    pages only cause duplicates, never gaps. ``complete`` is False when any
    page failed, so the caller retries the walk next run.
    """
    filter_ids: list[str] = []
    if departments:
        form = get(RESULTS_URL)
        if form is None:
            raise LegacyFetchError("sothebys results page returned 404")
        available = parse_department_filters(form)
        filter_ids = [available[name] for name in departments if name in available]
        missing = [name for name in departments if name not in available]
        if missing:
            LOGGER.warning("sothebys legacy departments not on results page: %s", missing)
        if not filter_ids:
            raise ValueError("no requested sothebys legacy departments exist on the results page")

    first = get(results_page_url(1, filter_ids))
    if first is None:
        raise LegacyFetchError("sothebys results page returned 404")
    sales, page_count = parse_results_page(first)
    pages = page_count or 1
    LOGGER.info("sothebys legacy listing pages=%s filters=%s", pages, len(filter_ids))

    ordered: dict[str, LegacySale] = {sale.key: sale for sale in sales}
    complete = True

    def _page(page: int) -> list[LegacySale]:
        body = get(results_page_url(page, filter_ids))
        if body is None:
            raise LegacyFetchError(f"sothebys results page {page} returned 404")
        return parse_results_page(body)[0]

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [pool.submit(_page, page) for page in range(2, pages + 1)]
        for page, future in enumerate(futures, start=2):
            try:
                found = future.result()
            except (LegacyFetchError, ValueError) as exc:
                LOGGER.error("sothebys legacy listing page=%s failed: %s", page, exc)
                complete = False
                continue
            for sale in found:
                ordered.setdefault(sale.key, sale)
            if page % 50 == 0:
                LOGGER.info(
                    "sothebys legacy listing progress page=%s/%s sales=%s",
                    page,
                    pages,
                    len(ordered),
                )
    return list(ordered.values()), complete


def crawl_legacy_sale(sale: LegacySale, *, get: GetFn) -> LegacySaleResult:
    """Every lot number of one legacy sale (first + last page when contiguous)."""
    first = get(sale.page_url(1))
    if first is None:
        return LegacySaleResult(status="missing", lot_count=0, lots=())
    count, first_lots = parse_sale_page(first, sale)
    total = count if count is not None else len(first_lots)
    pages = max(1, math.ceil(total / LOTS_PER_PAGE))
    if pages == 1 or len(first_lots) >= total:
        return LegacySaleResult(status="done", lot_count=total, lots=tuple(first_lots))

    last_body = get(sale.page_url(pages))
    if last_body is None:
        raise LegacyFetchError(f"sothebys legacy sale={sale.key} last page returned 404")
    last_lots = parse_sale_page(last_body, sale)[1]
    lots = contiguous_lot_numbers(total, first_lots, last_lots)
    if lots is None:
        collected = list(first_lots)
        for page in range(2, pages):
            body = get(sale.page_url(page))
            if body is None:
                raise LegacyFetchError(f"sothebys legacy sale={sale.key} page={page} returned 404")
            collected.extend(parse_sale_page(body, sale)[1])
        collected.extend(last_lots)
        lots = list(dict.fromkeys(collected))
    return LegacySaleResult(status="done", lot_count=total, lots=tuple(lots))


def lot_cache_path(state_path: Path) -> Path:
    return typed_cache_path(state_path)


def _record_listing(state: SaleSearchState, sales: Iterable[LegacySale]) -> int:
    added = 0
    for sale in sales:
        if state.status(sale.key) is None:
            state.mark(sale.key, "pending")
            added += 1
    return added


def expand_legacy_archive(
    *,
    state_path: Path,
    get: GetFn,
    departments: Sequence[str] | None = None,
    workers: int = DEFAULT_LEGACY_WORKERS,
    force: bool = False,
    max_sales: int | None = None,
    known_cache_paths: Sequence[Path] = (),
) -> list[SitemapEntry]:
    """
    List legacy sales (once), expand pending ones into lot URLs, append to JSONL.

    Returns a capped list of entities first seen this run. Sales end ``done``
    (expanded) or ``missing`` (404); fetch errors become ``failed`` and are
    retried next run. Keys already in ``known_cache_paths`` are skipped.
    """
    label = "sothebys legacy art" if departments else "sothebys legacy"
    cache_path = lot_cache_path(state_path)
    state = SaleSearchState(state_path)
    if force:
        state.reset()
        if cache_path.exists():
            cache_path.unlink()
        LOGGER.info("%s force: state and cache cleared", label)

    if state.status(LISTING_KEY) != "done":
        sales, complete = list_legacy_sales(get, departments=departments, workers=workers)
        added = _record_listing(state, sales)
        if complete:
            state.mark(LISTING_KEY, "done", hits=len(sales))
        state.flush()
        LOGGER.info(
            "%s listing sales=%s new=%s complete=%s", label, len(sales), added, complete
        )

    todo = [
        sale
        for sale in (LegacySale.from_key(k) for k in state.keys_with_status("pending", "failed"))
        if sale is not None
    ]
    if max_sales is not None and max_sales >= 0:
        todo = todo[:max_sales]
    if not todo:
        LOGGER.info("%s nothing to expand state=%s", label, state.counts())
        state.flush()
        return []
    seen = load_typed_cache_key_union([cache_path, *known_cache_paths], label=label)
    LOGGER.info(
        "%s sales todo=%s known=%s workers=%s state=%s",
        label,
        len(todo),
        len(seen),
        workers,
        state.counts(),
    )

    lock = threading.Lock()
    new_returned: list[SitemapEntry] = []
    counters = {"new": 0, "done": 0, "missing": 0, "failed": 0}

    def _ingest(sale: LegacySale, result: LegacySaleResult | None) -> None:
        with lock:
            if result is None:
                counters["failed"] += 1
                state.mark(sale.key, "failed")
            else:
                entries = [sale.sale_entry()] if result.status == "done" else []
                entries.extend(sale.lot_entry(lot) for lot in result.lots)
                fresh = [entry for entry in entries if seen.add(entry.entity_key)]
                append_typed_cache(cache_path, fresh)
                counters["new"] += len(fresh)
                counters[result.status] += 1
                room = MAX_RETURN_NEW - len(new_returned)
                if room > 0:
                    new_returned.extend(fresh[:room])
                state.mark(sale.key, result.status, hits=result.lot_count, written=len(fresh))
            processed = counters["done"] + counters["missing"] + counters["failed"]
            if processed % 100 == 0:
                LOGGER.info(
                    "%s progress processed=%s/%s new=%s missing=%s failed=%s",
                    label,
                    processed,
                    len(todo),
                    counters["new"],
                    counters["missing"],
                    counters["failed"],
                )

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {pool.submit(crawl_legacy_sale, sale, get=get): sale for sale in todo}
            for future in as_completed(futures):
                sale = futures[future]
                try:
                    result: LegacySaleResult | None = future.result()
                except Exception as exc:  # noqa: BLE001
                    LOGGER.error("%s sale=%s failed: %s", label, sale.key, exc)
                    result = None
                _ingest(sale, result)
    finally:
        state.flush()

    LOGGER.info(
        "%s complete done=%s missing=%s failed=%s new=%s returned=%s known=%s",
        label,
        counters["done"],
        counters["missing"],
        counters["failed"],
        counters["new"],
        len(new_returned),
        len(seen),
    )
    return new_returned


__all__ = [
    "DEFAULT_LEGACY_DELAY",
    "DEFAULT_LEGACY_WORKERS",
    "LEGACY_ART_DEPARTMENTS",
    "LegacyFetchError",
    "LegacyHtmlClient",
    "LegacySale",
    "LegacySaleResult",
    "contiguous_lot_numbers",
    "crawl_legacy_sale",
    "expand_legacy_archive",
    "list_legacy_sales",
    "lot_cache_path",
    "parse_department_filters",
    "parse_results_page",
    "parse_sale_page",
    "results_page_url",
]
