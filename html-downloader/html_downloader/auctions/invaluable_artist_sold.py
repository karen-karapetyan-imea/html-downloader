"""Expand Invaluable lot URLs by paginating artist sold-at-auction pages.

The public lot XML sitemaps only expose ~250k current lots. Historical lots are
reachable via artist ``sold-at-auction-prices`` pages listed in
``sitemap_artist_sold_*.xml`` (~15k URLs × 5 files), each paginated with
``?page=N`` (~48 lots per page).
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

from html_downloader.auctions.urls import invaluable_entity_from_url, normalize_auction_url
from html_downloader.discover.sitemap import (
    SitemapEntry,
    _looks_like_html_challenge,
    parse_url_entries,
)

LOGGER = logging.getLogger(__name__)

ARTIST_SOLD_SITEMAP_RE = re.compile(r"sitemap_artist_sold_\d+\.xml", re.IGNORECASE)
ARTIST_SOLD_PAGE_RE = re.compile(
    r"^https://www\.invaluable\.com/artist/[^/?#]+/sold-at-auction-prices/?$",
    re.IGNORECASE,
)
_LOT_HREF_RE = re.compile(
    r"""(?:href|content)=["']([^"']*auction-lot/[^"']+)["']""",
    re.IGNORECASE,
)
_LOT_PATH_RE = re.compile(r"/auction-lot/[^\"'?\s<>]+", re.IGNORECASE)

DEFAULT_MAX_PAGES_PER_ARTIST = 500
DEFAULT_PAGE_SLEEP = 0.35
DEFAULT_ARTIST_SLEEP = 0.15
DEFAULT_EMPTY_STREAK = 2
DEFAULT_PAGE_FETCH_RETRIES = 3

FetchHtmlFn = Callable[[str], str]

_ARTIST_PROFILE_RE = re.compile(
    r"^https://www\.invaluable\.com/artist/([A-Za-z0-9-]+)-([A-Za-z0-9]{6,})/?$",
    re.IGNORECASE,
)


def is_artist_sold_sitemap(url: str) -> bool:
    return bool(ARTIST_SOLD_SITEMAP_RE.search(url))


def is_artist_sold_page(url: str) -> bool:
    normalized = normalize_auction_url(url) or url.strip()
    return bool(ARTIST_SOLD_PAGE_RE.match(normalized.rstrip("/") + "/")) or bool(
        ARTIST_SOLD_PAGE_RE.match(normalized)
    )


def sold_seed_from_artist_profile_url(url: str, *, base_url: str | None = None) -> str | None:
    """Convert an artist profile URL into a sold-at-auction seed URL."""
    text = (url or "").strip()
    if not text:
        return None
    if base_url:
        text = urljoin(base_url, text)
    absolute = normalize_auction_url(text)
    if not absolute:
        return None
    # Already a sold seed
    sold = normalize_artist_sold_url(absolute)
    if sold:
        return sold
    match = _ARTIST_PROFILE_RE.match(absolute.rstrip("/") + "/") or _ARTIST_PROFILE_RE.match(
        absolute
    )
    if not match:
        # Try without forcing trailing slash via path rebuild
        parts_match = re.match(
            r"^https://www\.invaluable\.com/artist/([A-Za-z0-9-]+)-([A-Za-z0-9]{6,})$",
            absolute,
            re.IGNORECASE,
        )
        if not parts_match:
            return None
        slug, artist_id = parts_match.group(1), parts_match.group(2)
    else:
        slug, artist_id = match.group(1), match.group(2)
    return (
        f"https://www.invaluable.com/artist/{slug.lower()}-{artist_id.lower()}"
        f"/sold-at-auction-prices"
    )


def merge_artist_sold_seeds(
    primary_seeds: Sequence[str],
    artist_profile_urls: Sequence[str],
) -> list[str]:
    """Union sold seeds with seeds derived from artist profile URLs (gap fill)."""
    seen: set[str] = set()
    out: list[str] = []
    for seed in primary_seeds:
        normalized = normalize_artist_sold_url(seed)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    added = 0
    for profile in artist_profile_urls:
        seed = sold_seed_from_artist_profile_url(profile)
        if not seed or seed in seen:
            continue
        seen.add(seed)
        out.append(seed)
        added += 1
    if added:
        LOGGER.info("artist-sold gap-fill added_seeds=%s total_seeds=%s", added, len(out))
    return out


def coverage_overlap_report(
    xml_lot_ids: set[str],
    artist_sold_lot_ids: set[str],
) -> dict[str, int]:
    """Return overlap metrics between XML lot ids and artist-sold lot ids."""
    overlap = xml_lot_ids & artist_sold_lot_ids
    return {
        "xml_lots": len(xml_lot_ids),
        "artist_sold_lots": len(artist_sold_lot_ids),
        "overlap": len(overlap),
        "xml_only": len(xml_lot_ids - artist_sold_lot_ids),
        "artist_sold_only": len(artist_sold_lot_ids - xml_lot_ids),
    }


def normalize_artist_sold_url(url: str, *, base_url: str | None = None) -> str | None:
    text = (url or "").strip()
    if not text:
        return None
    if base_url:
        text = urljoin(base_url, text)
    absolute = normalize_auction_url(text)
    if not absolute:
        return None
    # Force trailing-slash-free canonical form without query
    if "/sold-at-auction-prices" not in absolute.lower():
        return None
    if not absolute.lower().endswith("/sold-at-auction-prices"):
        # allow trailing slash variant already stripped by normalize
        if "/sold-at-auction-prices/" in absolute.lower():
            absolute = absolute.rstrip("/")
        else:
            return None
    if ARTIST_SOLD_PAGE_RE.match(absolute + "/") or ARTIST_SOLD_PAGE_RE.match(absolute):
        return absolute.rstrip("/")
    return None


def parse_artist_sold_seed_urls(
    xml_bytes: bytes,
    *,
    base_url: str = "https://www.invaluable.com/",
) -> list[str]:
    """Extract artist sold-at-auction seed URLs from a sitemap urlset."""
    if _looks_like_html_challenge(xml_bytes):
        raise RuntimeError("artist sold sitemap returned bot-challenge HTML")
    try:
        entries = parse_url_entries(xml_bytes)
    except ET.ParseError as exc:
        raise RuntimeError(f"invalid artist sold sitemap XML: {exc}") from exc

    seeds: list[str] = []
    seen: set[str] = set()
    for loc, _lastmod in entries:
        normalized = normalize_artist_sold_url(loc, base_url=base_url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        seeds.append(normalized)
    return seeds


def extract_lot_entries_from_html(
    html: str,
    *,
    base_url: str,
    lastmod: str | None = None,
) -> list[SitemapEntry]:
    """Parse auction-lot links from an artist sold HTML page."""
    best: dict[tuple[str, str], SitemapEntry] = {}
    candidates: list[str] = []
    candidates.extend(_LOT_HREF_RE.findall(html))
    candidates.extend(_LOT_PATH_RE.findall(html))
    for raw in candidates:
        normalized = normalize_auction_url(raw, base_url=base_url)
        if not normalized:
            continue
        key = invaluable_entity_from_url(normalized)
        if key is None or key[0] != "lot":
            continue
        entity_type, entity_id = key
        best[key] = SitemapEntry(
            url=normalized,
            lastmod=lastmod,
            entity_type=entity_type,
            entity_id=entity_id,
        )
    return list(best.values())


def artist_sold_page_url(seed_url: str, page: int) -> str:
    base = seed_url.rstrip("/")
    if page <= 1:
        return base + "/"
    return f"{base}/?page={page}"


def crawl_artist_sold_pages(
    seed_url: str,
    *,
    fetch_html: FetchHtmlFn,
    max_pages: int = DEFAULT_MAX_PAGES_PER_ARTIST,
    page_sleep: float = DEFAULT_PAGE_SLEEP,
    empty_streak_limit: int = DEFAULT_EMPTY_STREAK,
    page_fetch_retries: int = DEFAULT_PAGE_FETCH_RETRIES,
) -> list[SitemapEntry]:
    """
    Paginate one artist sold-at-auction listing until a page yields no new lots.

    Failed fetches (including 403/WAF) are retried per page before counting toward
    the empty-streak stop condition.
    """
    collected: dict[tuple[str, str], SitemapEntry] = {}
    empty_streak = 0
    for page in range(1, max_pages + 1):
        if page > 1 and page_sleep > 0:
            time.sleep(page_sleep)
        page_url = artist_sold_page_url(seed_url, page)
        html: str | None = None
        last_error: Exception | None = None
        for attempt in range(max(1, page_fetch_retries)):
            try:
                html = fetch_html(page_url)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                LOGGER.warning(
                    "artist sold page retry %s/%s url=%s error=%s",
                    attempt + 1,
                    page_fetch_retries,
                    page_url,
                    exc,
                )
                if attempt + 1 < page_fetch_retries:
                    time.sleep(min(8.0, 1.5 * (attempt + 1)))
        if html is None:
            LOGGER.warning(
                "artist sold page failed url=%s error=%s",
                page_url,
                last_error,
            )
            empty_streak += 1
            if empty_streak >= empty_streak_limit:
                break
            continue
        entries = extract_lot_entries_from_html(html, base_url=page_url)
        new_count = 0
        for entry in entries:
            if entry.entity_key not in collected:
                collected[entry.entity_key] = entry
                new_count += 1
        LOGGER.debug(
            "artist sold page=%s lots=%s new=%s url=%s",
            page,
            len(entries),
            new_count,
            page_url,
        )
        if new_count == 0:
            empty_streak += 1
            if page == 1 or empty_streak >= empty_streak_limit:
                break
        else:
            empty_streak = 0
    return list(collected.values())


def artist_sold_lots_path(progress_path: Path) -> Path:
    """Sidecar JSONL of lots discovered so resume does not drop prior work."""
    return progress_path.with_name(progress_path.stem + "_lots.jsonl")


def load_artist_sold_progress(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    done = payload.get("completed_seeds")
    if isinstance(done, list):
        return {str(x) for x in done}
    return set()


def load_artist_sold_lot_entries(progress_path: Path) -> list[SitemapEntry]:
    lots_file = artist_sold_lots_path(progress_path)
    if not lots_file.is_file():
        return []
    best: dict[tuple[str, str], SitemapEntry] = {}
    for line in lots_file.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            continue
        url = str(row.get("url") or "").strip()
        entity_type = str(row.get("entity_type") or "").strip()
        entity_id = str(row.get("entity_id") or "").strip()
        if not url or not entity_type or not entity_id:
            continue
        key = (entity_type, entity_id)
        best[key] = SitemapEntry(
            url=url,
            lastmod=row.get("lastmod"),
            entity_type=entity_type,
            entity_id=entity_id,
        )
    return list(best.values())


def save_artist_sold_progress(path: Path, completed_seeds: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "completed_seeds": sorted(set(completed_seeds)),
        "updated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_artist_sold_lots(progress_path: Path, entries: Sequence[SitemapEntry]) -> None:
    if not entries:
        return
    lots_file = artist_sold_lots_path(progress_path)
    lots_file.parent.mkdir(parents=True, exist_ok=True)
    with lots_file.open("a", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(
                json.dumps(
                    {
                        "url": entry.url,
                        "lastmod": entry.lastmod,
                        "entity_type": entry.entity_type,
                        "entity_id": entry.entity_id,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def expand_lots_from_artist_sold(
    seed_urls: Sequence[str],
    *,
    fetch_html: FetchHtmlFn,
    progress_path: Path | None = None,
    concurrency: int = 4,
    max_artists: int | None = None,
    max_pages_per_artist: int = DEFAULT_MAX_PAGES_PER_ARTIST,
    artist_sleep: float = DEFAULT_ARTIST_SLEEP,
) -> list[SitemapEntry]:
    """
    Crawl artist sold pages and return deduplicated lot SitemapEntry values.

    Progress is checkpointed (completed seeds + lot JSONL) so long runs resume
    without losing previously discovered lots.
    """
    seeds = list(dict.fromkeys(seed_urls))
    if max_artists is not None:
        seeds = seeds[: max(0, max_artists)]

    completed = load_artist_sold_progress(progress_path) if progress_path else set()
    pending = [url for url in seeds if url not in completed]
    LOGGER.info(
        "artist-sold expansion seeds=%s pending=%s completed=%s concurrency=%s",
        len(seeds),
        len(pending),
        len(completed),
        concurrency,
    )

    best: dict[tuple[str, str], SitemapEntry] = {}
    if progress_path is not None:
        for entry in load_artist_sold_lot_entries(progress_path):
            best.setdefault(entry.entity_key, entry)
    done_now = set(completed)

    def crawl_one(seed: str) -> tuple[str, list[SitemapEntry]]:
        if artist_sleep > 0:
            time.sleep(artist_sleep)
        entries = crawl_artist_sold_pages(
            seed,
            fetch_html=fetch_html,
            max_pages=max_pages_per_artist,
        )
        return seed, entries

    workers = max(1, concurrency)
    processed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(crawl_one, seed): seed for seed in pending}
        for future in as_completed(futures):
            seed = futures[future]
            try:
                _seed, entries = future.result()
            except Exception as exc:
                LOGGER.warning("artist-sold crawl failed seed=%s error=%s", seed, exc)
                continue
            new_entries: list[SitemapEntry] = []
            for entry in entries:
                if entry.entity_key not in best:
                    best[entry.entity_key] = entry
                    new_entries.append(entry)
            done_now.add(seed)
            processed += 1
            if progress_path is not None:
                append_artist_sold_lots(progress_path, new_entries)
                if processed % 25 == 0 or processed == len(pending):
                    save_artist_sold_progress(progress_path, done_now)
            if processed % 25 == 0 or processed == len(pending):
                LOGGER.info(
                    "artist-sold progress %s/%s lots_so_far=%s",
                    processed,
                    len(pending),
                    len(best),
                )

    if progress_path is not None:
        save_artist_sold_progress(progress_path, done_now)

    LOGGER.info(
        "artist-sold expansion complete artists_crawled=%s lots=%s",
        len(done_now) - len(completed),
        len(best),
    )
    return list(best.values())
