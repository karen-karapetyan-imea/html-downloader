"""MutualArt discovery: HTML sitemap A-Z indexes for artists, orgs, exhibitions, auctions."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from html_downloader.discover.registry import load_urls, normalize_url
from html_downloader.discover.sitemap import SitemapEntry
from html_downloader.discover.urls import (
    MUTUALART_ARTIST_RE,
    MUTUALART_AUCTION_RE,
    MUTUALART_EXHIBITION_RE,
    MUTUALART_ORGANIZATION_RE,
    mutualart_entity_from_url,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_MUTUALART_SITEMAP = "https://www.mutualart.com/sitemap"
DEFAULT_MUTUALART_CONCURRENCY = 4
DEFAULT_MUTUALART_MAX_RETRIES = 3

_ARTISTS_INDEX_RE = re.compile(r"/ArtistsIndex/([a-z])/?$", re.IGNORECASE)
_ORGS_INDEX_RE = re.compile(r"/SiteMapOrganizations/([A-Za-z])/?$", re.IGNORECASE)
_EVENTS_INDEX_RE = re.compile(r"/SiteMapEvents/([A-Za-z])/?$", re.IGNORECASE)
_HREF_RE = re.compile(r"""href=["']([^"']+)["']""", re.IGNORECASE)

FetchHtmlFn = Callable[[str], str]

_thread_local = threading.local()


def _thread_session() -> Any:
    session = getattr(_thread_local, "session", None)
    if session is None:
        from curl_cffi import Session

        session = Session(impersonate="chrome")
        _thread_local.session = session
    return session


def _normalize_index_url(url: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path if parts.path.endswith("/") else parts.path + "/"
    return urlunsplit((parts.scheme.lower() or "https", parts.netloc.lower(), path, "", ""))


def _looks_like_bot_challenge(body: bytes) -> bool:
    """Return True for MutualArt CAPTCHA / HTTP 582 error pages."""
    lower = body.lower()
    if b"g-recaptcha" in lower or b"error code\":582" in lower or b"error_occurred_582" in lower:
        return True
    if b"just a moment" in lower or b"cf-chl" in lower:
        return True
    if b"human verification" in lower or b"captcha-container" in lower:
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


def extract_letter_index_urls(html: str, base_url: str = DEFAULT_MUTUALART_SITEMAP) -> list[str]:
    """Return absolute A-Z index URLs found on the MutualArt HTML sitemap root."""
    found: list[str] = []
    seen: set[str] = set()
    for href in _HREF_RE.findall(html):
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute = urljoin(base_url, href.split("?")[0].split("#")[0])
        parts = urlsplit(absolute)
        if not parts.netloc.lower().endswith("mutualart.com"):
            continue
        path = parts.path
        if not (
            _ARTISTS_INDEX_RE.search(path)
            or _ORGS_INDEX_RE.search(path)
            or _EVENTS_INDEX_RE.search(path)
        ):
            continue
        normalized = _normalize_index_url(absolute)
        if normalized not in seen:
            seen.add(normalized)
            found.append(normalized)
    return found


def _canonical_entity_url(url: str) -> str | None:
    """Build a canonical entity URL from a matched MutualArt page URL."""
    for pattern, kind in (
        (MUTUALART_ARTIST_RE, "Artist"),
        (MUTUALART_ORGANIZATION_RE, "Organization"),
        (MUTUALART_EXHIBITION_RE, "Exhibition"),
        (MUTUALART_AUCTION_RE, "Auction"),
    ):
        match = pattern.search(url)
        if match:
            slug = match.group(1)
            entity_id = match.group(2)
            return f"https://www.mutualart.com/{kind}/{slug}/{entity_id}"
    return None


def extract_entity_urls(html: str, base_url: str) -> list[str]:
    """Return unique canonical MutualArt entity URLs from an HTML page."""
    urls: list[str] = []
    seen: set[str] = set()
    for href in _HREF_RE.findall(html):
        if href.startswith(("#", "javascript:", "mailto:")):
            continue
        absolute = urljoin(base_url, href.split("?")[0].split("#")[0])
        normalized = normalize_url(absolute)
        if not normalized:
            continue
        if mutualart_entity_from_url(normalized) is None:
            continue
        canonical = _canonical_entity_url(normalized)
        if canonical is None or canonical in seen:
            continue
        seen.add(canonical)
        urls.append(canonical)
    return urls


def _fetch_with_retries(
    url: str,
    *,
    fetch_html: FetchHtmlFn,
    max_retries: int = DEFAULT_MUTUALART_MAX_RETRIES,
) -> str | None:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        if attempt > 0:
            backoff = 2 ** (attempt - 1)
            LOGGER.info(
                "mutualart retry %s/%s url=%s backoff=%ss",
                attempt + 1,
                max_retries,
                url,
                backoff,
            )
            time.sleep(backoff)
        try:
            return fetch_html(url)
        except Exception as exc:
            last_error = exc
            LOGGER.warning("mutualart page failed url=%s error=%s", url, exc)
    if last_error is not None:
        LOGGER.warning(
            "mutualart giving up url=%s after %s attempts last_error=%s",
            url,
            max_retries,
            last_error,
        )
    return None


def fetch_mutualart_sitemap_entries(
    sitemap_url: str = DEFAULT_MUTUALART_SITEMAP,
    *,
    concurrency: int = DEFAULT_MUTUALART_CONCURRENCY,
    proxy: dict[str, str] | None = None,
    fetch_html: FetchHtmlFn | None = None,
) -> list[SitemapEntry]:
    """Crawl MutualArt HTML sitemap A-Z indexes and return entity URLs."""
    if fetch_html is None:

        def fetch_html(url: str, _proxy: dict[str, str] | None = proxy) -> str:
            return fetch_html_stealth(url, session=_thread_session(), proxy=_proxy)

    root_html = _fetch_with_retries(sitemap_url, fetch_html=fetch_html)
    if root_html is None:
        raise RuntimeError(f"failed to fetch MutualArt sitemap root url={sitemap_url}")

    letter_urls = extract_letter_index_urls(root_html, sitemap_url)
    if not letter_urls:
        raise RuntimeError(f"no A-Z index links found on MutualArt sitemap url={sitemap_url}")

    LOGGER.info(
        "mutualart sitemap letter indexes=%s concurrency=%s",
        len(letter_urls),
        concurrency,
    )

    best_by_key: dict[tuple[str, str], SitemapEntry] = {}
    failed_pages = 0

    def fetch_one(page_url: str) -> tuple[str, str | None]:
        return page_url, _fetch_with_retries(page_url, fetch_html=fetch_html)

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(fetch_one, page_url): page_url for page_url in letter_urls}
        for future in as_completed(futures):
            page_url, html = future.result()
            if html is None:
                failed_pages += 1
                continue
            for entity_url in extract_entity_urls(html, page_url):
                key = mutualart_entity_from_url(entity_url)
                if key is None:
                    continue
                entity_type, entity_id = key
                if key not in best_by_key:
                    best_by_key[key] = SitemapEntry(
                        url=entity_url,
                        lastmod=None,
                        entity_type=entity_type,
                        entity_id=entity_id,
                    )

    if failed_pages:
        LOGGER.warning(
            "mutualart sitemap failed letter pages=%s (continuing with partial results)",
            failed_pages,
        )

    counts: dict[str, int] = {}
    for entity_type, _entity_id in best_by_key:
        counts[entity_type] = counts.get(entity_type, 0) + 1
    LOGGER.info(
        "mutualart discovery complete total=%s artists=%s organizations=%s exhibitions=%s auctions=%s",
        len(best_by_key),
        counts.get("artist", 0),
        counts.get("organization", 0),
        counts.get("exhibition", 0),
        counts.get("auction", 0),
    )
    return list(best_by_key.values())


def known_mutualart_keys_from_paths(paths: Iterable[Path]) -> set[tuple[str, str]]:
    """Load known MutualArt entity keys from URL list / JSONL files."""
    keys: set[tuple[str, str]] = set()
    for url in load_urls(paths):
        key = mutualart_entity_from_url(url)
        if key is not None:
            keys.add(key)
    return keys
