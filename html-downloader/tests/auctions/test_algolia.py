"""Tests for Invaluable Algolia archive browse discovery."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from html_downloader.auctions.invaluable import _merge_entry
from html_downloader.auctions.invaluable_algolia import (
    AlgoliaBrowseState,
    build_lot_url,
    expand_lots_from_algolia,
    hit_to_sitemap_entry,
    hits_to_sitemap_entries,
    iter_lot_cache_rows,
    load_lot_cache_ids,
    lot_cache_path,
    slugify,
    year_filter,
)
from html_downloader.auctions.service import run_auction_discover
from html_downloader.discover.sitemap import SitemapEntry


def test_slugify_and_build_lot_url() -> None:
    assert slugify("Sunset Over Water!") == "sunset-over-water"
    url = build_lot_url("Sunset Over Water!", "12", "AaBbCcDdEe")
    assert url == (
        "https://www.invaluable.com/auction-lot/sunset-over-water-12-c-aabbccddee"
    )


def test_year_filter_bounds() -> None:
    # 2024-01-01 UTC .. 2025-01-01 UTC
    assert year_filter(2024) == (
        "dateTimeUTCUnix>=1704067200 AND dateTimeUTCUnix<1735689600"
    )


def test_hit_to_sitemap_entry_ok() -> None:
    hit = {
        "lotRef": "AaBbCcDdEe",
        "lotTitle": "Blue Study",
        "lotNumber": 7,
        "dateTimeLocal": "2024-06-15T10:00:00",
    }
    entry = hit_to_sitemap_entry(hit)
    assert entry is not None
    assert entry.entity_type == "lot"
    assert entry.entity_id == "aabbccddee"
    assert entry.lastmod == "2024-06-15"
    assert entry.url.endswith("-7-c-aabbccddee")


def test_hit_to_sitemap_entry_skips_banned_and_missing_ref() -> None:
    assert hit_to_sitemap_entry({"banned": True, "lotRef": "aaaaaaaaaa"}) is None
    assert hit_to_sitemap_entry({"lotTitle": "x"}) is None


def test_hits_to_sitemap_entries_filters() -> None:
    hits = [
        {"lotRef": "aaaaaaaaaa", "lotTitle": "A", "lotNumber": 1, "dateTimeLocal": "2020-01-01"},
        {"banned": True, "lotRef": "bbbbbbbbbb"},
        {"lotTitle": "no ref"},
    ]
    entries = hits_to_sitemap_entries(hits)
    assert len(entries) == 1
    assert entries[0].entity_id == "aaaaaaaaaa"


def test_merge_dedupes_algolia_vs_xml() -> None:
    best: dict[tuple[str, str], SitemapEntry] = {}
    xml = SitemapEntry(
        url="https://www.invaluable.com/auction-lot/old-title-1-c-aaaaaaaaaa",
        lastmod="2024-01-01",
        entity_type="lot",
        entity_id="aaaaaaaaaa",
    )
    _merge_entry(best, xml)
    algolia = SitemapEntry(
        url="https://www.invaluable.com/auction-lot/new-title-1-c-aaaaaaaaaa",
        lastmod="2024-06-01",
        entity_type="lot",
        entity_id="aaaaaaaaaa",
    )
    _merge_entry(best, algolia)
    assert len(best) == 1
    kept = best[("lot", "aaaaaaaaaa")]
    assert kept.lastmod == "2024-06-01"
    assert kept.entity_id == "aaaaaaaaaa"
    assert "-c-aaaaaaaaaa" in kept.url


def test_load_lot_cache_ids_and_iter(tmp_path: Path) -> None:
    cache = tmp_path / "lots.jsonl"
    cache.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "entity_id": "aaaaaaaaaa",
                        "url": "https://www.invaluable.com/auction-lot/a-1-c-aaaaaaaaaa",
                        "lastmod": "2024-01-01",
                    }
                ),
                json.dumps(
                    {
                        "entity_id": "bbbbbbbbbb",
                        "url": "https://www.invaluable.com/auction-lot/b-2-c-bbbbbbbbbb",
                        "lastmod": "2024-02-01",
                    }
                ),
                "{bad",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    ids = load_lot_cache_ids(cache)
    assert ids == {"aaaaaaaaaa", "bbbbbbbbbb"}
    rows = list(iter_lot_cache_rows(cache))
    assert len(rows) == 2
    assert rows[0][0] == "aaaaaaaaaa"


def test_expand_lots_cursor_walk_and_resume(tmp_path: Path) -> None:
    state_path = tmp_path / "algolia_browse_state.json"
    pages: dict[str | None, dict[str, Any]] = {
        None: {
            "hits": [
                {
                    "lotRef": "aaaaaaaaaa",
                    "lotTitle": "One",
                    "lotNumber": 1,
                    "dateTimeLocal": "2024-01-02",
                }
            ],
            "cursor": "c1",
        },
        "c1": {
            "hits": [
                {
                    "lotRef": "bbbbbbbbbb",
                    "lotTitle": "Two",
                    "lotNumber": 2,
                    "dateTimeLocal": "2024-02-03",
                }
            ],
        },
    }

    def browse(filters: str, cursor: str | None) -> dict[str, Any] | None:
        assert "dateTimeUTCUnix" in filters
        return pages[cursor]

    entries = expand_lots_from_algolia(
        state_path=state_path,
        from_year=2024,
        to_year=2024,
        workers=1,
        delay=0.0,
        browse=browse,
    )
    ids = {e.entity_id for e in entries}
    assert ids == {"aaaaaaaaaa", "bbbbbbbbbb"}
    cache = lot_cache_path(state_path)
    assert load_lot_cache_ids(cache) == ids
    state = AlgoliaBrowseState(state_path)
    assert state.status("2024") == "done"

    # Second run: partitions done → return [] (stream cache from disk for URLs)
    calls: list[str | None] = []

    def browse_again(filters: str, cursor: str | None) -> dict[str, Any] | None:
        calls.append(cursor)
        raise AssertionError("should not browse when partitions are done")

    cached = expand_lots_from_algolia(
        state_path=state_path,
        from_year=2024,
        to_year=2024,
        workers=1,
        delay=0.0,
        browse=browse_again,
    )
    assert calls == []
    assert cached == []
    assert load_lot_cache_ids(cache) == ids


def test_expand_lots_invalid_cursor_restart(tmp_path: Path) -> None:
    state_path = tmp_path / "algolia_browse_state.json"
    calls: list[str | None] = []

    def browse(filters: str, cursor: str | None) -> dict[str, Any] | None:
        calls.append(cursor)
        if len(calls) == 1:
            return {"_invalid_cursor": True}
        return {
            "hits": [
                {
                    "lotRef": "cccccccccc",
                    "lotTitle": "Restarted",
                    "lotNumber": 3,
                    "dateTimeLocal": "2023-05-01",
                }
            ],
        }

    entries = expand_lots_from_algolia(
        state_path=state_path,
        from_year=2023,
        to_year=2023,
        workers=1,
        delay=0.0,
        browse=browse,
    )
    assert calls == [None, None]
    assert len(entries) == 1
    assert entries[0].entity_id == "cccccccccc"
    assert AlgoliaBrowseState(state_path).status("2023") == "done"


def test_algolia_force_clears_cache(tmp_path: Path) -> None:
    state_path = tmp_path / "algolia_browse_state.json"

    def browse_a(filters: str, cursor: str | None) -> dict[str, Any] | None:
        return {
            "hits": [
                {
                    "lotRef": "aaaaaaaaaa",
                    "lotTitle": "A",
                    "lotNumber": 1,
                    "dateTimeLocal": "2022-01-01",
                }
            ],
        }

    expand_lots_from_algolia(
        state_path=state_path,
        from_year=2022,
        to_year=2022,
        workers=1,
        delay=0.0,
        browse=browse_a,
    )

    def browse_b(filters: str, cursor: str | None) -> dict[str, Any] | None:
        return {
            "hits": [
                {
                    "lotRef": "bbbbbbbbbb",
                    "lotTitle": "B",
                    "lotNumber": 2,
                    "dateTimeLocal": "2022-02-01",
                }
            ],
        }

    entries = expand_lots_from_algolia(
        state_path=state_path,
        from_year=2022,
        to_year=2022,
        workers=1,
        delay=0.0,
        force=True,
        browse=browse_b,
    )
    assert {e.entity_id for e in entries} == {"bbbbbbbbbb"}
    assert load_lot_cache_ids(lot_cache_path(state_path)) == {"bbbbbbbbbb"}


def test_discover_streams_algolia_cache_into_job(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    state_root = tmp_path / "state"
    proxy_file = tmp_path / "proxy.txt"
    proxy_file.write_text("127.0.0.1:8080:user:pass\n", encoding="utf-8")

    memory_entries = [
        SitemapEntry(
            url="https://www.invaluable.com/catalog/0ak1fxhm3a",
            lastmod="2026-09-02",
            entity_type="catalog",
            entity_id="0ak1fxhm3a",
        ),
        SitemapEntry(
            url="https://www.invaluable.com/auction-lot/mem-1-c-mmmmmmmmmm",
            lastmod="2026-09-01",
            entity_type="lot",
            entity_id="mmmmmmmmmm",
        ),
    ]

    algolia_state = state_root / "auctions" / "invaluable_algolia_browse_state.json"
    algolia_state.parent.mkdir(parents=True, exist_ok=True)
    cache = lot_cache_path(algolia_state)
    cache.write_text(
        json.dumps(
            {
                "entity_id": "aaaaaaaaaa",
                "url": "https://www.invaluable.com/auction-lot/cache-1-c-aaaaaaaaaa",
                "lastmod": "2024-01-01",
            }
        )
        + "\n"
        + json.dumps(
            {
                "entity_id": "mmmmmmmmmm",
                "url": "https://www.invaluable.com/auction-lot/dup-c-mmmmmmmmmm",
                "lastmod": "2024-01-02",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with patch(
        "html_downloader.auctions.service.fetch_auction_entries",
        return_value=memory_entries,
    ):
        result = run_auction_discover(
            auction_house="invaluable",
            data_root=data_root,
            state_root=state_root,
            job_month="2026-09",
            incremental=False,
            include_updates=True,
            update_state=False,
            proxy_file=str(proxy_file),
            concurrency=1,
            dry_run=False,
            expand_artist_sold=False,
            expand_algolia=True,
            expand_auctions_list=False,
            include_hubs=False,
        )

    job = result.job
    all_urls = (job / "sitemap_all.txt").read_text(encoding="utf-8").strip().splitlines()
    crawl_urls = (job / "urls.txt").read_text(encoding="utf-8").strip().splitlines()
    assert "https://www.invaluable.com/catalog/0ak1fxhm3a" in all_urls
    assert "https://www.invaluable.com/auction-lot/mem-1-c-mmmmmmmmmm" in all_urls
    assert "https://www.invaluable.com/auction-lot/cache-1-c-aaaaaaaaaa" in all_urls
    # Duplicate lot id already in memory entries is not streamed again
    assert sum(1 for u in all_urls if "mmmmmmmmmm" in u) == 1
    assert result.all_count == 3
    assert result.crawl_count == 3
    assert set(crawl_urls) == set(all_urls)
