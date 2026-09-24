"""Saatchi Art discovery via Constructor.io browse API.

Phase 0 gate (``output/saatchi_probe/PHASE0_GATE.json``): no public Algolia;
Constructor search-only key is embedded in Saatchi ``_app`` / browse bundles.

Constructor caps each query at a 10k result window (``total_num_results`` and
page walk both stop at 10k). Coverage therefore requires facet partitions whose
**facet option counts** are ≤ ``MAX_WINDOW``, using the ladder:

  artwork_category → country → subject → size_bin → us_price_bin → city

The JSONL artwork cache is the source of truth for search URLs. Never load the
full corpus into a SitemapEntry list — stream ids / rows instead.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any

from html_downloader.discover.registry import normalize_url
from html_downloader.discover.sitemap import SitemapEntry
from html_downloader.discover.urls import saatchi_entity_from_url

LOGGER = logging.getLogger(__name__)

# Public search-only key from Saatchi ``getConstructorIndex()`` (same trust
# level as InstantSearch keys used for auction houses).
CONSTRUCTOR_KEY = "key_cn3mctZ73MD3U2jM"
CONSTRUCTOR_SERVICE_URL = "https://ac.cnstrc.com"
CONSTRUCTOR_SECTION = "Products"
CONSTRUCTOR_CLIENT = "ciojs-client-2.XX"

BASE_URL = "https://www.saatchiart.com"
HITS_PER_PAGE = 100
MAX_WINDOW = 10_000
MAX_PAGES_PER_PARTITION = MAX_WINDOW // HITS_PER_PAGE  # 100
DEFAULT_SEARCH_DELAY = 0.2
_MAX_RETURN_NEW = 50_000

# Seed categories (SSR / InstantSearch artwork_category facet values).
ARTWORK_CATEGORIES: tuple[str, ...] = (
    "painting",
    "photography",
    "sculpture",
    "drawing",
    "printmaking",
    "collage",
    "digital",
    "mixed media",
    "installation",
)

# Facets used to subdivide when a partition's facet count exceeds MAX_WINDOW.
# First element is always the browse path facet (artwork_category).
PARTITION_LADDER: tuple[str, ...] = (
    "artwork_category",
    "country",
    "subject",
    "size_bin",
    "us_price_bin",
    "city",
)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# (filter_pairs, page, hits_per_page) -> response dict (Constructor ``response``)
BrowseFn = Callable[[Sequence[tuple[str, str]], int, int], dict[str, Any]]


def artwork_cache_path(state_path: Path) -> Path:
    """Sidecar JSONL of discovered artworks (source of truth for search URLs)."""
    return state_path.with_name(state_path.stem + "_lots.jsonl")


def lot_cache_path(state_path: Path) -> Path:
    """Alias matching auction Algolia naming (JSONL sidecar)."""
    return artwork_cache_path(state_path)


def partition_key(filters: Sequence[tuple[str, str]]) -> str:
    """Stable state key for a filter chain."""
    return "|".join(f"{name}={value}" for name, value in filters)


def hit_to_artwork_url(hit: dict[str, Any]) -> str | None:
    """Build absolute Saatchi artwork URL from a Constructor hit."""
    data = hit.get("data") if isinstance(hit.get("data"), dict) else hit
    if not isinstance(data, dict):
        return None
    raw = str(data.get("url") or data.get("artworkOriginalUrl") or "").strip()
    if not raw:
        artwork_id = str(data.get("id") or data.get("artworkId") or "").strip()
        artist_id = str(data.get("artistId") or "").strip()
        if artwork_id and artist_id:
            raw = f"/art/Artwork/{artist_id}/{artwork_id}/view"
        else:
            return None
    if raw.startswith("http://") or raw.startswith("https://"):
        absolute = raw
    else:
        absolute = BASE_URL + (raw if raw.startswith("/") else f"/{raw}")
    return normalize_url(absolute) or absolute.rstrip("/")


def hit_to_sitemap_entry(hit: dict[str, Any]) -> SitemapEntry | None:
    url = hit_to_artwork_url(hit)
    if not url:
        return None
    key = saatchi_entity_from_url(url)
    if key is None or key[0] != "artwork":
        return None
    return SitemapEntry(
        url=url,
        lastmod=None,
        entity_type=key[0],
        entity_id=key[1],
    )


def hits_to_sitemap_entries(hits: Sequence[dict[str, Any]]) -> list[SitemapEntry]:
    out: list[SitemapEntry] = []
    for hit in hits:
        entry = hit_to_sitemap_entry(hit)
        if entry is not None:
            out.append(entry)
    return out


def iter_artwork_cache_rows(
    path: Path,
) -> Iterator[tuple[str, str, str | None]]:
    """Yield (entity_id, url, lastmod) from the artwork JSONL cache."""
    if not path.exists():
        return
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    row = json.loads(text)
                except json.JSONDecodeError:
                    continue
                entity_id = str(row.get("entity_id") or "").strip()
                url = str(row.get("url") or "").strip()
                if not entity_id or not url:
                    continue
                lastmod_raw = row.get("lastmod")
                lastmod = str(lastmod_raw) if lastmod_raw else None
                yield entity_id, url, lastmod
    except OSError as exc:
        LOGGER.warning("could not read Saatchi search cache %s: %s", path, exc)


# Auction-style alias used by shared stream helpers.
iter_lot_cache_rows = iter_artwork_cache_rows


def load_artwork_cache_keys(path: Path) -> set[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    if not path.exists():
        return seen
    count = 0
    for entity_id, _url, _lastmod in iter_artwork_cache_rows(path):
        seen.add(("artwork", entity_id))
        count += 1
        if count % 500_000 == 0:
            LOGGER.info(
                "saatchi search cache load progress lines=%s unique=%s",
                count,
                len(seen),
            )
    LOGGER.info(
        "saatchi search cache load complete lines=%s unique=%s",
        count,
        len(seen),
    )
    return seen


load_lot_cache_keys = load_artwork_cache_keys


def load_artwork_cache_urls(path: Path) -> list[str]:
    best: dict[str, str] = {}
    for entity_id, url, _lastmod in iter_artwork_cache_rows(path):
        best[entity_id] = url
    return sorted(best.values())


load_lot_cache_urls = load_artwork_cache_urls


def append_artwork_cache(path: Path, entries: Sequence[SitemapEntry]) -> int:
    if not entries:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(path, "a", encoding="utf-8") as handle:
        for entry in entries:
            if not entry.entity_id or entry.entity_type != "artwork":
                continue
            handle.write(
                json.dumps(
                    {
                        "entity_type": entry.entity_type,
                        "entity_id": entry.entity_id,
                        "url": entry.url,
                        "lastmod": entry.lastmod,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            written += 1
    return written


append_lot_cache = append_artwork_cache


class SearchBrowseState:
    """JSON resume state keyed by partition filter chain."""

    _SAVE_EVERY = 5

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._dirty = 0
        self._state: dict[str, Any] = {"partitions": {}}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get("partitions"), dict):
                    self._state = loaded
            except (OSError, json.JSONDecodeError) as exc:
                LOGGER.warning("saatchi search state load failed path=%s error=%s", path, exc)
        self._partitions: dict[str, Any] = self._state.setdefault("partitions", {})

    def status(self, key: str) -> str:
        entry = self._partitions.get(key) or {}
        return str(entry.get("status") or "pending")

    def next_page(self, key: str) -> int:
        entry = self._partitions.get(key) or {}
        try:
            return max(1, int(entry.get("next_page") or 1))
        except (TypeError, ValueError):
            return 1

    def mark_partition(
        self,
        key: str,
        status: str,
        *,
        next_page: int | None = None,
        records_delta: int = 0,
        estimated_count: int | None = None,
    ) -> None:
        with self._lock:
            entry = self._partitions.setdefault(
                key,
                {"status": "pending", "next_page": 1, "records_written": 0},
            )
            entry["status"] = status
            if next_page is not None:
                entry["next_page"] = next_page
            if records_delta:
                entry["records_written"] = int(entry.get("records_written") or 0) + records_delta
            if estimated_count is not None:
                entry["estimated_count"] = estimated_count
            self._dirty += 1
            if self._dirty >= self._SAVE_EVERY:
                self._save_unlocked()

    def reset_all(self) -> None:
        with self._lock:
            self._state = {"partitions": {}}
            self._partitions = self._state["partitions"]
            self._dirty += 1
            self._save_unlocked()

    def flush(self) -> None:
        with self._lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        text = json.dumps(self._state, ensure_ascii=False, indent=2) + "\n"
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)
        self._dirty = 0


def _constructor_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "Referer": f"{BASE_URL}/",
        "Origin": BASE_URL,
    }


def browse_page(
    filters: Sequence[tuple[str, str]],
    page: int,
    hits_per_page: int = HITS_PER_PAGE,
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """
    GET one Constructor browse page (direct; ignores HTTP_PROXY).

    First ``(name, value)`` pair is the browse path; remaining pairs become
    ``filters[name]=value`` query params.
    """
    if not filters:
        raise ValueError("filters must contain at least one (name, value) pair")
    head_name, head_value = filters[0]
    path = (
        f"/browse/{urllib.parse.quote(head_name, safe='')}/"
        f"{urllib.parse.quote(head_value, safe='')}"
    )
    params: list[tuple[str, str]] = [
        ("key", CONSTRUCTOR_KEY),
        ("c", CONSTRUCTOR_CLIENT),
        ("i", "saatchi-html-downloader"),
        ("s", "1"),
        ("num_results_per_page", str(int(hits_per_page))),
        ("page", str(int(page))),
        ("section", CONSTRUCTOR_SECTION),
    ]
    for name, value in filters[1:]:
        params.append((f"filters[{name}]", value))
    url = f"{CONSTRUCTOR_SERVICE_URL}{path}?{urllib.parse.urlencode(params)}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(url, method="GET", headers=_constructor_headers())
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(
            f"constructor browse status={exc.code} filters={filters!r} page={page}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"constructor browse network error filters={filters!r}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"constructor browse invalid JSON filters={filters!r}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"constructor browse unexpected payload type={type(payload)!r}")
    response = payload.get("response")
    if isinstance(response, dict):
        return response
    if payload.get("message"):
        raise RuntimeError(
            f"constructor browse error message={payload.get('message')!r} filters={filters!r}"
        )
    return payload


def _facet_options(
    filters: Sequence[tuple[str, str]],
    facet: str,
    *,
    browse: BrowseFn | None = None,
) -> list[tuple[str, int]]:
    """Return (value, count) for ``facet`` under ``filters``, largest first."""
    browse_fn = browse or (lambda f, page, hpp: browse_page(f, page, hpp))
    payload = browse_fn(filters, 1, 1)
    raw_facets = payload.get("facets") or []
    options: list[tuple[str, int]] = []
    if isinstance(raw_facets, list):
        for item in raw_facets:
            if not isinstance(item, dict):
                continue
            if str(item.get("name") or "") != facet:
                continue
            for opt in item.get("options") or []:
                if not isinstance(opt, dict):
                    continue
                value = str(opt.get("value") or opt.get("display_name") or "").strip()
                if not value:
                    continue
                try:
                    count = int(opt.get("count") or 0)
                except (TypeError, ValueError):
                    count = 0
                options.append((value, count))
            break
    options.sort(key=lambda item: item[1], reverse=True)
    return options


def plan_partitions(
    categories: Sequence[str] = ARTWORK_CATEGORIES,
    *,
    browse: BrowseFn | None = None,
    max_window: int = MAX_WINDOW,
    ladder: Sequence[str] = PARTITION_LADDER,
) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
    """
    Return ``(partition_key, filters)`` pairs under the Constructor 10k window.

    Uses facet option counts (not ``total_num_results``) to decide splits.
    """
    if not ladder or ladder[0] != "artwork_category":
        raise ValueError("ladder must start with artwork_category")
    browse_fn = browse or (lambda f, page, hpp: browse_page(f, page, hpp))
    ceiling = max(1, int(max_window))
    split_facets = tuple(ladder[1:])
    planned: list[tuple[str, tuple[tuple[str, str], ...]]] = []

    def _recurse(
        filters: tuple[tuple[str, str], ...],
        remaining: Sequence[str],
        estimated: int | None,
    ) -> None:
        if estimated is not None and estimated <= ceiling:
            planned.append((partition_key(filters), filters))
            return
        if not remaining:
            LOGGER.warning(
                "saatchi search partition=%s estimated=%s still above ceiling=%s; "
                "keeping leaf (coverage may truncate at %s)",
                partition_key(filters),
                estimated,
                ceiling,
                ceiling,
            )
            planned.append((partition_key(filters), filters))
            return
        facet = remaining[0]
        try:
            options = _facet_options(filters, facet, browse=browse_fn)
        except Exception as exc:
            LOGGER.warning(
                "saatchi search facet probe failed filters=%s facet=%s error=%s",
                filters,
                facet,
                exc,
            )
            planned.append((partition_key(filters), filters))
            return
        if not options:
            LOGGER.warning(
                "saatchi search filters=%s need split on %s but no options; keeping leaf",
                filters,
                facet,
            )
            planned.append((partition_key(filters), filters))
            return
        LOGGER.info(
            "saatchi search splitting filters=%s on facet=%s options=%s estimated=%s",
            partition_key(filters),
            facet,
            len(options),
            estimated,
        )
        for value, count in options:
            child = filters + ((facet, value),)
            if count <= ceiling:
                planned.append((partition_key(child), child))
            else:
                _recurse(child, remaining[1:], count)

    for category in categories:
        cleaned = (category or "").strip()
        if not cleaned:
            continue
        root: tuple[tuple[str, str], ...] = (("artwork_category", cleaned),)
        # Always expand categories: API total is capped at 10k while SSR shows
        # hundreds of thousands per category.
        _recurse(root, split_facets, estimated=None)

    return planned


def _walk_partition(
    *,
    key: str,
    filters: Sequence[tuple[str, str]],
    state: SearchBrowseState,
    seen_keys: set[tuple[str, str]],
    ingest: Callable[[list[SitemapEntry]], None],
    browse: BrowseFn,
    hits_per_page: int,
    delay: float,
    max_pages: int,
) -> int:
    if state.status(key) == "done":
        return 0

    page = state.next_page(key)
    mapped = 0
    empty_streak = 0
    state.mark_partition(key, "in_progress", next_page=page)

    while page <= max_pages:
        if page > 1 and delay > 0:
            time.sleep(delay)
        try:
            payload = browse(filters, page, hits_per_page)
        except Exception as exc:
            LOGGER.error(
                "saatchi search partition=%s page=%s failed: %s",
                key,
                page,
                exc,
            )
            state.mark_partition(key, "failed", next_page=page)
            return mapped

        results = payload.get("results") or []
        if not isinstance(results, list) or not results:
            empty_streak += 1
            if empty_streak == 1:
                time.sleep(max(delay, 0.5))
                continue
            state.mark_partition(key, "done", next_page=page)
            break

        empty_streak = 0
        entries = hits_to_sitemap_entries(results)
        fresh: list[SitemapEntry] = []
        for entry in entries:
            ek = entry.entity_key
            if ek in seen_keys:
                continue
            seen_keys.add(ek)
            fresh.append(entry)
        if fresh:
            ingest(fresh)
            mapped += len(fresh)
            state.mark_partition(
                key,
                "in_progress",
                next_page=page + 1,
                records_delta=len(fresh),
            )
        else:
            state.mark_partition(key, "in_progress", next_page=page + 1)

        if len(results) < hits_per_page:
            state.mark_partition(key, "done", next_page=page + 1)
            break
        page += 1
    else:
        LOGGER.warning(
            "saatchi search partition=%s hit page ceiling=%s; marking done",
            key,
            max_pages,
        )
        state.mark_partition(key, "done", next_page=page)

    LOGGER.info("saatchi search partition=%s complete mapped_new=%s", key, mapped)
    return mapped


def expand_artworks_from_search(
    *,
    state_path: Path,
    categories: Sequence[str] | None = None,
    hits_per_page: int = HITS_PER_PAGE,
    delay: float = DEFAULT_SEARCH_DELAY,
    force: bool = False,
    browse: BrowseFn | None = None,
    max_window: int = MAX_WINDOW,
    workers: int = 1,
) -> list[SitemapEntry]:
    """
    Paginate Constructor browse partitions into a durable JSONL cache.

    Returns a capped list of **new** artwork entries from this run. Full
    coverage lives on disk — callers should stream ``artwork_cache_path``.

    ``workers`` is accepted for CLI symmetry; partition walks are sequential
    (Constructor rate limits). Reserved for future parallel partition walks.
    """
    del workers  # sequential for now
    cats = tuple(categories) if categories is not None else ARTWORK_CATEGORIES
    browse_fn = browse or (lambda f, page, hpp: browse_page(f, page, hpp))
    max_pages = max(1, MAX_WINDOW // max(1, hits_per_page))

    cache_path = artwork_cache_path(state_path)
    state = SearchBrowseState(state_path)
    if force:
        state.reset_all()
        if cache_path.exists():
            cache_path.unlink()
        LOGGER.info("saatchi search force: state and artwork cache cleared")

    partitions = plan_partitions(
        cats,
        browse=browse,
        max_window=max_window,
    )
    planned_keys = {key for key, _filters in partitions}
    state_keys = set(state._partitions.keys())

    def _superseded(key: str) -> bool:
        prefix = key + "|"
        if any(pk.startswith(prefix) for pk in planned_keys):
            return True
        return any(
            sk.startswith(prefix) and state.status(sk) == "done" for sk in state_keys
        )

    todo = [
        p
        for p in partitions
        if state.status(p[0]) != "done" and not _superseded(p[0])
    ]

    seen_keys = load_artwork_cache_keys(cache_path) if cache_path.exists() else set()
    LOGGER.info(
        "saatchi search expand partitions=%s todo=%s cached_keys=%s hits_per_page=%s",
        len(partitions),
        len(todo),
        len(seen_keys),
        hits_per_page,
    )

    if not todo:
        LOGGER.info(
            "saatchi search: all partitions done — cache has %s keys",
            len(seen_keys),
        )
        state.flush()
        return []

    lock = threading.Lock()
    new_this_run: list[SitemapEntry] = []
    new_written = 0

    def ingest(entries: list[SitemapEntry]) -> None:
        nonlocal new_written
        if not entries:
            return
        append_artwork_cache(cache_path, entries)
        with lock:
            new_written += len(entries)
            remaining = _MAX_RETURN_NEW - len(new_this_run)
            if remaining > 0:
                new_this_run.extend(entries[:remaining])

    for key, filters in todo:
        _walk_partition(
            key=key,
            filters=filters,
            state=state,
            seen_keys=seen_keys,
            ingest=ingest,
            browse=browse_fn,
            hits_per_page=hits_per_page,
            delay=delay,
            max_pages=max_pages,
        )

    state.flush()
    LOGGER.info(
        "saatchi search expand complete new_written=%s returned=%s cached_keys=%s",
        new_written,
        len(new_this_run),
        len(seen_keys),
    )
    return list(new_this_run)


# CLI / service alias matching auction expand naming.
expand_lots_from_algolia = expand_artworks_from_search


__all__ = [
    "ARTWORK_CATEGORIES",
    "CONSTRUCTOR_KEY",
    "CONSTRUCTOR_SERVICE_URL",
    "HITS_PER_PAGE",
    "MAX_WINDOW",
    "PARTITION_LADDER",
    "append_artwork_cache",
    "artwork_cache_path",
    "browse_page",
    "expand_artworks_from_search",
    "hit_to_artwork_url",
    "hit_to_sitemap_entry",
    "hits_to_sitemap_entries",
    "iter_artwork_cache_rows",
    "iter_lot_cache_rows",
    "load_artwork_cache_keys",
    "load_lot_cache_keys",
    "load_artwork_cache_urls",
    "load_lot_cache_urls",
    "lot_cache_path",
    "partition_key",
    "plan_partitions",
]
