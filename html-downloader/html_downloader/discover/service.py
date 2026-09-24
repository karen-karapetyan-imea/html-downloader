"""Discover marketplace entity URLs from sitemaps into a dated job folder."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from html_downloader.discover.sitemap import (
    SitemapEntry,
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
    saatchi_search_browse_state_file,
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
    expand_search: bool = True,
    search_force: bool = False,
    search_delay: float = 0.2,
    search_workers: int = 1,
) -> DiscoverResult:
    spec = get_marketplace(marketplace)
    proxy = _require_proxy_if_needed(spec, proxy_file)
    workers = concurrency if concurrency is not None else spec.default_concurrency

    LOGGER.info("discover marketplace=%s concurrency=%s", spec.name, workers)
    try:
        entries = fetch_entries(spec, concurrency=workers, proxy=proxy)
        LOGGER.info("fetched entity entries=%s", len(entries))
    except Exception as exc:
        if spec.name == "saatchi" and expand_search:
            LOGGER.warning(
                "saatchi sitemap fetch failed (%s); continuing with Constructor expand only",
                exc,
            )
            entries = []
        else:
            raise

    search_state: Path | None = None
    if spec.name == "saatchi" and expand_search:
        search_state = saatchi_search_browse_state_file(state_root)
        from html_downloader.discover.saatchi_constructor import (
            expand_artworks_from_search,
        )

        LOGGER.info(
            "saatchi search expand force=%s delay=%s workers=%s state=%s",
            search_force,
            search_delay,
            search_workers,
            search_state,
        )
        if not dry_run:
            search_new = expand_artworks_from_search(
                state_path=search_state,
                delay=search_delay if search_delay > 0 else 0.0,
                force=search_force,
                workers=search_workers,
            )
            LOGGER.info("saatchi search expand new_in_memory=%s", len(search_new))
            # Merge capped in-memory new rows into the sitemap entry set for
            # lastmod / diff bookkeeping; full corpus is streamed from JSONL.
            by_key: dict[tuple[str, str], SitemapEntry] = {
                entry.entity_key: entry for entry in entries
            }
            for entry in search_new:
                by_key.setdefault(entry.entity_key, entry)
            entries = list(by_key.values())

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
    all_count = len(all_urls)
    crawl_count = len(crawl_urls)

    if not dry_run:
        ensure_job_dirs(job)
        sitemap_all_path = sitemap_all_file(job)
        urls_path = urls_file(job)
        write_url_list(sitemap_all_path, all_urls)
        write_url_list(urls_path, crawl_urls)
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

        if spec.name == "saatchi" and expand_search and search_state is not None:
            added_all, added_crawl = _stream_saatchi_search_cache_into_job_files(
                search_state,
                sitemap_all_path=sitemap_all_path,
                urls_path=urls_path,
                already_written_ids={
                    entry.entity_id for entry in entries if entry.entity_type == "artwork"
                },
                known_keys=known_keys,
                incremental=incremental,
            )
            all_count += added_all
            crawl_count += added_crawl
            LOGGER.info(
                "saatchi search cache streamed added_all=%s added_crawl=%s "
                "job_all=%s job_crawl=%s",
                added_all,
                added_crawl,
                all_count,
                crawl_count,
            )

    if crawl_count <= 0:
        LOGGER.info("nothing to crawl")

    return DiscoverResult(job=job, all_count=all_count, crawl_count=crawl_count)


def _stream_saatchi_search_cache_into_job_files(
    state_path: Path,
    *,
    sitemap_all_path: Path,
    urls_path: Path,
    already_written_ids: set[str],
    known_keys: set[tuple[str, str]],
    incremental: bool,
) -> tuple[int, int]:
    """Append Constructor artwork URLs from JSONL into job files."""
    from html_downloader.discover.saatchi_constructor import (
        artwork_cache_path,
        iter_artwork_cache_rows,
    )

    cache_path = artwork_cache_path(state_path)
    if not cache_path.exists():
        return 0, 0

    seen_ids = set(already_written_ids)
    added_all = 0
    added_crawl = 0
    scanned = 0

    with (
        sitemap_all_path.open("a", encoding="utf-8") as all_fh,
        urls_path.open("a", encoding="utf-8") as crawl_fh,
    ):
        for entity_id, url, _lastmod in iter_artwork_cache_rows(cache_path):
            scanned += 1
            if scanned % 1_000_000 == 0:
                LOGGER.info(
                    "saatchi search cache stream progress scanned=%s added_all=%s added_crawl=%s",
                    scanned,
                    added_all,
                    added_crawl,
                )
            if entity_id in seen_ids:
                continue
            seen_ids.add(entity_id)
            all_fh.write(url + "\n")
            added_all += 1
            if incremental and ("artwork", entity_id) in known_keys:
                continue
            crawl_fh.write(url + "\n")
            added_crawl += 1

    LOGGER.info(
        "saatchi search cache stream complete scanned=%s added_all=%s added_crawl=%s",
        scanned,
        added_all,
        added_crawl,
    )
    return added_all, added_crawl


def _require_proxy_if_needed(
    spec: MarketplaceSpec,
    proxy_file: str | None,
) -> dict[str, str] | None:
    if not spec.uses_stealth_proxy:
        proxies = load_proxy_list(proxy_file)
        return proxies[0] if proxies else None

    if not proxy_file:
        raise ValueError(f"{spec.name} sitemap fetch requires --proxy-file")
    proxies = load_proxy_list(proxy_file)
    if not proxies:
        raise ValueError("--proxy-file is required and must contain at least one host:port:user:pass")
    LOGGER.info("using proxy for %s sitemap fetch", spec.name)
    return proxies[0]
