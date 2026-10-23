"""Decodo Web Data API client for synchronous POST /v1/scrape.

The client performs one HTTP call per scrape(). Retries stay in the crawler.
Documented contract: https://help.decodo.com/docs/web-data-api-scrape-real-time-requests
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

from html_downloader.config import CrawlerConfig, DecodoSettings
from html_downloader.download.block_detector import BlockInfo, is_block
from html_downloader.download.fetch_provider import FetchOutcome, PageFetcher

SCRAPE_URL = "https://data.decodo.com/v1/scrape"
RATE_LIMIT_CIRCUIT = 3

# 429 is the only documented signal that the subscription may be exhausted.
# There is no separate quota status code, and the response body schema is not
# specified, so these substrings are a best-effort stop switch.
_QUOTA_MARKERS = (
    "quota",
    "no requests left",
    "insufficient",
    "balance",
    "usage limit",
    "exceeded your plan",
)

# Billing page: 500, 524, and 613 are not charged. 612 and 999 are not on that
# table, so the estimate excludes them rather than treating them as free.
_UNBILLED_STATUSES = frozenset({500, 524, 612, 613, 999})
_TRANSIENT_STATUSES = frozenset({204, 500, 524, 612, 613})


def redact_secret(text: str, secret: str) -> str:
    """Remove an API key from text that might be logged or stored."""
    if not text or not secret:
        return text
    return text.replace(secret, "[REDACTED]")


def _scrub(value: object, secret: str) -> object:
    if isinstance(value, str):
        return redact_secret(value, secret)
    if isinstance(value, dict):
        return {key: _scrub(item, secret) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_scrub(item, secret) for item in value)
    if isinstance(value, list):
        return [_scrub(item, secret) for item in value]
    return value


class _SecretLogFilter(logging.Filter):
    """Drop the API key from httpx/httpcore log records."""

    def __init__(self, secret: str) -> None:
        super().__init__()
        self._secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secret:
            return True
        if isinstance(record.msg, str):
            record.msg = redact_secret(record.msg, self._secret)
        if record.args:
            record.args = _scrub(record.args, self._secret)  # type: ignore[assignment]
        return True


def parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After delta or HTTP-date. Decodo does not document this header."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, parsed.timestamp() - time.time())


def _is_billable(status: int, body_nonempty: bool) -> bool:
    """Estimate from the documented billing table. Not a dollar amount."""
    if status in _UNBILLED_STATUSES:
        return False
    if status in (200, 204) or (200 <= status < 300):
        return True
    if 400 <= status < 500:
        return body_nonempty
    return False


def _traffic_bytes(headers: httpx.Headers) -> int:
    raw = headers.get("x-traffic-generated")
    if raw is None:
        return 0
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


@dataclass
class DecodoHttpResult:
    """Parsed /scrape response before HTML validation."""

    api_status: int
    error: str
    error_category: str
    retryable: bool
    stop_job: bool = False
    retry_after_seconds: float | None = None
    billable: bool = False
    traffic_bytes: int = 0
    target_status: int | None = None
    content: str | None = None
    target_headers: dict[str, str] | None = None
    not_sent: bool = False


class DecodoClient:
    """Shared httpx client. One instance per download job."""

    def __init__(
        self,
        settings: DecodoSettings,
        *,
        http_client: httpx.Client | None = None,
        semaphore: threading.Semaphore | None = None,
        rate_limit_circuit: int = RATE_LIMIT_CIRCUIT,
    ) -> None:
        self._settings = settings
        self._timeout = httpx.Timeout(
            settings.timeout_seconds,
            connect=settings.connect_timeout_seconds,
        )
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=self._timeout, follow_redirects=False)
        self._semaphore = semaphore or threading.Semaphore(settings.max_in_flight)
        self._headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {settings.api_key}",
        }
        self._rate_limit_circuit = max(1, rate_limit_circuit)
        self._rate_limits = 0
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._stop_reason = ""
        self._log_filter = _SecretLogFilter(settings.api_key)
        logging.getLogger("httpx").addFilter(self._log_filter)
        logging.getLogger("httpcore").addFilter(self._log_filter)

    def close(self) -> None:
        logging.getLogger("httpx").removeFilter(self._log_filter)
        logging.getLogger("httpcore").removeFilter(self._log_filter)
        if self._owns_client:
            self._client.close()

    @property
    def job_stopped(self) -> bool:
        return self._stop_event.is_set()

    @property
    def stop_reason(self) -> str:
        with self._lock:
            return self._stop_reason

    def scrape(self, url: str) -> DecodoHttpResult:
        """POST one URL. Does not retry and does not write HTML."""
        if self.job_stopped:
            return self._not_sent()
        self._semaphore.acquire()
        try:
            if self.job_stopped:
                return self._not_sent()
            return self._post(url)
        finally:
            self._semaphore.release()

    def _payload(self, url: str) -> dict[str, str]:
        body = {"url": url, "proxy_pool": self._settings.proxy_pool}
        if self._settings.js_rendering:
            body["headless"] = "html"
        return body

    def _post(self, url: str) -> DecodoHttpResult:
        try:
            response = self._client.post(
                SCRAPE_URL,
                json=self._payload(url),
                headers=self._headers,
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            return self._transport("timeout", exc)
        except httpx.TransportError as exc:
            return self._transport("network", exc)
        if response.status_code == 200:
            return self._parse_success(response)
        return self._classify(response)

    def _transport(self, category: str, exc: BaseException) -> DecodoHttpResult:
        detail = redact_secret(str(exc), self._settings.api_key).replace("\n", " ").strip()
        if len(detail) > 240:
            detail = detail[:240]
        error = category if not detail else f"{category}: {detail}"
        return DecodoHttpResult(
            api_status=0,
            error=error,
            error_category=category,
            retryable=True,
        )

    def _not_sent(self) -> DecodoHttpResult:
        return DecodoHttpResult(
            api_status=0,
            error="not_sent",
            error_category="not_sent",
            retryable=False,
            not_sent=True,
        )

    def _stop(self, reason: str) -> None:
        with self._lock:
            self._stop_event.set()
            if not self._stop_reason:
                self._stop_reason = reason

    def _note_rate_limit(self) -> bool:
        """Count a 429. Return True when the circuit opens on this response."""
        with self._lock:
            self._rate_limits += 1
            if self._rate_limits >= self._rate_limit_circuit:
                self._stop_event.set()
                if not self._stop_reason:
                    self._stop_reason = "decodo_rate_limit"
                return True
        return False

    def _parse_success(self, response: httpx.Response) -> DecodoHttpResult:
        traffic = _traffic_bytes(response.headers)
        invalid = DecodoHttpResult(
            api_status=200,
            error="invalid_body",
            error_category="invalid_body",
            retryable=False,
            billable=True,
            traffic_bytes=traffic,
        )
        try:
            payload = response.json()
        except ValueError:
            return invalid
        if not isinstance(payload, dict):
            return invalid
        results = payload.get("results")
        if not isinstance(results, list) or not results or not isinstance(results[0], dict):
            return invalid
        item = results[0]
        content = item.get("content")
        status = item.get("status_code")
        if not isinstance(content, str) or not isinstance(status, int):
            return invalid
        raw_headers = item.get("headers")
        headers: dict[str, str] = {}
        if isinstance(raw_headers, dict):
            headers = {str(key): "" if value is None else str(value) for key, value in raw_headers.items()}
        return DecodoHttpResult(
            api_status=200,
            error="",
            error_category="",
            retryable=False,
            billable=True,
            traffic_bytes=traffic,
            target_status=status,
            content=content,
            target_headers=headers,
        )

    def _classify(self, response: httpx.Response) -> DecodoHttpResult:
        status = response.status_code
        nonempty = bool(response.content)
        traffic = _traffic_bytes(response.headers)
        retry_after = parse_retry_after(response.headers.get("retry-after"))
        billable = _is_billable(status, nonempty)
        try:
            lowered = response.text.lower()
        except Exception:
            lowered = ""

        if status == 429:
            if any(marker in lowered for marker in _QUOTA_MARKERS):
                self._stop("decodo_quota")
                return DecodoHttpResult(
                    api_status=status,
                    error="api_quota",
                    error_category="api_quota",
                    retryable=False,
                    stop_job=True,
                    retry_after_seconds=retry_after,
                    billable=billable,
                    traffic_bytes=traffic,
                )
            opened = self._note_rate_limit()
            return DecodoHttpResult(
                api_status=status,
                error="api_rate_limit",
                error_category="api_rate_limit",
                retryable=not opened,
                stop_job=opened,
                retry_after_seconds=retry_after,
                billable=billable,
                traffic_bytes=traffic,
            )

        if status == 401:
            self._stop("decodo_unauthorized")
            return self._error(status, "api_unauthorized", "api_permanent", billable, traffic, stop=True)
        if status == 403:
            self._stop("decodo_forbidden")
            return self._error(status, "api_forbidden", "api_permanent", billable, traffic, stop=True)
        if status in (400, 422):
            self._stop("decodo_bad_request")
            return self._error(status, "api_bad_request", "api_bad_request", billable, traffic, stop=True)
        if status == 404:
            return self._error(status, "api_not_found", "api_not_found", billable, traffic)
        if status == 999:
            return self._error(status, "api_unavailable", "api_unavailable", billable, traffic)
        if status in _TRANSIENT_STATUSES:
            return self._error(
                status,
                "api_transient",
                "api_transient",
                billable,
                traffic,
                retryable=True,
                retry_after=retry_after,
            )
        if 300 <= status < 400:
            return self._error(status, "api_redirect", "api_permanent", billable, traffic)
        retryable = status >= 500
        category = "api_transient" if retryable else "api_permanent"
        return self._error(
            status,
            "api_error",
            category,
            billable,
            traffic,
            retryable=retryable,
            retry_after=retry_after,
        )

    def _error(
        self,
        status: int,
        error: str,
        category: str,
        billable: bool,
        traffic: int,
        *,
        retryable: bool = False,
        stop: bool = False,
        retry_after: float | None = None,
    ) -> DecodoHttpResult:
        return DecodoHttpResult(
            api_status=status,
            error=error,
            error_category=category,
            retryable=retryable,
            stop_job=stop,
            retry_after_seconds=retry_after,
            billable=billable,
            traffic_bytes=traffic,
        )


def build_decodo_client(settings: DecodoSettings) -> DecodoClient:
    return DecodoClient(settings)


class DecodoProvider(PageFetcher):
    """Validate Decodo content and write raw HTML bytes on success."""

    provider_name = "decodo"
    retry_jitter = True
    rate_limit_each_attempt = True

    def __init__(self, client: DecodoClient, config: CrawlerConfig) -> None:
        self._client = client
        self._config = config

    @property
    def job_stopped(self) -> bool:
        return self._client.job_stopped

    @property
    def stop_reason(self) -> str:
        return self._client.stop_reason

    def fetch(self, url: str, path: str) -> FetchOutcome:
        started = time.perf_counter()
        http_result = self._client.scrape(url)
        duration_ms = int((time.perf_counter() - started) * 1000)
        if http_result.not_sent or http_result.content is None:
            return self._from_http(http_result, duration_ms)
        return self._validate(http_result, path, duration_ms)

    def _from_http(self, http_result: DecodoHttpResult, duration_ms: int) -> FetchOutcome:
        return FetchOutcome(
            status_code=http_result.target_status or 0,
            error=http_result.error,
            block=BlockInfo(False, ""),
            duration_ms=duration_ms,
            retryable=http_result.retryable,
            provider=self.provider_name,
            retry_after_seconds=http_result.retry_after_seconds,
            error_category=http_result.error_category,
            api_status_code=http_result.api_status or None,
            billable=http_result.billable,
            traffic_bytes=http_result.traffic_bytes,
            stop_job=http_result.stop_job,
        )

    def _validate(self, http_result: DecodoHttpResult, path: str, duration_ms: int) -> FetchOutcome:
        content = http_result.content or ""
        body = content.encode("utf-8")
        headers = http_result.target_headers or {}
        target_status = http_result.target_status if http_result.target_status is not None else 0
        block = is_block(
            target_status,
            headers,
            body or None,
            block_status_codes=self._config.block_status_codes,
            scan_bytes=self._config.block_body_scan_bytes,
            keywords=self._config.block_keywords,
        )
        if (
            target_status == 200
            and body
            and not block.is_block
            and self._config.require_window_data
            and b"window.__data" not in body
            and len(body) < 8000
        ):
            block = BlockInfo(True, "missing_window_data")

        base = dict(
            status_code=target_status,
            duration_ms=duration_ms,
            provider=self.provider_name,
            api_status_code=http_result.api_status or None,
            html_bytes=len(body),
            billable=http_result.billable,
            traffic_bytes=http_result.traffic_bytes,
            retryable=False,
        )
        if block.is_block:
            return FetchOutcome(
                error="blocked",
                block=block,
                error_category="target_block",
                validated=False,
                **base,
            )
        if not content.strip() or b"<" not in body:
            return FetchOutcome(
                error="invalid_body",
                block=BlockInfo(False, ""),
                error_category="invalid_body",
                validated=False,
                **base,
            )
        if target_status != 200:
            return FetchOutcome(
                error="non-200",
                block=block,
                error_category="target_block" if block.is_block else "target_http",
                validated=False,
                **base,
            )
        Path(path).write_bytes(body)
        return FetchOutcome(
            error="",
            block=BlockInfo(False, ""),
            error_category="success",
            validated=True,
            **base,
        )
