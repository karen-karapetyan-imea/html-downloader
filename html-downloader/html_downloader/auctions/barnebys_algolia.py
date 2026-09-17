"""Barnebys search / pricebank discovery (Algolia-equivalent expander).

Public Algolia credentials are **not** embedded in frontend JS. Discovery instead
uses:

1. ``GET /api/search`` — live index (query required; ~80 unique pages/query)
2. SSR HTML on ``/realized-prices/{category}`` — pricebank / past lots
   (``search-pricebank``; hard cap ~312 pages per filter partition)

Art coverage: art category paths + live art query shards + country / price
partitions to push past the page caps.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from curl_cffi import requests as crequests

from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

BASE_URL = "https://www.barnebys.com"
SEARCH_API_URL = f"{BASE_URL}/api/search"

# Not available in public JS as of probe — left empty intentionally.
ALGOLIA_APP_ID = ""
ALGOLIA_API_KEY = ""
ALGOLIA_INDEX = "search"
ALGOLIA_PRICEBANK_INDEX = "search-pricebank"

EARLIEST_YEAR = 2000
DEFAULT_ALGOLIA_DELAY = 0.5
DEFAULT_ALGOLIA_WORKERS = 1
DEFAULT_SEARCH_PER_PAGE = 30
DEFAULT_LIVE_MAX_PAGES = 100
DEFAULT_REALIZED_MAX_PAGES = 312
_MAX_RETURN_NEW = 50_000

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_SLUG_TOKEN_ID_RE = re.compile(
    r"^(.+)-([A-Za-z0-9]{5,12})-(\d+)$",
)
_REALIZED_LOT_RE = re.compile(
    r"/realized-prices/lot/([A-Za-z0-9\-_/]+)",
)
_LIVE_LOT_RE = re.compile(
    r"/auctions/lot/([A-Za-z0-9\-_/]+)",
)
_TOTAL_RESULTS_RE = re.compile(r'totalResults\\?":\s*(\d+)')
_TOTAL_PAGES_RE = re.compile(r'totalPages\\?":\s*(\d+)')

# Category id (API ``category`` field) → realized-prices path slug.
ART_CATEGORY_IDS: frozenset[str] = frozenset(
    {
        "1",   # Arts & Graphics
        "8",   # Folk Art
        "15",  # Asian Art
        "17",  # Photographs
        "20",  # Sculptures
        "29",  # Ethnographic
        "32",  # Contemporary Art
    }
)

ART_CATEGORY_SLUGS: tuple[str, ...] = (
    "arts-and-graphics",
    "contemporary-art",
    "asian-art",
    "ancient-art",
    "sculptures",
    "photographs",
    "works-of-art",
    "folk-art",
    "ethnographic",
)

# Live /api/search requires a non-empty query; shard art vocabulary.
DEFAULT_ART_QUERIES: tuple[str, ...] = (
    "art",
    "painting",
    "oil",
    "watercolor",
    "drawing",
    "print",
    "lithograph",
    "etching",
    "sculpture",
    "bronze",
    "photograph",
    "portrait",
    "landscape",
    "canvas",
    "acrylic",
    "gouache",
    "pastel",
    "contemporary",
    "modern",
    "impressionist",
    "abstract",
    "figurative",
    "still life",
    "icon",
    "miniature",
)

# Country partitions for realized pricebank (URL ``c=``).
DEFAULT_COUNTRY_CODES: tuple[str, ...] = (
    "US",
    "GB",
    "SE",
    "DE",
    "FR",
    "IT",
    "NL",
    "DK",
    "NO",
    "FI",
    "AU",
    "CA",
    "CH",
    "AT",
    "BE",
    "ES",
    "JP",
    "HK",
    "CN",
    "IE",
    "PT",
    "PL",
    "CZ",
    "NZ",
)

# Price buckets (URL ``px-min`` / ``px-max``) to split capped categories.
DEFAULT_PRICE_BUCKETS: tuple[tuple[int | None, int | None], ...] = (
    (0, 100),
    (100, 500),
    (500, 1000),
    (1000, 5000),
    (5000, 20000),
    (20000, 100000),
    (100000, None),
)

FetchTextFn = Callable[[str], str]
FetchJsonFn = Callable[[str], dict[str, Any]]


def slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug or "lot"


def build_live_lot_url(*, lot_id: str, slug: str, token: str | None = None) -> str:
    """Build a live lot URL (prefer sitemap-style slug-token-id when token given)."""
    lot_id = str(lot_id).strip()
    if token:
        return f"{BASE_URL}/auctions/lot/{slugify(slug)}-{token}-{lot_id}"
    return f"{BASE_URL}/auctions/lot/{lot_id}/{slugify(slug)}"


def build_result_lot_url(*, lot_id: str, slug: str, token: str | None = None) -> str:
    """Build a realized-prices lot URL mirroring the live path shape."""
    lot_id = str(lot_id).strip()
    if token:
        return f"{BASE_URL}/realized-prices/lot/{slugify(slug)}-{token}-{lot_id}"
    return f"{BASE_URL}/realized-prices/lot/{lot_id}/{slugify(slug)}"


def parse_lot_path_suffix(suffix: str) -> tuple[str, str, str | None] | None:
    """
    Parse the path segment after /auctions/lot/ or /realized-prices/lot/.

    Returns (lot_id, slug_or_full, token_or_None).
    """
    text = (suffix or "").strip().strip("/")
    if not text:
        return None
    if "/" in text:
        left, right = text.split("/", 1)
        if left.isdigit() and right:
            return left, right, None
        return None
    match = _SLUG_TOKEN_ID_RE.match(text)
    if match:
        return match.group(3), match.group(1), match.group(2)
    return None


def hit_to_sitemap_entries(
    hit: dict[str, Any],
    *,
    include_realized: bool = True,
    include_live: bool = True,
    art_only: bool = False,
) -> list[SitemapEntry]:
    """
    Map an ``/api/search`` hit (or SSR-shaped dict) to SitemapEntry values.

    Prefers ``uid`` (``slug-token-id``) when present — matches sitemap paths.
    """
    if art_only:
        cat = str(hit.get("category") or hit.get("categoryId") or "").strip()
        if cat and cat not in ART_CATEGORY_IDS:
            name = str(hit.get("categoryName") or "").lower()
            if not any(
                token in name
                for token in (
                    "art",
                    "graphic",
                    "sculpt",
                    "photo",
                    "folk",
                    "ethnograph",
                )
            ):
                return []

    uid = str(hit.get("uid") or "").strip().strip("/")
    lot_id = ""
    slug = "lot"
    token: str | None = None

    if uid:
        parsed = parse_lot_path_suffix(uid)
        if parsed:
            lot_id, slug, token = parsed
        else:
            digits = re.search(r"(\d{6,})$", uid)
            lot_id = digits.group(1) if digits else ""
            slug = uid
    if not lot_id:
        lot_id = str(
            hit.get("lotId") or hit.get("id") or hit.get("objectID") or ""
        ).strip()
        if lot_id and not lot_id.isdigit():
            digits = re.search(r"(\d{6,})", lot_id)
            lot_id = digits.group(1) if digits else ""
    if not lot_id:
        return []

    if not uid:
        slug = str(hit.get("slug") or hit.get("name") or hit.get("title") or slug)
        raw_token = hit.get("token") or hit.get("hash")
        token = str(raw_token).strip() if raw_token else token

    use_uid = bool(uid and _SLUG_TOKEN_ID_RE.match(uid))
    out: list[SitemapEntry] = []
    if include_live:
        out.append(
            SitemapEntry(
                url=(
                    f"{BASE_URL}/auctions/lot/{uid}"
                    if use_uid
                    else build_live_lot_url(lot_id=lot_id, slug=slug, token=token)
                ),
                lastmod=None,
                entity_type="lot",
                entity_id=lot_id,
            )
        )
    if include_realized:
        out.append(
            SitemapEntry(
                url=(
                    f"{BASE_URL}/realized-prices/lot/{uid}"
                    if use_uid
                    else build_result_lot_url(lot_id=lot_id, slug=slug, token=token)
                ),
                lastmod=None,
                entity_type="result_lot",
                entity_id=lot_id,
            )
        )
    return out


def lot_urls_from_html(
    html: str,
    *,
    include_realized: bool = True,
    include_live: bool = True,
) -> list[SitemapEntry]:
    """Extract lot SitemapEntry values from realized/auctions SSR HTML."""
    best: dict[tuple[str, str], SitemapEntry] = {}

    def _add(entity_type: str, suffix: str) -> None:
        parsed = parse_lot_path_suffix(suffix)
        if parsed is None:
            return
        lot_id, _slug, token = parsed
        if entity_type == "lot":
            url = (
                f"{BASE_URL}/auctions/lot/{suffix.strip('/')}"
                if token
                else build_live_lot_url(lot_id=lot_id, slug=_slug, token=token)
            )
        else:
            url = (
                f"{BASE_URL}/realized-prices/lot/{suffix.strip('/')}"
                if token
                else build_result_lot_url(lot_id=lot_id, slug=_slug, token=token)
            )
        key = (entity_type, lot_id)
        best[key] = SitemapEntry(
            url=url,
            lastmod=None,
            entity_type=entity_type,
            entity_id=lot_id,
        )
        # Emit twin so sitemap-style coverage stays paired.
        twin_type = "result_lot" if entity_type == "lot" else "lot"
        if twin_type == "lot" and include_live:
            twin_url = (
                f"{BASE_URL}/auctions/lot/{suffix.strip('/')}"
                if token
                else build_live_lot_url(lot_id=lot_id, slug=_slug, token=token)
            )
            best[(twin_type, lot_id)] = SitemapEntry(
                url=twin_url,
                lastmod=None,
                entity_type=twin_type,
                entity_id=lot_id,
            )
        elif twin_type == "result_lot" and include_realized:
            twin_url = (
                f"{BASE_URL}/realized-prices/lot/{suffix.strip('/')}"
                if token
                else build_result_lot_url(lot_id=lot_id, slug=_slug, token=token)
            )
            best[(twin_type, lot_id)] = SitemapEntry(
                url=twin_url,
                lastmod=None,
                entity_type=twin_type,
                entity_id=lot_id,
            )

    if include_realized:
        for suffix in _REALIZED_LOT_RE.findall(html):
            _add("result_lot", suffix)
    if include_live:
        for suffix in _LIVE_LOT_RE.findall(html):
            _add("lot", suffix)
    return list(best.values())


def parse_realized_totals(html: str) -> tuple[int | None, int | None]:
    """Return (totalResults, totalPages) from SSR payload when present."""
    total_m = _TOTAL_RESULTS_RE.search(html)
    pages_m = _TOTAL_PAGES_RE.search(html)
    total = int(total_m.group(1)) if total_m else None
    pages = int(pages_m.group(1)) if pages_m else None
    return total, pages


def lot_cache_path(state_path: Path) -> Path:
    """Sidecar JSONL of discovered lots (source of truth for archive URLs)."""
    return state_path.with_name(state_path.stem + "_lots.jsonl")


def iter_lot_cache_rows(
    path: Path,
) -> Iterator[tuple[str, str, str, str | None]]:
    """Yield (entity_type, entity_id, url, lastmod) from the lot JSONL cache."""
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
                entity_type = str(row.get("entity_type") or "lot").strip()
                entity_id = str(row.get("entity_id") or "").strip()
                url = str(row.get("url") or "").strip()
                if not entity_id or not url:
                    continue
                lastmod_raw = row.get("lastmod")
                lastmod = str(lastmod_raw) if lastmod_raw else None
                yield entity_type, entity_id, url, lastmod
    except OSError as exc:
        LOGGER.warning("could not read Barnebys lot cache %s: %s", path, exc)


def load_lot_cache_keys(path: Path) -> set[tuple[str, str]]:
    """Stream (entity_type, entity_id) keys from the JSONL cache."""
    seen: set[tuple[str, str]] = set()
    if not path.exists():
        return seen
    count = 0
    for entity_type, entity_id, _url, _lastmod in iter_lot_cache_rows(path):
        seen.add((entity_type, entity_id))
        count += 1
        if count % 1_000_000 == 0:
            LOGGER.info(
                "Barnebys lot-cache key load progress lines=%s unique=%s",
                count,
                len(seen),
            )
    LOGGER.info(
        "Barnebys lot-cache key load complete lines=%s unique=%s",
        count,
        len(seen),
    )
    return seen


def append_lot_cache(path: Path, entries: Sequence[SitemapEntry]) -> None:
    if not entries:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(
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


@dataclass(frozen=True)
class SearchPartition:
    """One resumable browse unit (live API query or realized SSR filter)."""

    key: str
    kind: str  # "live" | "realized"
    query: str | None = None
    category_slug: str | None = None
    country: str | None = None
    px_min: int | None = None
    px_max: int | None = None


def build_art_partitions(
    *,
    art_only: bool = True,
    queries: Sequence[str] | None = None,
    category_slugs: Sequence[str] | None = None,
    countries: Sequence[str] | None = None,
    price_buckets: Sequence[tuple[int | None, int | None]] | None = None,
    include_live: bool = True,
    include_realized: bool = True,
    deep_realized: bool = False,
) -> list[SearchPartition]:
    """Build live + realized partitions for art (or all-category) coverage.

    When ``deep_realized`` is False, only category roots are queued for past
    lots (country/price splits are added adaptively while browsing).
    """
    _ = art_only
    out: list[SearchPartition] = []
    cats = tuple(category_slugs) if category_slugs is not None else ART_CATEGORY_SLUGS
    qlist = tuple(queries) if queries is not None else DEFAULT_ART_QUERIES
    clist = tuple(countries) if countries is not None else DEFAULT_COUNTRY_CODES
    buckets = (
        tuple(price_buckets)
        if price_buckets is not None
        else DEFAULT_PRICE_BUCKETS
    )

    if include_live:
        for query in qlist:
            q = query.strip()
            if not q:
                continue
            out.append(
                SearchPartition(
                    key=f"live:{q.lower()}",
                    kind="live",
                    query=q,
                )
            )

    if include_realized:
        for slug in cats:
            slug_s = slug.strip().strip("/")
            if not slug_s:
                continue
            out.append(
                SearchPartition(
                    key=f"realized:{slug_s}",
                    kind="realized",
                    category_slug=slug_s,
                )
            )
            if not deep_realized:
                continue
            for country in clist:
                code = country.strip().upper()
                if not code:
                    continue
                out.append(
                    SearchPartition(
                        key=f"realized:{slug_s}:c={code}",
                        kind="realized",
                        category_slug=slug_s,
                        country=code,
                    )
                )
            for lo, hi in buckets:
                lo_s = "" if lo is None else str(lo)
                hi_s = "" if hi is None else str(hi)
                out.append(
                    SearchPartition(
                        key=f"realized:{slug_s}:px={lo_s}-{hi_s}",
                        kind="realized",
                        category_slug=slug_s,
                        px_min=lo,
                        px_max=hi,
                    )
                )
    return out


def _child_partitions_for_capped(
    parent: SearchPartition,
    *,
    countries: Sequence[str] = DEFAULT_COUNTRY_CODES,
    price_buckets: Sequence[tuple[int | None, int | None]] = DEFAULT_PRICE_BUCKETS,
) -> list[SearchPartition]:
    """When a realized root hits the page cap, split by country then price."""
    if parent.kind != "realized" or not parent.category_slug:
        return []
    slug = parent.category_slug
    children: list[SearchPartition] = []
    if parent.country is None and parent.px_min is None and parent.px_max is None:
        for country in countries:
            code = country.strip().upper()
            if not code:
                continue
            children.append(
                SearchPartition(
                    key=f"realized:{slug}:c={code}",
                    kind="realized",
                    category_slug=slug,
                    country=code,
                )
            )
        return children
    if parent.country is not None and parent.px_min is None and parent.px_max is None:
        for lo, hi in price_buckets:
            lo_s = "" if lo is None else str(lo)
            hi_s = "" if hi is None else str(hi)
            children.append(
                SearchPartition(
                    key=f"realized:{slug}:c={parent.country}:px={lo_s}-{hi_s}",
                    kind="realized",
                    category_slug=slug,
                    country=parent.country,
                    px_min=lo,
                    px_max=hi,
                )
            )
    return children


def realized_list_url(partition: SearchPartition, *, page: int) -> str:
    if not partition.category_slug:
        raise ValueError("realized partition requires category_slug")
    params: dict[str, str] = {"p": str(max(1, page))}
    if partition.country:
        params["c"] = partition.country
    if partition.px_min is not None:
        params["px-min"] = str(partition.px_min)
    if partition.px_max is not None:
        params["px-max"] = str(partition.px_max)
    return (
        f"{BASE_URL}/realized-prices/{partition.category_slug}"
        f"?{urlencode(params)}"
    )


def live_search_url(
    query: str,
    *,
    page: int,
    per_page: int = DEFAULT_SEARCH_PER_PAGE,
) -> str:
    return (
        f"{SEARCH_API_URL}?"
        f"{urlencode({'query': query, 'page': str(max(1, page)), 'perPage': str(per_page)})}"
    )


def _proxy_url(proxy: dict[str, str] | None) -> str | None:
    if not proxy:
        return None
    return proxy.get("https") or proxy.get("http") or proxy.get("all")


def _fetch_text(
    url: str,
    *,
    proxy: dict[str, str] | None = None,
    timeout: float = 60.0,
    accept: str = "text/html,application/xhtml+xml,*/*;q=0.8",
    referer: str = f"{BASE_URL}/",
) -> str:
    proxy_url = _proxy_url(proxy)
    kwargs: dict[str, Any] = {
        "impersonate": "chrome131",
        "timeout": timeout,
        "headers": {
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": referer,
        },
    }
    if proxy_url:
        kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}
    response = crequests.get(url, **kwargs)
    status = int(response.status_code)
    body = bytes(response.content or b"")
    if status in {429, 503}:
        raise RuntimeError(f"search fetch status={status} url={url}")
    if status not in {200, 202}:
        raise RuntimeError(f"search fetch status={status} url={url}")
    return body.decode("utf-8", errors="replace")


def _fetch_json(
    url: str,
    *,
    proxy: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    text = _fetch_text(
        url,
        proxy=proxy,
        timeout=timeout,
        accept="application/json,text/plain,*/*;q=0.8",
        referer=f"{BASE_URL}/auctions",
    )
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid JSON from {url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"expected JSON object from {url}")
    if payload.get("error"):
        raise RuntimeError(f"search API error url={url} error={payload.get('error')}")
    return payload


def _make_rotating_text_fetcher(
    proxy_list: Sequence[dict[str, str]],
) -> FetchTextFn:
    state = {"i": 0}

    def _once(url: str, proxy: dict[str, str] | None) -> str:
        if "/api/search" in url:
            return _fetch_text(
                url,
                proxy=proxy,
                accept="application/json,text/plain,*/*;q=0.8",
                referer=f"{BASE_URL}/auctions",
            )
        return _fetch_text(
            url,
            proxy=proxy,
            accept="text/html,application/xhtml+xml,*/*;q=0.8",
            referer=f"{BASE_URL}/realized-prices",
        )

    def fetch_text(url: str) -> str:
        errors: list[str] = []
        try:
            return _once(url, None)
        except Exception as exc:
            errors.append(f"direct:{exc}")

        if not proxy_list:
            raise RuntimeError("; ".join(errors))

        n = min(5, len(proxy_list))
        for _ in range(n):
            proxy = proxy_list[state["i"] % len(proxy_list)]
            state["i"] += 1
            try:
                return _once(url, proxy)
            except Exception as exc:
                errors.append(f"proxy:{exc}")
        raise RuntimeError("; ".join(errors[-3:]))

    return fetch_text


class SearchBrowseState:
    """Resume state for live/realized search partitions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._partitions: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        parts = payload.get("partitions")
        if isinstance(parts, dict):
            self._partitions = {
                str(k): dict(v) for k, v in parts.items() if isinstance(v, dict)
            }

    def status(self, key: str) -> str | None:
        entry = self._partitions.get(key)
        if not entry:
            return None
        status = entry.get("status")
        return str(status) if status else None

    def mark(
        self,
        key: str,
        *,
        status: str,
        lots_found: int = 0,
        pages_fetched: int = 0,
        error: str | None = None,
    ) -> None:
        row: dict[str, Any] = {
            "status": status,
            "lots_found": lots_found,
            "pages_fetched": pages_fetched,
            "updated_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        if error:
            row["error"] = error[:300]
        self._partitions[key] = row

    def reset_all(self) -> None:
        self._partitions.clear()

    def flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "partitions": dict(sorted(self._partitions.items())),
            "last_fetch_at": datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),
        }
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self.path)


def _browse_live_partition(
    partition: SearchPartition,
    *,
    fetch_text: FetchTextFn,
    delay: float,
    max_pages: int,
    art_only: bool,
    on_entries: Callable[[list[SitemapEntry]], None],
) -> tuple[int, int]:
    assert partition.query
    lots = 0
    pages_fetched = 0
    empty_streak = 0
    for page in range(1, max_pages + 1):
        if page > 1 and delay > 0:
            time.sleep(delay)
        url = live_search_url(partition.query, page=page)
        text = fetch_text(url)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid search JSON page={page}: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError(f"expected object from search page={page}")
        hits = payload.get("hits") or []
        if not isinstance(hits, list) or not hits:
            empty_streak += 1
            if empty_streak >= 2:
                break
            continue
        empty_streak = 0
        pages_fetched = page
        batch: list[SitemapEntry] = []
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            batch.extend(
                hit_to_sitemap_entries(
                    hit,
                    include_realized=True,
                    include_live=True,
                    art_only=art_only,
                )
            )
        if batch:
            on_entries(batch)
            lots += len(batch)
    return lots, pages_fetched


def _browse_realized_partition(
    partition: SearchPartition,
    *,
    fetch_text: FetchTextFn,
    delay: float,
    max_pages: int,
    on_entries: Callable[[list[SitemapEntry]], None],
) -> tuple[int, int, bool]:
    """Returns (lots_found, pages_fetched, hit_page_cap)."""
    lots = 0
    pages_fetched = 0
    reported_pages: int | None = None
    for page in range(1, max_pages + 1):
        if reported_pages is not None and page > reported_pages:
            break
        if page > 1 and delay > 0:
            time.sleep(delay)
        url = realized_list_url(partition, page=page)
        html = fetch_text(url)
        total, pages = parse_realized_totals(html)
        if pages is not None:
            reported_pages = pages
        if pages == 0 or total == 0:
            break
        entries = lot_urls_from_html(html)
        pages_fetched = page
        if not entries:
            break
        on_entries(entries)
        lots += len(entries)
        if reported_pages is not None and page >= reported_pages:
            break
    hit_cap = reported_pages is not None and reported_pages >= max_pages
    return lots, pages_fetched, hit_cap


def expand_lots_from_algolia(
    *,
    state_path: Path,
    from_year: int = EARLIEST_YEAR,
    to_year: int | None = None,
    workers: int = DEFAULT_ALGOLIA_WORKERS,
    delay: float = DEFAULT_ALGOLIA_DELAY,
    force: bool = False,
    supercategories: Sequence[str] | None = None,
    proxy: dict[str, str] | None = None,
    proxies: Sequence[dict[str, str]] | None = None,
    fetch_text: FetchTextFn | None = None,
    art_only: bool = True,
    include_live: bool = True,
    include_realized: bool = True,
    live_max_pages: int = DEFAULT_LIVE_MAX_PAGES,
    realized_max_pages: int = DEFAULT_REALIZED_MAX_PAGES,
    queries: Sequence[str] | None = None,
    category_slugs: Sequence[str] | None = None,
) -> list[SitemapEntry]:
    """
    Expand Barnebys lots via public search API + realized pricebank HTML.

    ``supercategories`` (CLI artworks mode) maps onto art category slugs when
    provided; otherwise default art categories / queries are used.
    Year args are accepted for Invaluable-compatible call sites but unused
    (pricebank has no public year browse).
    """
    _ = (from_year, to_year, workers)

    proxy_list: list[dict[str, str]] = list(proxies or [])
    if not proxy_list and proxy is not None:
        proxy_list = [proxy]

    slugs: Sequence[str] | None = category_slugs
    if supercategories:
        # Map Invaluable-style names / raw slugs onto Barnebys path slugs.
        mapped: list[str] = []
        for name in supercategories:
            raw = name.strip()
            if not raw:
                continue
            lowered = raw.lower().replace(" ", "-").replace("&", "and")
            if lowered in ART_CATEGORY_SLUGS:
                mapped.append(lowered)
                continue
            # Fuzzy: "Fine Art" / "Paintings" → arts-and-graphics
            if "sculpt" in lowered:
                mapped.append("sculptures")
            elif "photo" in lowered:
                mapped.append("photographs")
            elif "asian" in lowered:
                mapped.append("asian-art")
            elif "contemporary" in lowered:
                mapped.append("contemporary-art")
            elif "ancient" in lowered:
                mapped.append("ancient-art")
            elif "folk" in lowered:
                mapped.append("folk-art")
            elif "ethnograph" in lowered:
                mapped.append("ethnographic")
            else:
                mapped.append("arts-and-graphics")
        # de-dupe preserve order
        seen_s: set[str] = set()
        slugs = []
        for s in mapped:
            if s not in seen_s:
                seen_s.add(s)
                slugs.append(s)
        art_only = True

    partitions = build_art_partitions(
        art_only=art_only,
        queries=queries,
        category_slugs=slugs,
        include_live=include_live,
        include_realized=include_realized,
        deep_realized=False,
    )

    cache_path = lot_cache_path(state_path)
    state = SearchBrowseState(state_path)
    if force:
        state.reset_all()
        if cache_path.exists():
            cache_path.unlink()
        LOGGER.info("Barnebys search force: browse state and lot cache cleared")

    # Queue as list so adaptive country/price children can append.
    queue: list[SearchPartition] = [
        p for p in partitions if state.status(p.key) != "done"
    ]
    queued_keys = {p.key for p in queue}
    seen_keys = load_lot_cache_keys(cache_path) if cache_path.exists() else set()
    LOGGER.info(
        "Barnebys search expand seed_partitions=%s todo=%s cached_keys=%s "
        "art_only=%s live=%s realized=%s delay=%s",
        len(partitions),
        len(queue),
        len(seen_keys),
        art_only,
        include_live,
        include_realized,
        delay,
    )
    if not queue:
        state.flush()
        return []

    text_fetcher = fetch_text or _make_rotating_text_fetcher(proxy_list)
    new_this_run: list[SitemapEntry] = []
    new_written = 0

    def _ingest(entries: list[SitemapEntry]) -> None:
        nonlocal new_written
        fresh: list[SitemapEntry] = []
        for entry in entries:
            if not entry.entity_id:
                continue
            key = (entry.entity_type, entry.entity_id)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            fresh.append(entry)
            new_written += 1
            if len(new_this_run) < _MAX_RETURN_NEW:
                new_this_run.append(entry)
        append_lot_cache(cache_path, fresh)

    i = 0
    while i < len(queue):
        partition = queue[i]
        i += 1
        if state.status(partition.key) == "done":
            continue
        state.mark(partition.key, status="in_progress")
        try:
            if partition.kind == "live":
                lots_found, pages_fetched = _browse_live_partition(
                    partition,
                    fetch_text=text_fetcher,
                    delay=delay,
                    max_pages=live_max_pages,
                    art_only=art_only,
                    on_entries=_ingest,
                )
                hit_cap = False
            else:
                lots_found, pages_fetched, hit_cap = _browse_realized_partition(
                    partition,
                    fetch_text=text_fetcher,
                    delay=delay,
                    max_pages=realized_max_pages,
                    on_entries=_ingest,
                )
            state.mark(
                partition.key,
                status="done",
                lots_found=lots_found,
                pages_fetched=pages_fetched,
            )
            if hit_cap:
                for child in _child_partitions_for_capped(partition):
                    if child.key in queued_keys:
                        continue
                    if state.status(child.key) == "done":
                        continue
                    queued_keys.add(child.key)
                    queue.append(child)
                LOGGER.info(
                    "Barnebys search partition capped key=%s — queued children "
                    "(queue_now=%s)",
                    partition.key,
                    len(queue),
                )
            LOGGER.info(
                "Barnebys search partition done key=%s lots=%s pages=%s "
                "cached_keys=%s (%s/%s)",
                partition.key,
                lots_found,
                pages_fetched,
                len(seen_keys),
                i,
                len(queue),
            )
        except Exception as exc:
            LOGGER.warning(
                "Barnebys search partition failed key=%s error=%s",
                partition.key,
                exc,
            )
            state.mark(
                partition.key,
                status="failed",
                error=str(exc),
            )
        if i % 5 == 0:
            state.flush()

    state.flush()
    LOGGER.info(
        "Barnebys search expand complete cached_keys=%s new_written=%s "
        "new_returned=%s",
        len(seen_keys),
        new_written,
        len(new_this_run),
    )
    return list(new_this_run)


__all__ = [
    "ALGOLIA_API_KEY",
    "ALGOLIA_APP_ID",
    "ALGOLIA_INDEX",
    "ALGOLIA_PRICEBANK_INDEX",
    "ART_CATEGORY_IDS",
    "ART_CATEGORY_SLUGS",
    "BASE_URL",
    "DEFAULT_ALGOLIA_DELAY",
    "DEFAULT_ALGOLIA_WORKERS",
    "DEFAULT_ART_QUERIES",
    "EARLIEST_YEAR",
    "SEARCH_API_URL",
    "SearchBrowseState",
    "SearchPartition",
    "append_lot_cache",
    "build_art_partitions",
    "build_live_lot_url",
    "build_result_lot_url",
    "expand_lots_from_algolia",
    "hit_to_sitemap_entries",
    "iter_lot_cache_rows",
    "live_search_url",
    "load_lot_cache_keys",
    "lot_cache_path",
    "lot_urls_from_html",
    "parse_lot_path_suffix",
    "parse_realized_totals",
    "realized_list_url",
    "slugify",
]
