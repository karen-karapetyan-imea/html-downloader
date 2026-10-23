from __future__ import annotations

import json
import logging
import threading
from datetime import date
from pathlib import Path

import httpx
import pytest

from html_downloader.cli import build_parser, main
from html_downloader.config import DecodoSettings, load_config, resolve_provider
from html_downloader.download.block_detector import BlockInfo
from html_downloader.download.crawler import hash_url, run_crawl, worker
from html_downloader.download.decodo_client import (
    SCRAPE_URL,
    DecodoClient,
    DecodoProvider,
    redact_secret,
)
from html_downloader.download.proxy_pool import ProxyPool
from html_downloader.download.rate_limiter import RateLimiter
from html_downloader.download.adaptive_backoff import AdaptiveBackoff
from html_downloader.download.service import run_download
from html_downloader.paths import html_dir, job_dir, manifest_file, results_file, urls_file

API_KEY = "super-secret-decodo-key"
URL = "https://www.saatchiart.com/art/Example/1/2/view"
HTML = '<html><script>window.__data = {"title": "a & b", "city": "Yerevan"};</script></html>'
CURL_KEYS = {
    "url",
    "filename",
    "status_code",
    "error",
    "block_detected",
    "block_reason",
    "duration_ms",
    "timestamp",
}


class ObservableSemaphore:
    def __init__(self, value: int) -> None:
        self._sem = threading.Semaphore(value)
        self.blocked = threading.Event()

    def acquire(self, blocking: bool = True) -> bool:
        if blocking:
            if self._sem.acquire(blocking=False):
                return True
            self.blocked.set()
            return self._sem.acquire()
        return self._sem.acquire(blocking=False)

    def release(self) -> None:
        self._sem.release()


def _settings(**overrides: object) -> DecodoSettings:
    values: dict[str, object] = {
        "api_key": API_KEY,
        "timeout_seconds": 5,
        "connect_timeout_seconds": 1,
        "max_in_flight": 1,
        "max_retries": 1,
        "js_rendering": False,
        "proxy_pool": "premium",
        "requests_per_second": 1000,
    }
    values.update(overrides)
    return DecodoSettings(**values)  # type: ignore[arg-type]


def _envelope(
    content: object,
    *,
    target_status: int = 200,
    target_headers: dict | None = None,
    traffic: str = "120",
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "results": [
                {
                    "content": content,
                    "headers": target_headers if target_headers is not None else {"content-type": "text/html"},
                    "status_code": target_status,
                    "task_id": "task-1",
                }
            ]
        },
        headers={"x-traffic-generated": traffic, "x-task-id": "task-1"},
    )


def _scripted(responses: list[httpx.Response | Exception]):
    calls: list[httpx.Request] = []
    lock = threading.Lock()
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            calls.append(request)
            item = responses[min(state["n"], len(responses) - 1)]
            state["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    return handler, calls


def _crawl(tmp_path: Path, urls: list[str], responses: list[httpx.Response | Exception], settings: DecodoSettings | None = None, **client_kwargs: object):
    settings = settings or _settings()
    handler, calls = _scripted(responses)
    client = DecodoClient(
        settings,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        **client_kwargs,  # type: ignore[arg-type]
    )
    config = load_config(
        output_dir=str(tmp_path / "html"),
        results_file=str(tmp_path / "results.jsonl"),
        proxy_file="",
        max_workers=settings.max_in_flight,
        requests_per_second=settings.requests_per_second,
        results_append=False,
    )
    config.provider = "decodo"
    config.jitter_min = 0.0
    config.jitter_max = 0.0
    config.max_retries = settings.max_retries
    stats = run_crawl(urls, config, fetcher=DecodoProvider(client, config))
    client.close()
    return stats, calls, config


def _records(config) -> list[dict]:
    text = config.results_path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return [json.loads(line) for line in text.splitlines()]


def test_provider_defaults_to_curl_cffi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOWNLOAD_PROVIDER", raising=False)
    assert resolve_provider(None) == "curl_cffi"
    assert load_config().provider == "curl_cffi"
    with pytest.raises(ValueError, match="unknown download provider"):
        resolve_provider("hybrid")


def test_settings_redact_api_key_and_reject_png(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECODO_API_KEY", API_KEY)
    monkeypatch.setenv("DECODO_TIMEOUT_SECONDS", "200")
    monkeypatch.setenv("DECODO_JS_RENDERING", "html")
    monkeypatch.setenv("DECODO_PROXY_POOL", "standard")
    settings = DecodoSettings.from_env()
    assert settings.timeout_seconds == 150
    assert settings.js_rendering is True
    assert settings.proxy_pool == "standard"
    assert API_KEY not in repr(settings)
    assert "[REDACTED]" in repr(settings)
    monkeypatch.setenv("DECODO_JS_RENDERING", "png")
    with pytest.raises(ValueError, match="DECODO_JS_RENDERING"):
        DecodoSettings.from_env()
    monkeypatch.setenv("DECODO_JS_RENDERING", "")
    monkeypatch.delenv("DECODO_API_KEY")
    with pytest.raises(ValueError, match="DECODO_API_KEY"):
        DecodoSettings.from_env()


def test_redact_secret_replaces_key() -> None:
    assert redact_secret(f"Bearer {API_KEY} failed", API_KEY) == "Bearer [REDACTED] failed"
    client = DecodoClient(_settings())
    try:
        record = logging.LogRecord(
            name="httpx",
            level=logging.DEBUG,
            pathname="",
            lineno=0,
            msg="Authorization: Bearer %s",
            args=(API_KEY,),
            exc_info=None,
        )
        assert client._log_filter.filter(record) is True
        assert API_KEY not in record.getMessage()
        assert "[REDACTED]" in record.getMessage()
    finally:
        client.close()


def test_decodo_request_and_raw_html(tmp_path: Path) -> None:
    stats, calls, config = _crawl(tmp_path, [URL], [_envelope(HTML)])
    request = calls[0]
    assert str(request.url) == SCRAPE_URL
    assert request.method == "POST"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["accept"] == "application/json"
    assert request.headers["content-type"].startswith("application/json")
    body = json.loads(request.content)
    assert body == {"url": URL, "proxy_pool": "premium"}
    assert "headless" not in body
    assert "markdown" not in body
    assert "parse" not in body

    written = (tmp_path / "html" / f"{hash_url(URL)}.html").read_bytes()
    assert written == HTML.encode("utf-8")
    assert b"window.__data" in written
    assert b"task_id" not in written
    assert b'"results"' not in written

    record = _records(config)[0]
    assert record["filename"] == f"{hash_url(URL)}.html"
    assert record["status_code"] == 200
    assert record["provider"] == "decodo"
    assert record["api_status_code"] == 200
    assert record["error_category"] == "success"
    assert record["validated"] is True
    assert record["html_bytes"] == len(written)
    assert record["retry_count"] == 0
    for key in ("filename", "url", "status_code", "timestamp"):
        assert key in record
    assert API_KEY not in json.dumps(record)
    assert stats.valid_html == 1
    assert stats.estimated_billable_requests == 1
    assert stats.traffic_bytes == 120
    assert stats.stopped is False


def test_js_rendering_and_standard_pool_are_explicit(tmp_path: Path) -> None:
    settings = _settings(js_rendering=True, proxy_pool="standard", max_retries=0)
    _stats, calls, _config = _crawl(tmp_path, [URL], [_envelope(HTML)], settings)
    assert json.loads(calls[0].content) == {
        "url": URL,
        "proxy_pool": "standard",
        "headless": "html",
    }


@pytest.mark.parametrize(
    ("content", "target_status", "target_headers", "category"),
    [
        ("<html>captcha required</html>", 200, None, "target_block"),
        ("<html><body>please wait</body></html>", 200, {"cf-mitigated": "challenge"}, "target_block"),
        ("<html>access denied</html>", 403, None, "target_block"),
        ("not html", 200, None, "invalid_body"),
        ("", 200, None, "invalid_body"),
    ],
)
def test_invalid_or_challenge_html_is_not_saved(
    tmp_path: Path,
    content: str,
    target_status: int,
    target_headers: dict | None,
    category: str,
) -> None:
    settings = _settings(max_retries=1)
    stats, calls, config = _crawl(
        tmp_path,
        [URL],
        [_envelope(content, target_status=target_status, target_headers=target_headers)],
        settings,
    )
    assert calls and len(calls) == 1
    assert not (tmp_path / "html" / f"{hash_url(URL)}.html").exists()
    record = _records(config)[0]
    assert record["validated"] is False
    assert record["error_category"] == category
    assert stats.valid_html == 0
    if category == "target_block":
        assert record["block_detected"] is True
        assert record["error"] == "blocked"
        assert stats.blocked == 1
    else:
        assert record["error"] == "invalid_body"
        assert stats.failed == 1


def test_non_string_content_is_not_saved(tmp_path: Path) -> None:
    response = httpx.Response(
        200,
        json={"results": [{"content": {"html": HTML}, "status_code": 200, "headers": {}}]},
    )
    stats, calls, config = _crawl(tmp_path, [URL], [response], _settings(max_retries=1))
    assert len(calls) == 1
    record = _records(config)[0]
    assert record["validated"] is False
    assert record["error_category"] == "invalid_body"
    assert stats.valid_html == 0
    assert not (tmp_path / "html").joinpath(f"{hash_url(URL)}.html").exists()


def test_retry_after_and_bounded_transient_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    slept: list[float] = []
    monkeypatch.setattr("html_downloader.download.crawler.time.sleep", lambda seconds: slept.append(seconds))
    monkeypatch.setattr("html_downloader.download.crawler.random.uniform", lambda _a, _b: 0)
    limited = httpx.Response(429, text="slow down", headers={"Retry-After": "7"})
    stats, calls, config = _crawl(tmp_path, [URL], [limited, _envelope(HTML)])
    assert len(calls) == 2
    assert 7.0 in slept
    assert max(slept) == 7.0
    assert _records(config)[0]["validated"] is True
    assert _records(config)[0]["retry_count"] == 1
    assert stats.retries == 1
    assert stats.estimated_billable_requests == 2

    slept.clear()
    failures = [httpx.Response(500, text="down"), httpx.Response(500, text="still"), _envelope(HTML)]
    stats, calls, config = _crawl(tmp_path, [URL], failures, _settings(max_retries=1))
    assert len(calls) == 2
    assert 1.0 in slept
    assert max(slept) == 1.0
    assert stats.valid_html == 0
    assert stats.api_errors == 1
    assert stats.estimated_billable_requests == 0
    assert _records(config)[0]["error_category"] == "api_transient"


def test_target_block_is_not_retried_and_differs_from_api_failure(tmp_path: Path) -> None:
    blocked = _envelope("<html>access denied</html>", target_status=403)
    stats, calls, config = _crawl(
        tmp_path,
        [URL, "https://example.com/next"],
        [blocked, _envelope(HTML)],
        _settings(max_retries=1),
    )
    assert len(calls) == 2
    rows = _records(config)
    blocked_row = next(row for row in rows if row["status_code"] == 403)
    ok_row = next(row for row in rows if row["validated"])
    assert blocked_row["error_category"] == "target_block"
    assert blocked_row["api_status_code"] == 200
    assert blocked_row["block_detected"] is True
    assert ok_row["error_category"] == "success"
    assert stats.blocked == 1
    assert stats.valid_html == 1
    assert stats.stopped is False

    stats, calls, config = _crawl(
        tmp_path,
        [URL],
        [httpx.Response(613, text="faulted")],
        _settings(max_retries=0),
    )
    row = _records(config)[0]
    assert row["status_code"] == 0
    assert row["api_status_code"] == 613
    assert row["error_category"] == "api_transient"
    assert row["block_detected"] is False
    assert stats.api_errors == 1
    assert stats.estimated_billable_requests == 0


@pytest.mark.parametrize(
    ("status", "reason", "error"),
    [
        (401, "decodo_unauthorized", "api_unauthorized"),
        (403, "decodo_forbidden", "api_forbidden"),
        (400, "decodo_bad_request", "api_bad_request"),
        (422, "decodo_bad_request", "api_bad_request"),
    ],
)
def test_permanent_api_errors_stop_the_job(tmp_path: Path, status: int, reason: str, error: str) -> None:
    urls = [URL, "https://example.com/later"]
    stats, calls, config = _crawl(
        tmp_path,
        urls,
        [httpx.Response(status, text="no"), _envelope(HTML)],
        _settings(max_retries=2),
    )
    assert len(calls) == 1
    assert stats.stopped is True
    assert stats.stop_reason == reason
    rows = {row["url"]: row for row in _records(config)}
    assert rows[URL]["error"] == error
    assert rows[URL]["retry_count"] == 0
    assert rows["https://example.com/later"]["error"] == "not_sent"
    assert rows["https://example.com/later"]["error_category"] == "not_sent"
    assert API_KEY not in config.results_path.read_text(encoding="utf-8")


def test_quota_text_stops_immediately(tmp_path: Path) -> None:
    quota = httpx.Response(429, text="quota exceeded, no requests left", headers={"Retry-After": "30"})
    stats, calls, config = _crawl(
        tmp_path,
        [URL, "https://example.com/later"],
        [quota, _envelope(HTML)],
        _settings(max_retries=2),
    )
    assert len(calls) == 1
    assert stats.stopped is True
    assert stats.stop_reason == "decodo_quota"
    assert _records(config)[0]["error_category"] == "api_quota"


def test_rate_limit_circuit_stops_further_calls(tmp_path: Path) -> None:
    urls = [f"https://example.com/{index}" for index in range(4)]
    limited = httpx.Response(429, text="slow down")
    stats, calls, config = _crawl(
        tmp_path,
        urls,
        [limited, limited, limited, _envelope(HTML)],
        _settings(max_retries=0),
        rate_limit_circuit=3,
    )
    assert len(calls) == 3
    assert stats.stopped is True
    assert stats.stop_reason == "decodo_rate_limit"
    assert sum(1 for row in _records(config) if row["error"] == "not_sent") == 1
    assert API_KEY not in config.results_path.read_text(encoding="utf-8")


def test_api_404_does_not_stop_the_job(tmp_path: Path) -> None:
    stats, calls, config = _crawl(
        tmp_path,
        [URL, "https://example.com/ok"],
        [httpx.Response(404, text="missing"), _envelope(HTML)],
        _settings(max_retries=0),
    )
    assert len(calls) == 2
    assert stats.stopped is False
    assert stats.valid_html == 1
    assert any(row["error_category"] == "api_not_found" for row in _records(config))


def test_timeout_and_connection_errors_redact_credentials(tmp_path: Path) -> None:
    stats, calls, config = _crawl(
        tmp_path,
        [URL],
        [httpx.ReadTimeout("timed out")],
        _settings(max_retries=0),
    )
    assert len(calls) == 1
    assert _records(config)[0]["error_category"] == "timeout"
    assert stats.api_errors == 1

    stats, calls, config = _crawl(
        tmp_path,
        [URL],
        [httpx.ConnectError(f"boom {API_KEY}")],
        _settings(max_retries=0),
    )
    row = _records(config)[0]
    assert row["error_category"] == "network"
    assert API_KEY not in row["error"]
    assert "[REDACTED]" in row["error"]
    assert API_KEY not in config.results_path.read_text(encoding="utf-8")


def test_in_flight_limit(tmp_path: Path) -> None:
    settings = _settings(max_in_flight=1, max_retries=0)
    entered: list[int] = []
    lock = threading.Lock()
    first_in = threading.Event()
    release = threading.Event()

    def handler(_request: httpx.Request) -> httpx.Response:
        with lock:
            entered.append(1)
            first = len(entered) == 1
        if first:
            first_in.set()
            assert release.wait(timeout=5)
        return _envelope(HTML)

    semaphore = ObservableSemaphore(1)
    client = DecodoClient(
        settings,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        semaphore=semaphore,  # type: ignore[arg-type]
    )
    config = load_config(output_dir=str(tmp_path), results_file=str(tmp_path / "results.jsonl"))
    provider = DecodoProvider(client, config)
    errors: list[BaseException] = []

    def run(url: str, path: Path) -> None:
        try:
            provider.fetch(url, str(path))
        except BaseException as exc:  # noqa: BLE001 - fail the test with the worker error
            errors.append(exc)

    first = threading.Thread(target=run, args=("https://example.com/a", tmp_path / "a.html"))
    second = threading.Thread(target=run, args=("https://example.com/b", tmp_path / "b.html"))
    first.start()
    assert first_in.wait(timeout=5)
    second.start()
    try:
        assert semaphore.blocked.wait(timeout=5)
        with lock:
            assert len(entered) == 1
    finally:
        release.set()
    first.join(timeout=5)
    second.join(timeout=5)
    client.close()
    assert errors == []
    assert len(entered) == 2
    assert (tmp_path / "a.html").read_bytes() == HTML.encode("utf-8")
    assert (tmp_path / "b.html").read_bytes() == HTML.encode("utf-8")


def test_skip_existing_makes_no_api_call(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("decodo was called")

    settings = _settings()
    client = DecodoClient(settings, http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    crawl_date = date(2026, 8, 19)
    job = job_dir(tmp_path, "saatchi", crawl_date)
    html_path = html_dir(job)
    html_path.mkdir(parents=True)
    (html_path / f"{hash_url(URL)}.html").write_text("<html>cached</html>", encoding="utf-8")
    urls_file(job).write_text(URL + "\n", encoding="utf-8")

    result = run_download(
        marketplace="saatchi",
        data_root=tmp_path,
        crawl_date=crawl_date,
        proxy_file=None,
        urls_override=None,
        max_workers=None,
        requests_per_second=None,
        skip_existing=True,
        results_append=True,
        provider="decodo",
        decodo_client=client,
        decodo_settings=settings,
    )
    assert result.status == "completed"
    record = json.loads(results_file(job).read_text(encoding="utf-8").strip())
    assert record["skipped_existing"] is True
    assert record["provider"] == "decodo"
    assert record["validated"] is False
    manifest = json.loads(manifest_file(job).read_text(encoding="utf-8"))
    for key in ("marketplace", "crawl_date", "status", "url_count", "workers", "rps", "proxy_file"):
        assert key in manifest
    assert manifest["provider"] == "decodo"
    assert manifest["metrics"]["attempted"] == 0
    assert manifest["proxy_file"] == ""
    assert API_KEY not in manifest_file(job).read_text(encoding="utf-8")
    client.close()


def test_cli_decodo_without_proxy_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DECODO_API_KEY", API_KEY)
    monkeypatch.setenv("DECODO_REQUESTS_PER_SECOND", "1000")
    monkeypatch.setenv("DECODO_MAX_IN_FLIGHT", "1")
    monkeypatch.setenv("DECODO_MAX_RETRIES", "0")
    monkeypatch.delenv("DOWNLOAD_PROVIDER", raising=False)
    crawl_date = date(2026, 8, 19)
    job = job_dir(tmp_path, "saatchi", crawl_date)
    job.mkdir(parents=True)
    urls_file(job).write_text(URL + "\n", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {API_KEY}"
        return _envelope(HTML)

    client = DecodoClient(
        _settings(max_retries=0),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr("html_downloader.download.service.build_decodo_client", lambda _settings: client)
    rc = main(
        [
            "download",
            "--marketplace",
            "saatchi",
            "--date",
            "2026-08-19",
            "--data-root",
            str(tmp_path),
            "--provider",
            "decodo",
        ]
    )
    assert rc == 0
    written = (html_dir(job) / f"{hash_url(URL)}.html").read_bytes()
    assert written == HTML.encode("utf-8")
    saved = results_file(job).read_text(encoding="utf-8") + manifest_file(job).read_text(encoding="utf-8")
    assert API_KEY not in saved
    manifest = json.loads(manifest_file(job).read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["metrics"]["valid_html"] == 1
    client.close()


def test_cli_help_and_auction_proxy_requirement() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "decodo" in help_text
    assert "DECODO_API_KEY" in help_text
    args = parser.parse_args(["download", "--marketplace", "saatchi"])
    assert args.proxy_file is None
    assert args.provider is None
    with pytest.raises(SystemExit):
        parser.parse_args(["auction", "download", "--auction-house", "christies"])


def test_curl_jsonl_schema_is_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_fetch(_session, url: str, path: str, _config, _proxy=None):
        del url
        Path(path).write_bytes(b"<html>ok</html>")
        return 200, "", BlockInfo(False, ""), 4

    monkeypatch.setattr("html_downloader.download.fetch_provider.fetch", fake_fetch)
    html_path = tmp_path / "html"
    html_path.mkdir()
    results_path = tmp_path / "results.jsonl"
    config = load_config(
        output_dir=str(html_path),
        results_file=str(results_path),
        requests_per_second=1000,
    )
    config.jitter_min = 0.0
    config.jitter_max = 0.0
    result = worker(
        URL,
        config,
        RateLimiter(1000, jitter_min=0.0, jitter_max=0.0),
        ProxyPool([]),
        AdaptiveBackoff(),
        results_path,
        threading.Lock(),
    )
    assert result.status_code == 200
    payload = json.loads(results_path.read_text(encoding="utf-8").strip())
    assert set(payload) == CURL_KEYS
    assert "provider" not in payload
    assert payload["error"] == ""
