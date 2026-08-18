from __future__ import annotations

import json
import threading
from datetime import date
from pathlib import Path

import pytest

from html_downloader.cli import main
from html_downloader.config import load_config
from html_downloader.download.adaptive_backoff import AdaptiveBackoff
from html_downloader.download.crawler import hash_url, worker
from html_downloader.download.proxy_pool import ProxyPool
from html_downloader.download.rate_limiter import RateLimiter
from html_downloader.download.service import ProxyRequiredError, run_download
from html_downloader.paths import html_dir, job_dir, manifest_file, results_file, urls_file


def test_skip_existing_does_not_fetch(tmp_path: Path) -> None:
    url = "https://www.saatchiart.com/account/profile/735695"
    html_path = tmp_path / "html"
    html_path.mkdir()
    existing = html_path / f"{hash_url(url)}.html"
    existing.write_text("<html>cached</html>", encoding="utf-8")
    results_path = tmp_path / "results.jsonl"

    config = load_config(
        output_dir=str(html_path),
        results_file=str(results_path),
        skip_existing=True,
    )
    result = worker(
        url,
        config,
        RateLimiter(100.0, jitter_min=0.0, jitter_max=0.0),
        ProxyPool([]),
        AdaptiveBackoff(),
        results_path,
        threading.Lock(),
    )
    assert result.status_code == 200
    payload = json.loads(results_path.read_text(encoding="utf-8").strip())
    assert payload["skipped_existing"] is True
    assert existing.read_text(encoding="utf-8") == "<html>cached</html>"


def test_download_requires_proxy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRAWLER_PROXY", raising=False)
    monkeypatch.delenv("CRAWLER_PROXY_FILE", raising=False)
    empty = tmp_path / "proxy.txt"
    empty.write_text("", encoding="utf-8")
    job = job_dir(tmp_path, "saatchi", date(2026, 8, 19))
    job.mkdir(parents=True)
    urls_file(job).write_text("https://example.com/\n", encoding="utf-8")

    with pytest.raises(ProxyRequiredError):
        run_download(
            marketplace="saatchi",
            data_root=tmp_path,
            crawl_date=date(2026, 8, 19),
            proxy_file=str(empty),
            urls_override=None,
            max_workers=1,
            requests_per_second=1.0,
            skip_existing=True,
            results_append=True,
        )


def test_cli_download_proxy_required_returns_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CRAWLER_PROXY", raising=False)
    monkeypatch.delenv("CRAWLER_PROXY_FILE", raising=False)
    empty = tmp_path / "proxy.txt"
    empty.write_text("# none\n", encoding="utf-8")
    job = job_dir(tmp_path, "saatchi", date(2026, 8, 19))
    job.mkdir(parents=True)
    urls_file(job).write_text("https://example.com/\n", encoding="utf-8")

    rc = main(
        [
            "download",
            "--marketplace",
            "saatchi",
            "--date",
            "2026-08-19",
            "--data-root",
            str(tmp_path),
            "--proxy-file",
            str(empty),
        ]
    )
    assert rc == 1


def test_run_download_skip_existing_writes_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CRAWLER_PROXY", raising=False)
    proxy = tmp_path / "proxy.txt"
    proxy.write_text("127.0.0.1:8080:user:pass\n", encoding="utf-8")
    crawl_date = date(2026, 8, 19)
    job = job_dir(tmp_path, "saatchi", crawl_date)
    html_path = html_dir(job)
    html_path.mkdir(parents=True)
    url = "https://www.saatchiart.com/account/profile/735695"
    (html_path / f"{hash_url(url)}.html").write_text("<html>ok</html>", encoding="utf-8")
    urls_file(job).write_text(url + "\n", encoding="utf-8")

    result = run_download(
        marketplace="saatchi",
        data_root=tmp_path,
        crawl_date=crawl_date,
        proxy_file=str(proxy),
        urls_override=None,
        max_workers=1,
        requests_per_second=8.0,
        skip_existing=True,
        results_append=True,
    )
    assert result.status == "completed"
    manifest = json.loads(manifest_file(job).read_text(encoding="utf-8"))
    assert manifest["marketplace"] == "saatchi"
    assert manifest["crawl_date"] == "2026-08-19"
    assert manifest["status"] == "completed"
    assert manifest["url_count"] == 1
    lines = results_file(job).read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(lines[0])["skipped_existing"] is True
