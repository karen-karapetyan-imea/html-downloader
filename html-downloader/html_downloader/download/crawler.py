"""
Crawler orchestration: load URLs, run workers with rate limiter, proxy pool,
block detection, adaptive backoff, exponential retry, JSONL output.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from html_downloader.config import CrawlerConfig, load_config
from html_downloader.download.adaptive_backoff import AdaptiveBackoff
from html_downloader.download.fetch_provider import CurlCffiProvider, FetchOutcome, PageFetcher
from html_downloader.download.proxy_pool import ProxyPool
from html_downloader.download.rate_limiter import RateLimiter
from html_downloader.download.result import Result

_API_ERROR_CATEGORIES = frozenset(
    {
        "api_transient",
        "api_permanent",
        "api_rate_limit",
        "api_quota",
        "api_not_found",
        "api_bad_request",
        "api_unavailable",
        "timeout",
        "network",
    }
)


def hash_url(url: str) -> str:
    """SHA1 hash of URL for filename."""
    return hashlib.sha1(url.encode()).hexdigest()


@dataclass
class CrawlStats:
    """Aggregate counts for one crawl. Billable requests are an estimate."""

    provider: str = "curl_cffi"
    attempted: int = 0
    valid_html: int = 0
    failed: int = 0
    blocked: int = 0
    api_errors: int = 0
    retries: int = 0
    elapsed_seconds: float = 0.0
    estimated_billable_requests: int = 0
    traffic_bytes: int = 0
    stopped: bool = False
    stop_reason: str = ""


def _write_result(
    results_path: Path,
    results_lock: threading.Lock,
    payload: dict,
) -> None:
    with results_lock:
        with open(results_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
            handle.flush()


def _payload(result: Result, *, provider_fields: bool) -> dict:
    payload = {
        "url": result.url,
        "filename": result.filename,
        "status_code": result.status_code,
        "error": result.error,
        "block_detected": result.block_detected,
        "block_reason": result.block_reason,
        "duration_ms": result.duration_ms,
        "timestamp": result.timestamp,
    }
    if result.skipped_existing:
        payload["skipped_existing"] = True
    if provider_fields:
        payload["provider"] = result.provider or "decodo"
        payload["api_status_code"] = result.api_status_code
        payload["error_category"] = result.error_category
        payload["retry_count"] = result.retry_count
        payload["html_bytes"] = result.html_bytes
        payload["validated"] = result.validated
    return payload


def _retry_delay(
    config: CrawlerConfig,
    outcome: FetchOutcome,
    delay: float,
    *,
    jitter: bool,
) -> float:
    if outcome.retry_after_seconds is not None:
        return min(max(0.0, outcome.retry_after_seconds), config.retry_max_delay)
    base = min(delay, config.retry_max_delay)
    if jitter and base > 0:
        base = min(base + random.uniform(0, base), config.retry_max_delay)
    return base


def _accumulate(stats: CrawlStats, result: Result) -> None:
    if result.skipped_existing or result.error_category == "not_sent":
        return
    stats.attempted += 1
    stats.retries += result.retry_count
    stats.estimated_billable_requests += result.billable_attempts
    stats.traffic_bytes += result.traffic_bytes
    if result.validated:
        stats.valid_html += 1
    elif result.block_detected:
        stats.blocked += 1
    elif result.error_category in _API_ERROR_CATEGORIES:
        stats.api_errors += 1
    else:
        stats.failed += 1


def worker(
    url: str,
    config: CrawlerConfig,
    rate_limiter: RateLimiter,
    proxy_pool: ProxyPool,
    adaptive_backoff: AdaptiveBackoff,
    results_path: Path,
    results_lock: threading.Lock,
    fetcher: PageFetcher | None = None,
) -> Result:
    """Fetch one URL with rate limit, retries, block detection, and JSONL write."""
    if fetcher is None:
        fetcher = CurlCffiProvider(config, proxy_pool)
    provider_fields = fetcher.provider_name == "decodo"
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
            skipped_existing=True,
            provider=fetcher.provider_name if provider_fields else "",
            error_category="skipped_existing" if provider_fields else "",
            html_bytes=path.stat().st_size if provider_fields else 0,
        )
        _write_result(results_path, results_lock, _payload(result, provider_fields=provider_fields))
        return result

    delay = config.retry_base_delay
    outcome: FetchOutcome | None = None
    retry_count = 0
    billable_attempts = 0
    traffic_bytes = 0

    for attempt in range(config.max_retries + 1):
        if fetcher.job_stopped:
            if outcome is None:
                outcome = fetcher.fetch(url, str(path))
            break
        if attempt == 0:
            adaptive_backoff.wait_if_cooldown()
        if attempt == 0 or fetcher.rate_limit_each_attempt:
            rate_limiter.wait()
        outcome = fetcher.fetch(url, str(path))
        if outcome.billable:
            billable_attempts += 1
        traffic_bytes += outcome.traffic_bytes
        if outcome.error_category != "not_sent":
            adaptive_backoff.notify(outcome.block.is_block)
        if outcome.error == "" and not outcome.block.is_block:
            break
        if not outcome.retryable or outcome.stop_job:
            break
        if attempt < config.max_retries:
            retry_count += 1
            time.sleep(
                _retry_delay(config, outcome, delay, jitter=fetcher.retry_jitter)
            )
            delay *= 2

    if outcome is None:
        outcome = fetcher.fetch(url, str(path))

    timestamp = datetime.now(timezone.utc).isoformat()
    result = Result(
        url=url,
        filename=filename,
        status_code=outcome.status_code,
        error=outcome.error,
        block_detected=outcome.block.is_block,
        block_reason=outcome.block.reason,
        duration_ms=outcome.duration_ms,
        timestamp=timestamp,
        provider=fetcher.provider_name if provider_fields else "",
        api_status_code=outcome.api_status_code if provider_fields else None,
        error_category=outcome.error_category if provider_fields else "",
        retry_count=retry_count,
        html_bytes=outcome.html_bytes,
        validated=outcome.validated,
        billable_attempts=billable_attempts,
        traffic_bytes=traffic_bytes,
    )
    _write_result(results_path, results_lock, _payload(result, provider_fields=provider_fields))
    return result


def run_crawl(
    urls: list[str],
    config: CrawlerConfig | None = None,
    fetcher: PageFetcher | None = None,
) -> CrawlStats:
    """Load config, create output dir and JSONL, run executor, write results."""
    if config is None:
        config = load_config()
    config.output_path.mkdir(parents=True, exist_ok=True)
    results_path = config.results_path
    results_path.parent.mkdir(parents=True, exist_ok=True)
    if not config.results_append and results_path.exists():
        results_path.write_text("", encoding="utf-8")
    results_lock = threading.Lock()

    proxy_pool = ProxyPool([])
    if fetcher is None:
        proxy_pool = ProxyPool.from_file_or_env(config.proxy_file)
        fetcher = CurlCffiProvider(config, proxy_pool)

    rate_limiter = RateLimiter(
        config.requests_per_second,
        jitter_min=config.jitter_min,
        jitter_max=config.jitter_max,
    )
    adaptive_backoff = AdaptiveBackoff(
        window_size=config.block_window_size,
        block_rate_threshold=config.block_rate_threshold,
        cooldown_seconds=config.cooldown_seconds,
        cooldown_duration_seconds=config.cooldown_duration_seconds,
    )

    stats = CrawlStats(provider=fetcher.provider_name)
    started = time.perf_counter()
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
                fetcher,
            ): url
            for url in urls
        }
        for future in as_completed(future_to_url):
            completed += 1
            if completed % 500 == 0:
                print(f"progress: {completed}/{len(urls)}", flush=True)
            try:
                _accumulate(stats, future.result())
            except Exception:
                url = future_to_url[future]
                payload = {
                    "url": url,
                    "filename": "",
                    "status_code": 0,
                    "error": "worker_exception",
                    "block_detected": False,
                    "block_reason": "",
                    "duration_ms": 0,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                if fetcher.provider_name == "decodo":
                    payload.update(
                        {
                            "provider": "decodo",
                            "api_status_code": None,
                            "error_category": "worker_exception",
                            "retry_count": 0,
                            "html_bytes": 0,
                            "validated": False,
                        }
                    )
                _write_result(results_path, results_lock, payload)
                stats.attempted += 1
                stats.failed += 1
    stats.stopped = fetcher.job_stopped
    stats.stop_reason = fetcher.stop_reason
    stats.elapsed_seconds = time.perf_counter() - started
    return stats
