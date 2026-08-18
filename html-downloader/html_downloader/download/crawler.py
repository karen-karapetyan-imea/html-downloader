"""
Crawler orchestration: load URLs, run workers with rate limiter, proxy pool,
block detection, adaptive backoff, exponential retry, JSONL output.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import Session

from html_downloader.config import CrawlerConfig, load_config
from html_downloader.download.adaptive_backoff import AdaptiveBackoff
from html_downloader.download.block_detector import BlockInfo
from html_downloader.download.fetcher import create_session, fetch
from html_downloader.download.proxy_pool import ProxyPool
from html_downloader.download.rate_limiter import RateLimiter
from html_downloader.download.result import Result


def hash_url(url: str) -> str:
    """SHA1 hash of URL for filename."""
    return hashlib.sha1(url.encode()).hexdigest()


_thread_local = threading.local()


def _get_session(impersonate: str) -> Session:
    """One Session per worker thread (connection pooling)."""
    if not hasattr(_thread_local, "session"):
        _thread_local.session = create_session(impersonate=impersonate)
    return _thread_local.session


def _write_result(
    results_path: Path,
    results_lock: threading.Lock,
    payload: dict,
) -> None:
    with results_lock:
        with open(results_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()


def worker(
    url: str,
    config: CrawlerConfig,
    rate_limiter: RateLimiter,
    proxy_pool: ProxyPool,
    adaptive_backoff: AdaptiveBackoff,
    results_path: Path,
    results_lock: threading.Lock,
) -> Result:
    """Fetch one URL with rate limit, proxy, retries, block detection, and JSONL write."""
    filename = hash_url(url) + ".html"
    path = config.output_path / filename

    if config.skip_existing and path.is_file() and path.stat().st_size > 0:
        timestamp = datetime.now(timezone.utc).isoformat()
        result = Result(
            url=url,
            filename=filename,
            status_code=200,
            error="",
            block_detected=False,
            block_reason="",
            duration_ms=0,
            timestamp=timestamp,
        )
        _write_result(
            results_path,
            results_lock,
            {
                "url": result.url,
                "filename": result.filename,
                "status_code": result.status_code,
                "error": result.error,
                "block_detected": result.block_detected,
                "block_reason": result.block_reason,
                "duration_ms": result.duration_ms,
                "timestamp": result.timestamp,
                "skipped_existing": True,
            },
        )
        return result

    adaptive_backoff.wait_if_cooldown()
    rate_limiter.wait()

    session = _get_session(config.impersonate)
    proxy_dict = proxy_pool.get_next()

    status_code = 0
    err_msg = ""
    block_info: BlockInfo = BlockInfo(False, "")
    duration_ms = 0
    delay = config.retry_base_delay

    for attempt in range(config.max_retries + 1):
        status_code, err_msg, block_info, duration_ms = fetch(
            session, url, str(path), config, proxy_dict
        )
        adaptive_backoff.notify(block_info.is_block)

        if err_msg == "" and not block_info.is_block:
            break
        if attempt < config.max_retries:
            time.sleep(min(delay, config.retry_max_delay))
            delay *= 2

    timestamp = datetime.now(timezone.utc).isoformat()
    result = Result(
        url=url,
        filename=filename,
        status_code=status_code,
        error=err_msg,
        block_detected=block_info.is_block,
        block_reason=block_info.reason,
        duration_ms=duration_ms,
        timestamp=timestamp,
    )

    _write_result(
        results_path,
        results_lock,
        {
            "url": result.url,
            "filename": result.filename,
            "status_code": result.status_code,
            "error": result.error,
            "block_detected": result.block_detected,
            "block_reason": result.block_reason,
            "duration_ms": result.duration_ms,
            "timestamp": result.timestamp,
        },
    )
    return result


def run_crawl(
    urls: list[str],
    config: CrawlerConfig | None = None,
) -> None:
    """Load config, create output dir and JSONL, run executor, write results."""
    if config is None:
        config = load_config()
    config.output_path.mkdir(parents=True, exist_ok=True)
    results_path = config.results_path
    results_path.parent.mkdir(parents=True, exist_ok=True)
    if not config.results_append and results_path.exists():
        results_path.write_text("", encoding="utf-8")
    results_lock = threading.Lock()

    rate_limiter = RateLimiter(
        config.requests_per_second,
        jitter_min=config.jitter_min,
        jitter_max=config.jitter_max,
    )
    proxy_pool = ProxyPool.from_file_or_env(config.proxy_file)
    adaptive_backoff = AdaptiveBackoff(
        window_size=config.block_window_size,
        block_rate_threshold=config.block_rate_threshold,
        cooldown_seconds=config.cooldown_seconds,
        cooldown_duration_seconds=config.cooldown_duration_seconds,
    )

    print(f"Starting crawl: {len(urls)} URLs -> {results_path}", flush=True)
    completed = 0
    with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
        future_to_url = {
            executor.submit(
                worker,
                url,
                config,
                rate_limiter,
                proxy_pool,
                adaptive_backoff,
                results_path,
                results_lock,
            ): url
            for url in urls
        }
        for future in as_completed(future_to_url):
            completed += 1
            if completed % 500 == 0:
                print(f"progress: {completed}/{len(urls)}", flush=True)
            try:
                future.result()
            except Exception:
                url = future_to_url[future]
                _write_result(
                    results_path,
                    results_lock,
                    {
                        "url": url,
                        "filename": "",
                        "status_code": 0,
                        "error": "worker_exception",
                        "block_detected": False,
                        "block_reason": "",
                        "duration_ms": 0,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
