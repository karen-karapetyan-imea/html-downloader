"""Auction discover + download services (monthly job folders)."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from html_downloader.auctions.paths import (
    auction_algolia_browse_state_file,
    auction_artist_sold_progress_file,
    auction_diff_file,
    auction_house_expand_progress_file,
    auction_html_dir,
    auction_job_dir,
    auction_lastmod_state_file,
    auction_manifest_file,
    auction_metadata_file,
    auction_results_file,
    auction_sitemap_all_file,
    auction_urls_file,
    ensure_auction_job_dirs,
    known_auction_result_paths,
    parse_job_month,
)
from html_downloader.auctions.registry import fetch_auction_entries, get_auction
from html_downloader.auctions.urls import invaluable_entity_from_url
from html_downloader.config import load_config
from html_downloader.discover.sitemap import (
    build_lastmod_state_from_entries,
    diff_sitemap_entries,
    load_lastmod_state,
    write_url_list,
)
from html_downloader.download.crawler import run_crawl
from html_downloader.download.proxy_pool import load_proxy_list
from html_downloader.download.service import ProxyRequiredError, read_url_list, require_proxies
from html_downloader.manifest import finish_manifest, new_manifest, write_manifest

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AuctionDiscoverResult:
    job: Path
    job_month: str
    all_count: int
    crawl_count: int


@dataclass(frozen=True, slots=True)
class AuctionDownloadResult:
    job: Path
    job_month: str
    url_count: int
    status: str


def _known_keys_for_house(data_root: Path, auction_house: str) -> set[tuple[str, str]]:
    paths = known_auction_result_paths(data_root, auction_house)
    if auction_house == "invaluable":
        from html_downloader.auctions.invaluable import known_invaluable_keys_from_paths

        return known_invaluable_keys_from_paths(paths)
    keys: set[tuple[str, str]] = set()
    for path in paths:
        # Generic fallback: parse as invaluable-style if possible
        from html_downloader.discover.registry import load_urls

        for url in load_urls([path]):
            key = invaluable_entity_from_url(url)
            if key is not None:
                keys.add(key)
    return keys


def _load_proxies_if_needed(
    uses_stealth_proxy: bool,
    auction_house: str,
    proxy_file: str | None,
) -> list[dict[str, str]]:
    if not uses_stealth_proxy:
        return load_proxy_list(proxy_file)

    if not proxy_file:
        raise ValueError(f"{auction_house} auction discovery requires --proxy-file")
    proxies = load_proxy_list(proxy_file)
    if not proxies:
        raise ValueError("--proxy-file is required and must contain at least one host:port:user:pass")
    LOGGER.info("using %s proxies for %s auction sitemap fetch", len(proxies), auction_house)
    return proxies


def run_auction_discover(
    *,
    auction_house: str,
    data_root: Path,
    state_root: Path,
    job_month: str | None,
    incremental: bool,
    include_updates: bool,
    update_state: bool,
    proxy_file: str | None,
    concurrency: int | None,
    dry_run: bool,
    expand_artist_sold: bool = False,
    artist_sold_concurrency: int = 4,
    max_artist_sold: int | None = None,
    expand_algolia: bool = True,
    algolia_from_year: int | None = None,
    algolia_to_year: int | None = None,
    algolia_workers: int = 2,
    algolia_delay: float = 0.4,
    algolia_force: bool = False,
    include_hubs: bool = True,
    expand_auctions_list: bool = True,
    expand_houses: bool = False,
    house_expand_concurrency: int = 2,
    max_houses: int | None = None,
) -> AuctionDiscoverResult:
    spec = get_auction(auction_house)
    month = parse_job_month(job_month)
    proxies = _load_proxies_if_needed(spec.uses_stealth_proxy, spec.name, proxy_file)
    proxy = proxies[0] if proxies else None
    workers = concurrency if concurrency is not None else spec.default_concurrency

    LOGGER.info(
        "auction discover house=%s month=%s concurrency=%s expand_algolia=%s "
        "expand_artist_sold=%s include_hubs=%s expand_auctions_list=%s expand_houses=%s",
        spec.name,
        month,
        workers,
        expand_algolia,
        expand_artist_sold,
        include_hubs,
        expand_auctions_list,
        expand_houses,
    )

    progress_path = (
        auction_artist_sold_progress_file(state_root, spec.name)
        if expand_artist_sold and spec.name == "invaluable"
        else None
    )
    house_progress = (
        auction_house_expand_progress_file(state_root, spec.name)
        if expand_houses and spec.name == "invaluable"
        else None
    )
    algolia_state = (
        auction_algolia_browse_state_file(state_root, spec.name)
        if expand_algolia and spec.name == "invaluable"
        else None
    )

    # Fetch first — do not touch job/state on failure
    entries = fetch_auction_entries(
        spec,
        concurrency=workers,
        proxy=proxy,
        proxies=proxies or None,
        expand_artist_sold=expand_artist_sold,
        artist_sold_concurrency=artist_sold_concurrency,
        max_artist_sold=max_artist_sold,
        artist_sold_progress_path=progress_path,
        expand_algolia=expand_algolia,
        algolia_state_path=algolia_state,
        algolia_from_year=algolia_from_year,
        algolia_to_year=algolia_to_year,
        algolia_workers=algolia_workers,
        algolia_delay=algolia_delay,
        algolia_force=algolia_force,
        include_hubs=include_hubs,
        expand_auctions_list=expand_auctions_list,
        expand_houses=expand_houses,
        house_expand_concurrency=house_expand_concurrency,
        max_houses=max_houses,
        house_expand_progress_path=house_progress,
    )
    LOGGER.info("fetched auction entries=%s", len(entries))

    known_keys = _known_keys_for_house(data_root, spec.name) if incremental else set()
    state_path = auction_lastmod_state_file(state_root, spec.name)
    lastmod_state = load_lastmod_state(state_path) if incremental else {}
    LOGGER.info("known auctions=%s lastmod_keys=%s", len(known_keys), len(lastmod_state))

    diff = diff_sitemap_entries(
        entries,
        known_entity_keys=known_keys,
        lastmod_state=lastmod_state,
        include_updates=include_updates,
    )
    all_urls = sorted({entry.url for entry in entries})
    crawl_urls = [entry.url for entry in diff.to_crawl] if incremental else all_urls

    LOGGER.info(
        "diff auction_urls=%s new=%s updated=%s unchanged=%s to_crawl=%s",
        diff.stats.entity_urls,
        diff.stats.new_entities,
        diff.stats.updated_entities,
        diff.stats.unchanged_entities,
        len(crawl_urls),
    )

    job = auction_job_dir(data_root, spec.name, month)
    discovered_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    if not dry_run:
        ensure_auction_job_dirs(job)
        write_url_list(auction_sitemap_all_file(job), all_urls)
        write_url_list(auction_urls_file(job), crawl_urls)
        auction_diff_file(job).write_text(
            json.dumps(diff.to_report(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        metadata = {
            "url_count": len(all_urls),
            "crawl_count": len(crawl_urls),
            "auction_house": spec.name,
            "job_month": month,
            "discovered_at": discovered_at,
            "sitemap_url": spec.default_indexes[0] if spec.default_indexes else None,
            "new_entities": diff.stats.new_entities,
            "updated_entities": diff.stats.updated_entities,
            "unchanged_entities": diff.stats.unchanged_entities,
        }
        auction_metadata_file(job).write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if update_state:
            from html_downloader.auctions.invaluable import save_auction_lastmod_state

            save_auction_lastmod_state(
                state_path,
                build_lastmod_state_from_entries(entries),
            )
            LOGGER.info("updated auction lastmod state house=%s path=%s", spec.name, state_path)

    if not crawl_urls:
        LOGGER.info("nothing to crawl")

    return AuctionDiscoverResult(
        job=job,
        job_month=month,
        all_count=len(all_urls),
        crawl_count=len(crawl_urls),
    )


def run_auction_download(
    *,
    auction_house: str,
    data_root: Path,
    job_month: str | None,
    proxy_file: str,
    urls_override: Path | None,
    max_workers: int | None,
    requests_per_second: float | None,
    skip_existing: bool,
    results_append: bool,
) -> AuctionDownloadResult:
    spec = get_auction(auction_house)
    month = parse_job_month(job_month)
    require_proxies(proxy_file)

    job = auction_job_dir(data_root, spec.name, month)
    ensure_auction_job_dirs(job)
    url_path = urls_override if urls_override is not None else auction_urls_file(job)
    urls = read_url_list(url_path)
    if not urls:
        raise ValueError(f"no URLs in {url_path}")

    config = load_config(
        urls_file=str(url_path),
        output_dir=str(auction_html_dir(job)),
        results_file=str(auction_results_file(job)),
        proxy_file=proxy_file,
        max_workers=max_workers,
        requests_per_second=requests_per_second,
        skip_existing=skip_existing,
        results_append=results_append,
    )

    # Reuse marketplace Manifest shape; marketplace field stores auction house name.
    manifest = new_manifest(
        marketplace=spec.name,
        crawl_date=month,
        workers=config.max_workers,
        rps=config.requests_per_second,
        proxy_file=proxy_file,
        url_count=len(urls),
    )
    write_manifest(auction_manifest_file(job), manifest)

    LOGGER.info(
        "auction download house=%s month=%s urls=%s html=%s",
        spec.name,
        month,
        len(urls),
        auction_html_dir(job),
    )
    status = "completed"
    try:
        run_crawl(urls, config)
    except Exception:
        status = "failed"
        finish_manifest(auction_manifest_file(job), manifest, status=status)
        raise

    finish_manifest(auction_manifest_file(job), manifest, status=status)
    return AuctionDownloadResult(
        job=job,
        job_month=month,
        url_count=len(urls),
        status=status,
    )
