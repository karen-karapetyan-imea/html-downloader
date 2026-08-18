"""
curl_cffi fetcher: Chrome TLS fingerprint, per-request proxy, write HTML to disk.
One Session per worker; pass proxy_dict per request for rotation.
"""

from __future__ import annotations

import time
from typing import Any

from curl_cffi import Session

from html_downloader.config import CrawlerConfig
from html_downloader.download.block_detector import BlockInfo, is_block
from html_downloader.download.headers import get_headers


def create_session(impersonate: str = "chrome") -> Session:
    """Create a curl_cffi Session with browser impersonation."""
    return Session(impersonate=impersonate)


def fetch(
    session: Session,
    url: str,
    path: str,
    config: CrawlerConfig,
    proxy_dict: dict[str, str] | None = None,
) -> tuple[int, str, BlockInfo, int]:
    """
    Fetch URL and write body to path. Returns (status_code, error_message, block_info, duration_ms).
    Empty error_message means success. Memory: write content to file then discard.
    """
    start = time.perf_counter()
    try:
        kwargs: dict[str, Any] = {
            "url": url,
            "timeout": config.timeout,
            "headers": get_headers(),
        }
        if proxy_dict:
            kwargs["proxies"] = proxy_dict

        resp = session.get(**kwargs)
        duration_ms = int((time.perf_counter() - start) * 1000)
        status_code = resp.status_code
        body = resp.content

        if status_code == 200 and body:
            with open(path, "wb") as handle:
                handle.write(body)

        block = is_block(
            status_code,
            resp.headers,
            body,
            block_status_codes=config.block_status_codes,
            scan_bytes=config.block_body_scan_bytes,
            keywords=config.block_keywords,
        )

        if status_code != 200:
            return status_code, "non-200", block, duration_ms
        return status_code, "", block, duration_ms

    except Exception as exc:
        duration_ms = int((time.perf_counter() - start) * 1000)
        return 0, str(exc), BlockInfo(False, ""), duration_ms
