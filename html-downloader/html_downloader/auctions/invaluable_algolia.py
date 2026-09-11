"""Invaluable historical lot discovery via Algolia archive_prod browse.

PRIMARY source for historical lots (full archive: all categories, sold + unsold).
Year-partitioned cursors bound blast radius; no Cloudflare / proxies required.

The JSONL lot cache is the source of truth for historical URLs. Never load the
full archive into a SitemapEntry dict — stream ids / rows instead.
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

# Public search-only key embedded in invaluable.com page JS (same trust level).
ALGOLIA_APP_ID = "0HJBNDV358"
ALGOLIA_API_KEY = "c72467a0649841b28a88222132bef0ea"
ALGOLIA_INDEX = "archive_prod"
ALGOLIA_BROWSE_URL = f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/browse"

BASE_URL = "https://www.invaluable.com"
HITS_PER_PAGE = 1000
EARLIEST_YEAR = 1989
MAX_CURSOR_RESTARTS = 3
DEFAULT_ALGOLIA_DELAY = 0.4
DEFAULT_ALGOLIA_WORKERS = 2
# Cap in-memory "new this run" return list; full set lives on disk in JSONL.
_MAX_RETURN_NEW = 50_000
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")

BrowseFn = Callable[[str, str | None], dict[str, Any] | None]


def slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or "lot"


def build_lot_url(lot_title: str, lot_number: str, lot_ref: str) -> str:
    return f"{BASE_URL}/auction-lot/{slugify(lot_title)}-{lot_number}-c-{lot_ref.lower()}"


def year_filter(year: int) -> str:
    """Algolia filter for one UTC calendar year (no category / price constraints)."""
    start = int(datetime(year, 1, 1, tzinfo=timezone.utc).timestamp())
    end = int(datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp())
    return f"dateTimeUTCUnix>={start} AND dateTimeUTCUnix<{end}"


def hit_to_sitemap_entry(hit: dict[str, Any]) -> SitemapEntry | None:
    """Map one archive_prod hit to a lot SitemapEntry, or None if unusable."""
    if hit.get("banned"):
        return None
    lot_ref = (hit.get("lotRef") or "").strip()
    if not lot_ref:
        return None
    lot_title = (hit.get("lotTitle") or "").strip()
    lot_number = str(hit.get("lotNumber") or "-")
    sale_date = (hit.get("dateTimeLocal") or "")[:10] or None
    if sale_date and len(sale_date) < 10:
        sale_date = None
    entity_id = lot_ref.lower()
    return SitemapEntry(
        url=build_lot_url(lot_title, lot_number, lot_ref),
        lastmod=sale_date,
        entity_type="lot",
        entity_id=entity_id,
    )


def hits_to_sitemap_entries(hits: list[dict[str, Any]]) -> list[SitemapEntry]:
    out: list[SitemapEntry] = []
    for hit in hits:
        entry = hit_to_sitemap_entry(hit)
        if entry is None:
            LOGGER.debug(
                "skipping Algolia hit banned/no lotRef objectID=%s",
                hit.get("objectID", "?"),
            )
            continue
        out.append(entry)
    return out


class AlgoliaBrowseState:
    """Year-partition cursor checkpoint for Algolia browse resume."""

    _SAVE_EVERY = 5

    def __init__(self, path: Path) -> None:
        self.path = path
        self._state: dict[str, Any] = {"partitions": {}}
        self._dirty = 0
        self._lock = threading.Lock()
        if path.exists():
            try:
                with open(path, encoding="utf-8") as fh:
                    loaded = json.load(fh)
                if isinstance(loaded, dict):
                    self._state = loaded
            except Exception as exc:
                LOGGER.warning("could not load Algolia browse state: %s", exc)
        self._state.setdefault("partitions", {})

    @property
    def _partitions(self) -> dict[str, dict[str, Any]]:
        return self._state["partitions"]

    def status(self, year: str) -> str | None:
        entry = self._partitions.get(year)
        return entry.get("status") if entry else None

    def cursor(self, year: str) -> str | None:
        entry = self._partitions.get(year)
        return entry.get("cursor") if entry else None

    def mark_partition(
        self,
        year: str,
        status: str,
        cursor: str | None = None,
        records_delta: int = 0,
    ) -> None:
        with self._lock:
            entry = self._partitions.setdefault(
                year, {"status": "pending", "cursor": None, "records_written": 0}
            )
            entry["status"] = status
            entry["cursor"] = cursor
            if records_delta:
                entry["records_written"] = entry.get("records_written", 0) + records_delta
            self._dirty += 1
            if self._dirty >= self._SAVE_EVERY:
                self._save_unlocked()

    def reset_partition(self, year: str) -> None:
        with self._lock:
            self._partitions[year] = {
                "status": "pending",
                "cursor": None,
                "records_written": 0,
            }
            self._dirty += 1
            if self._dirty >= self._SAVE_EVERY:
                self._save_unlocked()

    def reset_all(self) -> None:
        with self._lock:
            self._state["partitions"] = {}
            self._save_unlocked()

    def flush(self) -> None:
        with self._lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self._state, fh, indent=2)
        self._dirty = 0


def _jittered_delay(base: float) -> float:
    return base * random.uniform(0.8, 1.2)


class AlgoliaBrowseClient:
    """Synchronous Algolia /browse client (no proxies)."""

    def __init__(
        self,
        *,
        delay: float = DEFAULT_ALGOLIA_DELAY,
        client: httpx.Client | None = None,
    ) -> None:
        self.delay = delay
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> AlgoliaBrowseClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def browse(
        self,
        filters: str,
        cursor: str | None,
        *,
        retries: int = 5,
    ) -> dict[str, Any] | None:
        """
        POST Algolia /browse.

        Returns parsed JSON, {"_invalid_cursor": True} on expired cursor,
        or None on unrecoverable failure.
        """
        payload: dict[str, Any] = {
            "query": "",
            "hitsPerPage": HITS_PER_PAGE,
            "filters": filters,
        }
        if cursor:
            payload["cursor"] = cursor
        headers = {
            "X-Algolia-Application-Id": ALGOLIA_APP_ID,
            "X-Algolia-API-Key": ALGOLIA_API_KEY,
        }

        time.sleep(_jittered_delay(self.delay))
        for attempt in range(retries):
            try:
                resp = self._client.post(ALGOLIA_BROWSE_URL, json=payload, headers=headers)
                if resp.status_code in (429, 503):
                    wait = random.uniform(5, 15) * (attempt + 1)
                    LOGGER.warning(
                        "Algolia rate limited HTTP %s — sleeping %.0fs",
                        resp.status_code,
                        wait,
                    )
                    time.sleep(wait)
                    continue
                if resp.status_code >= 500:
                    LOGGER.warning("Algolia HTTP %s — retrying", resp.status_code)
                    time.sleep(2 ** (attempt + 1))
                    continue
                if resp.status_code >= 400:
                    message = ""
                    try:
                        message = str(resp.json().get("message", ""))
                    except Exception:
                        pass
                    if "cursor" in message.lower():
                        return {"_invalid_cursor": True}
                    LOGGER.error("Algolia HTTP %s: %s", resp.status_code, message)
                    return None
                data = resp.json()
                return data if isinstance(data, dict) else None
            except httpx.HTTPError as exc:
                if attempt < retries - 1:
                    time.sleep(2 ** (attempt + 1))
                else:
                    LOGGER.error("Algolia browse failed after %s attempts: %s", retries, exc)
        return None


def _process_year(
    year: str,
    *,
    state: AlgoliaBrowseState,
    browse: BrowseFn,
    on_entries: Callable[[list[SitemapEntry]], None] | None = None,
) -> int:
    """Browse one year partition. Returns hit-mapped count for this walk."""
    filters = year_filter(int(year))
    cursor = state.cursor(year) if state.status(year) == "in_progress" else None
    restarts = 0
    mapped = 0
    state.mark_partition(year, "in_progress", cursor=cursor)

    try:
        while True:
            data = browse(filters, cursor)
            if data is None:
                LOGGER.error("Algolia partition %s browse failed — marking failed", year)
                state.mark_partition(year, "failed", cursor=cursor)
                return mapped

            if data.get("_invalid_cursor"):
                restarts += 1
                if restarts > MAX_CURSOR_RESTARTS:
                    LOGGER.error(
                        "Algolia partition %s cursor kept expiring — marking failed",
                        year,
                    )
                    state.mark_partition(year, "failed", cursor=None)
                    return mapped
                LOGGER.warning(
                    "Algolia partition %s cursor expired — restarting from scratch",
                    year,
                )
                cursor = None
                mapped = 0
                continue

            hits = data.get("hits") or []
            if not isinstance(hits, list):
                hits = []
            page_entries = hits_to_sitemap_entries(hits)
            mapped += len(page_entries)
            if on_entries and page_entries:
                on_entries(page_entries)
            new_cursor = data.get("cursor")
            if new_cursor is not None:
                new_cursor = str(new_cursor)
            state.mark_partition(
                year,
                "in_progress",
                cursor=new_cursor,
                records_delta=len(page_entries),
            )
            cursor = new_cursor

            if not new_cursor or not hits:
                break

        state.mark_partition(year, "done", cursor=None)
        LOGGER.info("Algolia partition %s complete entries=%s", year, mapped)
    except Exception as exc:
        LOGGER.error("Algolia partition %s crashed: %s", year, exc)
        state.mark_partition(year, "failed", cursor=cursor)
    return mapped


def lot_cache_path(state_path: Path) -> Path:
    """Sidecar JSONL of discovered lots (source of truth for historical URLs)."""
    return state_path.with_name(state_path.stem + "_lots.jsonl")


def iter_lot_cache_rows(path: Path) -> Iterator[tuple[str, str, str | None]]:
    """Yield (entity_id, url, lastmod) from the lot JSONL cache."""
    if not path.exists():
        return
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError:
                    continue
                entity_id = str(row.get("entity_id") or "").strip().lower()
                url = str(row.get("url") or "").strip()
                if not entity_id or not url:
                    continue
                lastmod_raw = row.get("lastmod")
                lastmod = str(lastmod_raw) if lastmod_raw else None
                yield entity_id, url, lastmod
    except OSError as exc:
        LOGGER.warning("could not read Algolia lot cache %s: %s", path, exc)


def load_lot_cache_ids(path: Path) -> set[str]:
    """Stream entity ids only (no SitemapEntry objects). Logs every 1M lines."""
    seen: set[str] = set()
    if not path.exists():
        return seen
    count = 0
    for entity_id, _url, _lastmod in iter_lot_cache_rows(path):
        seen.add(entity_id)
        count += 1
        if count % 1_000_000 == 0:
            LOGGER.info(
                "Algolia lot-cache id load progress lines=%s unique=%s",
                count,
                len(seen),
            )
    LOGGER.info("Algolia lot-cache id load complete lines=%s unique=%s", count, len(seen))
    return seen


def _append_lot_cache(path: Path, entries: list[SitemapEntry]) -> None:
    if not entries:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(
                json.dumps(
                    {
                        "entity_id": entry.entity_id,
                        "url": entry.url,
                        "lastmod": entry.lastmod,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


def expand_lots_from_algolia(
    *,
    state_path: Path,
    from_year: int = EARLIEST_YEAR,
    to_year: int | None = None,
    workers: int = DEFAULT_ALGOLIA_WORKERS,
    delay: float = DEFAULT_ALGOLIA_DELAY,
    force: bool = False,
    browse: BrowseFn | None = None,
    client: httpx.Client | None = None,
) -> list[SitemapEntry]:
    """
    Browse archive_prod by year; persist lots to JSONL.

    Returns a capped list of **new** lot entries from this run (for small merges).
    The full historical set lives on disk — callers must stream `lot_cache_path`
    into job URL files instead of expecting a giant in-memory list.
    """
    if to_year is None:
        to_year = datetime.now(timezone.utc).year
    if from_year > to_year:
        raise ValueError(f"from_year {from_year} > to_year {to_year}")

    cache_path = lot_cache_path(state_path)
    state = AlgoliaBrowseState(state_path)
    if force:
        state.reset_all()
        if cache_path.exists():
            cache_path.unlink()
        LOGGER.info("Algolia force: browse state and lot cache cleared")

    years = [str(y) for y in range(from_year, to_year + 1)]
    todo = [y for y in years if state.status(y) != "done"]

    seen_ids = load_lot_cache_ids(cache_path) if cache_path.exists() else set()
    LOGGER.info(
        "Algolia browse years=%s-%s todo=%s cached_ids=%s workers=%s",
        from_year,
        to_year,
        len(todo),
        len(seen_ids),
        workers,
    )

    if not todo:
        LOGGER.info(
            "Algolia: all year partitions done — cache has %s ids (stream from disk for URLs)",
            len(seen_ids),
        )
        state.flush()
        return []

    own_client: AlgoliaBrowseClient | None = None
    browse_fn = browse
    if browse_fn is None:
        own_client = AlgoliaBrowseClient(delay=delay, client=client)
        browse_fn = own_client.browse

    lock = threading.Lock()
    new_this_run: list[SitemapEntry] = []
    new_written = 0

    def _ingest(entries: list[SitemapEntry]) -> None:
        nonlocal new_written
        with lock:
            fresh: list[SitemapEntry] = []
            for entry in entries:
                if entry.entity_id in seen_ids:
                    continue
                seen_ids.add(entry.entity_id)
                fresh.append(entry)
                new_written += 1
                if len(new_this_run) < _MAX_RETURN_NEW:
                    new_this_run.append(entry)
            _append_lot_cache(cache_path, fresh)

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            futures = {
                pool.submit(
                    _process_year,
                    year,
                    state=state,
                    browse=browse_fn,
                    on_entries=_ingest,
                ): year
                for year in todo
            }
            for future in as_completed(futures):
                year = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    LOGGER.error("Algolia year %s future failed: %s", year, exc)
    finally:
        state.flush()
        if own_client is not None:
            own_client.close()

    LOGGER.info(
        "Algolia browse complete cached_ids=%s new_written=%s new_returned=%s years_todo=%s",
        len(seen_ids),
        new_written,
        len(new_this_run),
        len(todo),
    )
    return list(new_this_run)


__all__ = [
    "ALGOLIA_APP_ID",
    "ALGOLIA_BROWSE_URL",
    "ALGOLIA_INDEX",
    "AlgoliaBrowseClient",
    "AlgoliaBrowseState",
    "DEFAULT_ALGOLIA_DELAY",
    "DEFAULT_ALGOLIA_WORKERS",
    "EARLIEST_YEAR",
    "build_lot_url",
    "expand_lots_from_algolia",
    "hit_to_sitemap_entry",
    "hits_to_sitemap_entries",
    "iter_lot_cache_rows",
    "load_lot_cache_ids",
    "lot_cache_path",
    "slugify",
    "year_filter",
]
