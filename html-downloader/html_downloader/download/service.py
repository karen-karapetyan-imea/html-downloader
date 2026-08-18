"""Download HTML into a dated marketplace job folder."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from html_downloader.config import load_config
from html_downloader.download.crawler import run_crawl
from html_downloader.download.proxy_pool import load_proxy_list
from html_downloader.marketplaces import get_marketplace
from html_downloader.manifest import finish_manifest, new_manifest, write_manifest
from html_downloader.paths import (
    ensure_job_dirs,
    html_dir,
    job_dir,
    manifest_file,
    results_file,
    urls_file,
)

LOGGER = logging.getLogger(__name__)


class ProxyRequiredError(ValueError):
    """Raised when download is attempted without a usable proxy list."""


@dataclass(frozen=True, slots=True)
class DownloadResult:
    job: Path
    url_count: int
    status: str


def read_url_list(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"URL list not found: {path}")
    urls: list[str] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            url = line.strip()
            if url and not url.startswith("#"):
                urls.append(url)
    return urls


def require_proxies(proxy_file: str) -> list[dict[str, str]]:
    proxies = load_proxy_list(proxy_file)
    if not proxies:
        raise ProxyRequiredError(
            "--proxy-file is required and must contain at least one host:port:user:pass"
        )
    return proxies


def run_download(
    *,
    marketplace: str,
    data_root: Path,
    crawl_date: date,
    proxy_file: str,
    urls_override: Path | None,
    max_workers: int | None,
    requests_per_second: float | None,
    skip_existing: bool,
    results_append: bool,
) -> DownloadResult:
    spec = get_marketplace(marketplace)
    require_proxies(proxy_file)

    job = job_dir(data_root, spec.name, crawl_date)
    ensure_job_dirs(job)
    url_path = urls_override if urls_override is not None else urls_file(job)
    urls = read_url_list(url_path)
    if not urls:
        raise ValueError(f"no URLs in {url_path}")

    config = load_config(
        urls_file=str(url_path),
        output_dir=str(html_dir(job)),
        results_file=str(results_file(job)),
        proxy_file=proxy_file,
        max_workers=max_workers,
        requests_per_second=requests_per_second,
        skip_existing=skip_existing,
        results_append=results_append,
    )

    manifest = new_manifest(
        marketplace=spec.name,
        crawl_date=crawl_date.isoformat(),
        workers=config.max_workers,
        rps=config.requests_per_second,
        proxy_file=proxy_file,
        url_count=len(urls),
    )
    write_manifest(manifest_file(job), manifest)

    LOGGER.info(
        "download marketplace=%s date=%s urls=%s html=%s",
        spec.name,
        crawl_date.isoformat(),
        len(urls),
        html_dir(job),
    )
    status = "completed"
    try:
        run_crawl(urls, config)
    except Exception:
        status = "failed"
        finish_manifest(manifest_file(job), manifest, status=status)
        raise

    finish_manifest(manifest_file(job), manifest, status=status)
    return DownloadResult(job=job, url_count=len(urls), status=status)
