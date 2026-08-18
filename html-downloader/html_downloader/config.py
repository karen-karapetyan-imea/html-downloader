"""
Centralized configuration for the stealth crawler.
Safe RPS ranges (documented): datacenter 2-8, residential 5-15, mobile 1-5.
Workers: datacenter 8-24, residential 16-32, mobile 4-12.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CrawlerConfig:
    """Crawler configuration with safe defaults."""

    max_workers: int = 16
    requests_per_second: float = 8.0

    timeout: int = 20
    max_retries: int = 3
    retry_base_delay: float = 1.0
    retry_max_delay: float = 30.0

    output_dir: str = "output"
    results_file: str = "results.jsonl"
    urls_file: str = "urls.txt"
    proxy_file: str | None = None

    jitter_min: float = 0.5
    jitter_max: float = 2.0

    block_status_codes: tuple[int, ...] = (403, 429, 503)
    block_body_scan_bytes: int = 16384
    block_keywords: tuple[str, ...] = (
        "cloudflare",
        "access denied",
        "blocked",
        "captcha",
        "challenge",
        "rate limit",
        "ddos",
        "akamai",
    )

    block_window_size: int = 100
    block_rate_threshold: float = 0.15
    cooldown_seconds: float = 45.0
    cooldown_duration_seconds: float = 60.0

    impersonate: str = "chrome"

    skip_existing: bool = False
    results_append: bool = True

    def __post_init__(self) -> None:
        if self.proxy_file is None:
            self.proxy_file = os.environ.get("CRAWLER_PROXY_FILE")
        self._output_path = Path(self.output_dir)
        self._results_path = Path(self.results_file)

    @property
    def output_path(self) -> Path:
        return self._output_path

    @property
    def results_path(self) -> Path:
        return self._results_path


def load_config(
    urls_file: str | None = None,
    output_dir: str | None = None,
    results_file: str | None = None,
    proxy_file: str | None = None,
    max_workers: int | None = None,
    requests_per_second: float | None = None,
    skip_existing: bool | None = None,
    results_append: bool | None = None,
) -> CrawlerConfig:
    """Load config with optional overrides (e.g. from CLI)."""
    cfg = CrawlerConfig()
    if urls_file is not None:
        cfg.urls_file = urls_file
    if output_dir is not None:
        cfg.output_dir = output_dir
        cfg._output_path = Path(output_dir)
    if results_file is not None:
        cfg.results_file = results_file
        cfg._results_path = Path(results_file)
    if proxy_file is not None:
        cfg.proxy_file = proxy_file
    if max_workers is not None:
        cfg.max_workers = min(max_workers, 64)
    if requests_per_second is not None:
        cfg.requests_per_second = max(0.1, requests_per_second)
    if skip_existing is not None:
        cfg.skip_existing = skip_existing
    if results_append is not None:
        cfg.results_append = results_append
    return cfg
