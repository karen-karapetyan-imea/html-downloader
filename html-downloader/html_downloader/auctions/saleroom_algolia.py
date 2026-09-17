"""Saleroom art/collectables discovery via Algolia InstantSearch (lots_sr_en).

Public search-only credentials are embedded in the-saleroom.com InstantSearch
config (same trust level as Invaluable). Browse ACL is denied — discovery uses
filtered ``/query`` pagination over masterCategoryCode partitions.

Lot URL template (from page config):
  /en-gb/auction-catalogues/{auctioneerRef}/catalogue-id-{auctionRef}/lot-{objectID}
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
from html_downloader.auctions.urls import normalize_auction_url, saleroom_entity_from_url
from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

# InstantSearch config from https://www.the-saleroom.com/en-gb (search-only key).
ALGOLIA_APP_ID = "2125HT9M59"
ALGOLIA_API_KEY = "b1f48e75f522cc67ff7751f1abd75458"
ALGOLIA_INDEX = "lots_sr_en"
ALGOLIA_QUERY_URL = (
    f"https://{ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"
)

BASE_URL = "https://www.the-saleroom.com"
HITS_PER_PAGE = 100
# Soft ceiling: if a single filter reports more pages than this, country-split.
MAX_PAGES_PER_PARTITION = 500
DEFAULT_ALGOLIA_DELAY = 0.25
_MAX_RETURN_NEW = 50_000

# Art + collectables family (probed masterCategoryCode values).
ART_MASTER_CATEGORY_CODES: tuple[str, ...] = (
    "FIA",  # Fine Art
    "DEA",  # Decorative Art
    "AA",  # Asian Art
    "ETA",  # Ethnographica & Tribal Art
    "COL",  # Collectables
    "GRE",  # Greek, Roman, Egyptian & Other Antiquities
)

ART_MASTER_CATEGORY_NAMES: dict[str, str] = {
    "FIA": "Fine Art",
    "DEA": "Decorative Art",
    "AA": "Asian Art",
    "ETA": "Ethnographica & Tribal Art",
    "COL": "Collectables",
    "GRE": "Greek, Roman, Egyptian & Other Antiquities",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

QueryFn = Callable[[str, int, int], dict[str, Any]]


def master_category_filter(code: str) -> str:
    """Algolia filter for one masterCategoryCode."""
    cleaned = (code or "").strip().upper()
    if not cleaned:
        raise ValueError("masterCategoryCode must be non-empty")
    return f"masterCategoryCode:{cleaned}"


def country_filter(country: str) -> str:
    escaped = (country or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'countryName:"{escaped}"'


def auctioneer_filter(name: str) -> str:
    escaped = (name or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'auctioneerName:"{escaped}"'


def combine_filters(*parts: str) -> str:
    clauses = [p.strip() for p in parts if p and p.strip()]
    return " AND ".join(clauses)


def hit_to_lot_url(hit: dict[str, Any]) -> str | None:
    """Build canonical en-gb lot URL from an Algolia hit."""
    auctioneer_ref = str(hit.get("auctioneerRef") or "").strip()
    auction_ref = str(hit.get("auctionRef") or "").strip()
    object_id = str(hit.get("objectID") or "").strip()
    if not auctioneer_ref or not auction_ref or not object_id:
        return None
    raw = (
        f"{BASE_URL}/en-gb/auction-catalogues/{auctioneer_ref}/"
        f"catalogue-id-{auction_ref}/lot-{object_id}"
    )
    return normalize_auction_url(raw)


def hit_to_sitemap_entry(hit: dict[str, Any]) -> SitemapEntry | None:
    """Map one lots_sr_en hit to a lot SitemapEntry."""
    url = hit_to_lot_url(hit)
    if not url:
        return None
    key = saleroom_entity_from_url(url)
    if key is None or key[0] != "lot":
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


def lot_cache_path(state_path: Path) -> Path:
    """Sidecar JSONL of discovered lots (source of truth for Algolia URLs)."""
    return state_path.with_name(state_path.stem + "_lots.jsonl")


def iter_lot_cache_rows(
    path: Path,
) -> Iterator[tuple[str, str, str | None]]:
    """Yield (entity_id, url, lastmod) from the lot JSONL cache."""
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
                entity_id = str(row.get("entity_id") or "").strip().lower()
                url = str(row.get("url") or "").strip()
                if not entity_id or not url:
                    continue
                lastmod_raw = row.get("lastmod")
                lastmod = str(lastmod_raw) if lastmod_raw else None
                yield entity_id, url, lastmod
    except OSError as exc:
        LOGGER.warning("could not read Saleroom Algolia lot cache %s: %s", path, exc)


def load_lot_cache_keys(path: Path) -> set[tuple[str, str]]:
    """Stream (entity_type, entity_id) keys from the JSONL cache."""
    seen: set[tuple[str, str]] = set()
    if not path.exists():
        return seen
    count = 0
    for entity_id, _url, _lastmod in iter_lot_cache_rows(path):
        seen.add(("lot", entity_id))
        count += 1
        if count % 500_000 == 0:
            LOGGER.info(
                "saleroom algolia lot-cache load progress lines=%s unique=%s",
                count,
                len(seen),
            )
    LOGGER.info(
        "saleroom algolia lot-cache load complete lines=%s unique=%s",
        count,
        len(seen),
    )
    return seen


def load_lot_cache_urls(path: Path) -> list[str]:
    """Unique lot URLs from JSONL (latest URL wins per entity_id)."""
    best: dict[str, str] = {}
    for entity_id, url, _lastmod in iter_lot_cache_rows(path):
        best[entity_id] = url
    return sorted(best.values())


def append_lot_cache(path: Path, entries: Sequence[SitemapEntry]) -> int:
    if not entries:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(path, "a", encoding="utf-8") as handle:
        for entry in entries:
            if not entry.entity_id or entry.entity_type != "lot":
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


class AlgoliaSearchState:
    """Partition page checkpoint for Saleroom Algolia search resume."""

    _SAVE_EVERY = 3

    def __init__(self, path: Path) -> None:
        self.path = path
        self._state: dict[str, Any] = {"partitions": {}}
        self._dirty = 0
        self._lock = threading.Lock()
        if path.exists():
            try:
                with open(path, encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    self._state = loaded
            except Exception as exc:
                LOGGER.warning("could not load Saleroom Algolia state: %s", exc)
        self._state.setdefault("partitions", {})

    @property
    def _partitions(self) -> dict[str, dict[str, Any]]:
        return self._state["partitions"]

    def status(self, key: str) -> str | None:
        entry = self._partitions.get(key)
        return entry.get("status") if entry else None

    def next_page(self, key: str) -> int:
        entry = self._partitions.get(key) or {}
        try:
            return max(0, int(entry.get("next_page") or 0))
        except (TypeError, ValueError):
            return 0

    def mark_partition(
        self,
        key: str,
        status: str,
        *,
        next_page: int | None = None,
        records_delta: int = 0,
        nb_hits: int | None = None,
    ) -> None:
        with self._lock:
            entry = self._partitions.setdefault(
                key,
                {"status": "pending", "next_page": 0, "records_written": 0},
            )
            entry["status"] = status
            if next_page is not None:
                entry["next_page"] = next_page
            if records_delta:
                entry["records_written"] = int(entry.get("records_written") or 0) + records_delta
            if nb_hits is not None:
                entry["nb_hits"] = nb_hits
            self._dirty += 1
            if self._dirty >= self._SAVE_EVERY:
                self._save_unlocked()

    def reset_all(self) -> None:
        with self._lock:
            self._state = {"partitions": {}}
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


def _algolia_headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Algolia-Application-Id": ALGOLIA_APP_ID,
        "X-Algolia-API-Key": ALGOLIA_API_KEY,
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }


def query_page(
    filters: str,
    page: int,
    hits_per_page: int = HITS_PER_PAGE,
    *,
    facets: str | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """POST one Algolia /query page (direct; ignores HTTP_PROXY)."""
    parts = [
        "query=",
        f"filters={urllib.parse.quote(filters, safe=':\"() ')}",
        f"page={int(page)}",
        f"hitsPerPage={int(hits_per_page)}",
        "attributesToHighlight=",
        "attributesToSnippet=",
    ]
    if facets:
        parts.append(f"facets={urllib.parse.quote(facets, safe='*,')}")
        parts.append("maxValuesPerFacet=100")
    params = "&".join(parts)
    body = json.dumps({"params": params}).encode("utf-8")
    # Empty ProxyHandler → ignore env proxies (residential proxies break Algolia/CDN).
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(
        ALGOLIA_QUERY_URL,
        data=body,
        method="POST",
        headers=_algolia_headers(),
    )
    try:
        with opener.open(req, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise RuntimeError(
            f"algolia query status={exc.code} filters={filters!r} page={page}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"algolia query network error filters={filters!r}: {exc}") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"algolia query invalid JSON filters={filters!r}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"algolia query unexpected payload type={type(payload)!r}")
    if payload.get("message") and payload.get("status"):
        raise RuntimeError(
            f"algolia query error status={payload.get('status')} "
            f"message={payload.get('message')}"
        )
    return payload


def _facet_names(
    filters: str,
    facet: str,
    *,
    query: QueryFn | None = None,
) -> list[str]:
    """Return facet values for ``filters``, largest first."""
    if query is not None:
        payload = query(filters, 0, 0)
    else:
        payload = query_page(filters, 0, 0, facets=facet)
    raw = (payload.get("facets") or {}).get(facet) or {}
    if not isinstance(raw, dict):
        return []
    ranked = sorted(
        ((str(name), int(count)) for name, count in raw.items() if name),
        key=lambda item: item[1],
        reverse=True,
    )
    return [name for name, _count in ranked]


def list_country_partitions(master_code: str, *, query: QueryFn | None = None) -> list[str]:
    """Return countryName facet values for a master category (largest first)."""
    return _facet_names(master_category_filter(master_code), "countryName", query=query)


def list_auctioneer_partitions(filters: str, *, query: QueryFn | None = None) -> list[str]:
    """Return auctioneerName facet values for a filter (largest first)."""
    return _facet_names(filters, "auctioneerName", query=query)


def plan_partitions(
    master_codes: Sequence[str] = ART_MASTER_CATEGORY_CODES,
    *,
    query: QueryFn | None = None,
    max_pages: int = MAX_PAGES_PER_PARTITION,
    hits_per_page: int = HITS_PER_PAGE,
) -> list[tuple[str, str]]:
    """
    Return (partition_key, filters) pairs.

    Uses masterCategoryCode alone when nbHits fits under max_pages * hits_per_page;
    otherwise expands to master+countryName, then master+country+auctioneerName
    when a country partition still exceeds the ceiling.
    """
    query_fn = query or (lambda filters, page, hpp: query_page(filters, page, hpp))
    planned: list[tuple[str, str]] = []
    ceiling = max(1, max_pages) * max(1, hits_per_page)

    for code in master_codes:
        code_u = code.strip().upper()
        if not code_u:
            continue
        filters = master_category_filter(code_u)
        try:
            payload = query_fn(filters, 0, 0)
        except Exception as exc:
            LOGGER.warning("saleroom algolia plan probe failed code=%s error=%s", code_u, exc)
            planned.append((code_u, filters))
            continue
        nb_hits = int(payload.get("nbHits") or 0)
        if nb_hits <= ceiling:
            planned.append((code_u, filters))
            continue
        countries = list_country_partitions(code_u, query=query)
        if not countries:
            LOGGER.warning(
                "saleroom algolia code=%s nbHits=%s exceeds ceiling=%s but no countries; "
                "keeping single partition",
                code_u,
                nb_hits,
                ceiling,
            )
            planned.append((code_u, filters))
            continue
        LOGGER.info(
            "saleroom algolia splitting code=%s nbHits=%s into countries=%s",
            code_u,
            nb_hits,
            len(countries),
        )
        for country in countries:
            country_filters = combine_filters(filters, country_filter(country))
            try:
                country_payload = query_fn(country_filters, 0, 0)
                country_hits = int(country_payload.get("nbHits") or 0)
            except Exception as exc:
                LOGGER.warning(
                    "saleroom algolia country probe failed code=%s country=%s error=%s",
                    code_u,
                    country,
                    exc,
                )
                planned.append((f"{code_u}|{country}", country_filters))
                continue
            if country_hits <= ceiling:
                planned.append((f"{code_u}|{country}", country_filters))
                continue
            auctioneers = list_auctioneer_partitions(country_filters, query=query)
            if not auctioneers:
                LOGGER.warning(
                    "saleroom algolia code=%s country=%s nbHits=%s exceeds ceiling but "
                    "no auctioneers; keeping country partition",
                    code_u,
                    country,
                    country_hits,
                )
                planned.append((f"{code_u}|{country}", country_filters))
                continue
            LOGGER.info(
                "saleroom algolia splitting code=%s country=%s nbHits=%s into auctioneers=%s",
                code_u,
                country,
                country_hits,
                len(auctioneers),
            )
            for auctioneer in auctioneers:
                key = f"{code_u}|{country}|{auctioneer}"
                planned.append(
                    (
                        key,
                        combine_filters(country_filters, auctioneer_filter(auctioneer)),
                    )
                )
    return planned


def _walk_partition(
    *,
    partition_key: str,
    filters: str,
    state: AlgoliaSearchState,
    seen_keys: set[tuple[str, str]],
    ingest: Callable[[list[SitemapEntry]], None],
    query: QueryFn,
    hits_per_page: int,
    delay: float,
) -> int:
    """Paginate one filter partition; return mapped hit count this walk."""
    if state.status(partition_key) == "done":
        return 0

    page = state.next_page(partition_key)
    mapped = 0
    empty_streak = 0
    state.mark_partition(partition_key, "in_progress", next_page=page)

    while True:
        if page > 0 and delay > 0:
            time.sleep(delay)
        try:
            payload = query(filters, page, hits_per_page)
        except Exception as exc:
            LOGGER.error(
                "saleroom algolia partition=%s page=%s failed: %s",
                partition_key,
                page,
                exc,
            )
            state.mark_partition(partition_key, "failed", next_page=page)
            return mapped

        hits = payload.get("hits") or []
        nb_hits = int(payload.get("nbHits") or 0)
        if not isinstance(hits, list) or not hits:
            empty_streak += 1
            # Transient empty pages happen near Algolia pagination edges; retry once.
            if empty_streak == 1:
                time.sleep(max(delay, 0.5))
                continue
            state.mark_partition(
                partition_key, "done", next_page=page, nb_hits=nb_hits
            )
            break

        empty_streak = 0
        entries = hits_to_sitemap_entries(hits)
        fresh: list[SitemapEntry] = []
        for entry in entries:
            key = entry.entity_key
            if key in seen_keys:
                continue
            seen_keys.add(key)
            fresh.append(entry)
        if fresh:
            ingest(fresh)
            mapped += len(fresh)
            state.mark_partition(
                partition_key,
                "in_progress",
                next_page=page + 1,
                records_delta=len(fresh),
                nb_hits=nb_hits,
            )
        else:
            state.mark_partition(
                partition_key,
                "in_progress",
                next_page=page + 1,
                nb_hits=nb_hits,
            )

        page += 1
        nb_pages = payload.get("nbPages")
        if isinstance(nb_pages, int) and nb_pages > 0 and page >= nb_pages:
            # nbPages can under-report while nbHits is still higher; keep going
            # if we still receive full pages below the soft ceiling.
            if len(hits) >= hits_per_page and page * hits_per_page < nb_hits:
                continue
            state.mark_partition(partition_key, "done", next_page=page, nb_hits=nb_hits)
            break
        if len(hits) < hits_per_page:
            state.mark_partition(partition_key, "done", next_page=page, nb_hits=nb_hits)
            break
        if page >= MAX_PAGES_PER_PARTITION:
            LOGGER.warning(
                "saleroom algolia partition=%s hit page ceiling=%s nbHits=%s; "
                "marking done (consider finer facet split)",
                partition_key,
                MAX_PAGES_PER_PARTITION,
                nb_hits,
            )
            state.mark_partition(partition_key, "done", next_page=page, nb_hits=nb_hits)
            break

    LOGGER.info(
        "saleroom algolia partition=%s complete mapped_new=%s",
        partition_key,
        mapped,
    )
    return mapped


def expand_lots_from_algolia(
    *,
    state_path: Path,
    master_codes: Sequence[str] | None = None,
    hits_per_page: int = HITS_PER_PAGE,
    delay: float = DEFAULT_ALGOLIA_DELAY,
    force: bool = False,
    query: QueryFn | None = None,
    max_pages_per_partition: int = MAX_PAGES_PER_PARTITION,
) -> list[SitemapEntry]:
    """
    Paginate art/collectables master categories into a durable JSONL cache.

    Returns a capped list of **new** lot entries from this run. Full coverage
    lives on disk — callers should stream ``lot_cache_path(state_path)``.
    """
    codes = tuple(master_codes) if master_codes is not None else ART_MASTER_CATEGORY_CODES
    query_fn = query or (lambda filters, page, hpp: query_page(filters, page, hpp))

    cache_path = lot_cache_path(state_path)
    state = AlgoliaSearchState(state_path)
    if force:
        state.reset_all()
        if cache_path.exists():
            cache_path.unlink()
        LOGGER.info("saleroom algolia force: state and lot cache cleared")

    partitions = plan_partitions(
        codes,
        query=query,  # None → live faceted country split; mocks inject custom query
        max_pages=max_pages_per_partition,
        hits_per_page=hits_per_page,
    )
    planned_keys = {key for key, _filters in partitions}
    state_keys = set(state._partitions.keys())

    def _superseded(key: str) -> bool:
        """True if a finer country/auctioneer child exists in plan or done state."""
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

    seen_keys = load_lot_cache_keys(cache_path) if cache_path.exists() else set()
    LOGGER.info(
        "saleroom algolia expand partitions=%s todo=%s cached_keys=%s hits_per_page=%s",
        len(partitions),
        len(todo),
        len(seen_keys),
        hits_per_page,
    )

    if not todo:
        LOGGER.info(
            "saleroom algolia: all partitions done — cache has %s keys",
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
        append_lot_cache(cache_path, entries)
        with lock:
            new_written += len(entries)
            remaining = _MAX_RETURN_NEW - len(new_this_run)
            if remaining > 0:
                new_this_run.extend(entries[:remaining])

    for partition_key, filters in todo:
        _walk_partition(
            partition_key=partition_key,
            filters=filters,
            state=state,
            seen_keys=seen_keys,
            ingest=ingest,
            query=query_fn,
            hits_per_page=hits_per_page,
            delay=delay,
        )

    state.flush()
    LOGGER.info(
        "saleroom algolia expand complete new_written=%s returned=%s cached_keys=%s",
        new_written,
        len(new_this_run),
        len(seen_keys),
    )
    return list(new_this_run)


__all__ = [
    "ALGOLIA_APP_ID",
    "ALGOLIA_API_KEY",
    "ALGOLIA_INDEX",
    "ALGOLIA_QUERY_URL",
    "ART_MASTER_CATEGORY_CODES",
    "ART_MASTER_CATEGORY_NAMES",
    "HITS_PER_PAGE",
    "append_lot_cache",
    "auctioneer_filter",
    "combine_filters",
    "country_filter",
    "expand_lots_from_algolia",
    "hit_to_lot_url",
    "hit_to_sitemap_entry",
    "hits_to_sitemap_entries",
    "iter_lot_cache_rows",
    "load_lot_cache_keys",
    "load_lot_cache_urls",
    "lot_cache_path",
    "master_category_filter",
    "plan_partitions",
    "query_page",
]
