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

    # Optional per-request header overrides (e.g. SEO bot UA for Imperva sites).
    extra_headers: dict[str, str] | None = None
    # When True, treat small 200 bodies without window.__data as soft blocks.
    require_window_data: bool = False

    skip_existing: bool = False
    results_append: bool = True

    # Marketplace download only. Auction crawls leave this at curl_cffi.
    provider: str = "curl_cffi"

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


PROVIDERS: tuple[str, ...] = ("curl_cffi", "decodo")
# Documented synchronous /scrape open-connection limit.
DECODO_TIMEOUT_CAP_SECONDS = 150.0


def resolve_provider(explicit: str | None = None) -> str:
    """Resolve the marketplace download provider. Default is curl_cffi."""
    raw = explicit if explicit is not None else os.environ.get("DOWNLOAD_PROVIDER")
    name = (raw or "curl_cffi").strip().lower()
    if name not in PROVIDERS:
        allowed = ", ".join(PROVIDERS)
        raise ValueError(f"unknown download provider {name!r}; expected one of: {allowed}")
    return name


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc


@dataclass(frozen=True, slots=True)
class DecodoSettings:
    """Decodo Web Data API settings. The API key is omitted from repr."""

    api_key: str
    timeout_seconds: float = 60.0
    connect_timeout_seconds: float = 10.0
    max_in_flight: int = 4
    max_retries: int = 1
    js_rendering: bool = False
    proxy_pool: str = "premium"
    requests_per_second: float = 1.0

    def __post_init__(self) -> None:
        if not self.api_key or not self.api_key.strip():
            raise ValueError("DECODO_API_KEY is required")
        if self.proxy_pool not in ("premium", "standard"):
            raise ValueError("DECODO_PROXY_POOL must be premium or standard")
        if self.timeout_seconds <= 0 or self.timeout_seconds > DECODO_TIMEOUT_CAP_SECONDS:
            raise ValueError(
                f"DECODO_TIMEOUT_SECONDS must be positive and at most {DECODO_TIMEOUT_CAP_SECONDS:g}"
            )
        if self.connect_timeout_seconds <= 0:
            raise ValueError("DECODO_CONNECT_TIMEOUT_SECONDS must be positive")
        if self.max_in_flight < 1:
            raise ValueError("DECODO_MAX_IN_FLIGHT must be >= 1")
        if self.max_retries < 0:
            raise ValueError("DECODO_MAX_RETRIES must be >= 0")
        if self.requests_per_second <= 0:
            raise ValueError("DECODO_REQUESTS_PER_SECOND must be positive")

    def __repr__(self) -> str:
        return (
            "DecodoSettings(api_key='[REDACTED]', "
            f"timeout_seconds={self.timeout_seconds}, "
            f"connect_timeout_seconds={self.connect_timeout_seconds}, "
            f"max_in_flight={self.max_in_flight}, "
            f"max_retries={self.max_retries}, "
            f"js_rendering={self.js_rendering}, "
            f"proxy_pool={self.proxy_pool!r}, "
            f"requests_per_second={self.requests_per_second})"
        )

    @classmethod
    def from_env(cls) -> DecodoSettings:
        """Load Decodo settings. Raises ValueError when the key or a value is invalid."""
        js_raw = os.environ.get("DECODO_JS_RENDERING", "").strip().lower()
        if js_raw in ("", "0", "false", "off", "none", "no"):
            js_rendering = False
        elif js_raw == "html":
            js_rendering = True
        else:
            raise ValueError("DECODO_JS_RENDERING must be unset or html")

        timeout = _env_float("DECODO_TIMEOUT_SECONDS", 60.0)
        if timeout <= 0:
            raise ValueError("DECODO_TIMEOUT_SECONDS must be positive")
        timeout = min(timeout, DECODO_TIMEOUT_CAP_SECONDS)

        return cls(
            api_key=os.environ.get("DECODO_API_KEY", "").strip(),
            timeout_seconds=timeout,
            connect_timeout_seconds=_env_float("DECODO_CONNECT_TIMEOUT_SECONDS", 10.0),
            max_in_flight=_env_int("DECODO_MAX_IN_FLIGHT", 4),
            max_retries=_env_int("DECODO_MAX_RETRIES", 1),
            js_rendering=js_rendering,
            proxy_pool=os.environ.get("DECODO_PROXY_POOL", "premium").strip().lower() or "premium",
            requests_per_second=_env_float("DECODO_REQUESTS_PER_SECOND", 1.0),
        )
