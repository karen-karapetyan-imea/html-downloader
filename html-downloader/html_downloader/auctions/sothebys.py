"""Sotheby's discovery entry point: current platform via Algolia + legacy HTML archive.

Sotheby's sitemaps (``/sitemap.xml`` → ``sitemap-YYYYMM.xml``) contain only
editorial pages, so lot and auction URLs come from three sources:

- ``sothebys_algolia``: current platform (~mid-2019 onward).
- ``sothebys_legacy``: late-2004–2019 sales from the ``/en/results`` listing.
- ``sothebys_search``: the public site-search index (1999 onward), which
  adds legacy lots the other two cannot reach.

Modes:

- Full archive: every real lot plus auction landing pages.
- Art-only: Algolia art ``departments`` / ``objectTypes`` facets, and legacy /
  site-search records tagged with art departments; separate state files and
  JSONL caches (never mixed with the full archive).

No lot parsing here — download saves HTML only.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator, Sequence
from pathlib import Path

from html_downloader.auctions.sitemap_cache import (
    TypedRow,
    iter_typed_cache_rows,
    save_auction_lastmod_state,
    typed_cache_path,
)
from html_downloader.auctions.sothebys_algolia import (
    ART_FACETS,
    DEFAULT_SEARCH_DELAY,
    DEFAULT_SEARCH_WORKERS,
    LOTS_INDEX,
    MAX_RETURN_NEW,
    SothebysAlgoliaClient,
    build_art_filter,
    expand_lots_from_algolia,
    list_auctions,
)
from html_downloader.auctions.sothebys_legacy import (
    DEFAULT_LEGACY_DELAY,
    DEFAULT_LEGACY_WORKERS,
    LEGACY_ART_DEPARTMENTS,
    LegacyHtmlClient,
    expand_legacy_archive,
)
from html_downloader.auctions.sothebys_search import (
    DEFAULT_SEARCH_CONFIG,
    DEFAULT_SITE_SEARCH_WORKERS,
    expand_site_search,
    make_site_search_client,
)
from html_downloader.auctions.urls import sothebys_entity_from_url
from html_downloader.discover.registry import load_urls
from html_downloader.discover.sitemap import SitemapEntry

LOGGER = logging.getLogger(__name__)

DEFAULT_SOTHEBYS_INDEX = f"https://kar1ueupjd-dsn.algolia.net/1/indexes/{LOTS_INDEX}/query"


def lot_cache_path(state_path: Path) -> Path:
    """Sidecar JSONL of discovered lot/sale URLs (source of truth for the archive)."""
    return typed_cache_path(state_path)


def iter_lot_cache_rows(path: Path) -> Iterator[TypedRow]:
    """Yield (entity_type, entity_id, url, lastmod) from the Sotheby's JSONL cache."""
    return iter_typed_cache_rows(path, label="sothebys")


def legacy_departments_for(art_categories: Sequence[str] | None) -> list[str] | None:
    """
    Results-page departments for the legacy pass (None = all sales).

    The default Algolia art facets map to ``LEGACY_ART_DEPARTMENTS`` (which
    adds legacy-only painting departments); custom names are used as-is.
    """
    if not art_categories:
        return None
    if set(art_categories) == set(ART_FACETS):
        return list(LEGACY_ART_DEPARTMENTS)
    return list(dict.fromkeys(art_categories))


def site_search_departments_for(art_categories: Sequence[str] | None) -> list[str] | None:
    """Site-search ``departments`` for art-only mode (legacy and Algolia names share it)."""
    legacy = legacy_departments_for(art_categories)
    if legacy is None:
        return None
    return list(dict.fromkeys([*legacy, *(art_categories or ())]))


def fetch_sothebys_entries(
    *,
    state_path: Path,
    art_categories: Sequence[str] | None = None,
    workers: int = DEFAULT_SEARCH_WORKERS,
    delay: float = DEFAULT_SEARCH_DELAY,
    force: bool = False,
    max_sales: int | None = None,
    client: SothebysAlgoliaClient | None = None,
    legacy_state_path: Path | None = None,
    legacy_workers: int = DEFAULT_LEGACY_WORKERS,
    legacy_delay: float = DEFAULT_LEGACY_DELAY,
    legacy_force: bool = False,
    legacy_client: LegacyHtmlClient | None = None,
    site_search_state_path: Path | None = None,
    site_search_workers: int = DEFAULT_SITE_SEARCH_WORKERS,
    site_search_force: bool = False,
    site_search_client: SothebysAlgoliaClient | None = None,
) -> list[SitemapEntry]:
    """
    Algolia pass, then the legacy pass, then the site-search pass.

    ``art_categories`` switches every pass to art-only mode; the legacy and
    site-search passes run only when their state path is set. Callers pass
    different state paths per mode. ``max_sales`` caps each pass separately
    (auctions / sales / month windows).

    Every pass skips keys held by the other sources' caches, so the three
    JSONL caches stay disjoint and can be streamed without re-deduplication.
    """
    caches = {
        name: lot_cache_path(path)
        for name, path in (
            ("algolia", state_path),
            ("legacy", legacy_state_path),
            ("site_search", site_search_state_path),
        )
        if path is not None
    }

    def others(name: str) -> list[Path]:
        return [path for key, path in caches.items() if key != name]

    art_filter = build_art_filter(art_categories) if art_categories else None
    own_client = client is None
    algolia = client or SothebysAlgoliaClient(delay=delay)
    try:
        auctions = list_auctions(algolia.query)
        entries = expand_lots_from_algolia(
            state_path=state_path,
            auctions=auctions,
            query=algolia.query,
            art_filter=art_filter,
            include_sales=art_filter is None,
            workers=workers,
            force=force,
            max_sales=max_sales,
            known_cache_paths=others("algolia"),
        )
    finally:
        if own_client:
            algolia.close()

    html_client = legacy_client

    def html() -> LegacyHtmlClient:
        nonlocal html_client
        if html_client is None:
            html_client = LegacyHtmlClient(delay=legacy_delay)
        return html_client

    if legacy_state_path is not None:
        entries.extend(
            expand_legacy_archive(
                state_path=legacy_state_path,
                get=html().get,
                departments=legacy_departments_for(art_categories),
                workers=legacy_workers,
                force=legacy_force,
                max_sales=max_sales,
                known_cache_paths=others("legacy"),
            )
        )

    if site_search_state_path is not None:
        own_search = site_search_client is None
        if site_search_client is not None:
            search_client, index = site_search_client, DEFAULT_SEARCH_CONFIG.index
        else:
            search_client, index = make_site_search_client(html().get, delay=delay)
        try:
            entries.extend(
                expand_site_search(
                    state_path=site_search_state_path,
                    query=search_client.query,
                    index=index,
                    departments=site_search_departments_for(art_categories),
                    workers=site_search_workers,
                    force=site_search_force,
                    max_windows=max_sales,
                    known_cache_paths=others("site_search"),
                )
            )
        finally:
            if own_search:
                search_client.close()
    return entries[:MAX_RETURN_NEW]


def known_sothebys_keys_from_paths(paths: Iterable[Path]) -> set[tuple[str, str]]:
    """Load known Sotheby's entity keys from URL list / JSONL files."""
    keys: set[tuple[str, str]] = set()
    for url in load_urls(paths):
        key = sothebys_entity_from_url(url)
        if key is not None:
            keys.add(key)
    return keys


__all__ = [
    "DEFAULT_SOTHEBYS_INDEX",
    "fetch_sothebys_entries",
    "iter_lot_cache_rows",
    "known_sothebys_keys_from_paths",
    "legacy_departments_for",
    "lot_cache_path",
    "save_auction_lastmod_state",
    "site_search_departments_for",
]
