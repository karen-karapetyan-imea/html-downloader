from __future__ import annotations

import gzip
import json
import logging
import random
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

import httpx

from html_downloader.discover.registry import entity_key_from_url, load_entity_keys, load_urls, normalize_url
from html_downloader.discover.urls import artmajeur_entity_from_url, artsy_entity_from_url, saatchi_entity_from_url

LOGGER = logging.getLogger(__name__)

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"
DEFAULT_INDEX = "https://www.artsper.com/sitemap.xml"
DEFAULT_SAATCHI_INDEX = "https://www.saatchiart.com/sitemap.xml"
ARTSY_ARTIST_INDEX = "https://www.artsy.net/sitemap-artists.xml"
ARTSY_ARTWORK_INDEX = "https://www.artsy.net/sitemap-artworks.xml"
DEFAULT_ARTSY_INDEXES = (ARTSY_ARTIST_INDEX, ARTSY_ARTWORK_INDEX)
DEFAULT_ARTMAJEUR_INDEX = "https://www.artmajeur.com/sitemap.xml"
_ARTSPER_CHILD_RE = ("artist", "artwork")
_ARTMAJEUR_CHILD_RE = ("members", "artworks")
_SAATCHI_CHILD_RE = ("artwork", "profile")
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

FetchBytesFn = Callable[[str], bytes]


def _tag(local: str) -> str:
    return f"{{{SITEMAP_NS}}}{local}"


def _local_name(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def parse_lastmod(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_url_entries(xml_bytes: bytes) -> list[tuple[str, str | None]]:
    root = ET.fromstring(xml_bytes)
    entries: list[tuple[str, str | None]] = []
    for url_el in root.findall(f".//{_tag('url')}"):
        loc_el = url_el.find(_tag("loc"))
        if loc_el is None or not loc_el.text:
            continue
        lastmod_el = url_el.find(_tag("lastmod"))
        lastmod = lastmod_el.text.strip() if lastmod_el is not None and lastmod_el.text else None
        entries.append((loc_el.text.strip(), lastmod))
    return entries


def parse_child_sitemap_locs(xml_bytes: bytes) -> list[str]:
    root = ET.fromstring(xml_bytes)
    locs: list[str] = []
    for sm_el in root.findall(f".//{_tag('sitemap')}"):
        loc_el = sm_el.find(_tag("loc"))
        if loc_el is not None and loc_el.text:
            locs.append(loc_el.text.strip())
    if locs:
        return locs
    return [url for url, _ in parse_url_entries(xml_bytes)]


def is_sitemap_index(xml_bytes: bytes) -> bool:
    """True when the document is a <sitemapindex>, not a <urlset>."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return False
    return _local_name(root.tag) == "sitemapindex"


def filter_artsper_child_sitemaps(urls: Iterable[str]) -> list[str]:
    selected: list[str] = []
    for url in urls:
        lower = url.lower()
        if any(token in lower for token in _ARTSPER_CHILD_RE):
            selected.append(url)
    return selected


def filter_saatchi_child_sitemaps(urls: Iterable[str]) -> list[str]:
    selected: list[str] = []
    for url in urls:
        lower = url.lower()
        if any(token in lower for token in _SAATCHI_CHILD_RE):
            selected.append(url)
    return selected


def filter_artmajeur_child_sitemaps(urls: Iterable[str]) -> list[str]:
    selected: list[str] = []
    for url in urls:
        lower = url.lower()
        if any(token in lower for token in _ARTMAJEUR_CHILD_RE):
            selected.append(url)
    return selected


@dataclass(slots=True)
class SitemapEntry:
    url: str
    lastmod: str | None
    entity_type: str
    entity_id: str

    @property
    def entity_key(self) -> tuple[str, str]:
        return (self.entity_type, self.entity_id)


@dataclass(slots=True)
class SitemapFetchStats:
    child_sitemaps: int = 0
    total_urls: int = 0
    entity_urls: int = 0
    new_entities: int = 0
    updated_entities: int = 0
    unchanged_entities: int = 0


@dataclass(slots=True)
class SitemapDiffResult:
    to_crawl: list[SitemapEntry] = field(default_factory=list)
    new_urls: list[str] = field(default_factory=list)
    updated_urls: list[str] = field(default_factory=list)
    stats: SitemapFetchStats = field(default_factory=SitemapFetchStats)

    def to_report(self) -> dict[str, Any]:
        return {
            "stats": {
                "child_sitemaps": self.stats.child_sitemaps,
                "total_urls": self.stats.total_urls,
                "entity_urls": self.stats.entity_urls,
                "new_entities": self.stats.new_entities,
                "updated_entities": self.stats.updated_entities,
                "unchanged_entities": self.stats.unchanged_entities,
                "to_crawl": len(self.to_crawl),
            },
            "new_urls_sample": self.new_urls[:50],
            "updated_urls_sample": self.updated_urls[:50],
        }


def load_lastmod_state(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    entities = payload.get("entities")
    if isinstance(entities, dict):
        return {str(k): str(v) for k, v in entities.items() if v}
    return {}


def save_lastmod_state(path: Path, entities: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "entities": dict(sorted(entities.items())),
        "last_fetch_at": datetime.now().astimezone().isoformat(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _entity_state_key(entity_type: str, entity_id: str) -> str:
    return f"{entity_type}:{entity_id}"


def fetch_sitemap_bytes(
    client: httpx.Client,
    url: str,
    *,
    max_retries: int = 5,
    base_delay: float = 2.0,
) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            response = client.get(url, timeout=60.0, follow_redirects=True)
            if response.status_code in _RETRYABLE_STATUS:
                response.raise_for_status()
            response.raise_for_status()
            return response.content
        except httpx.HTTPStatusError as exc:
            last_exc = exc
            if exc.response.status_code not in _RETRYABLE_STATUS or attempt + 1 >= max_retries:
                raise
        except httpx.TransportError as exc:
            last_exc = exc
            if attempt + 1 >= max_retries:
                raise
        delay = base_delay * (2**attempt) + random.uniform(0, 0.5)
        LOGGER.warning(
            "sitemap fetch retry url=%s attempt=%s/%s sleep=%.1fs error=%s",
            url,
            attempt + 1,
            max_retries,
            delay,
            last_exc,
        )
        time.sleep(delay)
    if last_exc:
        raise last_exc
    raise RuntimeError(f"failed to fetch sitemap url={url}")


def _maybe_decompress(url: str, body: bytes) -> bytes:
    if url.endswith(".gz") or body[:2] == b"\x1f\x8b":
        return gzip.decompress(body)
    return body


def _looks_like_html_challenge(body: bytes) -> bool:
    sample = body[:512].lstrip().lower()
    if sample.startswith(b"<!doctype html") or sample.startswith(b"<html"):
        return True
    if b"just a moment" in sample or b"cf-chl" in sample or b"cloudflare" in sample:
        return True
    return False


def fetch_sitemap_bytes_stealth(
    url: str,
    *,
    session: Any | None = None,
    proxy: dict[str, str] | None = None,
    timeout: float = 60.0,
) -> bytes:
    """Fetch sitemap XML with Chrome TLS impersonation (needed for Artsy/Cloudflare)."""
    from curl_cffi import Session

    own_session = session is None
    http = session or Session(impersonate="chrome")
    try:
        kwargs: dict[str, Any] = {"url": url, "timeout": timeout}
        if proxy:
            kwargs["proxies"] = proxy
        response = http.get(**kwargs)
        if response.status_code != 200:
            raise RuntimeError(f"sitemap fetch status={response.status_code} url={url}")
        body = bytes(response.content or b"")
        if not body:
            raise RuntimeError(f"empty sitemap body url={url}")
        if _looks_like_html_challenge(body):
            raise RuntimeError(f"cloudflare/html challenge instead of sitemap url={url}")
        return _maybe_decompress(url, body)
    finally:
        if own_session and hasattr(http, "close"):
            http.close()


def fetch_artsper_sitemap_entries(
    index_url: str = DEFAULT_INDEX,
    *,
    concurrency: int = 8,
    client: httpx.Client | None = None,
) -> list[SitemapEntry]:
    own_client = client is None
    http = client or httpx.Client(http2=True, headers={"User-Agent": "artsper-sitemap-fetch/1.0"})
    try:
        index_xml = fetch_sitemap_bytes(http, index_url)
        child_urls = filter_artsper_child_sitemaps(parse_child_sitemap_locs(index_xml))
        LOGGER.info("artsper sitemap child maps=%s", len(child_urls))

        entries: list[SitemapEntry] = []
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {pool.submit(fetch_sitemap_bytes, http, child_url): child_url for child_url in child_urls}
            for future in as_completed(futures):
                child_url = futures[future]
                try:
                    xml_bytes = future.result()
                except Exception as exc:
                    LOGGER.warning("child sitemap failed url=%s error=%s", child_url, exc)
                    continue
                for url, lastmod in parse_url_entries(xml_bytes):
                    normalized = normalize_url(url)
                    key = entity_key_from_url(normalized)
                    if key is None:
                        continue
                    entity_type, entity_id = key
                    entries.append(
                        SitemapEntry(
                            url=normalized,
                            lastmod=lastmod,
                            entity_type=entity_type,
                            entity_id=entity_id,
                        )
                    )
        return entries
    finally:
        if own_client:
            http.close()


def _entries_from_saatchi_urlset(xml_bytes: bytes) -> list[SitemapEntry]:
    entries: list[SitemapEntry] = []
    for url, lastmod in parse_url_entries(xml_bytes):
        normalized = normalize_url(url)
        key = saatchi_entity_from_url(normalized)
        if key is None:
            continue
        entity_type, entity_id = key
        entries.append(
            SitemapEntry(
                url=normalized,
                lastmod=lastmod,
                entity_type=entity_type,
                entity_id=entity_id,
            )
        )
    return entries


def _fetch_saatchi_child_batch(
    http: httpx.Client,
    child_urls: list[str],
    *,
    concurrency: int,
    max_retries: int = 5,
) -> tuple[list[SitemapEntry], list[str]]:
    entries: list[SitemapEntry] = []
    failed: list[str] = []

    def fetch_one(child_url: str) -> tuple[str, bytes | None]:
        try:
            return child_url, fetch_sitemap_bytes(http, child_url, max_retries=max_retries)
        except Exception as exc:
            LOGGER.warning("child sitemap failed url=%s error=%s", child_url, exc)
            return child_url, None

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        futures = {pool.submit(fetch_one, child_url): child_url for child_url in child_urls}
        for future in as_completed(futures):
            child_url, xml_bytes = future.result()
            if xml_bytes is None:
                failed.append(child_url)
                continue
            entries.extend(_entries_from_saatchi_urlset(xml_bytes))
    return entries, failed


def fetch_saatchi_sitemap_entries(
    index_url: str = DEFAULT_SAATCHI_INDEX,
    *,
    concurrency: int = 3,
    client: httpx.Client | None = None,
) -> list[SitemapEntry]:
    own_client = client is None
    http = client or httpx.Client(
        http2=False,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (compatible; SaatchiSitemapFetcher/1.0; "
                "+https://www.saatchiart.com/sitemap.xml)"
            ),
            "Accept": "application/xml,text/xml,*/*",
        },
    )
    try:
        index_xml = fetch_sitemap_bytes(http, index_url)
        child_urls = filter_saatchi_child_sitemaps(parse_child_sitemap_locs(index_xml))
        if not child_urls:
            child_urls = [index_url]
        LOGGER.info("saatchi sitemap child maps=%s concurrency=%s", len(child_urls), concurrency)

        entries, failed = _fetch_saatchi_child_batch(http, child_urls, concurrency=concurrency)
        if failed:
            LOGGER.info("saatchi sitemap retrying failed child maps=%s (serial)", len(failed))
            retry_entries, still_failed = _fetch_saatchi_child_batch(
                http,
                failed,
                concurrency=1,
                max_retries=8,
            )
            entries.extend(retry_entries)
            if still_failed:
                LOGGER.error(
                    "saatchi sitemap still failed count=%s sample=%s",
                    len(still_failed),
                    still_failed[:5],
                )
        return entries
    finally:
        if own_client:
            http.close()


def _collect_artsy_urlset_xml(
    index_urls: Iterable[str],
    *,
    fetch_bytes: FetchBytesFn,
    concurrency: int = 8,
) -> tuple[list[bytes], int]:
    """Walk nested sitemap indexes and return urlset XML bodies + child sitemap count."""
    pending = list(index_urls)
    seen: set[str] = set()
    urlsets: list[bytes] = []
    child_sitemaps = 0

    while pending:
        batch: list[str] = []
        while pending:
            url = pending.pop()
            if url in seen:
                continue
            seen.add(url)
            batch.append(url)
        if not batch:
            break

        results: dict[str, bytes] = {}
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {pool.submit(fetch_bytes, url): url for url in batch}
            for future in as_completed(futures):
                url = futures[future]
                try:
                    results[url] = future.result()
                except Exception as exc:
                    LOGGER.warning("artsy sitemap failed url=%s error=%s", url, exc)

        for url, xml_bytes in results.items():
            if is_sitemap_index(xml_bytes):
                children = parse_child_sitemap_locs(xml_bytes)
                child_sitemaps += len(children)
                for child in children:
                    if child not in seen:
                        pending.append(child)
                continue
            urlsets.append(xml_bytes)

    return urlsets, child_sitemaps


def fetch_artsy_sitemap_entries(
    indexes: Iterable[str] = DEFAULT_ARTSY_INDEXES,
    *,
    concurrency: int = 8,
    proxy: dict[str, str] | None = None,
    fetch_bytes: FetchBytesFn | None = None,
) -> list[SitemapEntry]:
    """Fetch Artsy artist/artwork entity URLs from nested sitemap indexes."""
    session: Any | None = None
    owned_session = False
    active_fetch = fetch_bytes

    if active_fetch is None:
        from curl_cffi import Session

        session = Session(impersonate="chrome")
        owned_session = True

        def active_fetch(url: str, _session: Any = session, _proxy: dict[str, str] | None = proxy) -> bytes:
            return fetch_sitemap_bytes_stealth(url, session=_session, proxy=_proxy)

    try:
        urlsets, child_count = _collect_artsy_urlset_xml(
            indexes,
            fetch_bytes=active_fetch,
            concurrency=concurrency,
        )
        LOGGER.info("artsy sitemap urlsets=%s child_sitemaps=%s", len(urlsets), child_count)

        entries: list[SitemapEntry] = []
        for xml_bytes in urlsets:
            for url, lastmod in parse_url_entries(xml_bytes):
                normalized = normalize_url(url)
                key = artsy_entity_from_url(normalized)
                if key is None:
                    continue
                entity_type, entity_id = key
                entries.append(
                    SitemapEntry(
                        url=normalized,
                        lastmod=lastmod,
                        entity_type=entity_type,
                        entity_id=entity_id,
                    )
                )
        return entries
    finally:
        if owned_session and session is not None and hasattr(session, "close"):
            session.close()


def fetch_artmajeur_sitemap_entries(
    index_url: str = DEFAULT_ARTMAJEUR_INDEX,
    *,
    concurrency: int = 8,
    proxy: dict[str, str] | None = None,
    fetch_bytes: FetchBytesFn | None = None,
) -> list[SitemapEntry]:
    """Fetch ArtMajeur member/artwork entity URLs from sitemap index (Cloudflare)."""
    session: Any | None = None
    owned_session = False
    active_fetch = fetch_bytes

    if active_fetch is None:
        from curl_cffi import Session

        session = Session(impersonate="chrome")
        owned_session = True

        def active_fetch(url: str, _session: Any = session, _proxy: dict[str, str] | None = proxy) -> bytes:
            return fetch_sitemap_bytes_stealth(url, session=_session, proxy=_proxy)

    try:
        index_xml = active_fetch(index_url)
        child_urls = filter_artmajeur_child_sitemaps(parse_child_sitemap_locs(index_xml))
        LOGGER.info("artmajeur sitemap child maps=%s", len(child_urls))

        entries: list[SitemapEntry] = []
        with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
            futures = {pool.submit(active_fetch, child_url): child_url for child_url in child_urls}
            for future in as_completed(futures):
                child_url = futures[future]
                try:
                    xml_bytes = future.result()
                except Exception as exc:
                    LOGGER.warning("child sitemap failed url=%s error=%s", child_url, exc)
                    continue
                for url, lastmod in parse_url_entries(xml_bytes):
                    normalized = normalize_url(url)
                    key = artmajeur_entity_from_url(normalized)
                    if key is None:
                        continue
                    entity_type, entity_id = key
                    entries.append(
                        SitemapEntry(
                            url=normalized,
                            lastmod=lastmod,
                            entity_type=entity_type,
                            entity_id=entity_id,
                        )
                    )
        return entries
    finally:
        if owned_session and session is not None and hasattr(session, "close"):
            session.close()


def known_artsy_keys_from_paths(paths: Iterable[Path]) -> set[tuple[str, str]]:
    """Load known Artsy entity keys (type, slug) from URL list / JSONL files."""
    keys: set[tuple[str, str]] = set()
    for url in load_urls(paths):
        key = artsy_entity_from_url(url)
        if key is not None:
            keys.add(key)
    return keys


def known_saatchi_keys_from_paths(paths: Iterable[Path]) -> set[tuple[str, str]]:
    """Load known Saatchi entity keys from URL list / JSONL files."""
    keys: set[tuple[str, str]] = set()
    for url in load_urls(paths):
        key = saatchi_entity_from_url(url)
        if key is not None:
            keys.add(key)
    return keys


def known_artmajeur_keys_from_paths(paths: Iterable[Path]) -> set[tuple[str, str]]:
    """Load known ArtMajeur entity keys from URL list / JSONL files."""
    keys: set[tuple[str, str]] = set()
    for url in load_urls(paths):
        key = artmajeur_entity_from_url(url)
        if key is not None:
            keys.add(key)
    return keys


def diff_sitemap_entries(
    entries: Iterable[SitemapEntry],
    *,
    known_entity_keys: set[tuple[str, str]] | None = None,
    lastmod_state: dict[str, str] | None = None,
    include_updates: bool = True,
) -> SitemapDiffResult:
    known = known_entity_keys or set()
    state = lastmod_state or {}
    result = SitemapDiffResult()
    best_by_key: dict[tuple[str, str], SitemapEntry] = {}

    for entry in entries:
        result.stats.total_urls += 1
        key = entry.entity_key
        current = best_by_key.get(key)
        if current is None:
            best_by_key[key] = entry
            continue
        current_dt = parse_lastmod(current.lastmod)
        entry_dt = parse_lastmod(entry.lastmod)
        if entry_dt and (current_dt is None or entry_dt > current_dt):
            best_by_key[key] = entry

    result.stats.entity_urls = len(best_by_key)

    for key, entry in sorted(best_by_key.items(), key=lambda item: item[1].url):
        state_key = _entity_state_key(*key)
        stored_lastmod = state.get(state_key)
        entry_lastmod = entry.lastmod

        if key not in known:
            result.stats.new_entities += 1
            result.new_urls.append(entry.url)
            result.to_crawl.append(entry)
            continue

        if not include_updates:
            result.stats.unchanged_entities += 1
            continue

        if entry_lastmod and stored_lastmod:
            entry_dt = parse_lastmod(entry_lastmod)
            stored_dt = parse_lastmod(stored_lastmod)
            if entry_dt and stored_dt and entry_dt > stored_dt:
                result.stats.updated_entities += 1
                result.updated_urls.append(entry.url)
                result.to_crawl.append(entry)
                continue
        result.stats.unchanged_entities += 1

    return result


def build_lastmod_state_from_entries(entries: Iterable[SitemapEntry]) -> dict[str, str]:
    state: dict[str, str] = {}
    for entry in entries:
        if not entry.lastmod:
            continue
        state_key = _entity_state_key(entry.entity_type, entry.entity_id)
        current = state.get(state_key)
        if current is None or (parse_lastmod(entry.lastmod) or datetime.min) > (parse_lastmod(current) or datetime.min):
            state[state_key] = entry.lastmod
    return state


def write_url_list(path: Path, urls: Iterable[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for url in urls:
            handle.write(url + "\n")
            count += 1
    return count


def known_keys_from_sources(
    *,
    known_paths: Iterable[Path] | None = None,
    source: str = "artsper",
) -> set[tuple[str, str]]:
    """Load known entity keys from URL lists / results.jsonl (no database)."""
    keys: set[tuple[str, str]] = set()
    if known_paths:
        if source == "artsy":
            keys |= known_artsy_keys_from_paths(known_paths)
        elif source == "saatchi":
            keys |= known_saatchi_keys_from_paths(known_paths)
        elif source == "artmajeur":
            keys |= known_artmajeur_keys_from_paths(known_paths)
        else:
            keys |= load_entity_keys(known_paths)
    return keys
