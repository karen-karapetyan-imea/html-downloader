"""Download HTML into a dated marketplace job folder."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from html_downloader.config import DecodoSettings, load_config, resolve_provider
from html_downloader.download.crawler import CrawlStats, run_crawl
from html_downloader.download.decodo_client import DecodoClient, DecodoProvider, build_decodo_client
from html_downloader.download.proxy_pool import load_proxy_list
from html_downloader.marketplaces import get_marketplace
from html_downloader.manifest import annotate_manifest, finish_manifest, new_manifest, write_manifest
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
    stop_reason: str = ""


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
            "--proxy-file is required for the curl_cffi provider and must contain "
            "at least one host:port:user:pass"
        )
    return proxies


def _metrics_payload(stats: CrawlStats) -> dict:
    payload: dict = {
        "provider": stats.provider,
        "metrics": {
            "attempted": stats.attempted,
            "valid_html": stats.valid_html,
            "failed": stats.failed,
            "blocked": stats.blocked,
            "api_errors": stats.api_errors,
            "retries": stats.retries,
            "elapsed_seconds": round(stats.elapsed_seconds, 3),
            "estimated_billable_requests": stats.estimated_billable_requests,
            "traffic_bytes": stats.traffic_bytes,
        },
    }
    if stats.provider == "decodo":
        payload["estimated_billable_requests_note"] = (
            "Estimate of Decodo responses whose HTTP status the billing page marks as "
            "charged. Not a dollar amount. Statuses 612 and 999 are excluded because "
            "they are not on that table."
        )
    if stats.stopped and stats.stop_reason:
        payload["stop_reason"] = stats.stop_reason
    return payload


def run_download(
    *,
    marketplace: str,
    data_root: Path,
    crawl_date: date,
    proxy_file: str | None,
    urls_override: Path | None,
    max_workers: int | None,
    requests_per_second: float | None,
    skip_existing: bool,
    results_append: bool,
    provider: str | None = None,
    decodo_client: DecodoClient | None = None,
    decodo_settings: DecodoSettings | None = None,
) -> DownloadResult:
    spec = get_marketplace(marketplace)
    provider_name = resolve_provider(provider)
    settings: DecodoSettings | None = None
    if provider_name == "curl_cffi":
        if not proxy_file:
            raise ProxyRequiredError(
                "--proxy-file is required for the curl_cffi provider and must contain "
                "at least one host:port:user:pass"
            )
        require_proxies(proxy_file)
        worker_limit = max_workers
        rps = requests_per_second
    else:
        settings = decodo_settings or DecodoSettings.from_env()
        in_flight = settings.max_in_flight
        worker_limit = min(16 if max_workers is None else max_workers, in_flight)
        rps = settings.requests_per_second if requests_per_second is None else requests_per_second

    job = job_dir(data_root, spec.name, crawl_date)
    ensure_job_dirs(job)
    url_path = urls_override if urls_override is not None else urls_file(job)
    urls = read_url_list(url_path)
    if not urls:
        raise ValueError(f"no URLs in {url_path}")

    configured_proxy = proxy_file
    if provider_name == "decodo" and not proxy_file:
        configured_proxy = ""
    config = load_config(
        urls_file=str(url_path),
        output_dir=str(html_dir(job)),
        results_file=str(results_file(job)),
        proxy_file=configured_proxy,
        max_workers=worker_limit,
        requests_per_second=rps,
        skip_existing=skip_existing,
        results_append=results_append,
    )
    config.provider = provider_name
    if provider_name == "decodo" and settings is not None:
        config.jitter_min = 0.0
        config.jitter_max = 0.0
        config.max_retries = settings.max_retries

    manifest_path = manifest_file(job)
    manifest = new_manifest(
        marketplace=spec.name,
        crawl_date=crawl_date.isoformat(),
        workers=config.max_workers,
        rps=config.requests_per_second,
        proxy_file=configured_proxy or "",
        url_count=len(urls),
    )
    write_manifest(manifest_path, manifest)

    LOGGER.info(
        "download marketplace=%s date=%s urls=%s html=%s provider=%s",
        spec.name,
        crawl_date.isoformat(),
        len(urls),
        html_dir(job),
        provider_name,
    )
    status = "completed"
    stats: CrawlStats | None = None
    owns_client = False
    client: DecodoClient | None = decodo_client
    try:
        if provider_name == "decodo":
            if client is None:
                if settings is None:
                    settings = DecodoSettings.from_env()
                client = build_decodo_client(settings)
                owns_client = True
            stats = run_crawl(urls, config, fetcher=DecodoProvider(client, config))
        else:
            stats = run_crawl(urls, config)
        if stats.stopped:
            status = "failed"
            LOGGER.error(
                "download stopped marketplace=%s provider=%s reason=%s",
                spec.name,
                provider_name,
                stats.stop_reason,
            )
    except Exception:
        status = "failed"
        finish_manifest(manifest_path, manifest, status=status)
        if stats is not None:
            annotate_manifest(manifest_path, _metrics_payload(stats))
        raise
    finally:
        if owns_client and client is not None:
            client.close()

    finish_manifest(manifest_path, manifest, status=status)
    if stats is not None:
        annotate_manifest(manifest_path, _metrics_payload(stats))
    return DownloadResult(
        job=job,
        url_count=len(urls),
        status=status,
        stop_reason=stats.stop_reason if stats is not None else "",
    )
