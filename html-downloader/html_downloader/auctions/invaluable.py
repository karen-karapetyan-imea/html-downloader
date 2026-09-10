"""Invaluable discovery: XML sitemap index + Algolia archive + optional HTML expansions.

The HTML page https://www.invaluable.com/sitemap is a navigation hub only.

PRIMARY: lot XML parts + catalog XML + Algolia archive_prod (historical lots).
SECONDARY: auctionhouse / artists / category XML hubs; /auctions/ listing;
optional house-page expansion; optional artist-sold HTML expansion.
FALLBACK: past_search_sitemap.xml (soft-skip when 404).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from html_downloader.auctions.urls import (
    invaluable_entity_from_url,
    normalize_auction_url,
    prefer_entity_url,
)
from html_downloader.discover.registry import load_urls
from html_downloader.discover.sitemap import (
    SitemapEntry,
    _looks_like_html_challenge,
    is_sitemap_index,
    parse_child_sitemap_locs,
    parse_url_entries,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_INVALUABLE_INDEX = "https://www.invaluable.com/sitemap_inv_com-index.xml"
DEFAULT_INVALUABLE_CATALOG_SITEMAP = (
    "https://www.invaluable.com/sitemap_inv_com-catalog.xml"
)
DEFAULT_INVALUABLE_MAX_RETRIES = 8
DEFAULT_INVALUABLE_FETCH_TIMEOUT = 300.0
DEFAULT_INVALUABLE_INTER_FILE_SLEEP = 2.5
# past_search is listed in the index but usually 404/202 — don't stall the run
_PAST_SEARCH_MAX_RETRIES = 2
_HUB_SITEMAP_MAX_RETRIES = 4

_INCLUDE_SITEMAP_TOKENS = (
    "sitemap_inv_com-lot-",
    "past_search",
    "sitemap_inv_com-catalog",
)
_HUB_SITEMAP_TOKENS = (
    "sitemap_inv_com-auctionhouse",
    "sitemap_inv_com-artists",
    "sitemap_inv_com-category",
    "sitemap_inv_com-subcategory",
    "sitemap_inv_com-supercategory",
)
_ARTIST_SOLD_TOKEN = "sitemap_artist_sold_"

FetchBytesFn = Callable[[str], bytes]


def is_invaluable_target_sitemap(url: str) -> bool:
    """True for lot / past_search / catalog child sitemaps (fail-closed)."""
    lower = url.lower()
    return any(token in lower for token in _INCLUDE_SITEMAP_TOKENS)


def is_invaluable_hub_sitemap(url: str) -> bool:
    """True for house / artist / category child sitemaps (soft-fail)."""
    lower = url.lower()
    return any(token in lower for token in _HUB_SITEMAP_TOKENS)


def is_invaluable_artist_sold_sitemap(url: str) -> bool:
    return _ARTIST_SOLD_TOKEN in url.lower()


def parse_invaluable_entries(
    xml_bytes: bytes,
    *,
    base_url: str = "https://www.invaluable.com/",
) -> list[SitemapEntry]:
    """Parse a urlset into unique crawl-target SitemapEntry values."""
    if _looks_like_html_challenge(xml_bytes):
        raise RuntimeError("Invaluable sitemap returned a bot-challenge HTML page")

    try:
        entries_raw = parse_url_entries(xml_bytes)
    except ET.ParseError as exc:
        raise RuntimeError(f"invalid Invaluable sitemap XML: {exc}") from exc

    best: dict[tuple[str, str], SitemapEntry] = {}
    for loc, lastmod in entries_raw:
        normalized = normalize_auction_url(loc, base_url=base_url)
        if not normalized:
            continue
        key = invaluable_entity_from_url(normalized)
        if key is None:
            continue
        entity_type, entity_id = key
        entry = SitemapEntry(
            url=normalized,
            lastmod=lastmod,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        existing = best.get(key)
        if existing is None:
            best[key] = entry
            continue
        preferred_url = prefer_entity_url(existing.url, entry.url)
        if lastmod and (existing.lastmod is None or lastmod > existing.lastmod):
            best[key] = SitemapEntry(
                url=preferred_url,
                lastmod=lastmod,
                entity_type=entity_type,
                entity_id=entity_id,
            )
        elif preferred_url != existing.url:
            best[key] = SitemapEntry(
                url=preferred_url,
                lastmod=existing.lastmod,
                entity_type=entity_type,
                entity_id=entity_id,
            )

    return list(best.values())


parse_invaluable_catalog_entries = parse_invaluable_entries


def _fetch_invaluable_bytes(
    url: str,
    *,
    proxy: dict[str, str] | None = None,
    timeout: float = DEFAULT_INVALUABLE_FETCH_TIMEOUT,
) -> bytes:
    """Fetch sitemap XML; treat empty/WAF bodies as failures for retry."""
    from curl_cffi import Session

    http = Session(impersonate="chrome131")
    try:
        kwargs: dict[str, Any] = {"url": url, "timeout": timeout}
        if proxy:
            kwargs["proxies"] = proxy
        response = http.get(**kwargs)
        status = int(response.status_code)
        body = bytes(response.content or b"")
        head = body[:4096].lower()
        looks_xml = b"<urlset" in head or b"<sitemapindex" in head
        if not body:
            raise RuntimeError(f"sitemap fetch status={status} empty body url={url}")
        if status == 404:
            raise RuntimeError(f"sitemap fetch status=404 url={url}")
        if status in {429, 503}:
            raise RuntimeError(f"sitemap fetch status={status} url={url}")
        if status == 202 and not looks_xml:
            raise RuntimeError(f"sitemap fetch status=202 non-xml url={url}")
        if status not in {200, 202}:
            raise RuntimeError(f"sitemap fetch status={status} url={url}")
        if _looks_like_html_challenge(body) and not looks_xml:
            raise RuntimeError(f"bot challenge for url={url}")
        return body
    finally:
        if hasattr(http, "close"):
            http.close()


def _is_permanent_miss(exc: BaseException) -> bool:
    text = str(exc)
    return "status=404" in text


def _fetch_with_retries(
    url: str,
    *,
    fetch_bytes: FetchBytesFn,
    max_retries: int = DEFAULT_INVALUABLE_MAX_RETRIES,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        if attempt > 0:
            backoff = min(90.0, 3.0 * (2 ** (attempt - 1)))
            LOGGER.info(
                "invaluable sitemap retry %s/%s url=%s backoff=%.0fs",
                attempt + 1,
                max_retries,
                url,
                backoff,
            )
            time.sleep(backoff)
        try:
            body = fetch_bytes(url)
            if not body:
                raise RuntimeError(f"empty body url={url}")
            return body
        except Exception as exc:
            last_error = exc
            LOGGER.warning("invaluable sitemap fetch failed url=%s error=%s", url, exc)
            if _is_permanent_miss(exc):
                break
    raise RuntimeError(
        f"failed to fetch Invaluable sitemap url={url} after {max_retries} attempts"
    ) from last_error


def _make_rotating_fetcher(
    proxy_list: Sequence[dict[str, str]],
) -> FetchBytesFn:
    """
    Build a fetcher that tries: direct → next proxies on each call.

    Large lot files often succeed direct; when rate-limited (202), rotating
    residential proxies recovers. Parallel fetches worsen 202 storms — callers
    should fetch children sequentially.
    """
    state = {"i": 0}

    def fetch_bytes(url: str) -> bytes:
        errors: list[str] = []
        # Attempt 1: direct
        try:
            return _fetch_invaluable_bytes(url, proxy=None)
        except Exception as exc:
            errors.append(f"direct:{exc}")
            if _is_permanent_miss(exc):
                raise

        if not proxy_list:
            raise RuntimeError("; ".join(errors))

        # Attempt 2+: rotate through up to 5 proxies per fetch_bytes call
        n = min(5, len(proxy_list))
        for _ in range(n):
            proxy = proxy_list[state["i"] % len(proxy_list)]
            state["i"] += 1
            try:
                return _fetch_invaluable_bytes(url, proxy=proxy)
            except Exception as exc:
                errors.append(f"proxy:{exc}")
                if _is_permanent_miss(exc):
                    raise
        raise RuntimeError("; ".join(errors[-3:]))

    return fetch_bytes


def _merge_entry(
    best: dict[tuple[str, str], SitemapEntry],
    entry: SitemapEntry,
) -> None:
    key = entry.entity_key
    existing = best.get(key)
    if existing is None:
        best[key] = entry
        return
    preferred_url = prefer_entity_url(existing.url, entry.url)
    if entry.lastmod and (existing.lastmod is None or entry.lastmod > existing.lastmod):
        best[key] = SitemapEntry(
            url=preferred_url,
            lastmod=entry.lastmod,
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
        )
    elif preferred_url != existing.url:
        best[key] = SitemapEntry(
            url=preferred_url,
            lastmod=existing.lastmod,
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
        )


def fetch_invaluable_sitemap_entries(
    sitemap_url: str = DEFAULT_INVALUABLE_INDEX,
    *,
    concurrency: int = 1,
    proxy: dict[str, str] | None = None,
    proxies: list[dict[str, str]] | None = None,
    fetch_bytes: FetchBytesFn | None = None,
    max_retries: int = DEFAULT_INVALUABLE_MAX_RETRIES,
    expand_artist_sold: bool = False,
    artist_sold_concurrency: int = 4,
    max_artist_sold: int | None = None,
    artist_sold_progress_path: Path | None = None,
    expand_algolia: bool = True,
    algolia_state_path: Path | None = None,
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
    house_expand_progress_path: Path | None = None,
    fetch_html: Callable[[str], str] | None = None,
) -> list[SitemapEntry]:
    """
    Discover Invaluable entity URLs from the XML sitemap index (+ expansions).

    Lot/catalog XML children are fetched **sequentially** and fail-closed.
    Hub sitemaps (houses/artists/categories) soft-fail. Algolia archive browse
    is the primary historical-lot source; artist-sold HTML expansion is optional.
    """
    _ = concurrency  # kept for AuctionSpec API parity; XML children always sequential
    proxy_list = list(proxies or [])
    if proxy is not None and proxy not in proxy_list:
        proxy_list.insert(0, proxy)

    if fetch_bytes is None:
        fetch_bytes = _make_rotating_fetcher(proxy_list)

    LOGGER.info(
        "invaluable discover seed=%s proxies=%s mode=sequential "
        "expand_algolia=%s expand_artist_sold=%s include_hubs=%s "
        "expand_auctions_list=%s expand_houses=%s",
        sitemap_url,
        len(proxy_list),
        expand_algolia,
        expand_artist_sold,
        include_hubs,
        expand_auctions_list,
        expand_houses,
    )

    seed_body = _fetch_with_retries(
        sitemap_url, fetch_bytes=fetch_bytes, max_retries=max_retries
    )
    if not is_sitemap_index(seed_body):
        entries = parse_invaluable_entries(seed_body)
        LOGGER.info("invaluable leaf sitemap entities=%s", len(entries))
        return entries

    children = parse_child_sitemap_locs(seed_body)
    targets: list[str] = []
    nested: list[str] = []
    hub_maps: list[str] = []
    artist_sold_maps: list[str] = []
    for child in children:
        if is_invaluable_artist_sold_sitemap(child):
            artist_sold_maps.append(child)
            continue
        if include_hubs and is_invaluable_hub_sitemap(child):
            hub_maps.append(child)
            continue
        if not is_invaluable_target_sitemap(child):
            continue
        if "past_search" in child.lower():
            nested.append(child)
        else:
            targets.append(child)

    for nested_url in nested:
        try:
            nested_body = _fetch_with_retries(
                nested_url,
                fetch_bytes=fetch_bytes,
                max_retries=_PAST_SEARCH_MAX_RETRIES,
            )
        except Exception as exc:
            LOGGER.warning(
                "invaluable nested sitemap skipped url=%s error=%s",
                nested_url,
                exc,
            )
            continue
        if is_sitemap_index(nested_body):
            for child in parse_child_sitemap_locs(nested_body):
                if is_invaluable_target_sitemap(child):
                    targets.append(child)
        else:
            targets.append(nested_url)

    seen: set[str] = set()
    ordered_targets: list[str] = []
    for url in targets:
        if url not in seen:
            seen.add(url)
            ordered_targets.append(url)

    LOGGER.info(
        "invaluable target sitemaps=%s hub_sitemaps=%s artist_sold_maps=%s",
        len(ordered_targets),
        len(hub_maps),
        len(artist_sold_maps),
    )
    for url in ordered_targets:
        LOGGER.info("  target %s", url)
    for url in hub_maps:
        LOGGER.info("  hub %s", url)

    best: dict[tuple[str, str], SitemapEntry] = {}
    failed_urls: list[str] = []

    def merge_entries(entries: list[SitemapEntry]) -> None:
        for entry in entries:
            _merge_entry(best, entry)

    for index, url in enumerate(ordered_targets):
        if index > 0:
            time.sleep(DEFAULT_INVALUABLE_INTER_FILE_SLEEP)
        try:
            body = _fetch_with_retries(
                url, fetch_bytes=fetch_bytes, max_retries=max_retries
            )
            entries = parse_invaluable_entries(body)
        except Exception as exc:
            LOGGER.warning("invaluable child failed url=%s error=%s", url, exc)
            failed_urls.append(url)
            continue
        LOGGER.info("invaluable child parsed url=%s entities=%s", url, len(entries))
        merge_entries(entries)

    if failed_urls:
        LOGGER.info(
            "invaluable cooldown retry for %s failed sitemap(s) (sleep 20s)",
            len(failed_urls),
        )
        time.sleep(20)
        still_failed: list[str] = []
        for url in failed_urls:
            time.sleep(DEFAULT_INVALUABLE_INTER_FILE_SLEEP)
            try:
                body = _fetch_with_retries(
                    url, fetch_bytes=fetch_bytes, max_retries=max_retries
                )
                entries = parse_invaluable_entries(body)
            except Exception as exc:
                LOGGER.warning("invaluable child failed url=%s error=%s", url, exc)
                still_failed.append(url)
                continue
            LOGGER.info("invaluable child parsed url=%s entities=%s", url, len(entries))
            merge_entries(entries)
        failed_urls = still_failed

    if failed_urls and not best:
        raise RuntimeError(
            f"failed to fetch all Invaluable target sitemaps ({len(failed_urls)} failures)"
        )
    if failed_urls:
        raise RuntimeError(
            f"Invaluable discovery incomplete: {len(failed_urls)}/{len(ordered_targets)} "
            f"sitemap(s) failed after retries (parsed partial={len(best)}). "
            "Not writing job/state; re-run discover with --concurrency 1 after a short wait."
        )

    xml_lot_ids = {entity_id for (kind, entity_id) in best if kind == "lot"}
    xml_catalog_count = sum(1 for (kind, _id) in best if kind == "catalog")
    LOGGER.info(
        "invaluable XML discovery complete lots=%s catalogs=%s",
        len(xml_lot_ids),
        xml_catalog_count,
    )

    # Soft-fail hub sitemaps (houses / artists / categories)
    artist_profile_urls: list[str] = []
    house_urls: list[str] = []
    for index, url in enumerate(hub_maps):
        if index > 0 or ordered_targets:
            time.sleep(DEFAULT_INVALUABLE_INTER_FILE_SLEEP)
        try:
            body = _fetch_with_retries(
                url,
                fetch_bytes=fetch_bytes,
                max_retries=_HUB_SITEMAP_MAX_RETRIES,
            )
            entries = parse_invaluable_entries(body)
        except Exception as exc:
            LOGGER.warning("invaluable hub sitemap skipped url=%s error=%s", url, exc)
            continue
        LOGGER.info("invaluable hub parsed url=%s entities=%s", url, len(entries))
        merge_entries(entries)
        for entry in entries:
            if entry.entity_type == "artist":
                artist_profile_urls.append(entry.url)
            elif entry.entity_type == "house":
                house_urls.append(entry.url)

    if fetch_html is None:
        html_counter = {"i": 0}

        def _default_fetch_html(url: str) -> str:
            return _fetch_invaluable_html(
                url, proxy_list=proxy_list, counter=html_counter
            )

        fetch_html = _default_fetch_html

    if expand_auctions_list:
        from html_downloader.auctions.invaluable_hubs import (
            expand_catalogs_from_auctions_list,
        )

        try:
            auction_entries = expand_catalogs_from_auctions_list(fetch_html=fetch_html)
            before = len(best)
            merge_entries(auction_entries)
            LOGGER.info(
                "auctions-list merge added=%s total_now=%s",
                len(best) - before,
                len(best),
            )
        except Exception as exc:
            LOGGER.warning("auctions-list expansion skipped error=%s", exc)

    if expand_algolia:
        from html_downloader.auctions.invaluable_algolia import (
            DEFAULT_ALGOLIA_DELAY,
            DEFAULT_ALGOLIA_WORKERS,
            EARLIEST_YEAR,
            expand_lots_from_algolia,
        )

        if algolia_state_path is None:
            raise ValueError("expand_algolia requires algolia_state_path")
        before = len(best)
        try:
            algolia_entries = expand_lots_from_algolia(
                state_path=algolia_state_path,
                from_year=algolia_from_year if algolia_from_year is not None else EARLIEST_YEAR,
                to_year=algolia_to_year,
                workers=algolia_workers if algolia_workers > 0 else DEFAULT_ALGOLIA_WORKERS,
                delay=algolia_delay if algolia_delay > 0 else DEFAULT_ALGOLIA_DELAY,
                force=algolia_force,
            )
            merge_entries(algolia_entries)
            LOGGER.info(
                "algolia merge added=%s total_now=%s fetched=%s",
                len(best) - before,
                len(best),
                len(algolia_entries),
            )
        except Exception as exc:
            LOGGER.warning("algolia expansion skipped error=%s", exc)

    if expand_artist_sold and artist_sold_maps:
        from html_downloader.auctions.invaluable_artist_sold import (
            coverage_overlap_report,
            expand_lots_from_artist_sold,
            merge_artist_sold_seeds,
            parse_artist_sold_seed_urls,
        )

        seed_urls: list[str] = []
        for map_url in artist_sold_maps:
            time.sleep(DEFAULT_INVALUABLE_INTER_FILE_SLEEP)
            try:
                body = _fetch_with_retries(
                    map_url, fetch_bytes=fetch_bytes, max_retries=max_retries
                )
            except Exception as exc:
                LOGGER.warning("artist sold sitemap skipped url=%s error=%s", map_url, exc)
                continue
            seeds = parse_artist_sold_seed_urls(body)
            LOGGER.info("artist sold sitemap url=%s seeds=%s", map_url, len(seeds))
            seed_urls.extend(seeds)

        seed_urls = merge_artist_sold_seeds(seed_urls, artist_profile_urls)

        expanded = expand_lots_from_artist_sold(
            seed_urls,
            fetch_html=fetch_html,
            progress_path=artist_sold_progress_path,
            concurrency=max(1, artist_sold_concurrency),
            max_artists=max_artist_sold,
        )
        before = len(best)
        artist_sold_ids = {e.entity_id for e in expanded if e.entity_type == "lot"}
        merge_entries(expanded)
        report = coverage_overlap_report(xml_lot_ids, artist_sold_ids)
        LOGGER.info(
            "artist-sold merge added=%s total_now=%s coverage=%s",
            len(best) - before,
            len(best),
            report,
        )

    if expand_houses and house_urls:
        from html_downloader.auctions.invaluable_hubs import expand_from_house_pages

        try:
            house_entries = expand_from_house_pages(
                house_urls,
                fetch_html=fetch_html,
                progress_path=house_expand_progress_path,
                concurrency=max(1, house_expand_concurrency),
                max_houses=max_houses,
            )
            before = len(best)
            merge_entries(house_entries)
            LOGGER.info(
                "house-page merge added=%s total_now=%s",
                len(best) - before,
                len(best),
            )
        except Exception as exc:
            LOGGER.warning("house expansion skipped error=%s", exc)

    counts: dict[str, int] = {}
    for entity_type, _entity_id in best:
        counts[entity_type] = counts.get(entity_type, 0) + 1
    LOGGER.info(
        "invaluable discovery complete total=%s counts=%s targets=%s hubs=%s",
        len(best),
        counts,
        len(ordered_targets),
        len(hub_maps),
    )
    return list(best.values())


def _fetch_invaluable_html(
    url: str,
    *,
    proxy_list: Sequence[dict[str, str]],
    counter: dict[str, int],
    timeout: float = 90.0,
) -> str:
    """Fetch HTML listing pages with proxy rotation (not XML-validated)."""
    from curl_cffi import Session

    errors: list[str] = []
    attempts: list[dict[str, str] | None] = [None]
    if proxy_list:
        for _ in range(min(4, len(proxy_list))):
            proxy = proxy_list[counter["i"] % len(proxy_list)]
            counter["i"] += 1
            attempts.append(proxy)

    last_exc: Exception | None = None
    for proxy in attempts:
        http = Session(impersonate="chrome131")
        try:
            kwargs: dict[str, Any] = {"url": url, "timeout": timeout}
            if proxy:
                kwargs["proxies"] = proxy
            response = http.get(**kwargs)
            status = int(response.status_code)
            body = bytes(response.content or b"")
            if status != 200 or not body:
                raise RuntimeError(f"html fetch status={status} empty={not body}")
            if _looks_like_html_challenge(body) and len(body) < 8000:
                raise RuntimeError("html bot challenge")
            return body.decode("utf-8", errors="ignore")
        except Exception as exc:
            last_exc = exc
            errors.append(str(exc))
        finally:
            if hasattr(http, "close"):
                http.close()
    raise RuntimeError(f"html fetch failed url={url} errors={errors[-3:]}") from last_exc


def known_invaluable_keys_from_paths(paths: Iterable[Path]) -> set[tuple[str, str]]:
    """Load known Invaluable entity keys from URL list / JSONL files."""
    keys: set[tuple[str, str]] = set()
    for url in load_urls(paths):
        key = invaluable_entity_from_url(url)
        if key is not None:
            keys.add(key)
    return keys


def save_auction_lastmod_state(path: Path, entities: dict[str, str]) -> None:
    """Atomic write of auction lastmod state (temp file + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "entities": dict(sorted(entities.items())),
        "last_fetch_at": datetime.now().astimezone().isoformat(),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
