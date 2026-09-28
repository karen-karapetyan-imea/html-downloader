"""Shared helpers for auction houses that persist discovered URLs as typed JSONL.

Row shape: ``{"entity_type", "entity_id", "url", "lastmod"}`` — one per line,
append-only, deduplicated at write time by the caller's ``TypedKeySet``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

FetchBytesFn = Callable[[str], bytes]
FetchOneFn = Callable[[str, dict[str, str] | None], bytes]
TypedRow = tuple[str, str, str, str | None]


class TypedKeySet:
    """
    Memory-lean set of ``(entity_type, entity_id)`` keys.

    Canonical numeric ids are stored as ``int`` (28 B) instead of tuples of
    strings (~150 B), which matters for multi-million lot archives.
    """

    __slots__ = ("_ids",)

    def __init__(self) -> None:
        self._ids: dict[str, set[int | str]] = {}

    @staticmethod
    def _compact(entity_id: str) -> int | str:
        if entity_id.isdigit() and (entity_id == "0" or not entity_id.startswith("0")):
            return int(entity_id)
        return entity_id

    def add(self, key: tuple[str, str]) -> bool:
        """Add a key; return True when it was not already present."""
        entity_type, entity_id = key
        bucket = self._ids.setdefault(entity_type, set())
        value = self._compact(entity_id)
        if value in bucket:
            return False
        bucket.add(value)
        return True

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, tuple) or len(key) != 2:
            return False
        entity_type, entity_id = key
        bucket = self._ids.get(str(entity_type))
        return bucket is not None and self._compact(str(entity_id)) in bucket

    def __len__(self) -> int:
        return sum(len(bucket) for bucket in self._ids.values())

    def count(self, entity_type: str) -> int:
        return len(self._ids.get(entity_type, ()))


def typed_cache_path(progress_path: Path) -> Path:
    """Sidecar JSONL next to a progress/state file."""
    return progress_path.with_name(progress_path.stem + "_lots.jsonl")


def iter_typed_cache_rows(
    path: Path,
    *,
    default_type: str = "lot",
    label: str = "auction",
) -> Iterator[TypedRow]:
    """Yield ``(entity_type, entity_id, url, lastmod)`` from a typed JSONL cache."""
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
                entity_type = str(row.get("entity_type") or default_type).strip()
                entity_id = str(row.get("entity_id") or "").strip()
                url = str(row.get("url") or "").strip()
                if not entity_id or not url:
                    continue
                lastmod_raw = row.get("lastmod")
                yield entity_type, entity_id, url, (str(lastmod_raw) if lastmod_raw else None)
    except OSError as exc:
        LOGGER.warning("could not read %s lot cache %s: %s", label, path, exc)


def load_typed_cache_keys(path: Path, *, label: str = "auction") -> TypedKeySet:
    """Stream keys from a typed JSONL cache (no SitemapEntry objects)."""
    keys = TypedKeySet()
    if not path.exists():
        return keys
    lines = 0
    for entity_type, entity_id, _url, _lastmod in iter_typed_cache_rows(path, label=label):
        keys.add((entity_type, entity_id))
        lines += 1
        if lines % 1_000_000 == 0:
            LOGGER.info("%s lot-cache key load progress lines=%s unique=%s", label, lines, len(keys))
    LOGGER.info("%s lot-cache key load complete lines=%s unique=%s", label, lines, len(keys))
    return keys


def append_typed_cache(path: Path, entries: Sequence[SitemapEntry]) -> int:
    """Append entries to the typed JSONL cache; returns rows written."""
    if not entries:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with open(path, "a", encoding="utf-8") as handle:
        for entry in entries:
            if not entry.entity_id:
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
    """Atomic write (temp file + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sitemaps": dict(sorted(sitemaps.items())),
        "last_fetch_at": datetime.now().astimezone().isoformat(),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def is_permanent_miss(exc: BaseException) -> bool:
    return "status=404" in str(exc)


def fetch_with_retries(
    url: str,
    *,
    fetch_bytes: FetchBytesFn,
    max_retries: int,
    label: str = "auction",
    max_backoff: float = 90.0,
) -> bytes:
    """Exponential-backoff wrapper; 404 is treated as permanent."""
    last_error: Exception | None = None
    for attempt in range(max_retries):
        if attempt > 0:
            backoff = min(max_backoff, 3.0 * (2 ** (attempt - 1)))
            LOGGER.info(
                "%s sitemap retry %s/%s url=%s backoff=%.0fs",
                label,
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
            LOGGER.warning("%s sitemap fetch failed url=%s error=%s", label, url, exc)
            if is_permanent_miss(exc):
                break
    raise RuntimeError(
        f"failed to fetch {label} sitemap url={url} after {max_retries} attempts"
    ) from last_error


def make_rotating_fetcher(
    fetch_one: FetchOneFn,
    proxy_list: Sequence[dict[str, str]],
    *,
    max_proxy_attempts: int = 5,
) -> FetchBytesFn:
    """Try direct first, then rotate through up to ``max_proxy_attempts`` proxies."""
    state = {"i": 0}

    def fetch_bytes(url: str) -> bytes:
        errors: list[str] = []
        try:
            return fetch_one(url, None)
        except Exception as exc:
            errors.append(f"direct:{exc}")
            if is_permanent_miss(exc):
                raise
        if not proxy_list:
            raise RuntimeError("; ".join(errors))
        for _ in range(min(max_proxy_attempts, len(proxy_list))):
            proxy = proxy_list[state["i"] % len(proxy_list)]
            state["i"] += 1
            try:
                return fetch_one(url, proxy)
            except Exception as exc:
                errors.append(f"proxy:{exc}")
                if is_permanent_miss(exc):
                    raise
        raise RuntimeError("; ".join(errors[-3:]))

    return fetch_bytes


def proxy_url(proxy: dict[str, str] | None) -> str | None:
    if not proxy:
        return None
    return proxy.get("https") or proxy.get("http") or proxy.get("all")


__all__ = [
    "FetchBytesFn",
    "FetchOneFn",
    "TypedKeySet",
    "TypedRow",
    "append_typed_cache",
    "fetch_with_retries",
    "is_permanent_miss",
    "iter_typed_cache_rows",
    "load_sitemap_progress",
    "load_typed_cache_keys",
    "make_rotating_fetcher",
    "proxy_url",
    "save_sitemap_progress",
    "typed_cache_path",
]
