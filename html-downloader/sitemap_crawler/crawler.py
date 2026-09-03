"""HTTP fetch and BFS crawl logic for 1stDibs art HTML sitemap."""

from __future__ import annotations

import logging
import signal
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from sitemap_crawler.parser import is_art_sitemap_page, parse_page_links
from sitemap_crawler.storage import CrawlerState

LOGGER = logging.getLogger(__name__)

CHECKPOINT_EVERY = 50
PROGRESS_EVERY = 10


@dataclass
class CrawlConfig:
    delay: float = 0.5
    timeout: float = 30.0
    max_retries: int = 3
    max_pages: int | None = None
    checkpoint_every: int = CHECKPOINT_EVERY
    concurrency: int = 10


@dataclass
class CrawlResult:
    art_sitemap_pages_visited: int = 0
    pending_sitemap_pages: int = 0
    artwork_urls: int = 0
    dealer_urls: int = 0
    creator_urls: int = 0
    total_unique_urls: int = 0
    interrupted: bool = False


class SitemapCrawler:
    def __init__(
        self,
        state: CrawlerState,
        config: CrawlConfig | None = None,
        fetcher: Callable[[str], str] | None = None,
    ) -> None:
        self.state = state
        self.config = config or CrawlConfig()
        self._fetcher = fetcher or self._default_fetch
        self._shutdown_requested = False
        self._previous_handler: Any = None
        self._lock = threading.Lock()
        self._pages_since_checkpoint = 0
        self._pages_since_progress = 0

    def run(self) -> CrawlResult:
        self._install_signal_handlers()
        try:
            return self._run_crawl()
        finally:
            self._restore_signal_handlers()

    def _run_crawl(self) -> CrawlResult:
        pending: deque[str] = deque(self.state.pending_sitemap_pages)
        batch_size = max(1, self.config.concurrency) * 4

        while pending and not self._shutdown_requested:
            if self._reached_max_pages():
                LOGGER.info("reached max_pages=%s; stopping", self.config.max_pages)
                break

            batch: list[str] = []
            while pending and len(batch) < batch_size:
                page_url = pending.popleft()
                if page_url in self.state.visited_sitemap_pages:
                    continue
                if not is_art_sitemap_page(page_url):
                    continue
                batch.append(page_url)

            if not batch:
                break

            with ThreadPoolExecutor(max_workers=max(1, self.config.concurrency)) as pool:
                futures = {pool.submit(self._process_page, url): url for url in batch}
                for future in as_completed(futures):
                    if self._shutdown_requested:
                        break
                    page_url = futures[future]
                    try:
                        children = future.result()
                    except Exception as exc:
                        LOGGER.warning(
                            "failed to process sitemap page url=%s error=%s",
                            page_url,
                            exc,
                        )
                        with self._lock:
                            self.state.visited_sitemap_pages.add(page_url)
                        continue

                    with self._lock:
                        for child in children:
                            if (
                                child not in self.state.visited_sitemap_pages
                                and child not in pending
                            ):
                                pending.append(child)

            self.state.pending_sitemap_pages = list(pending)
            if self._pages_since_checkpoint >= self.config.checkpoint_every:
                self.state.save()
                self._pages_since_checkpoint = 0

            self._throttle()

        self.state.pending_sitemap_pages = list(pending)
        self.state.save()
        return self._build_result(interrupted=self._shutdown_requested)

    def _process_page(self, page_url: str) -> list[str]:
        try:
            html = self._fetcher(page_url)
        except Exception as exc:
            LOGGER.warning("failed to fetch sitemap page url=%s error=%s", page_url, exc)
            with self._lock:
                self.state.visited_sitemap_pages.add(page_url)
            return []

        internal_urls, sitemap_children = parse_page_links(html, page_url)
        with self._lock:
            self.state.visited_sitemap_pages.add(page_url)
            for url in internal_urls:
                self.state.ingest_url(url)
            self._pages_since_checkpoint += 1
            self._pages_since_progress += 1
            if self._pages_since_progress >= PROGRESS_EVERY:
                self._log_progress(len(self.state.pending_sitemap_pages))
                self._pages_since_progress = 0

        return sitemap_children

    def _reached_max_pages(self) -> bool:
        if self.config.max_pages is None:
            return False
        return self.state.art_sitemap_pages_visited() >= self.config.max_pages

    def _log_progress(self, pending_count: int) -> None:
        LOGGER.info(
            "Art sitemap pages visited: %s\n"
            "Pending sitemap pages: %s\n\n"
            "Artwork URLs: %s\n"
            "Dealer URLs: %s\n"
            "Creator URLs: %s\n\n"
            "Total unique URLs: %s",
            self.state.art_sitemap_pages_visited(),
            pending_count,
            len(self.state.artwork_urls),
            len(self.state.dealer_urls),
            len(self.state.creator_urls),
            self.state.total_unique_urls(),
        )

    def _build_result(self, *, interrupted: bool) -> CrawlResult:
        return CrawlResult(
            art_sitemap_pages_visited=self.state.art_sitemap_pages_visited(),
            pending_sitemap_pages=len(self.state.pending_sitemap_pages),
            artwork_urls=len(self.state.artwork_urls),
            dealer_urls=len(self.state.dealer_urls),
            creator_urls=len(self.state.creator_urls),
            total_unique_urls=self.state.total_unique_urls(),
            interrupted=interrupted,
        )

    def _default_fetch(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries):
            if attempt > 0:
                backoff = 2 ** (attempt - 1)
                LOGGER.info(
                    "retry %s/%s url=%s backoff=%ss",
                    attempt + 1,
                    self.config.max_retries,
                    url,
                    backoff,
                )
                time.sleep(backoff)
            try:
                return _fetch_with_curl_cffi(url, timeout=self.config.timeout)
            except Exception as exc:
                last_error = exc
                LOGGER.warning("curl_cffi fetch failed url=%s error=%s", url, exc)
            try:
                return _fetch_with_httpx(url, timeout=self.config.timeout)
            except Exception as exc:
                last_error = exc
                LOGGER.warning("httpx fetch failed url=%s error=%s", url, exc)
        raise RuntimeError(f"fetch failed after {self.config.max_retries} attempts url={url}") from last_error

    def _throttle(self) -> None:
        if self.config.delay > 0:
            time.sleep(self.config.delay)

    def _install_signal_handlers(self) -> None:
        def handler(signum: int, frame: Any) -> None:
            del signum, frame
            if self._shutdown_requested:
                LOGGER.warning("forced shutdown")
                raise SystemExit(1)
            self._shutdown_requested = True
            LOGGER.warning("shutdown requested; saving state and exiting after current batch")

        try:
            self._previous_handler = signal.signal(signal.SIGINT, handler)
        except ValueError:
            pass

    def _restore_signal_handlers(self) -> None:
        if self._previous_handler is not None:
            try:
                signal.signal(signal.SIGINT, self._previous_handler)
            except ValueError:
                pass


def _looks_like_bot_challenge(body: bytes) -> bool:
    lower = body.lower()
    if b"/sitemap/" in lower or b"art sitemap" in lower or b"sitemap" in lower[:2048]:
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


def _fetch_with_curl_cffi(url: str, *, timeout: float) -> str:
    from curl_cffi import requests

    response = requests.get(url, impersonate="chrome", timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"status={response.status_code} url={url}")
    body = bytes(response.content or b"")
    if not body:
        raise RuntimeError(f"empty body url={url}")
    if _looks_like_bot_challenge(body):
        raise RuntimeError(f"bot challenge url={url}")
    return body.decode("utf-8", errors="replace")


def _fetch_with_httpx(url: str, *, timeout: float) -> str:
    import httpx

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    with httpx.Client(headers=headers, follow_redirects=True, timeout=timeout) as client:
        response = client.get(url)
    if response.status_code != 200:
        raise RuntimeError(f"status={response.status_code} url={url}")
    body = bytes(response.content or b"")
    if _looks_like_bot_challenge(body):
        raise RuntimeError(f"bot challenge url={url}")
    return response.text


def path_category_counts(urls: set[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for url in urls:
        path = urlsplit(url).path
        segments = [segment for segment in path.split("/") if segment]
        key = f"/{segments[0]}/" if segments else "/"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))
