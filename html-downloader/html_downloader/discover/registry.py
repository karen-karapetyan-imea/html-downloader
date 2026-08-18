"""URL normalize, SHA1 HTML filenames, and file-based known-entity loading."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from html_downloader.discover.urls import artsper_entity_from_url

_KATANA_JSON_URL_KEYS = ("url", "request", "endpoint", "input")


def normalize_url(url: str) -> str:
    """Lowercase host, drop query/fragment, strip trailing slash."""
    text = (url or "").strip()
    if not text:
        return ""
    parts = urlsplit(text)
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def html_filename_for_url(url: str) -> str:
    """Same basename as the HTML crawler (sha1 of URL)."""
    return hashlib.sha1(url.encode()).hexdigest() + ".html"


def entity_key_from_url(url: str) -> tuple[str, str] | None:
    """Stable (entity_type, external_id) for Artsper artist/artwork URLs."""
    return artsper_entity_from_url(normalize_url(url) or url)


def iter_urls_from_lines(path: Path) -> Iterator[str]:
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            url = line.strip()
            if url and not url.startswith("#"):
                yield url


def iter_urls_from_jsonl(path: Path) -> Iterator[str]:
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            for key in _KATANA_JSON_URL_KEYS:
                value = row.get(key)
                if isinstance(value, str) and value.strip():
                    yield value.strip()
                    break
            else:
                request = row.get("request")
                if isinstance(request, dict):
                    endpoint = request.get("endpoint")
                    if isinstance(endpoint, str) and endpoint.strip():
                        yield endpoint.strip()


def load_urls(paths: Iterable[Path]) -> set[str]:
    urls: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        iterator: Iterator[str]
        if path.suffix.lower() == ".jsonl":
            iterator = iter_urls_from_jsonl(path)
        else:
            iterator = iter_urls_from_lines(path)
        for url in iterator:
            normalized = normalize_url(url)
            if normalized:
                urls.add(normalized)
    return urls


def load_entity_keys(paths: Iterable[Path]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for url in load_urls(paths):
        key = entity_key_from_url(url)
        if key is not None:
            keys.add(key)
    return keys
