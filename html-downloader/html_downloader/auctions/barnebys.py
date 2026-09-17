"""Barnebys discovery: lot sitemap + search/pricebank expansion.

PRIMARY: https://www.barnebys.com/sitemap.xml
Children: /sitemap/lots-{0..N}.xml.gz (~50k URLs each; ~386k as of 2026-09 probe)

SECONDARY: ``barnebys_algolia.expand_lots_from_algolia`` — live ``/api/search``
plus realized pricebank HTML (no public Algolia credentials).

Azure WAF may challenge raw HTTP; discovery uses curl_cffi + optional stealth proxies.

No lot parsing here — download saves HTML only.
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

from curl_cffi import requests as crequests

from html_downloader.auctions.barnebys_algolia import (
    append_lot_cache,
    iter_lot_cache_rows,
    load_lot_cache_keys,
    lot_cache_path,
)
from html_downloader.auctions.urls import (
    barnebys_entity_from_url,
    barnebys_realized_twin_url,
    normalize_auction_url,
)
from html_downloader.discover.registry import load_urls
from html_downloader.discover.sitemap import (
    SitemapEntry,
    _looks_like_html_challenge,
    _maybe_decompress,
    is_sitemap_index,
    parse_child_sitemap_locs,
    parse_url_entries,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_BARNEBYS_INDEX = "https://www.barnebys.com/sitemap.xml"
# Index has 8 lot shards (~50k lots each, ~386k total as of 2026-09 probe).
# Full archive with realized twins ≈ 770k+ URLs — use min_urls=0 to process every
# pending shard in one run (no early stop). LiveAuctioneers-scale min_urls also works.
DEFAULT_MAX_SITEMAPS = 100
DEFAULT_MIN_URLS = 0
DEFAULT_BARNEBYS_MAX_RETRIES = 6
DEFAULT_BARNEBYS_FETCH_TIMEOUT = 120.0
DEFAULT_BARNEBYS_INTER_FILE_SLEEP = 1.5

AZURE_WAF_KEYWORDS: tuple[str, ...] = (
    "azwaf",
    "azure waf",
    "afd_azwaf",
    "azure-ref",
)

FetchBytesFn = Callable[[str], bytes]


def is_barnebys_lot_sitemap(url: str) -> bool:
    """True for child sitemaps that list auction lot URLs."""
    lower = url.lower()
    return "/sitemap/lots-" in lower or "lots-" in Path(lower).name


def _looks_like_azure_waf(body: bytes) -> bool:
    if _looks_like_html_challenge(body):
        return True
    sample = body[:4096].lower()
    return any(kw.encode("ascii") in sample for kw in AZURE_WAF_KEYWORDS)


def parse_barnebys_entries(
    xml_bytes: bytes,
    *,
    base_url: str = "https://www.barnebys.com/",
    include_realized: bool = True,
) -> list[SitemapEntry]:
    """Parse a urlset into unique live (+ optional realized twin) SitemapEntry values."""
    if _looks_like_azure_waf(xml_bytes):
        raise RuntimeError("Barnebys sitemap returned an Azure WAF challenge page")

    try:
        entries_raw = parse_url_entries(xml_bytes)
    except ET.ParseError as exc:
        raise RuntimeError(f"invalid Barnebys sitemap XML: {exc}") from exc

    best: dict[tuple[str, str], SitemapEntry] = {}
    for loc, lastmod in entries_raw:
        if "/auctions/lot/" not in loc and "/realized-prices/lot/" not in loc:
            continue
        normalized = normalize_auction_url(loc, base_url=base_url)
        if not normalized:
            continue
        key = barnebys_entity_from_url(normalized)
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

        if include_realized and entity_type == "lot":
            twin = barnebys_realized_twin_url(normalized)
            if twin:
                twin_key = barnebys_entity_from_url(twin)
                if twin_key is not None:
                    twin_entry = SitemapEntry(
                        url=twin,
                        lastmod=lastmod,
                        entity_type=twin_key[0],
                        entity_id=twin_key[1],
                    )
                    existing_twin = best.get(twin_key)
                    if existing_twin is None or (
                        lastmod
                        and (
                            existing_twin.lastmod is None
                            or lastmod > existing_twin.lastmod
                        )
                    ):
                        best[twin_key] = twin_entry

    return list(best.values())


def _proxy_url(proxy: dict[str, str] | None) -> str | None:
    if not proxy:
        return None
    return proxy.get("https") or proxy.get("http") or proxy.get("all")


def _fetch_barnebys_bytes(
    url: str,
    *,
    proxy: dict[str, str] | None = None,
    timeout: float = DEFAULT_BARNEBYS_FETCH_TIMEOUT,
) -> bytes:
    """Fetch sitemap bytes with Chrome impersonation; decompress .gz."""
    proxy_url = _proxy_url(proxy)
    kwargs: dict[str, Any] = {
        "impersonate": "chrome131",
        "timeout": timeout,
        "headers": {
            "Accept": "application/xml,text/xml,application/gzip,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.barnebys.com/",
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
    try:
        body = _maybe_decompress(url, body)
    except OSError as exc:
        # curl_cffi may already decompress Content-Encoding: gzip, leaving
        # plain XML for a URL that still ends in .xml.gz.
        head = body[:200].lstrip()
        if head.startswith(b"<?xml") or head.startswith(b"<urlset") or head.startswith(
            b"<sitemapindex"
        ):
            LOGGER.debug(
                "barnebys sitemap already decompressed url=%s (%s)",
                url,
                exc,
            )
        else:
            raise RuntimeError(f"gzip decompress failed url={url}: {exc}") from exc
    head = body[:4096].lower()
    looks_xml = b"<urlset" in head or b"<sitemapindex" in head
    if _looks_like_azure_waf(body) and not looks_xml:
        raise RuntimeError(f"Azure WAF challenge for url={url}")
    return body


def _is_permanent_miss(exc: BaseException) -> bool:
    return "status=404" in str(exc)


def _fetch_with_retries(
    url: str,
    *,
    fetch_bytes: FetchBytesFn,
    max_retries: int = DEFAULT_BARNEBYS_MAX_RETRIES,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        if attempt > 0:
            backoff = min(90.0, 3.0 * (2 ** (attempt - 1)))
            LOGGER.info(
                "barnebys sitemap retry %s/%s url=%s backoff=%.0fs",
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
            LOGGER.warning("barnebys sitemap fetch failed url=%s error=%s", url, exc)
            if _is_permanent_miss(exc):
                break
    raise RuntimeError(
        f"failed to fetch Barnebys sitemap url={url} after {max_retries} attempts"
    ) from last_error


def _make_rotating_fetcher(
    proxy_list: Sequence[dict[str, str]],
) -> FetchBytesFn:
    state = {"i": 0}

    def fetch_bytes(url: str) -> bytes:
        errors: list[str] = []
        try:
            return _fetch_barnebys_bytes(url, proxy=None)
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
                return _fetch_barnebys_bytes(url, proxy=proxy)
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
            LOGGER.info("barnebys sitemap force: cleared %s", path)


def select_child_sitemaps(
    children: Sequence[str],
    progress: dict[str, dict[str, Any]],
    *,
    max_sitemaps: int,
) -> list[str]:
    """Prefer unknown/pending/failed lot children; skip done."""
    pending: list[str] = []
    for url in children:
        if not is_barnebys_lot_sitemap(url):
            continue
        entry = progress.get(url) or {}
        status = entry.get("status")
        if status == "done":
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
            "barnebys cache backfill: re-queued done children=%s "
            "cached_keys=%s done_lots_sum=%s",
            reclaimed,
            cached_keys,
            done_lots,
        )
    return reclaimed


def fetch_barnebys_sitemap_entries(
    sitemap_url: str = DEFAULT_BARNEBYS_INDEX,
    *,
    concurrency: int = 1,
    proxy: dict[str, str] | None = None,
    proxies: list[dict[str, str]] | None = None,
    fetch_bytes: FetchBytesFn | None = None,
    max_retries: int = DEFAULT_BARNEBYS_MAX_RETRIES,
    max_sitemaps: int = DEFAULT_MAX_SITEMAPS,
    min_urls: int = DEFAULT_MIN_URLS,
    sitemap_progress_path: Path | None = None,
    sitemap_force: bool = False,
    include_realized: bool = True,
) -> list[SitemapEntry]:
    """
    Discover Barnebys lot URLs from the public sitemap index.

    Expands pending ``lots-*.xml.gz`` children until ``max_sitemaps`` children are
    attempted, or earlier when ``min_urls`` **new** unique entries are collected
    (``min_urls=0`` disables that early stop — full pass over all pending shards).
    When ``include_realized`` is True, each live lot also yields a realized-prices
    twin URL. Progress + JSONL lot cache live beside ``sitemap_progress_path``.
    Returns only this-run new entries — callers must stream the JSONL for the
    full archive.
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
        "barnebys discover seed=%s proxies=%s max_sitemaps=%s min_urls=%s "
        "force=%s include_realized=%s cache=%s",
        sitemap_url,
        len(proxy_list),
        max_sitemaps,
        min_urls,
        sitemap_force,
        include_realized,
        cache_path,
    )

    seed_body = _fetch_with_retries(
        sitemap_url, fetch_bytes=fetch_bytes, max_retries=max_retries
    )
    if not is_sitemap_index(seed_body):
        entries = parse_barnebys_entries(seed_body, include_realized=include_realized)
        if cache_path is not None:
            seen_keys = load_lot_cache_keys(cache_path)
            fresh = [
                e
                for e in entries
                if e.entity_id and (e.entity_type, e.entity_id) not in seen_keys
            ]
            append_lot_cache(cache_path, fresh)
            LOGGER.info(
                "barnebys leaf sitemap entities=%s new=%s",
                len(entries),
                len(fresh),
            )
            return fresh
        LOGGER.info("barnebys leaf sitemap entities=%s", len(entries))
        return entries

    children = [
        loc
        for loc in parse_child_sitemap_locs(seed_body)
        if is_barnebys_lot_sitemap(loc)
    ]
    progress = load_sitemap_progress(sitemap_progress_path)
    seen_keys = load_lot_cache_keys(cache_path) if cache_path is not None else set()
    reclaim_done_children_for_cache_backfill(progress, cached_keys=len(seen_keys))
    selected = select_child_sitemaps(children, progress, max_sitemaps=max_sitemaps)
    LOGGER.info(
        "barnebys index children=%s known=%s selected=%s cached_keys=%s",
        len(children),
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
                "barnebys min_urls reached total=%s target=%s attempted=%s",
                len(best),
                min_urls,
                attempted,
            )
            break
        if i > 0 and DEFAULT_BARNEBYS_INTER_FILE_SLEEP > 0:
            time.sleep(DEFAULT_BARNEBYS_INTER_FILE_SLEEP)
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
            entries = parse_barnebys_entries(
                body, include_realized=include_realized
            )
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
                "status": "done",
                "lots_found": len(entries),
            }
            LOGGER.info(
                "barnebys child parsed url=%s entities=%s new=%s "
                "running_new=%s cached_keys=%s",
                child_url,
                len(entries),
                len(fresh),
                len(best),
                len(seen_keys),
            )
        except Exception as exc:
            LOGGER.warning("barnebys child failed url=%s error=%s", child_url, exc)
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
            f"all {len(failed)} Barnebys child sitemap(s) failed; no entries parsed"
        )
    if failed and not best:
        LOGGER.warning(
            "barnebys no new entries this run failed=%s cached_keys=%s",
            len(failed),
            len(seen_keys),
        )
    elif failed:
        LOGGER.warning(
            "barnebys partial success failed=%s new=%s",
            len(failed),
            len(best),
        )

    LOGGER.info(
        "barnebys discovery complete new=%s cached_keys=%s",
        len(best),
        len(seen_keys),
    )
    return list(best.values())


def known_barnebys_keys_from_paths(paths: Iterable[Path]) -> set[tuple[str, str]]:
    """Load known Barnebys entity keys from URL list / JSONL files."""
    keys: set[tuple[str, str]] = set()
    for url in load_urls(paths):
        key = barnebys_entity_from_url(url)
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
    "AZURE_WAF_KEYWORDS",
    "DEFAULT_BARNEBYS_INDEX",
    "DEFAULT_MAX_SITEMAPS",
    "DEFAULT_MIN_URLS",
    "clear_sitemap_cache",
    "fetch_barnebys_sitemap_entries",
    "is_barnebys_lot_sitemap",
    "iter_lot_cache_rows",
    "known_barnebys_keys_from_paths",
    "load_sitemap_progress",
    "lot_cache_path",
    "parse_barnebys_entries",
    "save_auction_lastmod_state",
    "save_sitemap_progress",
    "select_child_sitemaps",
]
