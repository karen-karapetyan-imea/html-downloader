"""Fetch providers for marketplace downloads.

The crawler schedules URLs and writes results. A provider performs one HTTP
attempt and, on success, writes the raw HTML bytes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from html_downloader.config import CrawlerConfig
from html_downloader.download.block_detector import BlockInfo
from html_downloader.download.fetcher import create_session, fetch
from html_downloader.download.proxy_pool import ProxyPool


@dataclass
class FetchOutcome:
    """One physical fetch attempt."""

    status_code: int
    error: str
    block: BlockInfo
    duration_ms: int
    retryable: bool
    provider: str
    retry_after_seconds: float | None = None
    error_category: str = ""
    api_status_code: int | None = None
    html_bytes: int = 0
    validated: bool = False
    billable: bool = False
    traffic_bytes: int = 0
    stop_job: bool = False


class PageFetcher:
    """One attempt per fetch() call. The crawler owns the retry loop."""

    provider_name = "base"
    retry_jitter = False
    rate_limit_each_attempt = False

    def fetch(self, url: str, path: str) -> FetchOutcome:
        raise NotImplementedError

    @property
    def job_stopped(self) -> bool:
        return False

    @property
    def stop_reason(self) -> str:
        return ""


class CurlCffiProvider(PageFetcher):
    """Existing curl_cffi fetch. One session and one proxy per worker URL."""

    provider_name = "curl_cffi"
    retry_jitter = False
    rate_limit_each_attempt = False

    def __init__(self, config: CrawlerConfig, proxy_pool: ProxyPool) -> None:
        self._config = config
        self._proxy_pool = proxy_pool
        self._local = threading.local()

    def fetch(self, url: str, path: str) -> FetchOutcome:
        status, err, block, duration_ms = fetch(
            self._session(),
            url,
            path,
            self._config,
            self._proxy_for(url),
        )
        success = err == "" and not block.is_block
        html_bytes = 0
        if success:
            try:
                html_bytes = Path(path).stat().st_size
            except OSError:
                html_bytes = 0
        if success:
            category = "success"
        elif block.is_block:
            category = "target_block"
        elif err == "non-200":
            category = "target_http"
        else:
            category = "transport"
        return FetchOutcome(
            status_code=status,
            error=err,
            block=block,
            duration_ms=duration_ms,
            retryable=not success,
            provider=self.provider_name,
            error_category=category,
            html_bytes=html_bytes,
            validated=success,
        )

    def _session(self):
        if not hasattr(self._local, "session"):
            self._local.session = create_session(self._config.impersonate)
        return self._local.session

    def _proxy_for(self, url: str) -> dict[str, str] | None:
        """Keep the same proxy for every retry of one URL on this worker."""
        if getattr(self._local, "url", None) != url:
            self._local.url = url
            self._local.proxy = self._proxy_pool.get_next()
        return self._local.proxy
