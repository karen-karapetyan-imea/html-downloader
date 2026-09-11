"""Artcurial discovery: public /ace JSON API → /en/sales/{ref}/lots/{index}-{sub} URLs.

PRIMARY: https://www.artcurial.com/ace/sales/results (finished sales, 2003→now)
Then:    /ace/sales/{ref}/items (paginated lots)

XML sitemaps currently 500; robots.txt allows /ace and Disallows /motorcars.
Skip CARS / VOIT / AUTOMOBILIA sales only. No fine-art filter — maximize corpus.
No lot parsing here — download saves SSR HTML only.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from html_downloader.auctions.urls import (
    artcurial_entity_from_url,
    normalize_auction_url,
)
from html_downloader.discover.registry import load_urls
from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

SITE_BASE = "https://www.artcurial.com"
API_BASE = SITE_BASE + "/ace"
DEFAULT_ARTCURIAL_INDEX = API_BASE + "/sales/results"
ITEMS_URL = API_BASE + "/sales/{ref}/items"
LOT_PAGE_URL = SITE_BASE + "/en/sales/{ref}/lots/{index}-{sub}"

PAGE_SIZE = 200
DEFAULT_MAX_SALES: int | None = None  # None = all finished sales
DEFAULT_ARTCURIAL_MAX_RETRIES = 5
DEFAULT_ARTCURIAL_FETCH_TIMEOUT = 40.0
DEFAULT_ARTCURIAL_INTER_SALE_SLEEP = 0.05

# Honour robots.txt /motorcars Disallow — not art anyway.
SKIP_SPECIALTIES: frozenset[str] = frozenset({"CARS", "VOIT", "AUTOMOBILIA"})

FetchJsonFn = Callable[[str, dict[str, Any] | None], Any]


def sale_specialty_refs(sale: dict[str, Any]) -> set[str]:
    """Collect specialty + parent department refs from a sale payload."""
    refs: set[str] = set()
    for item in sale.get("specialties") or []:
        if not isinstance(item, dict):
            continue
        specialty = item.get("specialty") or {}
        if not isinstance(specialty, dict):
            continue
        ref = specialty.get("ref")
        if ref:
            refs.add(str(ref))
        parent = specialty.get("parent") or {}
        if isinstance(parent, dict) and parent.get("ref"):
            refs.add(str(parent["ref"]))
    return refs


def should_skip_sale(sale: dict[str, Any]) -> bool:
    """True when the sale is motorcars / automobilia (robots Disallow)."""
    return bool(sale_specialty_refs(sale) & SKIP_SPECIALTIES)


def lot_page_url(ref: str, index: int | str, sub: str) -> str:
    return LOT_PAGE_URL.format(ref=ref, index=index, sub=sub)


def _sale_lastmod(sale: dict[str, Any]) -> str | None:
    begin = (sale.get("validity") or {}).get("beginDate")
    if not begin:
        return None
    text = str(begin)
    return text[:10] if len(text) >= 10 else text


def _item_lastmod(item: dict[str, Any], sale_lastmod: str | None) -> str | None:
    adj = item.get("adjudicationDate")
    if adj:
        text = str(adj)
        return text[:10] if len(text) >= 10 else text
    return sale_lastmod


def entries_from_sale_items(
    sale: dict[str, Any],
    items: Sequence[dict[str, Any]],
) -> list[SitemapEntry]:
    """Map /items payloads to unique lot SitemapEntry values."""
    ref = str(sale.get("ref") or "").strip()
    if not ref:
        return []
    sale_lm = _sale_lastmod(sale)
    best: dict[tuple[str, str], SitemapEntry] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("publishLot") is False:
            continue
        index = item.get("index")
        if index is None:
            continue
        sub = str(item.get("subIndex") or "a").lower()
        raw_url = lot_page_url(ref, index, sub)
        normalized = normalize_auction_url(raw_url)
        if not normalized:
            continue
        key = artcurial_entity_from_url(normalized)
        if key is None:
            continue
        entity_type, entity_id = key
        lastmod = _item_lastmod(item, sale_lm)
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


def _json_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9,fr;q=0.8",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }


def _proxy_url(proxy: dict[str, str] | None) -> str | None:
    if not proxy:
        return None
    return proxy.get("https") or proxy.get("http") or proxy.get("all")


def _fetch_artcurial_json(
    url: str,
    params: dict[str, Any] | None = None,
    *,
    proxy: dict[str, str] | None = None,
    timeout: float = DEFAULT_ARTCURIAL_FETCH_TIMEOUT,
) -> Any:
    proxy_url = _proxy_url(proxy)
    with httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        http2=True,
        proxy=proxy_url,
        headers=_json_headers(),
    ) as client:
        response = client.get(url, params=params)
        status = int(response.status_code)
        if status == 404:
            return None
        if status in {403, 429, 500, 502, 503, 520, 522}:
            raise RuntimeError(f"artcurial fetch status={status} url={url}")
        if status != 200:
            raise RuntimeError(f"artcurial fetch status={status} url={url}")
        return response.json()


def _fetch_json_with_retries(
    url: str,
    params: dict[str, Any] | None,
    *,
    fetch_json: FetchJsonFn,
    max_retries: int = DEFAULT_ARTCURIAL_MAX_RETRIES,
) -> Any:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        if attempt > 0:
            backoff = min(60.0, 2.0 * (2 ** (attempt - 1)))
            LOGGER.info(
                "artcurial retry %s/%s url=%s backoff=%.0fs",
                attempt + 1,
                max_retries,
                url,
                backoff,
            )
            time.sleep(backoff)
        try:
            return fetch_json(url, params)
        except Exception as exc:
            last_error = exc
            LOGGER.warning("artcurial fetch failed url=%s error=%s", url, exc)
    raise RuntimeError(
        f"failed to fetch Artcurial url={url} after {max_retries} attempts"
    ) from last_error


def _make_rotating_json_fetcher(
    proxy_list: Sequence[dict[str, str]],
) -> FetchJsonFn:
    state = {"i": 0}

    def fetch_json(url: str, params: dict[str, Any] | None) -> Any:
        errors: list[str] = []
        try:
            return _fetch_artcurial_json(url, params, proxy=None)
        except Exception as exc:
            errors.append(f"direct:{exc}")

        if not proxy_list:
            raise RuntimeError("; ".join(errors))

        n = min(5, len(proxy_list))
        for _ in range(n):
            proxy = proxy_list[state["i"] % len(proxy_list)]
            state["i"] += 1
            try:
                return _fetch_artcurial_json(url, params, proxy=proxy)
            except Exception as exc:
                errors.append(f"proxy:{exc}")
        raise RuntimeError("; ".join(errors[-3:]))

    return fetch_json


def load_sales_progress(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    sales = payload.get("sales")
    if isinstance(sales, dict):
        return {str(k): dict(v) for k, v in sales.items() if isinstance(v, dict)}
    return {}


def save_sales_progress(path: Path, sales: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sales": dict(sorted(sales.items())),
        "last_fetch_at": datetime.now().astimezone().isoformat(),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def select_pending_sales(
    sales: Sequence[dict[str, Any]],
    progress: dict[str, dict[str, Any]],
    *,
    max_sales: int | None,
) -> list[dict[str, Any]]:
    """Prefer unknown/pending/failed sales; skip done and motorcars."""
    pending: list[dict[str, Any]] = []
    for sale in sales:
        ref = str(sale.get("ref") or "").strip()
        if not ref:
            continue
        if should_skip_sale(sale):
            continue
        entry = progress.get(ref) or {}
        if entry.get("status") == "done":
            continue
        pending.append(sale)
    if max_sales is None:
        return pending
    if max_sales <= 0:
        return []
    return pending[:max_sales]


def _paginate_sales(
    sales_url: str,
    *,
    fetch_json: FetchJsonFn,
    max_retries: int,
) -> list[dict[str, Any]]:
    first = _fetch_json_with_retries(
        sales_url,
        {"page": 0, "size": 100, "sort": "validity.beginDate,desc"},
        fetch_json=fetch_json,
        max_retries=max_retries,
    )
    if not first or not isinstance(first, dict):
        return []
    sales = [s for s in (first.get("content") or []) if isinstance(s, dict)]
    total_pages = int(first.get("totalPages") or 1)
    for page in range(1, total_pages):
        data = _fetch_json_with_retries(
            sales_url,
            {"page": page, "size": 100, "sort": "validity.beginDate,desc"},
            fetch_json=fetch_json,
            max_retries=max_retries,
        )
        if data and isinstance(data, dict):
            sales.extend(s for s in (data.get("content") or []) if isinstance(s, dict))
    return sales


def _paginate_sale_items(
    ref: str,
    *,
    fetch_json: FetchJsonFn,
    max_retries: int,
) -> list[dict[str, Any]]:
    url = ITEMS_URL.format(ref=ref)
    first = _fetch_json_with_retries(
        url,
        {"page": 0, "size": PAGE_SIZE},
        fetch_json=fetch_json,
        max_retries=max_retries,
    )
    if not first or not isinstance(first, dict):
        return []
    items = [it for it in (first.get("content") or []) if isinstance(it, dict)]
    total_pages = int(first.get("totalPages") or 1)
    for page in range(1, total_pages):
        data = _fetch_json_with_retries(
            url,
            {"page": page, "size": PAGE_SIZE},
            fetch_json=fetch_json,
            max_retries=max_retries,
        )
        if data and isinstance(data, dict):
            items.extend(it for it in (data.get("content") or []) if isinstance(it, dict))
    return items


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


def fetch_artcurial_entries(
    sales_url: str = DEFAULT_ARTCURIAL_INDEX,
    *,
    concurrency: int = 4,
    proxy: dict[str, str] | None = None,
    proxies: list[dict[str, str]] | None = None,
    fetch_json: FetchJsonFn | None = None,
    max_retries: int = DEFAULT_ARTCURIAL_MAX_RETRIES,
    max_sales: int | None = DEFAULT_MAX_SALES,
    sales_progress_path: Path | None = None,
) -> list[SitemapEntry]:
    """
    Discover Artcurial lot URLs from the public /ace JSON API.

    Enumerates finished sales, skips motorcars specialties, expands lot pages,
    and resumes from ``sales_progress_path`` (completed sale refs marked done).
    """
    proxy_list: list[dict[str, str]] = list(proxies or [])
    if not proxy_list and proxy is not None:
        proxy_list = [proxy]

    if fetch_json is None:
        fetch_json = _make_rotating_json_fetcher(proxy_list)

    workers = max(1, concurrency)
    LOGGER.info(
        "artcurial discover seed=%s proxies=%s concurrency=%s max_sales=%s",
        sales_url,
        len(proxy_list),
        workers,
        max_sales,
    )

    all_sales = _paginate_sales(
        sales_url, fetch_json=fetch_json, max_retries=max_retries
    )
    progress = load_sales_progress(sales_progress_path)

    # Mark skipped motorcars sales as done so we never re-examine them.
    skipped = 0
    for sale in all_sales:
        ref = str(sale.get("ref") or "").strip()
        if not ref or not should_skip_sale(sale):
            continue
        skipped += 1
        if progress.get(ref, {}).get("status") != "done":
            progress[ref] = {
                "type": "sale",
                "status": "done",
                "lots_found": 0,
                "skipped": True,
            }

    pending = select_pending_sales(all_sales, progress, max_sales=max_sales)
    LOGGER.info(
        "artcurial sales total=%s skipped_motorcars=%s pending=%s",
        len(all_sales),
        skipped,
        len(pending),
    )

    best: dict[tuple[str, str], SitemapEntry] = {}
    failed: list[str] = []

    def process_sale(sale: dict[str, Any]) -> tuple[str, list[SitemapEntry], str | None]:
        ref = str(sale["ref"])
        try:
            items = _paginate_sale_items(
                ref, fetch_json=fetch_json, max_retries=max_retries
            )
            entries = entries_from_sale_items(sale, items)
            return ref, entries, None
        except Exception as exc:
            return ref, [], str(exc)

    if workers == 1:
        for i, sale in enumerate(pending):
            if i > 0 and DEFAULT_ARTCURIAL_INTER_SALE_SLEEP > 0:
                time.sleep(DEFAULT_ARTCURIAL_INTER_SALE_SLEEP)
            ref = str(sale["ref"])
            progress[ref] = {
                "type": "sale",
                "status": "in_progress",
                "lots_found": progress.get(ref, {}).get("lots_found", 0),
            }
            ref, entries, error = process_sale(sale)
            if error:
                LOGGER.warning("artcurial sale failed ref=%s error=%s", ref, error)
                progress[ref] = {
                    "type": "sale",
                    "status": "failed",
                    "lots_found": 0,
                    "error": error[:200],
                }
                failed.append(ref)
                continue
            for entry in entries:
                _merge_entry(best, entry)
            progress[ref] = {
                "type": "sale",
                "status": "done",
                "lots_found": len(entries),
            }
            LOGGER.info(
                "artcurial sale parsed ref=%s lots=%s running_total=%s",
                ref,
                len(entries),
                len(best),
            )
            if sales_progress_path is not None and (i + 1) % 25 == 0:
                save_sales_progress(sales_progress_path, progress)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(process_sale, sale): sale for sale in pending}
            for future in as_completed(futures):
                sale = futures[future]
                ref = str(sale["ref"])
                try:
                    ref, entries, error = future.result()
                except Exception as exc:
                    error = str(exc)
                    entries = []
                if error:
                    LOGGER.warning("artcurial sale failed ref=%s error=%s", ref, error)
                    progress[ref] = {
                        "type": "sale",
                        "status": "failed",
                        "lots_found": 0,
                        "error": error[:200],
                    }
                    failed.append(ref)
                    continue
                for entry in entries:
                    _merge_entry(best, entry)
                progress[ref] = {
                    "type": "sale",
                    "status": "done",
                    "lots_found": len(entries),
                }
                LOGGER.info(
                    "artcurial sale parsed ref=%s lots=%s running_total=%s",
                    ref,
                    len(entries),
                    len(best),
                )

    if sales_progress_path is not None:
        save_sales_progress(sales_progress_path, progress)

    if failed and not best:
        raise RuntimeError(
            f"all {len(failed)} Artcurial sale(s) failed; no entries parsed"
        )
    if failed:
        LOGGER.warning(
            "artcurial partial success failed=%s parsed=%s",
            len(failed),
            len(best),
        )

    LOGGER.info(
        "artcurial discovery complete total=%s lots=%s",
        len(best),
        sum(1 for k in best if k[0] == "lot"),
    )
    return list(best.values())


def known_artcurial_keys_from_paths(paths: Iterable[Path]) -> set[tuple[str, str]]:
    """Load known Artcurial entity keys from URL list / JSONL files."""
    keys: set[tuple[str, str]] = set()
    for url in load_urls(paths):
        key = artcurial_entity_from_url(url)
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
