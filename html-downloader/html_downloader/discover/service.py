"""Discover marketplace entity URLs from sitemaps into a dated job folder."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from html_downloader.discover.sitemap import (
    build_lastmod_state_from_entries,
    diff_sitemap_entries,
    known_keys_from_sources,
    load_lastmod_state,
    save_lastmod_state,
    write_url_list,
)
from html_downloader.download.proxy_pool import load_proxy_list
from html_downloader.marketplaces import MarketplaceSpec, fetch_entries, get_marketplace
from html_downloader.paths import (
    diff_file,
    ensure_job_dirs,
    job_dir,
    known_result_paths,
    lastmod_state_file,
    sitemap_all_file,
    urls_file,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DiscoverResult:
    job: Path
    all_count: int
    crawl_count: int


def run_discover(
    *,
    marketplace: str,
    data_root: Path,
    state_root: Path,
    crawl_date: date,
    incremental: bool,
    include_updates: bool,
    update_state: bool,
    proxy_file: str | None,
    concurrency: int | None,
    dry_run: bool,
) -> DiscoverResult:
    spec = get_marketplace(marketplace)
    proxy = _require_proxy_if_needed(spec, proxy_file)
    workers = concurrency if concurrency is not None else spec.default_concurrency

    LOGGER.info("discover marketplace=%s concurrency=%s", spec.name, workers)
    entries = fetch_entries(spec, concurrency=workers, proxy=proxy)
    LOGGER.info("fetched entity entries=%s", len(entries))

    known_paths = known_result_paths(data_root, spec.name) if incremental else []
    known_keys = (
        known_keys_from_sources(known_paths=known_paths, source=spec.name) if incremental else set()
    )
    lastmod_state = load_lastmod_state(lastmod_state_file(state_root, spec.name)) if incremental else {}
    LOGGER.info("known entities=%s lastmod_keys=%s", len(known_keys), len(lastmod_state))

    diff = diff_sitemap_entries(
        entries,
        known_entity_keys=known_keys,
        lastmod_state=lastmod_state,
        include_updates=include_updates,
    )
    all_urls = sorted({entry.url for entry in entries})
    crawl_urls = [entry.url for entry in diff.to_crawl] if incremental else all_urls

    LOGGER.info(
        "diff entity_urls=%s new=%s updated=%s unchanged=%s to_crawl=%s",
        diff.stats.entity_urls,
        diff.stats.new_entities,
        diff.stats.updated_entities,
        diff.stats.unchanged_entities,
        len(crawl_urls),
    )

    job = job_dir(data_root, spec.name, crawl_date)
    if not dry_run:
        ensure_job_dirs(job)
        write_url_list(sitemap_all_file(job), all_urls)
        write_url_list(urls_file(job), crawl_urls)
        report_path = diff_file(job)
        report_path.write_text(
            json.dumps(diff.to_report(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if update_state:
            save_lastmod_state(
                lastmod_state_file(state_root, spec.name),
                build_lastmod_state_from_entries(entries),
            )
            LOGGER.info("updated lastmod state marketplace=%s", spec.name)

    if not crawl_urls:
        LOGGER.info("nothing to crawl")

    return DiscoverResult(job=job, all_count=len(all_urls), crawl_count=len(crawl_urls))


def _require_proxy_if_needed(
    spec: MarketplaceSpec,
    proxy_file: str | None,
) -> dict[str, str] | None:
    if not spec.uses_stealth_proxy:
        proxies = load_proxy_list(proxy_file)
        return proxies[0] if proxies else None

    if not proxy_file:
        raise ValueError("Artsy sitemap fetch requires --proxy-file")
    proxies = load_proxy_list(proxy_file)
    if not proxies:
        raise ValueError("--proxy-file is required and must contain at least one host:port:user:pass")
    LOGGER.info("using proxy for artsy sitemap fetch")
    return proxies[0]
