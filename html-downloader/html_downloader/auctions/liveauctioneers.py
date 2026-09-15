"""LiveAuctioneers discovery: gzipped price-result sitemap index → /price-result/ URLs.

PRIMARY: https://www.liveauctioneers.com/price-result-sitemap-index.xml.gz
Each child lists SEO sold-lot pages. Bounded by --max-sitemaps / --min-urls per run
with resume progress. Durable JSONL lot cache is the source of truth for the full
archive (streamed into monthly job files by the auction service).

Imperva blocks Chrome UAs; discovery uses SEO bot User-Agents (Googlebot/bingbot).
No lot parsing here — download saves HTML only.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx

from html_downloader.auctions.urls import (
    liveauctioneers_entity_from_url,
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

DEFAULT_LIVEAUCTIONEERS_INDEX = (
    "https://www.liveauctioneers.com/price-result-sitemap-index.xml.gz"
)
# Child sitemaps vary wildly (1–50k+ URLs). Per-run batch aims for Invaluable-scale
# archive growth; stop early once ``min_urls`` new price-result URLs are collected.
DEFAULT_MAX_SITEMAPS = 5_000
DEFAULT_MIN_URLS = 1_000_000
DEFAULT_LIVEAUCTIONEERS_MAX_RETRIES = 6
DEFAULT_LIVEAUCTIONEERS_FETCH_TIMEOUT = 90.0
DEFAULT_LIVEAUCTIONEERS_INTER_FILE_SLEEP = 1.5

BOT_USER_AGENTS: tuple[str, ...] = (
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    (
        "Mozilla/5.0 (Linux; Android 6.0.1; Nexus 5X Build/MMB29P) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Mobile "
        "Safari/537.36 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
    ),
    "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)",
)

IMPERVA_BLOCK_KEYWORDS: tuple[str, ...] = (
    "incapsula",
    "just a moment",
    "attention required",
    "unusual traffic",
    "access denied",
    "captcha",
    "verify you are human",
    "request blocked",
    "checking your browser",
)

FetchBytesFn = Callable[[str], bytes]


def is_liveauctioneers_price_result_sitemap(url: str) -> bool:
    """True for child sitemaps that belong to the price-result archive."""
    return "price-result" in url.lower()


def _looks_like_imperva_challenge(body: bytes) -> bool:
    if _looks_like_html_challenge(body):
        return True
    sample = body[:2048].lower()
    return any(kw.encode("ascii") in sample for kw in IMPERVA_BLOCK_KEYWORDS)


def parse_liveauctioneers_entries(
    xml_bytes: bytes,
    *,
    base_url: str = "https://www.liveauctioneers.com/",
) -> list[SitemapEntry]:
    """Parse a urlset into unique price-result SitemapEntry values."""
    if _looks_like_imperva_challenge(xml_bytes):
        raise RuntimeError("LiveAuctioneers sitemap returned a bot-challenge HTML page")

    try:
        entries_raw = parse_url_entries(xml_bytes)
    except ET.ParseError as exc:
        raise RuntimeError(f"invalid LiveAuctioneers sitemap XML: {exc}") from exc

    best: dict[tuple[str, str], SitemapEntry] = {}
    for loc, lastmod in entries_raw:
        if "/price-result/" not in loc:
            continue
        normalized = normalize_auction_url(loc, base_url=base_url)
        if not normalized:
            continue
        key = liveauctioneers_entity_from_url(normalized)
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
        if lastmod and (existing.lastmod is None or lastmod > existing.lastmod):
            best[key] = entry

    return list(best.values())


def _bot_headers() -> dict[str, str]:
    return {
        "User-Agent": random.choice(BOT_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
    }


def _proxy_url(proxy: dict[str, str] | None) -> str | None:
    """Convert curl_cffi-style proxy dict to an httpx proxy URL."""
    if not proxy:
        return None
    # ProxyPool / load_proxy_list use {"http": url, "https": url}
    return proxy.get("https") or proxy.get("http") or proxy.get("all")


def _fetch_liveauctioneers_bytes(
    url: str,
    *,
    proxy: dict[str, str] | None = None,
    timeout: float = DEFAULT_LIVEAUCTIONEERS_FETCH_TIMEOUT,
) -> bytes:
    """Fetch sitemap bytes with SEO bot UA; decompress .gz."""
    proxy_url = _proxy_url(proxy)
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        http2=True,
        proxy=proxy_url,
        headers=_bot_headers(),
    ) as client:
        response = client.get(url)
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
            raise RuntimeError(f"gzip decompress failed url={url}: {exc}") from exc
        head = body[:4096].lower()
        looks_xml = b"<urlset" in head or b"<sitemapindex" in head
        if _looks_like_imperva_challenge(body) and not looks_xml:
            raise RuntimeError(f"bot challenge for url={url}")
        return body


def _is_permanent_miss(exc: BaseException) -> bool:
    return "status=404" in str(exc)


def _fetch_with_retries(
    url: str,
    *,
    fetch_bytes: FetchBytesFn,
    max_retries: int = DEFAULT_LIVEAUCTIONEERS_MAX_RETRIES,
) -> bytes:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        if attempt > 0:
            backoff = min(90.0, 3.0 * (2 ** (attempt - 1)))
            LOGGER.info(
                "liveauctioneers sitemap retry %s/%s url=%s backoff=%.0fs",
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
            LOGGER.warning("liveauctioneers sitemap fetch failed url=%s error=%s", url, exc)
            if _is_permanent_miss(exc):
                break
    raise RuntimeError(
        f"failed to fetch LiveAuctioneers sitemap url={url} after {max_retries} attempts"
    ) from last_error


def _make_rotating_fetcher(
    proxy_list: Sequence[dict[str, str]],
) -> FetchBytesFn:
    state = {"i": 0}

    def fetch_bytes(url: str) -> bytes:
        errors: list[str] = []
        try:
            return _fetch_liveauctioneers_bytes(url, proxy=None)
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
                return _fetch_liveauctioneers_bytes(url, proxy=proxy)
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


def lot_cache_path(progress_path: Path) -> Path:
    """Sidecar JSONL of discovered price-result URLs (source of truth for archive)."""
    return progress_path.with_name(progress_path.stem + "_lots.jsonl")


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
        LOGGER.warning("could not read LiveAuctioneers lot cache %s: %s", path, exc)


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
                "liveauctioneers lot-cache id load progress lines=%s unique=%s",
                count,
                len(seen),
            )
    LOGGER.info(
        "liveauctioneers lot-cache id load complete lines=%s unique=%s",
        count,
        len(seen),
    )
    return seen


def append_lot_cache(path: Path, entries: Sequence[SitemapEntry]) -> None:
    """Append price-result rows to the durable JSONL cache."""
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


def clear_sitemap_cache(*, progress_path: Path) -> None:
    """Remove progress + lot JSONL so the next run rebuilds from scratch."""
    cache_path = lot_cache_path(progress_path)
    for path in (progress_path, cache_path):
        if path.exists():
            path.unlink()
            LOGGER.info("liveauctioneers sitemap force: cleared %s", path)


# Dense bucket shards often look like ...-a-0.xml.gz / ...--p-0.xml.gz.
_DENSE_SHARD_RE = re.compile(r"-\d+(?:\.xml(?:\.gz)?)?$", re.IGNORECASE)


def _child_sitemap_density_key(url: str) -> tuple[int, int, str]:
    """Sort key: denser basename patterns first, then longer names, then URL."""
    basename = Path(urlparse(url).path).name.lower()
    dense = 0 if _DENSE_SHARD_RE.search(basename) else 1
    return (dense, -len(basename), basename)


def select_child_sitemaps(
    children: Sequence[str],
    progress: dict[str, dict[str, Any]],
    *,
    max_sitemaps: int,
) -> list[str]:
    """Prefer unknown/pending/failed children; denser shards first; skip done."""
    pending: list[str] = []
    for url in children:
        if not is_liveauctioneers_price_result_sitemap(url):
            continue
        entry = progress.get(url) or {}
        status = entry.get("status")
        if status == "done":
            continue
        pending.append(url)
    pending.sort(key=_child_sitemap_density_key)
    if max_sitemaps <= 0:
        return []
    return pending[:max_sitemaps]


def reclaim_done_children_for_cache_backfill(
    progress: dict[str, dict[str, Any]],
    *,
    cached_ids: int,
) -> int:
    """
    Re-queue ``done`` children when the durable JSONL is far behind progress.

    Happens after upgrading to the lot-cache design: older runs marked children
    done without appending rows, so the archive would stay tiny forever.
    """
    done_lots = sum(
        int(entry.get("lots_found") or 0)
        for entry in progress.values()
        if entry.get("status") == "done"
    )
    if done_lots <= 0:
        return 0
    # Cache already covers most of what progress claims — nothing to reclaim.
    if cached_ids >= max(1, done_lots // 2):
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
            "liveauctioneers cache backfill: re-queued done children=%s "
            "cached_ids=%s done_lots_sum=%s",
            reclaimed,
            cached_ids,
            done_lots,
        )
    return reclaimed


def fetch_liveauctioneers_sitemap_entries(
    sitemap_url: str = DEFAULT_LIVEAUCTIONEERS_INDEX,
    *,
    concurrency: int = 1,
    proxy: dict[str, str] | None = None,
    proxies: list[dict[str, str]] | None = None,
    fetch_bytes: FetchBytesFn | None = None,
    max_retries: int = DEFAULT_LIVEAUCTIONEERS_MAX_RETRIES,
    max_sitemaps: int = DEFAULT_MAX_SITEMAPS,
    min_urls: int = DEFAULT_MIN_URLS,
    sitemap_progress_path: Path | None = None,
    sitemap_force: bool = False,
) -> list[SitemapEntry]:
    """
    Discover LiveAuctioneers /price-result/ URLs from the gzipped sitemap index.

    Expands pending child sitemaps until ``min_urls`` **new** unique entries are
    collected this run, or ``max_sitemaps`` children have been attempted. Progress
    and a durable JSONL lot cache live beside ``sitemap_progress_path``. Returns
    only this-run new entries — callers must stream the JSONL for the full archive.
    """
    _ = concurrency  # sequential only; parallel triggers Imperva storms
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
        "liveauctioneers discover seed=%s proxies=%s max_sitemaps=%s min_urls=%s "
        "force=%s cache=%s",
        sitemap_url,
        len(proxy_list),
        max_sitemaps,
        min_urls,
        sitemap_force,
        cache_path,
    )

    seed_body = _fetch_with_retries(
        sitemap_url, fetch_bytes=fetch_bytes, max_retries=max_retries
    )
    if not is_sitemap_index(seed_body):
        # Rare: seed itself is a urlset
        entries = parse_liveauctioneers_entries(seed_body)
        if cache_path is not None:
            seen_ids = load_lot_cache_ids(cache_path)
            fresh = [e for e in entries if e.entity_id and e.entity_id not in seen_ids]
            append_lot_cache(cache_path, fresh)
            LOGGER.info(
                "liveauctioneers leaf sitemap entities=%s new=%s",
                len(entries),
                len(fresh),
            )
            return fresh
        LOGGER.info("liveauctioneers leaf sitemap entities=%s", len(entries))
        return entries

    children = [
        loc
        for loc in parse_child_sitemap_locs(seed_body)
        if is_liveauctioneers_price_result_sitemap(loc)
    ]
    progress = load_sitemap_progress(sitemap_progress_path)
    seen_ids = load_lot_cache_ids(cache_path) if cache_path is not None else set()
    reclaim_done_children_for_cache_backfill(progress, cached_ids=len(seen_ids))
    selected = select_child_sitemaps(children, progress, max_sitemaps=max_sitemaps)
    LOGGER.info(
        "liveauctioneers index children=%s known=%s selected=%s cached_ids=%s",
        len(children),
        len(progress),
        len(selected),
        len(seen_ids),
    )

    best: dict[tuple[str, str], SitemapEntry] = {}
    failed: list[str] = []
    attempted = 0

    for i, child_url in enumerate(selected):
        if min_urls > 0 and len(best) >= min_urls:
            LOGGER.info(
                "liveauctioneers min_urls reached total=%s target=%s attempted=%s",
                len(best),
                min_urls,
                attempted,
            )
            break
        if i > 0 and DEFAULT_LIVEAUCTIONEERS_INTER_FILE_SLEEP > 0:
            time.sleep(DEFAULT_LIVEAUCTIONEERS_INTER_FILE_SLEEP)
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
            entries = parse_liveauctioneers_entries(body)
            fresh: list[SitemapEntry] = []
            for entry in entries:
                if not entry.entity_id:
                    continue
                if entry.entity_id in seen_ids:
                    continue
                seen_ids.add(entry.entity_id)
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
                "liveauctioneers child parsed url=%s entities=%s new=%s "
                "running_new=%s cached_ids=%s",
                child_url,
                len(entries),
                len(fresh),
                len(best),
                len(seen_ids),
            )
        except Exception as exc:
            LOGGER.warning("liveauctioneers child failed url=%s error=%s", child_url, exc)
            progress[child_url] = {
                "type": "sitemap",
                "status": "failed",
                "lots_found": 0,
                "error": str(exc)[:200],
            }
            failed.append(child_url)

    if sitemap_progress_path is not None:
        save_sitemap_progress(sitemap_progress_path, progress)

    if failed and not best and not seen_ids:
        raise RuntimeError(
            f"all {len(failed)} LiveAuctioneers child sitemap(s) failed; no entries parsed"
        )
    if failed and not best:
        # Retries may yield no *new* rows while the durable cache already has data.
        LOGGER.warning(
            "liveauctioneers no new entries this run failed=%s cached_ids=%s",
            len(failed),
            len(seen_ids),
        )
    elif failed:
        LOGGER.warning(
            "liveauctioneers partial success failed=%s new=%s",
            len(failed),
            len(best),
        )

    LOGGER.info(
        "liveauctioneers discovery complete new=%s price_results=%s cached_ids=%s",
        len(best),
        sum(1 for k in best if k[0] == "price_result"),
        len(seen_ids),
    )
    return list(best.values())


def known_liveauctioneers_keys_from_paths(paths: Iterable[Path]) -> set[tuple[str, str]]:
    """Load known LiveAuctioneers entity keys from URL list / JSONL files."""
    keys: set[tuple[str, str]] = set()
    for url in load_urls(paths):
        key = liveauctioneers_entity_from_url(url)
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


def liveauctioneers_download_headers() -> dict[str, str]:
    """Headers for HTML download (SEO bot UA — Imperva serves SSR to crawlers)."""
    return {
        "User-Agent": BOT_USER_AGENTS[0],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Cache-Control": "no-cache",
        "Upgrade-Insecure-Requests": "1",
    }
