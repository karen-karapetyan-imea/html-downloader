"""Drouot discovery: EN lot/sale XML sitemaps + optional search backfill.

PRIMARY: https://drouot.com/sitemap-en-lot.xml + sitemap-en-sale.xml
Children: sitemap-en-lot{1..N}.xml (~50k URLs each), sitemap-en-sale1.xml

FALLBACK: when unique lot cache count < search ``totalItems`` and
``expand_algolia`` is on, paginate ``/en/s/__data.json?query=*`` (see
``drouot_search``). Search is robots-Disallow — fallback only.

No lot parsing here — download saves HTML only.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from curl_cffi import requests as crequests

from html_downloader.auctions.urls import (
    drouot_entity_from_url,
    normalize_auction_url,
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

DEFAULT_DROUOT_LOT_INDEX = "https://drouot.com/sitemap-en-lot.xml"
DEFAULT_DROUOT_SALE_INDEX = "https://drouot.com/sitemap-en-sale.xml"
DEFAULT_MAX_SITEMAPS = 50
DEFAULT_MIN_URLS = 0
DEFAULT_DROUOT_MAX_RETRIES = 6
DEFAULT_DROUOT_FETCH_TIMEOUT = 120.0
DEFAULT_DROUOT_INTER_FILE_SLEEP = 0.75

CLOUDFLARE_KEYWORDS: tuple[str, ...] = (
    "just a moment",
    "cf-browser-verification",
    "challenge-platform",
    "cf-challenge",
    "cdn-cgi/challenge-platform",
)

FetchBytesFn = Callable[[str], bytes]


def is_drouot_lot_sitemap(url: str) -> bool:
    """True for EN lot child sitemaps (sitemap-en-lotN.xml), not the index."""
    name = Path(url.lower()).name
    if name == "sitemap-en-lot.xml":
        return False
    return name.startswith("sitemap-en-lot") and name.endswith(".xml")


def is_drouot_sale_sitemap(url: str) -> bool:
    """True for EN sale child sitemaps (sitemap-en-saleN.xml), not the index."""
    name = Path(url.lower()).name
    if name == "sitemap-en-sale.xml":
        return False
    return name.startswith("sitemap-en-sale") and name.endswith(".xml")


def is_drouot_child_sitemap(url: str) -> bool:
    return is_drouot_lot_sitemap(url) or is_drouot_sale_sitemap(url)


def _looks_like_cloudflare(body: bytes) -> bool:
    if _looks_like_html_challenge(body):
        return True
    sample = body[:4096].lower()
    return any(kw.encode("ascii") in sample for kw in CLOUDFLARE_KEYWORDS)


def parse_drouot_entries(
    xml_bytes: bytes,
    *,
    base_url: str = "https://drouot.com/",
) -> list[SitemapEntry]:
    """Parse a urlset into unique lot/sale SitemapEntry values."""
    if _looks_like_cloudflare(xml_bytes):
        raise RuntimeError("Drouot sitemap returned a Cloudflare challenge page")

    try:
        entries_raw = parse_url_entries(xml_bytes)
    except ET.ParseError as exc:
        raise RuntimeError(f"invalid Drouot sitemap XML: {exc}") from exc

    best: dict[tuple[str, str], SitemapEntry] = {}
    for loc, lastmod in entries_raw:
        lower = loc.lower()
        if "/l/" not in lower and "/v/" not in lower:
            continue
        normalized = normalize_auction_url(loc, base_url=base_url)
        if not normalized:
            continue
        key = drouot_entity_from_url(normalized)
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
        if existing is None or (
            lastmod and (existing.lastmod is None or lastmod > existing.lastmod)
        ):
            best[key] = entry
    return list(best.values())


def lot_cache_path(progress_path: Path) -> Path:
    """Sidecar JSONL of discovered lot/sale URLs."""
    return progress_path.with_name(progress_path.stem + "_lots.jsonl")


def iter_lot_cache_rows(
    path: Path,
) -> Iterator[tuple[str, str, str, str | None]]:
    """Yield (entity_type, entity_id, url, lastmod) from the JSONL cache."""
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
                entity_type = str(row.get("entity_type") or "lot").strip()
                entity_id = str(row.get("entity_id") or "").strip()
                url = str(row.get("url") or "").strip()
                if not entity_id or not url:
                    continue
                lastmod_raw = row.get("lastmod")
                lastmod = str(lastmod_raw) if lastmod_raw else None
                yield entity_type, entity_id, url, lastmod
    except OSError as exc:
        LOGGER.warning("could not read Drouot lot cache %s: %s", path, exc)


def load_lot_cache_keys(path: Path) -> set[tuple[str, str]]:
    """Stream (entity_type, entity_id) keys from the JSONL cache."""
    seen: set[tuple[str, str]] = set()
    if not path.exists():
        return seen
    count = 0
    for entity_type, entity_id, _url, _lastmod in iter_lot_cache_rows(path):
        seen.add((entity_type, entity_id))
        count += 1
        if count % 500_000 == 0:
            LOGGER.info(
                "drouot lot-cache key load progress lines=%s unique=%s",
                count,
                len(seen),
            )
    LOGGER.info(
        "drouot lot-cache key load complete lines=%s unique=%s",
        count,
        len(seen),
    )
    return seen


def count_lot_cache_lots(path: Path) -> int:
    """Count unique lot entity ids in the JSONL cache."""
    return sum(1 for entity_type, _id, _url, _lm in iter_lot_cache_rows(path) if entity_type == "lot")


def append_lot_cache(path: Path, entries: Sequence[SitemapEntry]) -> int:
    if not entries:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(path, "a", encoding="utf-8") as handle:
        for entry in entries:
            if not entry.entity_id or entry.entity_type not in {"lot", "sale"}:
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


def load_sitemap_progress(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    sitemaps = payload.get("sitemaps")
    if isinstance(sitemaps, dict):
        return {str(k): dict(v) for k, v in sitemaps.items() if isinstance(v, dict)}
    return {}


def save_sitemap_progress(path: Path, sitemaps: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sitemaps": dict(sorted(sitemaps.items())),
        "last_fetch_at": datetime.now().astimezone().isoformat(),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def clear_sitemap_cache(*, progress_path: Path) -> None:
    cache_path = lot_cache_path(progress_path)
    for path in (progress_path, cache_path):
        if path.exists():
            path.unlink()
            LOGGER.info("drouot sitemap force: cleared %s", path)


def select_child_sitemaps(
    children: Sequence[str],
    progress: dict[str, dict[str, Any]],
    *,
    max_sitemaps: int,
) -> list[str]:
    """Prefer unknown/pending/failed children; skip done."""
    pending: list[str] = []
    for url in children:
        if not is_drouot_child_sitemap(url):
            continue
        entry = progress.get(url) or {}
        if entry.get("status") == "done":
            continue
        pending.append(url)
    pending.sort(key=lambda u: Path(u).name.lower())
    if max_sitemaps <= 0:
        return []
    return pending[:max_sitemaps]


def reclaim_done_children_for_cache_backfill(
    progress: dict[str, dict[str, Any]],
    *,
    cached_keys: int,
) -> int:
    done_lots = sum(
        int(entry.get("lots_found") or 0)
        for entry in progress.values()
        if entry.get("status") == "done"
    )
    if done_lots <= 0:
        return 0
    if cached_keys >= max(1, done_lots // 2):
        return 0
    reclaimed = 0
    for entry in progress.values():
        if entry.get("status") != "done":
            continue
        entry["status"] = "pending"
        entry["backfill"] = True
        reclaimed += 1
    if reclaimed:
        LOGGER.warning(
            "drouot cache backfill: re-queued done children=%s "
            "cached_keys=%s done_lots_sum=%s",
            reclaimed,
            cached_keys,
            done_lots,
        )
    return reclaimed


def _proxy_url(proxy: dict[str, str] | None) -> str | None:
    if not proxy:
        return None
    return proxy.get("https") or proxy.get("http") or proxy.get("all")


def _fetch_drouot_bytes(
    url: str,
    *,
    proxy: dict[str, str] | None = None,
    timeout: float = DEFAULT_DROUOT_FETCH_TIMEOUT,
) -> bytes:
    proxy_url = _proxy_url(proxy)
    kwargs: dict[str, Any] = {
        "impersonate": "chrome131",
        "timeout": timeout,
        "headers": {
            "Accept": "application/xml,text/xml,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://drouot.com/",
        },
    }
    if proxy_url:
        kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}

    response = crequests.get(url, **kwargs)
    status = int(response.status_code)
    body = bytes(response.content or b"")
    if not body:
        raise RuntimeError(f"sitemap fetch status={status} empty body url={url}")
    if status == 404:
        raise RuntimeError(f"sitemap fetch status=404 url={url}")
    if status in {429, 503}:
        raise RuntimeError(f"sitemap fetch status={status} url={url}")
    if status not in {200, 202}:
        raise RuntimeError(f"sitemap fetch status={status} url={url}")
    head = body[:4096].lower()
    looks_xml = b"<urlset" in head or b"<sitemapindex" in head or b"<?xml" in head
    if _looks_like_cloudflare(body) and not looks_xml:
        raise RuntimeError(f"Cloudflare challenge for url={url}")
    return body


def _is_permanent_miss(exc: BaseException) -> bool:
    return "status=404" in str(exc)


def _fetch_with_retries(
    url: str,
    *,
    fetch_bytes: FetchBytesFn,
    max_retries: int = DEFAULT_DROUOT_MAX_RETRIES,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        if attempt > 0:
            backoff = min(90.0, 3.0 * (2 ** (attempt - 1)))
            LOGGER.info(
                "drouot sitemap retry %s/%s url=%s backoff=%.0fs",
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
            LOGGER.warning("drouot sitemap fetch failed url=%s error=%s", url, exc)
            if _is_permanent_miss(exc):
                break
    raise RuntimeError(
        f"failed to fetch Drouot sitemap url={url} after {max_retries} attempts"
    ) from last_error


def _make_rotating_fetcher(
    proxy_list: Sequence[dict[str, str]],
) -> FetchBytesFn:
    state = {"i": 0}

    def fetch_bytes(url: str) -> bytes:
        errors: list[str] = []
        try:
            return _fetch_drouot_bytes(url, proxy=None)
        except Exception as exc:
            errors.append(f"direct:{exc}")
            if _is_permanent_miss(exc):
                raise

        if not proxy_list:
            raise RuntimeError("; ".join(errors))

        n = min(5, len(proxy_list))
        for _ in range(n):
            proxy = proxy_list[state["i"] % len(proxy_list)]
            state["i"] += 1
            try:
                return _fetch_drouot_bytes(url, proxy=proxy)
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
    if entry.lastmod and (existing.lastmod is None or entry.lastmod > existing.lastmod):
        best[key] = entry


def _collect_children(
    index_url: str,
    *,
    fetch_bytes: FetchBytesFn,
    max_retries: int,
) -> list[str]:
    body = _fetch_with_retries(
        index_url, fetch_bytes=fetch_bytes, max_retries=max_retries
    )
    if not is_sitemap_index(body):
        # Leaf index — treat as a single child of itself.
        return [index_url]
    return [
        loc
        for loc in parse_child_sitemap_locs(body)
        if is_drouot_child_sitemap(loc)
    ]


def fetch_drouot_sitemap_entries(
    sitemap_url: str = DEFAULT_DROUOT_LOT_INDEX,
    *,
    sale_index_url: str = DEFAULT_DROUOT_SALE_INDEX,
    concurrency: int = 1,
    proxy: dict[str, str] | None = None,
    proxies: list[dict[str, str]] | None = None,
    fetch_bytes: FetchBytesFn | None = None,
    max_retries: int = DEFAULT_DROUOT_MAX_RETRIES,
    max_sitemaps: int = DEFAULT_MAX_SITEMAPS,
    min_urls: int = DEFAULT_MIN_URLS,
    sitemap_progress_path: Path | None = None,
    sitemap_force: bool = False,
    expand_algolia: bool = True,
    algolia_state_path: Path | None = None,
    algolia_delay: float = 0.35,
    algolia_force: bool = False,
) -> list[SitemapEntry]:
    """
    Discover Drouot lot/sale URLs from EN XML sitemaps.

    When ``expand_algolia`` is True and the lot cache count is below search
    ``totalItems``, backfill via ``drouot_search.expand_lots_from_search``.
    Returns only this-run new entries — callers stream the JSONL for the full set.
    """
    _ = concurrency
    proxy_list: list[dict[str, str]] = list(proxies or [])
    if not proxy_list and proxy is not None:
        proxy_list = [proxy]

    if fetch_bytes is None:
        fetch_bytes = _make_rotating_fetcher(proxy_list)

    cache_path: Path | None = None
    if sitemap_progress_path is not None:
        if sitemap_force:
            clear_sitemap_cache(progress_path=sitemap_progress_path)
        cache_path = lot_cache_path(sitemap_progress_path)

    LOGGER.info(
        "drouot discover lot_index=%s sale_index=%s proxies=%s max_sitemaps=%s "
        "min_urls=%s force=%s expand_search=%s cache=%s",
        sitemap_url,
        sale_index_url,
        len(proxy_list),
        max_sitemaps,
        min_urls,
        sitemap_force,
        expand_algolia,
        cache_path,
    )

    children: list[str] = []
    for index_url in (sitemap_url, sale_index_url):
        if not index_url:
            continue
        try:
            found = _collect_children(
                index_url, fetch_bytes=fetch_bytes, max_retries=max_retries
            )
            children.extend(found)
            LOGGER.info("drouot index=%s children=%s", index_url, len(found))
        except Exception as exc:
            LOGGER.warning("drouot index failed url=%s error=%s", index_url, exc)

    # Deduplicate while preserving order
    seen_children: set[str] = set()
    unique_children: list[str] = []
    for url in children:
        if url in seen_children:
            continue
        seen_children.add(url)
        unique_children.append(url)

    progress = load_sitemap_progress(sitemap_progress_path)
    seen_keys = load_lot_cache_keys(cache_path) if cache_path is not None else set()
    reclaim_done_children_for_cache_backfill(progress, cached_keys=len(seen_keys))
    selected = select_child_sitemaps(
        unique_children, progress, max_sitemaps=max_sitemaps
    )
    LOGGER.info(
        "drouot index children=%s known=%s selected=%s cached_keys=%s",
        len(unique_children),
        len(progress),
        len(selected),
        len(seen_keys),
    )

    best: dict[tuple[str, str], SitemapEntry] = {}
    failed: list[str] = []
    attempted = 0

    for i, child_url in enumerate(selected):
        if min_urls > 0 and len(best) >= min_urls:
            LOGGER.info(
                "drouot min_urls reached total=%s target=%s attempted=%s",
                len(best),
                min_urls,
                attempted,
            )
            break
        if i > 0 and DEFAULT_DROUOT_INTER_FILE_SLEEP > 0:
            time.sleep(DEFAULT_DROUOT_INTER_FILE_SLEEP)
        attempted += 1
        progress[child_url] = {
            "type": "sitemap",
            "status": "in_progress",
            "lots_found": progress.get(child_url, {}).get("lots_found", 0),
        }
        try:
            body = _fetch_with_retries(
                child_url, fetch_bytes=fetch_bytes, max_retries=max_retries
            )
            if is_sitemap_index(body):
                raise RuntimeError(f"expected urlset, got sitemapindex url={child_url}")
            entries = parse_drouot_entries(body)
            fresh: list[SitemapEntry] = []
            for entry in entries:
                if not entry.entity_id:
                    continue
                key = (entry.entity_type, entry.entity_id)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                _merge_entry(best, entry)
                fresh.append(entry)
            if cache_path is not None:
                append_lot_cache(cache_path, fresh)
            progress[child_url] = {
                "type": "sitemap",
                "kind": "lot" if is_drouot_lot_sitemap(child_url) else "sale",
                "status": "done",
                "lots_found": len(entries),
            }
            LOGGER.info(
                "drouot child parsed url=%s entities=%s new=%s "
                "running_new=%s cached_keys=%s",
                child_url,
                len(entries),
                len(fresh),
                len(best),
                len(seen_keys),
            )
        except Exception as exc:
            LOGGER.warning("drouot child failed url=%s error=%s", child_url, exc)
            progress[child_url] = {
                "type": "sitemap",
                "status": "failed",
                "lots_found": 0,
                "error": str(exc)[:200],
            }
            failed.append(child_url)

    if sitemap_progress_path is not None:
        save_sitemap_progress(sitemap_progress_path, progress)

    if failed and not best and not seen_keys:
        raise RuntimeError(
            f"all {len(failed)} Drouot child sitemap(s) failed; no entries parsed"
        )
    if failed and not best:
        LOGGER.warning(
            "drouot no new entries this run failed=%s cached_keys=%s",
            len(failed),
            len(seen_keys),
        )
    elif failed:
        LOGGER.warning(
            "drouot partial success failed=%s new=%s",
            len(failed),
            len(best),
        )

    # Search backfill when sitemap lot count under-reports live totalItems.
    if expand_algolia and cache_path is not None:
        if algolia_state_path is None:
            raise ValueError("drouot search backfill requires algolia_state_path")
        try:
            from html_downloader.auctions.drouot_search import (
                DEFAULT_SEARCH_DELAY,
                expand_lots_from_search,
                probe_search_total,
            )

            lot_count = sum(1 for t, _i in seen_keys if t == "lot")
            # Prefer unique count from cache stream if keys may include sales.
            if cache_path.exists():
                lot_count = len(
                    {
                        eid
                        for et, eid, _u, _lm in iter_lot_cache_rows(cache_path)
                        if et == "lot"
                    }
                )
            total_items = probe_search_total()
            LOGGER.info(
                "drouot count compare sitemap_lots=%s search_totalItems=%s",
                lot_count,
                total_items,
            )
            if lot_count < total_items:
                LOGGER.info(
                    "drouot search backfill starting gap=%s",
                    total_items - lot_count,
                )
                search_new = expand_lots_from_search(
                    state_path=algolia_state_path,
                    cache_path=cache_path,
                    known_keys=seen_keys,
                    delay=(
                        algolia_delay if algolia_delay > 0 else DEFAULT_SEARCH_DELAY
                    ),
                    force=algolia_force or sitemap_force,
                )
                for entry in search_new:
                    if entry.entity_id and entry.entity_key not in best:
                        best[entry.entity_key] = entry
                        seen_keys.add(entry.entity_key)
            else:
                LOGGER.info("drouot search backfill skipped (sitemap count sufficient)")
        except Exception as exc:
            LOGGER.warning("drouot search expansion skipped error=%s", exc)

    LOGGER.info(
        "drouot discovery complete new=%s cached_keys=%s",
        len(best),
        len(seen_keys),
    )
    return list(best.values())


def known_drouot_keys_from_paths(paths: Iterable[Path]) -> set[tuple[str, str]]:
    """Load known Drouot entity keys from URL list / JSONL files."""
    keys: set[tuple[str, str]] = set()
    for url in load_urls(paths):
        key = drouot_entity_from_url(url)
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


__all__ = [
    "CLOUDFLARE_KEYWORDS",
    "DEFAULT_DROUOT_LOT_INDEX",
    "DEFAULT_DROUOT_SALE_INDEX",
    "DEFAULT_MAX_SITEMAPS",
    "DEFAULT_MIN_URLS",
    "append_lot_cache",
    "clear_sitemap_cache",
    "count_lot_cache_lots",
    "fetch_drouot_sitemap_entries",
    "is_drouot_child_sitemap",
    "is_drouot_lot_sitemap",
    "is_drouot_sale_sitemap",
    "iter_lot_cache_rows",
    "known_drouot_keys_from_paths",
    "load_lot_cache_keys",
    "load_sitemap_progress",
    "lot_cache_path",
    "parse_drouot_entries",
    "save_auction_lastmod_state",
    "save_sitemap_progress",
    "select_child_sitemaps",
]
