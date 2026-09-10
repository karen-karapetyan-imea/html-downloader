"""Secondary Invaluable HTML discovery: /auctions/ listing and house pages."""

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

from html_downloader.auctions.urls import (
    invaluable_entity_from_url,
    normalize_auction_url,
    prefer_entity_url,
)
from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

DEFAULT_AUCTIONS_LIST_URL = "https://www.invaluable.com/auctions/"
DEFAULT_AUCTIONS_PAGE_SLEEP = 0.5
DEFAULT_HOUSE_SLEEP = 0.2
DEFAULT_MAX_AUCTIONS_PAGES = 50

_CATALOG_HREF_RE = re.compile(
    r"""(?:href|content)=["']([^"']*catalog/[A-Za-z0-9]+)[^"']*["']""",
    re.IGNORECASE,
)
_LOT_HREF_RE = re.compile(
    r"""(?:href|content)=["']([^"']*auction-lot/[^"']+)["']""",
    re.IGNORECASE,
)
_LOT_PATH_RE = re.compile(r"/auction-lot/[^\"'?\s<>]+", re.IGNORECASE)
_CATALOG_PATH_RE = re.compile(r"/catalog/[A-Za-z0-9]{6,}", re.IGNORECASE)
_PAGINATION_RE = re.compile(
    r'"pagination"\s*:\s*\{[^}]*"pageNumber"\s*:\s*(\d+)[^}]*"totalPages"\s*:\s*(\d+)',
    re.IGNORECASE,
)
_TOTAL_PAGES_RE = re.compile(r'"totalPages"\s*:\s*(\d+)', re.IGNORECASE)

FetchHtmlFn = Callable[[str], str]


def auctions_list_page_url(base_url: str = DEFAULT_AUCTIONS_LIST_URL, *, page: int) -> str:
    """Build /auctions/ URL for 0-based page index (SSR pageNumber)."""
    root = base_url.rstrip("/") + "/"
    if page <= 0:
        return root
    return f"{root}?page={page}"


def parse_auctions_total_pages(html: str) -> int | None:
    match = _PAGINATION_RE.search(html)
    if match:
        return max(1, int(match.group(2)))
    match = _TOTAL_PAGES_RE.search(html)
    if match:
        return max(1, int(match.group(1)))
    return None


def extract_entity_entries_from_html(
    html: str,
    *,
    base_url: str,
    allowed_types: frozenset[str] | None = None,
) -> list[SitemapEntry]:
    """Parse lot/catalog (and optional) entity links from HTML."""
    allowed = allowed_types or frozenset({"lot", "catalog", "house", "artist", "category"})
    best: dict[tuple[str, str], SitemapEntry] = {}
    candidates: list[str] = []
    candidates.extend(_CATALOG_HREF_RE.findall(html))
    candidates.extend(_LOT_HREF_RE.findall(html))
    candidates.extend(_CATALOG_PATH_RE.findall(html))
    candidates.extend(_LOT_PATH_RE.findall(html))
    for raw in candidates:
        normalized = normalize_auction_url(raw, base_url=base_url)
        if not normalized:
            continue
        key = invaluable_entity_from_url(normalized)
        if key is None or key[0] not in allowed:
            continue
        entity_type, entity_id = key
        entry = SitemapEntry(
            url=normalized,
            lastmod=None,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        existing = best.get(key)
        if existing is None:
            best[key] = entry
        else:
            preferred = prefer_entity_url(existing.url, entry.url)
            if preferred != existing.url:
                best[key] = entry
    return list(best.values())


def expand_catalogs_from_auctions_list(
    *,
    fetch_html: FetchHtmlFn,
    base_url: str = DEFAULT_AUCTIONS_LIST_URL,
    max_pages: int = DEFAULT_MAX_AUCTIONS_PAGES,
    page_sleep: float = DEFAULT_AUCTIONS_PAGE_SLEEP,
) -> list[SitemapEntry]:
    """
    Paginate upcoming `/auctions/` SSR pages and collect catalog (+ featured lot) URLs.
    """
    best: dict[tuple[str, str], SitemapEntry] = {}
    total_pages = 1
    for page in range(0, max_pages):
        if page > 0 and page_sleep > 0:
            time.sleep(page_sleep)
        page_url = auctions_list_page_url(base_url, page=page)
        try:
            html = fetch_html(page_url)
        except Exception as exc:
            LOGGER.warning("auctions list page failed url=%s error=%s", page_url, exc)
            if page == 0:
                break
            continue
        if page == 0:
            detected = parse_auctions_total_pages(html)
            if detected is not None:
                total_pages = min(max_pages, detected)
                LOGGER.info("auctions list total_pages=%s", total_pages)
        entries = extract_entity_entries_from_html(
            html,
            base_url=page_url,
            allowed_types=frozenset({"catalog", "lot"}),
        )
        for entry in entries:
            existing = best.get(entry.entity_key)
            if existing is None:
                best[entry.entity_key] = entry
            else:
                preferred = prefer_entity_url(existing.url, entry.url)
                if preferred != existing.url:
                    best[entry.entity_key] = entry
        LOGGER.info(
            "auctions list page=%s entities=%s total=%s",
            page,
            len(entries),
            len(best),
        )
        if page + 1 >= total_pages:
            break
    return list(best.values())


def house_progress_path(progress_path: Path) -> Path:
    return progress_path


def house_lots_path(progress_path: Path) -> Path:
    return progress_path.with_name(progress_path.stem + "_lots.jsonl")


def load_house_progress(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return set()
    done = payload.get("completed_houses")
    if isinstance(done, list):
        return {str(x) for x in done}
    return set()


def save_house_progress(path: Path, completed: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "completed_houses": sorted(set(completed)),
        "updated_at": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_house_entries(progress_path: Path, entries: Sequence[SitemapEntry]) -> None:
    if not entries:
        return
    lots_file = house_lots_path(progress_path)
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


def load_house_lot_entries(progress_path: Path) -> list[SitemapEntry]:
    lots_file = house_lots_path(progress_path)
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
        best[(entity_type, entity_id)] = SitemapEntry(
            url=url,
            lastmod=row.get("lastmod"),
            entity_type=entity_type,
            entity_id=entity_id,
        )
    return list(best.values())


def expand_from_house_pages(
    house_urls: Sequence[str],
    *,
    fetch_html: FetchHtmlFn,
    progress_path: Path | None = None,
    concurrency: int = 2,
    max_houses: int | None = None,
    house_sleep: float = DEFAULT_HOUSE_SLEEP,
) -> list[SitemapEntry]:
    """Fetch auction-house pages and collect linked catalog/lot URLs."""
    seeds = list(dict.fromkeys(house_urls))
    if max_houses is not None:
        seeds = seeds[: max(0, max_houses)]

    completed = load_house_progress(progress_path) if progress_path else set()
    pending = [url for url in seeds if url not in completed]
    LOGGER.info(
        "house expansion houses=%s pending=%s completed=%s concurrency=%s",
        len(seeds),
        len(pending),
        len(completed),
        concurrency,
    )

    best: dict[tuple[str, str], SitemapEntry] = {}
    if progress_path is not None:
        for entry in load_house_lot_entries(progress_path):
            best.setdefault(entry.entity_key, entry)
    done_now = set(completed)

    def crawl_one(house_url: str) -> tuple[str, list[SitemapEntry]]:
        if house_sleep > 0:
            time.sleep(house_sleep)
        html = fetch_html(house_url)
        entries = extract_entity_entries_from_html(
            html,
            base_url=house_url,
            allowed_types=frozenset({"catalog", "lot"}),
        )
        return house_url, entries

    workers = max(1, concurrency)
    processed = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(crawl_one, seed): seed for seed in pending}
        for future in as_completed(futures):
            seed = futures[future]
            try:
                _house, entries = future.result()
            except Exception as exc:
                LOGGER.warning("house crawl failed url=%s error=%s", seed, exc)
                continue
            new_entries: list[SitemapEntry] = []
            for entry in entries:
                if entry.entity_key not in best:
                    best[entry.entity_key] = entry
                    new_entries.append(entry)
            done_now.add(seed)
            processed += 1
            if progress_path is not None:
                append_house_entries(progress_path, new_entries)
                if processed % 25 == 0 or processed == len(pending):
                    save_house_progress(progress_path, done_now)
            if processed % 25 == 0 or processed == len(pending):
                LOGGER.info(
                    "house expansion progress %s/%s entities=%s",
                    processed,
                    len(pending),
                    len(best),
                )

    if progress_path is not None:
        save_house_progress(progress_path, done_now)

    LOGGER.info(
        "house expansion complete crawled=%s entities=%s",
        len(done_now) - len(completed),
        len(best),
    )
    return list(best.values())
