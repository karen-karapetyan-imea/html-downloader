"""1stDibs discovery: art HTML sitemap crawl for items, dealers, and creators."""

from __future__ import annotations

import logging
import math
import re
import threading
from collections import deque
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from html_downloader.discover.registry import load_urls, normalize_url
from html_downloader.discover.sitemap import SitemapEntry
from html_downloader.discover.urls import firstdibs_entity_from_url
from html_downloader.paths import PROJECT_ROOT

LOGGER = logging.getLogger(__name__)

DEFAULT_1STDIBS_SEEDS = (
    "https://www.1stdibs.com/sitemap/art/items/",
    "https://www.1stdibs.com/sitemap/art/dealers/",
    "https://www.1stdibs.com/sitemap/art/creators/",
)

DEFAULT_1STDIBS_DEALER_SEED = "https://www.1stdibs.com/sitemap/art/dealers/"
DEFAULT_1STDIBS_SITEMAP_STATE_DIR = PROJECT_ROOT / "state" / "sitemap_crawler"
DEFAULT_1STDIBS_SITEMAP_DELAY = 0.5

DEFAULT_1STDIBS_ART_CATEGORIES = (
    "https://www.1stdibs.com/art/paintings/",
    "https://www.1stdibs.com/art/prints-works-on-paper/",
    "https://www.1stdibs.com/art/photography/",
    "https://www.1stdibs.com/art/drawings-watercolor-paintings/",
    "https://www.1stdibs.com/art/mixed-media/",
    "https://www.1stdibs.com/art/sculptures/",
    "https://www.1stdibs.com/art/more-art/",
)

_BROWSE_ITEMS_PER_PAGE = 20

_ART_SITEMAP_PATH_RE = re.compile(r"^/sitemap/art/dealers/", re.IGNORECASE)
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)
_TOTAL_RESULTS_RE = re.compile(r'"totalResults"\s*:\s*(\d+)')
_ITEM_HREF_RE = re.compile(r'href="(/art/[^"]*id-a_(\d+)/)"', re.IGNORECASE)

FetchHtmlFn = Callable[[str], str]

_thread_local = threading.local()


def _thread_session() -> Any:
    """One curl_cffi session per worker thread (not thread-safe to share)."""
    session = getattr(_thread_local, "session", None)
    if session is None:
        from curl_cffi import Session

        session = Session(impersonate="chrome")
        _thread_local.session = session
    return session


def _is_art_dealer_sitemap_path(path: str) -> bool:
    return bool(_ART_SITEMAP_PATH_RE.match(path))


def _normalize_page_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path if parts.path.endswith("/") else parts.path + "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def _category_slug(category_url: str) -> str:
    path = urlsplit(category_url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] if path else category_url


def _looks_like_bot_challenge(body: bytes) -> bool:
    """Return True only for actual block/challenge pages, not normal 1stDibs HTML."""
    lower = body.lower()
    if b"/sitemap/art/" in lower or b"art sitemap" in lower:
        return False
    if b"itemsearchquery" in lower or (b"/art/" in lower and b"id-a_" in lower):
        return False
    if b"id-a_" in lower or b'href="/dealers/' in lower:
        return False
    if b"just a moment" in lower or b"cf-chl" in lower:
        return True
    if b"human verification" in lower or b"captcha-container" in lower:
        return True
    if b"awswaf" in lower and b"captcha" in lower:
        return True
    return False


def fetch_html_stealth(
    url: str,
    *,
    session: Any | None = None,
    proxy: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> str:
    """Fetch HTML with Chrome TLS impersonation; reject bot-challenge pages."""
    from curl_cffi import Session

    own_session = session is None
    http = session or Session(impersonate="chrome")
    try:
        kwargs: dict[str, Any] = {"url": url, "timeout": timeout}
        if proxy:
            kwargs["proxies"] = proxy
        response = http.get(**kwargs)
        if response.status_code != 200:
            raise RuntimeError(f"html fetch status={response.status_code} url={url}")
        body = bytes(response.content or b"")
        if not body:
            raise RuntimeError(f"empty html body url={url}")
        if _looks_like_bot_challenge(body):
            raise RuntimeError(f"bot challenge instead of html url={url}")
        return body.decode("utf-8", errors="replace")
    finally:
        if own_session and hasattr(http, "close"):
            http.close()


def parse_browse_total_results(html: str) -> int | None:
    """Return totalResults from SSR browse page JSON, or None if missing."""
    match = _TOTAL_RESULTS_RE.search(html)
    if not match:
        return None
    return int(match.group(1))


def browse_page_url(category_url: str, page: int) -> str:
    """Build paginated browse URL for a 1stDibs art subcategory."""
    base = _normalize_page_url(category_url)
    if page <= 1:
        return base
    return f"{base}?page={page}"


def extract_browse_item_urls(html: str, base_url: str) -> list[str]:
    """Return normalized item page URLs from a browse results HTML page."""
    urls: list[str] = []
    seen: set[str] = set()
    for path, _item_id in _ITEM_HREF_RE.findall(html):
        absolute = urljoin(base_url, path)
        normalized = normalize_url(absolute)
        if not normalized or normalized in seen:
            continue
        if firstdibs_entity_from_url(normalized) is None:
            continue
        seen.add(normalized)
        urls.append(normalized)
    return urls


def extract_sitemap_links(html: str, base_url: str) -> list[str]:
    """Return absolute art dealer sitemap URLs found in HTML."""
    links: list[str] = []
    seen: set[str] = set()
    for href in _HREF_RE.findall(html):
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute.split("?")[0].split("#")[0])
        if not parts.netloc.lower().endswith("1stdibs.com"):
            continue
        if not _is_art_dealer_sitemap_path(parts.path):
            continue
        normalized = _normalize_page_url(absolute)
        if normalized not in seen:
            seen.add(normalized)
            links.append(normalized)
    return links


def extract_entity_urls(html: str, base_url: str) -> list[str]:
    """Return normalized entity page URLs from dealer sitemap HTML."""
    urls: list[str] = []
    seen: set[str] = set()
    for href in _HREF_RE.findall(html):
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute = urljoin(base_url, href.split("?")[0].split("#")[0])
        normalized = normalize_url(absolute)
        if not normalized or normalized in seen:
            continue
        if firstdibs_entity_from_url(normalized) is None:
            continue
        seen.add(normalized)
        urls.append(normalized)
    return urls


def _fetch_pages_parallel(
    page_urls: list[str],
    *,
    concurrency: int,
    fetch_html: FetchHtmlFn,
    log_prefix: str,
) -> dict[str, str | None]:
    results: dict[str, str | None] = {}

    def fetch_one(page_url: str) -> tuple[str, str | None]:
        try:
            return page_url, fetch_html(page_url)
        except Exception as exc:
            LOGGER.warning("%s page failed url=%s error=%s", log_prefix, page_url, exc)
            return page_url, None

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(fetch_one, page_url): page_url for page_url in page_urls}
        for future in as_completed(futures):
            page_url, html = future.result()
            results[page_url] = html
    return results


def fetch_firstdibs_browse_item_entries(
    categories: Iterable[str] = DEFAULT_1STDIBS_ART_CATEGORIES,
    *,
    concurrency: int = 8,
    proxy: dict[str, str] | None = None,
    fetch_html: FetchHtmlFn | None = None,
) -> list[SitemapEntry]:
    """Paginate art category browse pages and return item entity URLs."""
    if fetch_html is None:
        def fetch_html(url: str, _proxy: dict[str, str] | None = proxy) -> str:
            return fetch_html_stealth(url, session=_thread_session(), proxy=_proxy)

    best_by_key: dict[tuple[str, str], SitemapEntry] = {}
    pages_fetched = 0
    batch_size = max(1, concurrency) * 4

    category_list = list(categories)
    LOGGER.info(
        "firstdibs browse crawl starting concurrency=%s categories=%s",
        concurrency,
        len(category_list),
    )

    for category_url in category_list:
        slug = _category_slug(category_url)
        first_url = browse_page_url(category_url, 1)
        first_html = fetch_html(first_url)
        pages_fetched += 1

        total = parse_browse_total_results(first_html)
        if total is None:
            LOGGER.warning("firstdibs browse category=%s missing totalResults; skipping", slug)
            continue

        max_page = max(1, math.ceil(total / _BROWSE_ITEMS_PER_PAGE))
        LOGGER.info(
            "firstdibs browse category=%s totalResults=%s pages=%s",
            slug,
            total,
            max_page,
        )

        pending_pages = list(range(2, max_page + 1))
        category_new = 0

        def ingest_page(html: str, page_url: str) -> int:
            nonlocal category_new
            new_on_page = 0
            for entity_url in extract_browse_item_urls(html, page_url):
                key = firstdibs_entity_from_url(entity_url)
                if key is None:
                    continue
                entity_type, entity_id = key
                if key in best_by_key:
                    continue
                best_by_key[key] = SitemapEntry(
                    url=entity_url,
                    lastmod=None,
                    entity_type=entity_type,
                    entity_id=entity_id,
                )
                new_on_page += 1
                category_new += 1
            return new_on_page

        ingest_page(first_html, first_url)

        while pending_pages:
            batch_pages = pending_pages[:batch_size]
            pending_pages = pending_pages[batch_size:]
            batch_urls = [browse_page_url(category_url, page) for page in batch_pages]
            results = _fetch_pages_parallel(
                batch_urls,
                concurrency=concurrency,
                fetch_html=fetch_html,
                log_prefix="firstdibs browse",
            )
            pages_fetched += len(batch_urls)

            empty_pages = 0
            for page_num, page_url in zip(batch_pages, batch_urls, strict=True):
                html = results.get(page_url)
                if html is None:
                    continue
                if ingest_page(html, page_url) == 0:
                    empty_pages += 1

            if empty_pages == len(batch_pages):
                LOGGER.info(
                    "firstdibs browse category=%s stopping early at page=%s (empty batch)",
                    slug,
                    batch_pages[-1],
                )
                break

            last_page = batch_pages[-1]
            LOGGER.info(
                "firstdibs browse category=%s page=%s/%s category_new=%s total_items=%s",
                slug,
                last_page,
                max_page,
                category_new,
                sum(1 for k in best_by_key if k[0] == "item"),
            )

        LOGGER.info(
            "firstdibs browse category=%s done category_new=%s total_items=%s",
            slug,
            category_new,
            sum(1 for k in best_by_key if k[0] == "item"),
        )

    LOGGER.info(
        "firstdibs browse pages=%s item_urls=%s",
        pages_fetched,
        sum(1 for k in best_by_key if k[0] == "item"),
    )
    return list(best_by_key.values())


def fetch_firstdibs_dealer_sitemap_entries(
    seed: str = DEFAULT_1STDIBS_DEALER_SEED,
    *,
    concurrency: int = 8,
    proxy: dict[str, str] | None = None,
    fetch_html: FetchHtmlFn | None = None,
) -> list[SitemapEntry]:
    """Crawl 1stDibs art dealer HTML sitemap and return dealer entity URLs."""
    if fetch_html is None:
        def fetch_html(url: str, _proxy: dict[str, str] | None = proxy) -> str:
            return fetch_html_stealth(url, session=_thread_session(), proxy=_proxy)

    pending: deque[str] = deque([_normalize_page_url(seed)])
    seen_pages: set[str] = set()
    best_by_key: dict[tuple[str, str], SitemapEntry] = {}

    LOGGER.info(
        "firstdibs dealer sitemap crawl starting concurrency=%s",
        concurrency,
    )

    batch_num = 0
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        while pending:
            batch: list[str] = []
            while pending and len(batch) < max(1, concurrency) * 4:
                url = pending.popleft()
                if url in seen_pages:
                    continue
                seen_pages.add(url)
                batch.append(url)
            if not batch:
                break

            batch_num += 1
            LOGGER.info(
                "firstdibs dealer sitemap batch=%s fetching=%s pending=%s entities=%s",
                batch_num,
                len(batch),
                len(pending),
                len(best_by_key),
            )

            results: dict[str, str | None] = {}

            def fetch_one(page_url: str) -> tuple[str, str | None]:
                try:
                    return page_url, fetch_html(page_url)
                except Exception as exc:
                    LOGGER.warning(
                        "firstdibs dealer sitemap page failed url=%s error=%s",
                        page_url,
                        exc,
                    )
                    return page_url, None

            futures = {pool.submit(fetch_one, page_url): page_url for page_url in batch}
            for future in as_completed(futures):
                page_url, html = future.result()
                results[page_url] = html

            for page_url, html in results.items():
                if html is None:
                    continue
                for entity_url in extract_entity_urls(html, page_url):
                    key = firstdibs_entity_from_url(entity_url)
                    if key is None:
                        continue
                    entity_type, entity_id = key
                    canonical = f"https://www.1stdibs.com/dealers/{entity_id}/"
                    if key not in best_by_key:
                        best_by_key[key] = SitemapEntry(
                            url=canonical,
                            lastmod=None,
                            entity_type=entity_type,
                            entity_id=entity_id,
                        )
                for link in extract_sitemap_links(html, page_url):
                    if link not in seen_pages:
                        pending.append(link)

            LOGGER.info(
                "firstdibs dealer sitemap batch=%s done pages=%s pending=%s entities=%s",
                batch_num,
                len(seen_pages),
                len(pending),
                len(best_by_key),
            )

    LOGGER.info(
        "firstdibs dealer sitemap pages=%s dealer_urls=%s",
        len(seen_pages),
        len(best_by_key),
    )
    return list(best_by_key.values())


def fetch_firstdibs_sitemap_entries(
    seeds: Iterable[str] = DEFAULT_1STDIBS_SEEDS,
    *,
    concurrency: int = 10,
    proxy: dict[str, str] | None = None,
    fetch_html: FetchHtmlFn | None = None,
    state_dir: Path | None = None,
    delay: float = DEFAULT_1STDIBS_SITEMAP_DELAY,
) -> list[SitemapEntry]:
    """Discover 1stDibs art items, dealers, and creators via the art HTML sitemap."""
    del seeds  # art sitemap seeds are fixed; kept for API compatibility

    if fetch_html is None:
        def fetch_html(url: str, _proxy: dict[str, str] | None = proxy) -> str:
            return fetch_html_stealth(url, session=_thread_session(), proxy=_proxy)

    from sitemap_crawler.crawler import CrawlConfig, SitemapCrawler
    from sitemap_crawler.storage import CrawlerState

    state = CrawlerState(state_dir or DEFAULT_1STDIBS_SITEMAP_STATE_DIR)
    state.load()

    crawler = SitemapCrawler(
        state=state,
        config=CrawlConfig(delay=delay, concurrency=concurrency),
        fetcher=fetch_html,
    )
    result = crawler.run()

    entries = _entries_from_crawler_state(state)
    LOGGER.info(
        "firstdibs art sitemap discovery complete items=%s dealers=%s creators=%s total=%s "
        "art_pages=%s pending=%s interrupted=%s",
        sum(1 for entry in entries if entry.entity_type == "item"),
        sum(1 for entry in entries if entry.entity_type == "dealer"),
        sum(1 for entry in entries if entry.entity_type == "creator"),
        len(entries),
        result.art_sitemap_pages_visited,
        result.pending_sitemap_pages,
        result.interrupted,
    )
    return entries


def _entries_from_crawler_state(state: CrawlerState) -> list[SitemapEntry]:
    entries: list[SitemapEntry] = []
    for item_id, url in state.artwork_urls.items():
        entries.append(
            SitemapEntry(url=url, lastmod=None, entity_type="item", entity_id=item_id)
        )
    for slug, url in state.dealer_urls.items():
        entries.append(
            SitemapEntry(url=url, lastmod=None, entity_type="dealer", entity_id=slug)
        )
    for slug, url in state.creator_urls.items():
        entries.append(
            SitemapEntry(url=url, lastmod=None, entity_type="creator", entity_id=slug)
        )
    return entries


def known_firstdibs_keys_from_paths(paths: Iterable[Path]) -> set[tuple[str, str]]:
    """Load known 1stDibs entity keys from URL list / JSONL files."""
    keys: set[tuple[str, str]] = set()
    for url in load_urls(paths):
        key = firstdibs_entity_from_url(url)
        if key is not None:
            keys.add(key)
    return keys
