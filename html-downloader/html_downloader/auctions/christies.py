"""Christie's discovery: public XML sitemaps (full archive) + optional art-only lotsearch.

PRIMARY (full archive): https://www.christies.com/sitemap/sitemap_index.xml
  - lot_past_{1..N}_sitemap.xml (~49k EN lots each, newest lot ids first)
  - lot_upcoming_1_sitemap.xml
  - auction_past_1_sitemap.xml / auction_upcoming_1_sitemap.xml (EN + zh dupes)

Past-lot children are re-sorted every month (new lots push older ones into the
next file), so a child is only "done" for the index ``<lastmod>`` it was walked
at. Dedup is by lot id against the typed JSONL cache.

ART-ONLY: ``christies_search`` walks every past sale through the public
``lotsearch`` JSON API with an OR'd ``CoaCategories`` facet.

No lot parsing here — download saves HTML only.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from curl_cffi import requests as crequests

from html_downloader.auctions.sitemap_cache import (
    FetchBytesFn,
    TypedKeySet,
    TypedRow,
    append_typed_cache,
    fetch_with_retries,
    iter_typed_cache_rows,
    load_sitemap_progress,
    load_typed_cache_keys,
    make_rotating_fetcher,
    proxy_url,
    save_auction_lastmod_state,
    save_sitemap_progress,
    typed_cache_path,
)
from html_downloader.auctions.urls import (
    CHRISTIES_LOT_RE,
    christies_entity_from_url,
    normalize_auction_url,
)
from html_downloader.discover.registry import load_urls
from html_downloader.discover.sitemap import (
    SitemapEntry,
    _looks_like_html_challenge,
    is_sitemap_index,
    parse_child_sitemap_entries,
    parse_url_entries,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_CHRISTIES_INDEX = "https://www.christies.com/sitemap/sitemap_index.xml"
DEFAULT_MAX_SITEMAPS = 200
DEFAULT_MIN_URLS = 0
DEFAULT_CHRISTIES_MAX_RETRIES = 6
DEFAULT_CHRISTIES_FETCH_TIMEOUT = 120.0
DEFAULT_CHRISTIES_INTER_FILE_SLEEP = 1.0
# Cap in-memory "new this run" list; the full archive streams from JSONL.
MAX_RETURN_NEW = 50_000

# Akamai deny page markers. Real lot pages embed an AWS WAF ``challenge.js``
# URL and the word "blocked" in their first 16 KB, so the generic download
# keywords ("challenge", "blocked", ...) must NOT be used for Christie's.
AKAMAI_KEYWORDS: tuple[str, ...] = (
    "access denied",
    "errors.edgesuite.net",
    "you don't have permission to access",
)

_CHILD_RE = re.compile(
    r"/(lot|auction)_(past|upcoming)_(\d+)_sitemap\.xml$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ChildSitemap:
    url: str
    lastmod: str | None
    kind: str  # "lot" | "sale"
    phase: str  # "past" | "upcoming"
    number: int


@dataclass(frozen=True, slots=True)
class ChristiesSale:
    sale_number: str
    sale_room_code: str
    lastmod: str | None

    @property
    def key(self) -> str:
        return f"{self.sale_number}-{self.sale_room_code.lower()}"


def classify_child_sitemap(url: str, lastmod: str | None = None) -> ChildSitemap | None:
    """Return a ChildSitemap for lot/auction children; None for stories, images, etc."""
    match = _CHILD_RE.search(url.split("?", 1)[0])
    if not match:
        return None
    kind = "lot" if match.group(1).lower() == "lot" else "sale"
    return ChildSitemap(
        url=url,
        lastmod=lastmod,
        kind=kind,
        phase=match.group(2).lower(),
        number=int(match.group(3)),
    )


def is_christies_lot_sitemap(url: str) -> bool:
    child = classify_child_sitemap(url)
    return child is not None and child.kind == "lot"


def is_christies_auction_sitemap(url: str) -> bool:
    child = classify_child_sitemap(url)
    return child is not None and child.kind == "sale"


def _child_sort_key(child: ChildSitemap) -> tuple[int, int, int]:
    # Small sale maps first, then upcoming lots, then past lots by file number.
    return (0 if child.kind == "sale" else 1, 0 if child.phase == "upcoming" else 1, child.number)


def _looks_like_block(body: bytes) -> bool:
    if _looks_like_html_challenge(body):
        return True
    sample = body[:4096].lower()
    return any(kw.encode("ascii") in sample for kw in AKAMAI_KEYWORDS)


def _entry_from_loc(loc: str, lastmod: str | None) -> SitemapEntry | None:
    # Fast path: canonical EN lot URLs are ~98% of the archive.
    fast = CHRISTIES_LOT_RE.match(loc)
    if fast and loc.startswith("https://www.christies.com/en/lot/lot-") and not loc.endswith("/"):
        return SitemapEntry(url=loc, lastmod=lastmod, entity_type="lot", entity_id=fast.group(1))
    normalized = normalize_auction_url(loc)
    if not normalized:
        return None
    key = christies_entity_from_url(normalized)
    if key is None:
        return None
    return SitemapEntry(url=normalized, lastmod=lastmod, entity_type=key[0], entity_id=key[1])


def parse_christies_entries(xml_bytes: bytes) -> list[SitemapEntry]:
    """Parse a urlset into unique EN lot/sale entries (zh variants collapse to EN)."""
    if _looks_like_block(xml_bytes):
        raise RuntimeError("Christie's sitemap returned an HTML/Akamai block page")
    try:
        raw = parse_url_entries(xml_bytes)
    except ET.ParseError as exc:
        raise RuntimeError(f"invalid Christie's sitemap XML: {exc}") from exc

    best: dict[tuple[str, str], SitemapEntry] = {}
    for loc, lastmod in raw:
        entry = _entry_from_loc(loc, lastmod)
        if entry is None:
            continue
        existing = best.get(entry.entity_key)
        if existing is None or (
            lastmod and (existing.lastmod is None or lastmod > existing.lastmod)
        ):
            best[entry.entity_key] = entry
    return list(best.values())


def lot_cache_path(progress_path: Path) -> Path:
    """Sidecar JSONL of discovered lot/sale URLs (source of truth for the archive)."""
    return typed_cache_path(progress_path)


def iter_lot_cache_rows(path: Path) -> Iterator[TypedRow]:
    """Yield (entity_type, entity_id, url, lastmod) from the Christie's JSONL cache."""
    return iter_typed_cache_rows(path, label="christies")


def load_lot_cache_keys(path: Path) -> TypedKeySet:
    return load_typed_cache_keys(path, label="christies")


def append_lot_cache(path: Path, entries: Sequence[SitemapEntry]) -> int:
    return append_typed_cache(path, entries)


def clear_sitemap_cache(*, progress_path: Path) -> None:
    for path in (progress_path, lot_cache_path(progress_path)):
        if path.exists():
            path.unlink()
            LOGGER.info("christies sitemap force: cleared %s", path)


def _progress_epoch(child: ChildSitemap) -> str:
    """Freshness token: index lastmod, else current UTC month."""
    if child.lastmod:
        return child.lastmod
    now = datetime.now(timezone.utc)
    return f"{now.year:04d}-{now.month:02d}"


def select_child_sitemaps(
    children: Sequence[ChildSitemap],
    progress: dict[str, dict[str, Any]],
    *,
    max_sitemaps: int,
) -> list[ChildSitemap]:
    """Children not yet done for their current index lastmod, in processing order."""
    if max_sitemaps <= 0:
        return []
    pending = [
        child
        for child in children
        if not (
            (progress.get(child.url) or {}).get("status") == "done"
            and (progress.get(child.url) or {}).get("epoch") == _progress_epoch(child)
        )
    ]
    pending.sort(key=_child_sort_key)
    return pending[:max_sitemaps]


def _fetch_christies_bytes(
    url: str,
    proxy: dict[str, str] | None = None,
    *,
    timeout: float = DEFAULT_CHRISTIES_FETCH_TIMEOUT,
) -> bytes:
    kwargs: dict[str, Any] = {
        "impersonate": "chrome131",
        "timeout": timeout,
        "headers": {
            "Accept": "application/xml,text/xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    purl = proxy_url(proxy)
    if purl:
        kwargs["proxies"] = {"http": purl, "https": purl}
    response = crequests.get(url, **kwargs)
    status = int(response.status_code)
    body = bytes(response.content or b"")
    if status != 200:
        raise RuntimeError(f"sitemap fetch status={status} url={url}")
    if not body:
        raise RuntimeError(f"sitemap fetch status={status} empty body url={url}")
    head = body[:4096].lower()
    looks_xml = b"<urlset" in head or b"<sitemapindex" in head or b"<?xml" in head
    if not looks_xml and _looks_like_block(body):
        raise RuntimeError(f"Akamai/HTML block for url={url}")
    return body


def build_fetcher(proxies: Sequence[dict[str, str]] | None = None) -> FetchBytesFn:
    """Direct-first fetcher with proxy rotation fallback."""
    return make_rotating_fetcher(_fetch_christies_bytes, list(proxies or []))


def collect_child_sitemaps(
    index_url: str,
    *,
    fetch_bytes: FetchBytesFn,
    max_retries: int = DEFAULT_CHRISTIES_MAX_RETRIES,
) -> list[ChildSitemap]:
    """Fetch the sitemap index and keep lot/auction children (with index lastmod)."""
    body = fetch_with_retries(
        index_url, fetch_bytes=fetch_bytes, max_retries=max_retries, label="christies"
    )
    if not is_sitemap_index(body):
        raise RuntimeError(f"expected sitemapindex at {index_url}")
    seen: set[str] = set()
    children: list[ChildSitemap] = []
    for loc, lastmod in parse_child_sitemap_entries(body):
        child = classify_child_sitemap(loc, lastmod)
        if child is None or child.url in seen:
            continue
        seen.add(child.url)
        children.append(child)
    return children


def collect_christies_sales(
    index_url: str = DEFAULT_CHRISTIES_INDEX,
    *,
    fetch_bytes: FetchBytesFn,
    include_upcoming: bool = False,
    max_retries: int = DEFAULT_CHRISTIES_MAX_RETRIES,
) -> list[ChristiesSale]:
    """
    Sale list for lotsearch, newest first.

    Upcoming sales are excluded by default: their lots have no results yet and
    they reappear in ``auction_past`` once closed.
    """
    children = collect_child_sitemaps(index_url, fetch_bytes=fetch_bytes, max_retries=max_retries)
    sales: dict[str, ChristiesSale] = {}
    for child in children:
        if child.kind != "sale" or (child.phase == "upcoming" and not include_upcoming):
            continue
        body = fetch_with_retries(
            child.url, fetch_bytes=fetch_bytes, max_retries=max_retries, label="christies"
        )
        for entry in parse_christies_entries(body):
            if entry.entity_type != "sale":
                continue
            sale_number, _, room = entry.entity_id.partition("-")
            sale = ChristiesSale(sale_number=sale_number, sale_room_code=room.upper(), lastmod=entry.lastmod)
            existing = sales.get(sale.key)
            if existing is None or (sale.lastmod or "") > (existing.lastmod or ""):
                sales[sale.key] = sale
    ordered = sorted(sales.values(), key=lambda s: (s.lastmod or "", int(s.sale_number)), reverse=True)
    LOGGER.info("christies sales collected=%s include_upcoming=%s", len(ordered), include_upcoming)
    return ordered


def fetch_christies_sitemap_entries(
    sitemap_url: str = DEFAULT_CHRISTIES_INDEX,
    *,
    concurrency: int = 1,
    proxy: dict[str, str] | None = None,
    proxies: list[dict[str, str]] | None = None,
    fetch_bytes: FetchBytesFn | None = None,
    max_retries: int = DEFAULT_CHRISTIES_MAX_RETRIES,
    max_sitemaps: int = DEFAULT_MAX_SITEMAPS,
    min_urls: int = DEFAULT_MIN_URLS,
    sitemap_progress_path: Path | None = None,
    sitemap_force: bool = False,
    inter_file_sleep: float = DEFAULT_CHRISTIES_INTER_FILE_SLEEP,
) -> list[SitemapEntry]:
    """
    Walk lot/auction child sitemaps; persist new entities to the JSONL cache.

    Returns a capped list of entities first seen this run — callers stream
    ``lot_cache_path(sitemap_progress_path)`` for the full archive.
    """
    _ = concurrency  # sequential by design (Akamai)
    proxy_list = list(proxies or ([] if proxy is None else [proxy]))
    fetch = fetch_bytes or build_fetcher(proxy_list)

    cache_path: Path | None = None
    if sitemap_progress_path is not None:
        if sitemap_force:
            clear_sitemap_cache(progress_path=sitemap_progress_path)
        cache_path = lot_cache_path(sitemap_progress_path)

    children = collect_child_sitemaps(sitemap_url, fetch_bytes=fetch, max_retries=max_retries)
    progress = load_sitemap_progress(sitemap_progress_path)
    seen_keys = load_lot_cache_keys(cache_path) if cache_path is not None else TypedKeySet()
    selected = select_child_sitemaps(children, progress, max_sitemaps=max_sitemaps)
    LOGGER.info(
        "christies index children=%s selected=%s cached_keys=%s proxies=%s "
        "max_sitemaps=%s min_urls=%s force=%s",
        len(children),
        len(selected),
        len(seen_keys),
        len(proxy_list),
        max_sitemaps,
        min_urls,
        sitemap_force,
    )

    new_returned: list[SitemapEntry] = []
    new_total = 0
    failed: list[str] = []

    for i, child in enumerate(selected):
        if min_urls > 0 and new_total >= min_urls:
            LOGGER.info("christies min_urls reached new=%s target=%s", new_total, min_urls)
            break
        if i > 0 and inter_file_sleep > 0:
            time.sleep(inter_file_sleep)
        epoch = _progress_epoch(child)
        progress[child.url] = {"status": "in_progress", "epoch": epoch, "kind": child.kind}
        try:
            body = fetch_with_retries(
                child.url, fetch_bytes=fetch, max_retries=max_retries, label="christies"
            )
            if is_sitemap_index(body):
                raise RuntimeError(f"expected urlset, got sitemapindex url={child.url}")
            entries = parse_christies_entries(body)
            fresh = [entry for entry in entries if seen_keys.add(entry.entity_key)]
            if cache_path is not None:
                append_lot_cache(cache_path, fresh)
            new_total += len(fresh)
            room = MAX_RETURN_NEW - len(new_returned)
            if room > 0:
                new_returned.extend(fresh[:room])
            progress[child.url] = {
                "status": "done",
                "epoch": epoch,
                "kind": child.kind,
                "entities_found": len(entries),
                "new": len(fresh),
            }
            LOGGER.info(
                "christies child parsed url=%s entities=%s new=%s running_new=%s cached_keys=%s",
                child.url,
                len(entries),
                len(fresh),
                new_total,
                len(seen_keys),
            )
        except Exception as exc:
            LOGGER.warning("christies child failed url=%s error=%s", child.url, exc)
            progress[child.url] = {
                "status": "failed",
                "epoch": epoch,
                "kind": child.kind,
                "error": str(exc)[:200],
            }
            failed.append(child.url)
        if sitemap_progress_path is not None:
            save_sitemap_progress(sitemap_progress_path, progress)

    if selected and len(failed) == len(selected) and len(seen_keys) == 0:
        raise RuntimeError(f"all {len(failed)} Christie's child sitemap(s) failed; no entries parsed")
    if failed:
        LOGGER.warning("christies partial success failed=%s new=%s", len(failed), new_total)

    LOGGER.info(
        "christies sitemap discovery complete new=%s returned=%s cached_keys=%s lots=%s sales=%s",
        new_total,
        len(new_returned),
        len(seen_keys),
        seen_keys.count("lot"),
        seen_keys.count("sale"),
    )
    return new_returned


def fetch_christies_entries(
    sitemap_url: str = DEFAULT_CHRISTIES_INDEX,
    *,
    concurrency: int = 1,
    proxy: dict[str, str] | None = None,
    proxies: list[dict[str, str]] | None = None,
    max_sitemaps: int = DEFAULT_MAX_SITEMAPS,
    min_urls: int = DEFAULT_MIN_URLS,
    sitemap_progress_path: Path | None = None,
    sitemap_force: bool = False,
    art_categories: Sequence[str] | None = None,
    art_state_path: Path | None = None,
    art_workers: int = 2,
    art_delay: float = 0.4,
    art_force: bool = False,
    max_sales: int | None = None,
) -> list[SitemapEntry]:
    """
    Dispatch: art-only lotsearch when ``art_categories`` is set, else full sitemaps.

    Art-only mode never touches the full-archive sitemap cache.
    """
    proxy_list = list(proxies or ([] if proxy is None else [proxy]))
    if art_categories:
        if art_state_path is None:
            raise ValueError("christies art-only discovery requires art_state_path")
        from html_downloader.auctions.christies_search import expand_lots_from_lotsearch

        sales = collect_christies_sales(sitemap_url, fetch_bytes=build_fetcher(proxy_list))
        return expand_lots_from_lotsearch(
            state_path=art_state_path,
            sales=sales,
            categories=art_categories,
            workers=art_workers,
            delay=art_delay,
            force=art_force,
            max_sales=max_sales,
        )
    return fetch_christies_sitemap_entries(
        sitemap_url,
        concurrency=concurrency,
        proxies=proxy_list,
        max_sitemaps=max_sitemaps,
        min_urls=min_urls,
        sitemap_progress_path=sitemap_progress_path,
        sitemap_force=sitemap_force,
    )


def known_christies_keys_from_paths(paths: Iterable[Path]) -> set[tuple[str, str]]:
    """Load known Christie's entity keys from URL list / JSONL files."""
    keys: set[tuple[str, str]] = set()
    for url in load_urls(paths):
        key = christies_entity_from_url(url)
        if key is not None:
            keys.add(key)
    return keys


__all__ = [
    "AKAMAI_KEYWORDS",
    "DEFAULT_CHRISTIES_INDEX",
    "DEFAULT_MAX_SITEMAPS",
    "DEFAULT_MIN_URLS",
    "ChildSitemap",
    "ChristiesSale",
    "build_fetcher",
    "classify_child_sitemap",
    "collect_child_sitemaps",
    "collect_christies_sales",
    "fetch_christies_entries",
    "fetch_christies_sitemap_entries",
    "is_christies_auction_sitemap",
    "is_christies_lot_sitemap",
    "iter_lot_cache_rows",
    "known_christies_keys_from_paths",
    "load_lot_cache_keys",
    "lot_cache_path",
    "parse_christies_entries",
    "save_auction_lastmod_state",
    "select_child_sitemaps",
]
