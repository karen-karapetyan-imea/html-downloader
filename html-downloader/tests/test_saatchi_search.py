"""Tests for Saatchi Constructor.io browse discovery."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import patch

from html_downloader.discover.saatchi_constructor import (
    ARTWORK_CATEGORIES,
    artwork_cache_path,
    expand_artworks_from_search,
    hit_to_artwork_url,
    hit_to_sitemap_entry,
    load_artwork_cache_urls,
    partition_key,
    plan_partitions,
)
from html_downloader.discover.service import run_discover
from html_downloader.discover.sitemap import SitemapEntry
from html_downloader.paths import sitemap_all_file, urls_file


def _hit(
    *,
    artwork_id: str = "10544327",
    artist_id: str = "626451",
    title: str = "Octhopuses",
) -> dict[str, Any]:
    return {
        "data": {
            "id": artwork_id,
            "url": f"/art/Painting-{title}/{artist_id}/{artwork_id}/view",
            "sku": f"P1-U{artist_id}-A{artwork_id}-T1",
        }
    }


def test_hit_to_artwork_url_and_entry() -> None:
    url = hit_to_artwork_url(_hit())
    assert url == "https://www.saatchiart.com/art/Painting-Octhopuses/626451/10544327/view"
    entry = hit_to_sitemap_entry(_hit())
    assert entry is not None
    assert entry.entity_type == "artwork"
    assert entry.entity_id == "10544327"
    assert entry.url == url


def test_hit_missing_ids_returns_none() -> None:
    assert hit_to_artwork_url({"data": {"title": "x"}}) is None
    assert hit_to_sitemap_entry({"data": {}}) is None


def test_partition_key() -> None:
    assert (
        partition_key((("artwork_category", "painting"), ("country", "ukraine")))
        == "artwork_category=painting|country=ukraine"
    )


def test_plan_partitions_splits_on_country_then_subject() -> None:
    def fake_browse(
        filters: list[tuple[str, str]] | tuple[tuple[str, str], ...],
        page: int,
        hits_per_page: int,
    ) -> dict[str, Any]:
        _ = (page, hits_per_page)
        filt = tuple(filters)
        if filt == (("artwork_category", "painting"),):
            return {
                "results": [],
                "facets": [
                    {
                        "name": "country",
                        "options": [
                            {"value": "united states", "count": 50_000},
                            {"value": "kazakhstan", "count": 500},
                        ],
                    }
                ],
            }
        if filt == (("artwork_category", "painting"), ("country", "united states")):
            return {
                "results": [],
                "facets": [
                    {
                        "name": "subject",
                        "options": [
                            {"value": "abstract", "count": 30_000},
                            {"value": "floral", "count": 8_000},
                        ],
                    }
                ],
            }
        if filt == (
            ("artwork_category", "painting"),
            ("country", "united states"),
            ("subject", "abstract"),
        ):
            return {
                "results": [],
                "facets": [
                    {
                        "name": "size_bin",
                        "options": [
                            {"value": "small", "count": 12_000},
                            {"value": "medium", "count": 9_000},
                        ],
                    }
                ],
            }
        if filt == (
            ("artwork_category", "painting"),
            ("country", "united states"),
            ("subject", "abstract"),
            ("size_bin", "small"),
        ):
            return {
                "results": [],
                "facets": [
                    {
                        "name": "us_price_bin",
                        "options": [
                            {"value": "0-500", "count": 6_000},
                            {"value": "501-1000", "count": 6_000},
                        ],
                    }
                ],
            }
        return {"results": [], "facets": []}

    planned = plan_partitions(
        ["painting"],
        browse=fake_browse,
        max_window=10_000,
    )
    keys = {key for key, _filters in planned}
    assert "artwork_category=painting|country=kazakhstan" in keys
    assert (
        "artwork_category=painting|country=united states|subject=floral" in keys
    )
    assert (
        "artwork_category=painting|country=united states|subject=abstract|size_bin=medium"
        in keys
    )
    assert (
        "artwork_category=painting|country=united states|subject=abstract|"
        "size_bin=small|us_price_bin=0-500"
        in keys
    )
    # Large abstract+small must be further split — not kept as a leaf above ceiling.
    assert (
        "artwork_category=painting|country=united states|subject=abstract|size_bin=small"
        not in keys
    )


def test_expand_artworks_resume_and_force(tmp_path: Path) -> None:
    state_path = tmp_path / "saatchi_search_browse_state.json"
    pages: dict[str, int] = {}

    def fake_browse(
        filters: list[tuple[str, str]] | tuple[tuple[str, str], ...],
        page: int,
        hits_per_page: int,
    ) -> dict[str, Any]:
        key = partition_key(filters)
        pages[key] = pages.get(key, 0) + 1
        # Plan path: category → one small country.
        if tuple(filters) == (("artwork_category", "collage"),) and page == 1:
            return {
                "results": [],
                "facets": [
                    {"name": "country", "options": [{"value": "malta", "count": 2}]},
                ],
            }
        if tuple(filters) == (("artwork_category", "collage"), ("country", "malta")):
            if page == 1:
                return {
                    "results": [_hit(artwork_id="1", artist_id="10"), _hit(artwork_id="2", artist_id="10")],
                    "facets": [],
                }
            return {"results": [], "facets": []}
        return {"results": [], "facets": []}

    first = expand_artworks_from_search(
        state_path=state_path,
        categories=["collage"],
        browse=fake_browse,
        delay=0.0,
        force=False,
        max_window=10_000,
    )
    assert len(first) == 2
    cache = artwork_cache_path(state_path)
    assert cache.exists()
    urls = load_artwork_cache_urls(cache)
    assert len(urls) == 2

    # Resume: partition marked done → no new rows.
    second = expand_artworks_from_search(
        state_path=state_path,
        categories=["collage"],
        browse=fake_browse,
        delay=0.0,
        force=False,
        max_window=10_000,
    )
    assert second == []
    assert len(load_artwork_cache_urls(cache)) == 2

    # Force clears and re-writes.
    third = expand_artworks_from_search(
        state_path=state_path,
        categories=["collage"],
        browse=fake_browse,
        delay=0.0,
        force=True,
        max_window=10_000,
    )
    assert len(third) == 2


def test_discover_streams_search_cache(tmp_path: Path) -> None:
    sitemap_entries = [
        SitemapEntry(
            url="https://www.saatchiart.com/art/Painting-A/1/100/view",
            lastmod=None,
            entity_type="artwork",
            entity_id="100",
        ),
    ]
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    proxy_file = tmp_path / "proxy.txt"
    proxy_file.write_text("127.0.0.1:8080:user:pass\n", encoding="utf-8")
    state_path = state_root / "saatchi_search_browse_state.json"
    cache_path = artwork_cache_path(state_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps(
            {
                "entity_type": "artwork",
                "entity_id": "200",
                "url": "https://www.saatchiart.com/art/Painting-B/2/200/view",
                "lastmod": None,
            }
        )
        + "\n"
        + json.dumps(
            {
                "entity_type": "artwork",
                "entity_id": "100",
                "url": "https://www.saatchiart.com/art/Painting-A/1/100/view",
                "lastmod": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    def fake_expand(**kwargs: Any) -> list[SitemapEntry]:
        _ = kwargs
        return []

    with (
        patch("html_downloader.discover.service.fetch_entries", return_value=sitemap_entries),
        patch(
            "html_downloader.discover.saatchi_constructor.expand_artworks_from_search",
            side_effect=fake_expand,
        ),
    ):
        result = run_discover(
            marketplace="saatchi",
            data_root=data_root,
            state_root=state_root,
            crawl_date=date(2026, 9, 23),
            incremental=False,
            include_updates=True,
            update_state=False,
            proxy_file=str(proxy_file),
            concurrency=1,
            dry_run=False,
            expand_search=True,
        )

    all_urls = sitemap_all_file(result.job).read_text(encoding="utf-8").strip().splitlines()
    crawl_urls = urls_file(result.job).read_text(encoding="utf-8").strip().splitlines()
    assert "https://www.saatchiart.com/art/Painting-A/1/100/view" in all_urls
    assert "https://www.saatchiart.com/art/Painting-B/2/200/view" in all_urls
    # Deduped: cache row for 100 already in sitemap entries.
    assert all_urls.count("https://www.saatchiart.com/art/Painting-A/1/100/view") == 1
    assert result.all_count == 2
    assert set(crawl_urls) == set(all_urls)


def test_artwork_categories_seed() -> None:
    assert "painting" in ARTWORK_CATEGORIES
    assert "photography" in ARTWORK_CATEGORIES
    assert len(ARTWORK_CATEGORIES) == 9
