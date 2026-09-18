"""Drouot first-party search backfill (Algolia-equivalent expander).

Drouot does not expose a public Algolia index. Live lot coverage is available via
SvelteKit ``GET /en/s/__data.json?query=*&page=N`` (robots Disallow ``/en/s/`` —
use only when the EN lot sitemap under-counts ``totalItems``).
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from curl_cffi import requests as crequests

from html_downloader.auctions.urls import build_drouot_lot_url
from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

BASE_URL = "https://drouot.com"
SEARCH_DATA_PATH = "/en/s/__data.json"
DEFAULT_SEARCH_QUERY = "*"
DEFAULT_SEARCH_DELAY = 0.35
DEFAULT_SEARCH_MAX_RETRIES = 5
DEFAULT_SEARCH_TIMEOUT = 60.0
_MAX_RETURN_NEW = 50_000

FetchJsonFn = Callable[[str], dict[str, Any]]


@dataclass
class SearchBrowseState:
    """Page-checkpoint state for Drouot search backfill."""

    query: str = DEFAULT_SEARCH_QUERY
    total_items: int | None = None
    total_pages: int | None = None
    next_page: int = 1
    status: str = "pending"  # pending | in_progress | done | failed
    pages_done: list[int] = field(default_factory=list)
    lots_found: int = 0
    last_error: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "total_items": self.total_items,
            "total_pages": self.total_pages,
            "next_page": self.next_page,
            "status": self.status,
            "pages_done": sorted(set(self.pages_done)),
            "lots_found": self.lots_found,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SearchBrowseState:
        pages_raw = payload.get("pages_done") or []
        pages = [int(p) for p in pages_raw if isinstance(p, (int, str)) and str(p).isdigit()]
        return cls(
            query=str(payload.get("query") or DEFAULT_SEARCH_QUERY),
            total_items=(
                int(payload["total_items"])
                if payload.get("total_items") is not None
                else None
            ),
            total_pages=(
                int(payload["total_pages"])
                if payload.get("total_pages") is not None
                else None
            ),
            next_page=max(1, int(payload.get("next_page") or 1)),
            status=str(payload.get("status") or "pending"),
            pages_done=pages,
            lots_found=int(payload.get("lots_found") or 0),
            last_error=(
                str(payload["last_error"]) if payload.get("last_error") else None
            ),
            updated_at=(
                str(payload["updated_at"]) if payload.get("updated_at") else None
            ),
        )


def search_data_url(*, query: str = DEFAULT_SEARCH_QUERY, page: int = 1) -> str:
    """Build the SvelteKit search ``__data.json`` URL (1-based page)."""
    params = urlencode({"query": query, "page": max(1, int(page))})
    return f"{BASE_URL}{SEARCH_DATA_PATH}?{params}"


def _resolve_ref(arr: list[Any], value: Any) -> Any:
    """Resolve one SvelteKit numbered ref (no nested chase of integer values)."""
    if isinstance(value, int) and 0 <= value < len(arr):
        return arr[value]
    return value


def decode_search_payload(
    payload: dict[str, Any],
) -> tuple[int | None, int | None, list[SitemapEntry]]:
    """
    Decode a SvelteKit search ``__data.json`` body.

    Returns ``(total_items, total_pages, lot SitemapEntry list)``.
    """
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        return None, None, []

    for node in nodes:
        if not isinstance(node, dict):
            continue
        arr = node.get("data")
        if not isinstance(arr, list) or not arr:
            continue
        head = arr[0]
        if not isinstance(head, dict) or "totalItems" not in head or "lots" not in head:
            continue

        total_items_raw = _resolve_ref(arr, head.get("totalItems"))
        total_pages_raw = _resolve_ref(arr, head.get("totalPages"))
        total_items = int(total_items_raw) if total_items_raw is not None else None
        total_pages = int(total_pages_raw) if total_pages_raw is not None else None

        lots_raw = _resolve_ref(arr, head.get("lots"))
        entries: list[SitemapEntry] = []
        if isinstance(lots_raw, list):
            for item in lots_raw:
                lot = _resolve_ref(arr, item)
                if not isinstance(lot, dict):
                    continue
                lot_id = _resolve_ref(arr, lot.get("id"))
                slug = _resolve_ref(arr, lot.get("slug"))
                if lot_id is None:
                    continue
                if not slug:
                    slug = "lot"
                entity_id = str(lot_id).strip()
                if not entity_id.isdigit():
                    continue
                url = build_drouot_lot_url(entity_id, str(slug))
                entries.append(
                    SitemapEntry(
                        url=url,
                        lastmod=None,
                        entity_type="lot",
                        entity_id=entity_id,
                    )
                )
        return total_items, total_pages, entries

    return None, None, []


def _fetch_search_json(
    url: str,
    *,
    timeout: float = DEFAULT_SEARCH_TIMEOUT,
) -> dict[str, Any]:
    response = crequests.get(
        url,
        impersonate="chrome131",
        timeout=timeout,
        headers={
            "Accept": "application/json,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": f"{BASE_URL}/en/s",
        },
    )
    status = int(response.status_code)
    body = response.text or ""
    if status != 200:
        raise RuntimeError(f"drouot search status={status} url={url}")
    if not body.strip().startswith("{"):
        raise RuntimeError(f"drouot search non-json body url={url}")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"drouot search invalid json url={url}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"drouot search unexpected payload type url={url}")
    return payload


def _fetch_with_retries(
    url: str,
    *,
    fetch_json: FetchJsonFn,
    max_retries: int = DEFAULT_SEARCH_MAX_RETRIES,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in range(max_retries):
        if attempt > 0:
            backoff = min(60.0, 2.0 * (2 ** (attempt - 1)))
            LOGGER.info(
                "drouot search retry %s/%s url=%s backoff=%.0fs",
                attempt + 1,
                max_retries,
                url,
                backoff,
            )
            time.sleep(backoff)
        try:
            return fetch_json(url)
        except Exception as exc:
            last_error = exc
            LOGGER.warning("drouot search fetch failed url=%s error=%s", url, exc)
    raise RuntimeError(
        f"failed to fetch Drouot search url={url} after {max_retries} attempts"
    ) from last_error


def probe_search_total(
    *,
    query: str = DEFAULT_SEARCH_QUERY,
    fetch_json: FetchJsonFn | None = None,
) -> int:
    """Return ``totalItems`` for a search query (page 1)."""
    fetch = fetch_json or _fetch_search_json
    url = search_data_url(query=query, page=1)
    payload = _fetch_with_retries(url, fetch_json=fetch)
    total_items, _total_pages, _entries = decode_search_payload(payload)
    if total_items is None:
        raise RuntimeError(f"drouot search missing totalItems url={url}")
    return int(total_items)


def load_search_state(path: Path | None) -> SearchBrowseState:
    if path is None or not path.is_file():
        return SearchBrowseState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return SearchBrowseState()
    if not isinstance(payload, dict):
        return SearchBrowseState()
    return SearchBrowseState.from_dict(payload)


def save_search_state(path: Path, state: SearchBrowseState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    text = json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def clear_search_state(path: Path) -> None:
    if path.exists():
        path.unlink()
        LOGGER.info("drouot search force: cleared %s", path)


def expand_lots_from_search(
    *,
    state_path: Path,
    cache_path: Path,
    known_keys: set[tuple[str, str]] | None = None,
    query: str = DEFAULT_SEARCH_QUERY,
    delay: float = DEFAULT_SEARCH_DELAY,
    force: bool = False,
    fetch_json: FetchJsonFn | None = None,
    max_pages: int | None = None,
) -> list[SitemapEntry]:
    """
    Paginate Drouot search and append missing lot URLs to ``cache_path``.

    Returns a capped list of **new** in-memory entries from this run.
    """
    from html_downloader.auctions.drouot import append_lot_cache, load_lot_cache_keys

    fetch = fetch_json or _fetch_search_json
    if force:
        clear_search_state(state_path)

    state = load_search_state(state_path)
    if state.status == "done" and not force:
        LOGGER.info(
            "drouot search expand already done lots_found=%s total_items=%s",
            state.lots_found,
            state.total_items,
        )
        return []

    seen = set(known_keys or ())
    seen |= load_lot_cache_keys(cache_path)

    state.query = query
    state.status = "in_progress"
    save_search_state(state_path, state)

    # Probe page 1 for totals (and first page hits).
    page = max(1, state.next_page)
    if not state.pages_done:
        page = 1

    new_entries: list[SitemapEntry] = []
    pages_walked = 0

    try:
        while True:
            if max_pages is not None and pages_walked >= max_pages:
                LOGGER.info(
                    "drouot search max_pages reached walked=%s next_page=%s",
                    pages_walked,
                    page,
                )
                break
            if page in state.pages_done:
                page += 1
                if state.total_pages is not None and page > state.total_pages:
                    break
                continue

            if pages_walked > 0 and delay > 0:
                time.sleep(delay)

            url = search_data_url(query=query, page=page)
            payload = _fetch_with_retries(url, fetch_json=fetch)
            total_items, total_pages, entries = decode_search_payload(payload)
            if total_items is not None:
                state.total_items = total_items
            if total_pages is not None:
                state.total_pages = total_pages

            fresh: list[SitemapEntry] = []
            for entry in entries:
                key = entry.entity_key
                if key in seen:
                    continue
                seen.add(key)
                fresh.append(entry)

            if fresh:
                append_lot_cache(cache_path, fresh)
                state.lots_found += len(fresh)
                remaining = _MAX_RETURN_NEW - len(new_entries)
                if remaining > 0:
                    new_entries.extend(fresh[:remaining])

            state.pages_done.append(page)
            state.next_page = page + 1
            state.last_error = None
            save_search_state(state_path, state)
            pages_walked += 1

            LOGGER.info(
                "drouot search page=%s/%s hits=%s new=%s cache_keys=%s",
                page,
                state.total_pages,
                len(entries),
                len(fresh),
                len(seen),
            )

            lot_keys = {k for k in seen if k[0] == "lot"}
            if (
                state.total_items is not None
                and len(lot_keys) >= state.total_items
                and state.total_pages is not None
                and page >= state.total_pages
            ):
                break

            if state.total_pages is not None and page >= state.total_pages:
                break
            page += 1

        state.status = "done"
        state.last_error = None
        save_search_state(state_path, state)
    except Exception as exc:
        state.status = "failed"
        state.last_error = str(exc)[:300]
        save_search_state(state_path, state)
        raise

    LOGGER.info(
        "drouot search expand complete new_in_memory=%s total_items=%s "
        "pages_done=%s cache_lot_keys≈%s",
        len(new_entries),
        state.total_items,
        len(state.pages_done),
        sum(1 for k in seen if k[0] == "lot"),
    )
    return new_entries


__all__ = [
    "DEFAULT_SEARCH_DELAY",
    "DEFAULT_SEARCH_QUERY",
    "SearchBrowseState",
    "clear_search_state",
    "decode_search_payload",
    "expand_lots_from_search",
    "load_search_state",
    "probe_search_total",
    "save_search_state",
    "search_data_url",
]
